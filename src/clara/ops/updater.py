"""Updates: tagged releases only, prepared while the old server still runs, with automatic rollback.

    1. You pick a release tag (console /update, Admin page). Nothing installs before that.
    2. Its assets are downloaded from GitHub Releases (CLARA_RELEASE_REPO): `clara-<tag>.tar.gz`, `SHA256SUMS`,
       `manifest.json` and, when CLARA_RELEASE_PUBKEY is set, `SHA256SUMS.sig` (an Ed25519 signature, then required).
       Every file is checked against its sum.
    3. The tarball is unpacked (no absolute path, no `..`, no link) into `<releases>/<tag>`, and a virtualenv is built
       with uv from the release's hash-pinned `requirements.lock` and its wheel. The web site comes built inside it.
    4. A preflight runs the new code against a *copy* of the database: settings, migrations, the secrets key.
    5. The sandbox image the release pins (by digest) is pulled.
    6. A backup is taken, the plan is written to `run/update.json`, and the server stops (drain, bounded) with exit
       code 75. The launcher switches the `current` link, starts the new release and waits up to 60 s for `/health` and
       the new boot id; if that fails it switches back, restores the database if a migration ran, starts the old
       release again and records why: the restarted server raises an alert.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .. import __version__
from ..db import backup_database
from ..db.migrate import schema_version
from ..timeutil import utcnow
from .lifecycle import EXIT_UPDATE

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

API = "https://api.github.com"
TAG = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
MAX_ASSET = 600_000_000
PLAN = "update.json"
RESULT = "update-result.json"


class UpdateError(RuntimeError):
    pass


def check_sums(sums: str, folder: Path, names: list[str]) -> None:
    expected = {}
    for line in sums.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            expected[parts[1].lstrip("*")] = parts[0].lower()
    for name in names:
        digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        if expected.get(name) != digest:
            raise UpdateError(f"{name} does not match its checksum: not installed.")


def check_signature(sums: bytes, signature: bytes, public_key: str) -> None:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(base64.b64decode(signature.strip()), sums)
    except (InvalidSignature, ValueError) as error:
        raise UpdateError(f"The release's signature does not match CLARA_RELEASE_PUBKEY ({type(error).__name__}).") from None


def safe_extract(archive: Path, destination: Path) -> None:
    """Unpack, refusing absolute paths, `..`, links and devices."""
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts:
                raise UpdateError(f"The archive holds an unsafe path: {name}")
            if not (member.isfile() or member.isdir()):
                raise UpdateError(f"The archive holds a link or a device: {name}")
        tar.extractall(destination, filter="data")


class Updater:
    def __init__(self, s: Services):
        self.s = s
        self.launched = os.environ.get("CLARA_LAUNCHED") == "1"
        self.current = os.environ.get("CLARA_RELEASE", "")
        self.state = "idle"
        self.detail = ""
        self._task: asyncio.Task | None = None

    def describe(self) -> dict:
        return {"launched": self.launched, "current": self.current or f"source {__version__}", "state": self.state,
                "detail": self.detail, "repo": self.s.settings.release_repo,
                "signed": bool(self.s.settings.release_pubkey)}

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "clara-updater"}
        token = self.s.secrets.get("release_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def releases(self) -> list[dict]:
        repo = self.s.settings.release_repo
        if not repo:
            raise UpdateError("Set CLARA_RELEASE_REPO (owner/name) to list releases.")
        async with httpx.AsyncClient(timeout=20, headers=self._headers(), follow_redirects=True) as http:
            response = await http.get(f"{API}/repos/{repo}/releases", params={"per_page": 20})
        if response.is_error:
            raise UpdateError(f"GitHub answered HTTP {response.status_code}.")
        found = []
        for release in response.json():
            if release.get("draft") or not TAG.match(str(release.get("tag_name", ""))):
                continue
            found.append({"tag": release["tag_name"], "name": release.get("name") or release["tag_name"],
                          "published_at": release.get("published_at"), "prerelease": bool(release.get("prerelease")),
                          "notes": str(release.get("body") or "")[:6000], "current": release["tag_name"] == self.current,
                          "assets": {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}})
        return found

    async def start(self, tag: str) -> str:
        if not self.launched:
            return "Updates need the launcher (clara-launcher under systemd): this server was started by hand."
        if not TAG.match(tag):
            return f"{tag!r} is not a release tag."
        if self._task is not None and not self._task.done():
            return f"An update is already being prepared ({self.state})."
        if self.s.lifecycle.stopping:
            return "The server is stopping."
        self._task = asyncio.ensure_future(self._prepare(tag))
        return f"Preparing {tag}: download, checks, preflight. Clara restarts into it when it is ready."

    def _progress(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        log.info("update: %s %s", state, detail)

    async def _prepare(self, tag: str) -> None:
        settings = self.s.settings
        releases = settings.releases_dir
        staging = releases / ".staging" / tag
        try:
            self._progress("downloading", tag)
            release = next((item for item in await self.releases() if item["tag"] == tag), None)
            if release is None:
                raise UpdateError(f"No release {tag}.")
            archive_name = f"clara-{tag}.tar.gz"
            wanted = [archive_name, "SHA256SUMS", "manifest.json"] + (["SHA256SUMS.sig"] if settings.release_pubkey else [])
            missing = [name for name in wanted if name not in release["assets"]]
            if missing:
                raise UpdateError(f"The release lacks {', '.join(missing)}.")
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, mode=0o700)
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, read=300), headers=self._headers(),
                                         follow_redirects=True) as http:
                for name in wanted:
                    await self._download(http, release["assets"][name], staging / name)
            self._progress("checking", "checksums and signature")
            sums = (staging / "SHA256SUMS").read_bytes()
            if settings.release_pubkey:
                check_signature(sums, (staging / "SHA256SUMS.sig").read_bytes(), settings.release_pubkey)
            check_sums(sums.decode(), staging, [archive_name, "manifest.json"])
            manifest = json.loads((staging / "manifest.json").read_text())
            target = releases / tag
            if not target.exists():
                self._progress("installing", f"unpacking into {target}")
                partial = releases / f".{tag}.partial"
                shutil.rmtree(partial, ignore_errors=True)
                partial.mkdir(parents=True)
                await asyncio.to_thread(safe_extract, staging / archive_name, partial)
                await self._venv(partial, manifest)
                partial.rename(target)
            self._progress("preflight", "the new code against a copy of the database")
            await self._preflight(target, staging)
            image = manifest.get("sandbox_image")
            if image and self.s.sandbox is not None and self.s.settings.run == "auto":
                self._progress("image", image)
                await self._run(settings.podman, "pull", image, timeout=1200)
                self.s.runtime.set("sandbox_image", image)
            self._progress("backup")
            stamp = utcnow().strftime("%Y%m%d-%H%M%S")
            backup = backup_database(self.s.db.connection, settings.backups_dir / "pre-update" / f"clara-{stamp}.db")
            current = (releases.parent / "current").resolve()
            plan = {"from": str(current), "to": str(target), "tag": tag, "backup": str(backup),
                    "schema_before": schema_version(self.s.db.connection), "at": utcnow().isoformat(timespec="seconds")}
            plan_path = settings.run_dir / PLAN
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            os.chmod(plan_path, 0o600)
            self._progress("ready", f"restarting into {tag}")
            self.s.events.publish("update", f"Updating to {tag}", "Clara restarts now; she checks herself and rolls "
                                  "back on her own if the new release does not come up.")
            self.s.lifecycle.stop(exit_code=EXIT_UPDATE)
        except Exception as error:
            self._progress("failed", str(error))
            self.s.alerts.raise_("update", f"The update to {tag} was not installed", str(error), cooldown=60)
        finally:
            shutil.rmtree(staging / "preflight", ignore_errors=True)

    async def _download(self, http: httpx.AsyncClient, url: str, path: Path) -> None:
        async with http.stream("GET", url) as response:
            if response.is_error:
                raise UpdateError(f"Download of {path.name} failed: HTTP {response.status_code}.")
            size = 0
            with open(path, "wb") as handle:  # noqa: ASYNC230 - one download, written as it comes
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_ASSET:
                        raise UpdateError(f"{path.name} is too big.")
                    handle.write(chunk)

    async def _run(self, *args: str, timeout: float = 900, env: dict | None = None) -> str:
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.STDOUT, env=env)
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise UpdateError(f"{Path(args[0]).name} took too long.") from None
        text = out.decode(errors="replace").strip()
        if process.returncode != 0:
            raise UpdateError(f"{Path(args[0]).name} {args[1] if len(args) > 1 else ''} failed: {text[-800:]}")
        return text

    async def _venv(self, root: Path, manifest: dict) -> None:
        uv = shutil.which("uv") or "/usr/local/bin/uv"
        python = manifest.get("python", "3.12")
        await self._run(uv, "venv", "--python", python, str(root / ".venv"))
        interpreter = str(root / ".venv" / "bin" / "python")
        await self._run(uv, "pip", "install", "--python", interpreter, "--require-hashes", "-r", str(root / "requirements.lock"))
        wheels = sorted(root.glob("clara-*.whl"))  # noqa: ASYNC240 - a folder of a few files
        if not wheels:
            raise UpdateError("The release has no wheel.")
        await self._run(uv, "pip", "install", "--python", interpreter, "--no-deps", str(wheels[-1]))

    async def _preflight(self, root: Path, staging: Path) -> None:
        folder = staging / "preflight"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        copy = backup_database(self.s.db.connection, folder / "clara.db")
        env = dict(os.environ)
        await self._run(str(root / ".venv" / "bin" / "clara"), "preflight", "--database", str(copy), timeout=300, env=env)

    def report(self) -> None:
        """At start: what the launcher found after the last update (a success, or a rollback and why)."""
        path = self.s.settings.run_dir / RESULT
        if not path.exists():
            return
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            result = {}
        path.unlink(missing_ok=True)
        if result.get("result") == "ok":
            self.s.events.publish("update", f"Updated to {result.get('tag')}", "The new release is running.")
            self.s.alerts.clear("update")
        elif result:
            self.s.alerts.raise_("update", f"The update to {result.get('tag')} was rolled back",
                                 str(result.get("reason") or "the new release did not come up"), cooldown=60)
