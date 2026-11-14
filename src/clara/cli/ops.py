"""`clara`: operations on this machine, run as the service user (`sudo -u clara clara …`).

clara token create <name> <client> [--approvals all|safe|none]    a client token (shown once)
clara token list | revoke <name>
clara secrets new-key                       a fresh raw key for the systemd credential
clara secrets list | set <name> | unset <name>
clara secrets rotate --new-key-file <path>  re-encrypt every secret under a new key
clara moodle connect | disconnect           sign in to Moodle (paste back the link of the mobile sign-in), or forget the token
clara auriga connect | disconnect           sign in to Auriga (paste back the address of the page you land on), or forget it
clara auriga forget-password                forget the school email and password kept to sign in to Auriga again
clara event fire <source> [field=value ...] fire an event of an event source from this machine, with your API token (a trusted source: scope `all`)
clara doctor [--pull]                       check what Clara needs (rootless Podman, Tailscale, providers…)
clara backup [init-remote]                  a backup now (and the remote copy), or create the restic repository
clara restore --verify <file>               open a backup, check it, count its rows
clara restore <file>                        put a backup back (the server must be stopped)
clara mode import-legacy                    print a default-mode file made from the old auto levels and rules (writes nothing)
clara migrate | purge                       bring the schema up to date | vacuum the file (server stopped)
clara preflight --database <copy>           what the updater runs on a copy before switching releases
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import secrets as secrets_module
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import httpx

from .. import __version__
from ..auth import APPROVALS, CLIENTS, TokenError, Tokens
from ..connectors.auriga import authorize_url, lapse_phrase
from ..connectors.auriga import sign_in as auriga_sign_in
from ..connectors.drive import pkce
from ..connectors.moodle import launch_url
from ..connectors.moodle import sign_in as moodle_sign_in
from ..connectors.school import LABELS, SECRETS, SchoolError
from ..connectors.store import Connections
from ..db import AlreadyRunning, Database, InstanceLock, SchemaTooNew, backup_database, migrate
from ..ops.backups import verify
from ..ops.private import check_private
from ..secrets import KNOWN_SECRETS, LOGIN_SECRETS, KeyMaterial, SecretError, SecretsStore, load_key_material
from ..secrets.store import new_raw_key
from ..settings import Settings, SettingsError, load_settings

OK, WARN, FAIL = "PASS", "WARN", "FAIL"


def _settings() -> Settings:
    try:
        return load_settings()
    except SettingsError as error:
        raise SystemExit(str(error)) from None


def _database(settings: Settings) -> Database:
    try:
        migrate(settings.database, settings.backups_dir)
    except SchemaTooNew as error:
        raise SystemExit(str(error)) from None
    return Database(settings.database)


def _stopped(settings: Settings) -> InstanceLock:
    lock = InstanceLock(settings.lock_file)
    try:
        lock.acquire()
    except AlreadyRunning as error:
        raise SystemExit(f"{error}\nStop it first: sudo systemctl stop clara") from None
    return lock


def _store(settings: Settings, db: Database) -> SecretsStore:
    try:
        return SecretsStore(db, load_key_material(settings.secret_key_file, settings.secret_key))
    except SecretError as error:
        raise SystemExit(str(error)) from None


# ----------------------------------------------------------------------
def token(args: argparse.Namespace) -> int:
    settings = _settings()
    tokens = Tokens(_database(settings))
    try:
        if args.action == "create":
            info, secret = tokens.create(args.name, args.client, args.approvals)
            print(f"Token {info.name} ({info.client}, may answer: {info.approvals}). Shown once:\n{secret}")
        elif args.action == "revoke":
            print(f"Revoked {tokens.revoke(args.name).name}: refused from now on, no restart needed.")
        else:
            for info in tokens.list():
                state = f"revoked {info.revoked_at}" if info.revoked_at else f"last used {info.last_used_at or 'never'}"
                print(f"{info.name:<20} {info.client:<8} approvals={info.approvals:<5} {state}")
    except TokenError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def secrets(args: argparse.Namespace) -> int:
    if args.action == "new-key":
        print(new_raw_key())
        return 0
    settings = _settings()
    store = _store(settings, _database(settings))
    if args.action == "list":
        for item in store.status():
            print(f"{item['name']:<24} {'set' if item['set'] else '-':<4} {item['purpose']}")
    elif args.action == "set":
        if args.name not in KNOWN_SECRETS:
            print(f"Unknown secret {args.name}: {', '.join(KNOWN_SECRETS)}", file=sys.stderr)
            return 1
        if args.name in LOGIN_SECRETS:
            print(
                f"{args.name} is kept only by `clara auriga connect` (a hidden prompt for the password).",
                file=sys.stderr,
            )
            return 1
        value = (
            sys.stdin.read().strip() if not sys.stdin.isatty() else getpass.getpass(f"{args.name} (hidden): ").strip()
        )
        store.set(args.name, value)
        print(f"{args.name}: set (a running server reads it at its next use).")
    elif args.action == "unset":
        print(f"{args.name}: {'removed' if store.unset(args.name) else 'was not set'}")
    elif args.action == "rotate":
        if not args.new_key_file:
            print("Give --new-key-file (make one with: clara secrets new-key).", file=sys.stderr)
            return 1
        new = KeyMaterial.parse(Path(args.new_key_file).read_text(encoding="utf-8"), args.new_key_file)
        count = store.rotate(new)
        print(f"Re-encrypted {count} secret(s). Now point the clara-secret-key credential at the new key and restart.")
    return 0


def mode(args: argparse.Namespace) -> int:
    """The default-mode file the levels and rules of the old database say, on the standard output: review it, install it."""
    from ..app import static_tools
    from ..gate.legacy import import_legacy

    db = _database(_settings())
    print(import_legacy(db, {tool.name: tool.classes for tool in static_tools()}))
    print(
        "# Review it, then install it as /etc/clara/default-mode.toml and restart (docs/OPERATIONS.md).",
        file=sys.stderr,
    )
    return 0


# ----------------------------------------------------------------------
def _pasted(prompt: str) -> str:
    """What the owner pastes: hidden at a terminal (a link holds a token), a line of the input otherwise."""
    return getpass.getpass(prompt).strip() if sys.stdin.isatty() else sys.stdin.readline().strip()


def _asked(prompt: str, hidden: bool = False) -> str:
    """One answer of the owner, as typed (a password keeps its spaces): in the open, or hidden at a terminal, a line of the input
    otherwise."""
    if hidden and sys.stdin.isatty():
        return getpass.getpass(prompt)
    try:
        return input(prompt)
    except EOFError:
        return ""


def _keep_login(store: SecretsStore) -> str:
    """After a sign-in to Auriga: offers to keep the school email and password, so that Clara can sign in again by herself. The
    password goes only into the encrypted store: it is typed hidden, never printed, never read back (only the sign-in reads it)."""
    kept = all(store.has(name) for name in LOGIN_SECRETS)
    asked = _asked(
        "Replace the school email and password Clara keeps? [y/N] "
        if kept
        else "Keep your school email and password so Clara can sign in again by herself? "
        "(the password is stored encrypted and never shown again) [y/N] "
    )
    if asked.strip().lower() not in ("y", "yes"):
        return "School password: kept as it was." if kept else "School password: not kept."
    email, password = _asked("School email: ").strip(), _asked("School password (hidden): ", hidden=True)
    if not (email and password):
        return "School password: no email or no password was entered, nothing changed."
    for name, value in zip(LOGIN_SECRETS, (email, password), strict=True):
        store.set(name, value)
    return (
        "School password: kept, encrypted. When the Auriga sign-in lapses Clara signs in again by herself, once; "
        "if that fails she raises the alert and waits for you."
    )


def school(args: argparse.Namespace) -> int:
    """Sign in to Moodle or Auriga from this machine (the same sign-in as the Connections page), or forget the token."""
    site = args.command
    settings = _settings()
    db = _database(settings)
    store, connections = _store(settings, db), Connections(db)
    if args.action == "forget-password":
        print(
            f"Auriga: school password {'forgotten' if any([store.unset(name) for name in LOGIN_SECRETS]) else 'none was kept'}"
        )
        return 0
    if args.action == "disconnect":
        removed = store.unset(SECRETS[site])
        if site == "auriga":
            removed = any([store.unset(name) for name in LOGIN_SECRETS]) or removed
        removed = connections.remove(site) or removed
        print(f"{LABELS[site]}: {'disconnected, its token forgotten' if removed else 'was not connected'}")
        return 0
    try:
        if site == "moodle":
            print(
                "Open this address in a browser where you are signed in to Moodle:\n\n  "
                f"{launch_url()}\n\nThe page shows a link «Cliquez ici si l'app ne s'ouvre pas automatiquement». "
                "Copy the address of that link (it starts with moodlemobile://token=) and paste it here."
            )
            secret, account = asyncio.run(moodle_sign_in(_pasted("Link (hidden): ")))
            note = "Moodle has no renewal: when the token lapses, run this again."
        else:
            verifier, challenge = pkce()
            state = secrets_module.token_urlsafe(24)
            print(
                "Open this address in a browser and sign in to Auriga (with Microsoft, as usual):\n\n  "
                f"{authorize_url(state, challenge)}\n\nYou land on a blank page of auriga.epita.fr (a 404 is normal). "
                "Copy the whole address of that page and paste it here."
            )
            secret, account, kept, ends = asyncio.run(auriga_sign_in(_pasted("Address (hidden): "), state, verifier))
            phrase = lapse_phrase(ends, settings.zone())
            if not kept:
                note = (
                    "Auriga gave a session token only: it ends with Auriga's session, so you will sign in again often."
                )
                note += f" This sign-in {phrase}." if phrase else ""
            elif phrase:  # the date is Keycloak's and absolute: using the sign-in does not extend it
                note = f"This sign-in {phrase}, and using it does not extend it: sign in again then."
            else:
                note = "Auriga's token does not say when it ends."
    except SchoolError as error:
        print(error, file=sys.stderr)
        return 1
    store.set(SECRETS[site], secret)
    connections.set(site, account)
    print(f"{LABELS[site]}: connected as {account}. {note}")
    if site == "auriga":
        print(_keep_login(store))
    return 0


def event(args: argparse.Namespace) -> int:
    """Fire an event of an event source from this machine. It goes through the running server and your API token (the file the
    other clients read: `clara-admin --save-token`), so no source secret is needed: this machine and this token are the proof."""
    from uuid import uuid4

    from ..automations.sources import SourceError, convert
    from .common import Api, ClientError, read_token, token_file

    try:
        api = Api(args.url, read_token(token_file(args.token_file)), "cli")
        found = next(
            (
                item
                for item in api.get("/v1/event-sources")["sources"]
                if args.source in (item["id"], item["name"], item["kind"])
            ),
            None,
        )
        if found is None:
            raise ClientError(f"No event source {args.source!r}: `clara-admin /sources` lists them.")
        body: dict = {"key": args.key or f"cli-{uuid4().hex[:12]}", "data": convert(found["fields"], args.fields)}
        body |= {name: value for name in ("title", "actor") if (value := getattr(args, name))}
        answer = api.post(f"/v1/event-sources/{found['id']}/fire", body)
    except (ClientError, SourceError) as error:
        print(error, file=sys.stderr)
        return 1
    print(
        "Already stored: nothing started."
        if answer.get("duplicate")
        else f"Event #{answer['id']} of {found['kind']}: stored and matched."
    )
    return 0


# ----------------------------------------------------------------------
def _pinned_image(settings: Settings) -> str:
    """The sandbox image the last release pinned (runtime setting `sandbox_image`), read without write access; '' when
    there is none, so the caller falls back to CLARA_SANDBOX_IMAGE as the server does."""
    try:
        with closing(sqlite3.connect(f"file:{settings.database}?mode=ro", uri=True)) as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'sandbox_image'").fetchone()
        return json.loads(row[0]) if row else ""
    except (sqlite3.Error, ValueError):
        return ""


async def _doctor(settings: Settings, pull: bool) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = [("settings", OK, f"Clara {__version__}, zone {settings.timezone}")]
    problems = check_private(settings.env_file, settings.data_dir, settings.secret_key_file)
    results.append(("permissions", FAIL if problems else OK, "; ".join(problems) or "private"))
    for tool in ("git", "uv"):
        results.append(
            (
                tool,
                OK if shutil.which(tool) else (FAIL if tool == "git" else WARN),
                shutil.which(tool) or f"{tool} not found",
            )
        )
    try:
        material = load_key_material(settings.secret_key_file, settings.secret_key)
        results.append(("secret key", OK, f"from {material.origin}"))
    except SecretError as error:
        results.append(("secret key", FAIL, f"{error} (by hand, use the `clara` alias of docs/OPERATIONS.md)"))
    if settings.run == "auto":
        from ..workspace.sandbox import Sandbox
        from ..workspace.store import Workspace

        workspace = Workspace(settings.workspace_dir, settings.workspace_max_bytes, settings.workspace_max_file_bytes)
        image = _pinned_image(settings) or settings.sandbox_image  # what the server runs, as setup.py picks it
        sandbox = Sandbox(settings, workspace, None, image)  # type: ignore[arg-type]
        if pull and image.startswith("localhost/"):  # no registry holds that name
            results.append(
                (
                    "image pull",
                    WARN,
                    f"{image} is a local name: pull the image the release names in manifest.json and "
                    "tag it, or build it (docs/OPERATIONS.md, step 4)",
                )
            )
        elif pull and shutil.which(settings.podman):
            code, out = await sandbox._exec(settings.podman, "pull", image, timeout=1200)
            results.append(("image pull", OK if code == 0 else FAIL, out.splitlines()[-1] if out else image))
        for name, ok, detail in await sandbox.check():
            results.append((f"sandbox: {name}", OK if ok else FAIL, detail))
    else:
        results.append(("sandbox", WARN, "CLARA_RUN=off: scripts are not offered"))
    if settings.tailscale == "serve":
        from ..ops.tailscale import FunnelOn, check_funnel_now

        try:
            check_funnel_now(settings.tailscale_bin, settings.tailscale_port)
            results.append(("funnel", OK, f"off for port {settings.tailscale_port}"))
        except FunnelOn as error:
            results.append(("funnel", FAIL, str(error)))
        found = shutil.which(settings.tailscale_bin)
        results.append(("tailscale", OK if found else FAIL, found or "tailscale not found"))
    async with httpx.AsyncClient(timeout=5) as http:
        for host in settings.ollama_hosts:
            try:
                response = await http.get(f"{host.url}/api/tags")
                count = len(response.json().get("models") or [])
                results.append((f"ollama {host.id}", OK, f"{host.url}: {count} model(s)"))
            except (httpx.HTTPError, ValueError) as error:
                results.append((f"ollama {host.id}", WARN, f"{host.url}: {type(error).__name__}"))
        try:
            response = await http.get(f"{settings.searxng_url}/search", params={"q": "clara", "format": "json"})
            results.append(
                (
                    "searxng",
                    OK if response.status_code == 200 else WARN,
                    f"HTTP {response.status_code}"
                    + ("" if response.status_code == 200 else " (enable the json format)"),
                )
            )
        except httpx.HTTPError as error:
            results.append(
                (
                    "searxng",
                    WARN,
                    f"{settings.searxng_url}: {type(error).__name__} (web search is off until it answers, unless a Brave key is set)",
                )
            )
    if settings.restic_repository:
        results.append(
            (
                "restic",
                OK if shutil.which(settings.restic_bin) else FAIL,
                shutil.which(settings.restic_bin) or "restic not found",
            )
        )
    usage = shutil.disk_usage(settings.data_dir if settings.data_dir.exists() else "/")
    percent = 100 * usage.used / usage.total
    results.append(("disk", OK if percent < settings.disk_alert_percent else WARN, f"{percent:.0f}% used"))
    return results


def doctor(args: argparse.Namespace) -> int:
    settings = _settings()
    results = asyncio.run(_doctor(settings, args.pull))
    for name, state, detail in results:
        print(f"{state:<5} {name:<22} {detail}")
    return 1 if any(state == FAIL for _, state, _ in results) else 0


# ----------------------------------------------------------------------
def backup(args: argparse.Namespace) -> int:
    settings = _settings()
    if args.action == "init-remote":
        from ..ops.backups import Backups

        db = _database(settings)
        holder = type(
            "S",
            (),
            {"settings": settings, "secrets": _store(settings, db), "db": db, "workspace": None, "alerts": None},
        )()
        print(asyncio.run(Backups(holder).init_remote()))  # type: ignore[arg-type]
        return 0
    db = _database(settings)
    from ..timeutil import utcnow

    path = backup_database(db.connection, settings.backups_dir / "manual" / f"clara-{utcnow():%Y%m%d-%H%M%S}.db")
    result = verify(path)
    print(f"{path} ({result['integrity']}, schema {result['schema']})")
    return 0 if result["integrity"] == "ok" else 1


def restore(args: argparse.Namespace) -> int:
    source = Path(args.file)
    if not source.is_file():
        print(f"No file {source}.", file=sys.stderr)
        return 1
    result = verify(source)
    print(f"{source}: integrity {result['integrity']}, schema {result['schema']}")
    for table, count in result["rows"].items():
        if count:
            print(f"  {table:<20} {count:>8}")
    if result["integrity"] != "ok":
        return 1
    if args.verify:
        return 0
    settings = _settings()
    lock = _stopped(settings)
    try:
        if settings.database.exists():
            backup_database(settings.database, settings.backups_dir / "manual" / "before-restore.db")
        for suffix in ("-wal", "-shm"):
            Path(f"{settings.database}{suffix}").unlink(missing_ok=True)
        backup_database(source, settings.database)
        os.chmod(settings.database, 0o600)
        print(f"Restored into {settings.database} (the previous one is in backups/manual/before-restore.db).")
    finally:
        lock.release()
    return 0


def migrate_command(args: argparse.Namespace) -> int:
    settings = _settings()
    lock = _stopped(settings)
    try:
        before, after, made = migrate(settings.database, settings.backups_dir)
        print(f"Schema {before} -> {after}" + (f" (backup {made})" if made else ""))
    finally:
        lock.release()
    return 0


def purge(args: argparse.Namespace) -> int:
    settings = _settings()
    lock = _stopped(settings)
    try:
        db = Database(settings.database)
        before = settings.database.stat().st_size
        db.execute("VACUUM")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        print(
            f"Vacuumed: {before / 1e6:.1f} MB -> {settings.database.stat().st_size / 1e6:.1f} MB "
            "(deleted text is gone from the file)."
        )
    finally:
        lock.release()
    return 0


def preflight(args: argparse.Namespace) -> int:
    """The new release against a copy of the database: settings, migrations, key, imports."""
    settings = _settings()
    copy = Path(args.database)
    try:
        before, after, _ = migrate(copy, copy.parent / "backups")
    except SchemaTooNew as error:
        print(error, file=sys.stderr)
        return 1
    db = Database(copy)
    if db.value("PRAGMA quick_check") != "ok":
        print("The database copy is damaged.", file=sys.stderr)
        return 1
    _store(settings, db)
    from .. import app  # every module of the server imports
    from ..gate.modes import DefaultModeError, load_default

    try:  # an update must not switch to a release that refuses to start on the default-mode file you have
        load_default(settings.default_mode_file, {tool.name for tool in app.static_tools()})
    except DefaultModeError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"preflight ok: Clara {__version__}, schema {before} -> {after}, {app.__name__} imports")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clara", description="Operations on Clara (run as the service user).")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version").set_defaults(run=lambda a: print(__version__) or 0)
    tokens = commands.add_parser("token")
    tokens.add_argument("action", choices=("create", "list", "revoke"))
    tokens.add_argument("name", nargs="?")
    tokens.add_argument("client", nargs="?", choices=CLIENTS)
    tokens.add_argument("--approvals", choices=APPROVALS)
    tokens.set_defaults(run=token)
    keys = commands.add_parser("secrets")
    keys.add_argument("action", choices=("new-key", "list", "set", "unset", "rotate"))
    keys.add_argument("name", nargs="?")
    keys.add_argument("--new-key-file")
    keys.set_defaults(run=secrets)
    for site in ("moodle", "auriga"):
        sign_in = commands.add_parser(site)
        sign_in.add_argument(
            "action", choices=("connect", "disconnect", *(("forget-password",) if site == "auriga" else ()))
        )
        sign_in.set_defaults(run=school)
    check = commands.add_parser("doctor")
    check.add_argument("--pull", action="store_true", help="pull the sandbox image first")
    check.set_defaults(run=doctor)
    copy = commands.add_parser("backup")
    copy.add_argument("action", nargs="?", choices=("now", "init-remote"), default="now")
    copy.set_defaults(run=backup)
    back = commands.add_parser("restore")
    back.add_argument("file")
    back.add_argument("--verify", action="store_true")
    back.set_defaults(run=restore)
    fired = commands.add_parser("event")
    fired.add_argument("action", choices=("fire",))
    fired.add_argument("source", help="the name of the source (door), its kind (custom.door) or its id")
    fired.add_argument(
        "fields", nargs="*", metavar="field=value", help="the fields of the event (a yes/no field takes yes or no)"
    )
    fired.add_argument("--key", help="events with the same key are one event (default: a new one each time)")
    fired.add_argument("--title")
    fired.add_argument("--actor")
    fired.add_argument("--url", default=os.getenv("CLARA_URL", "http://127.0.0.1:8765"))
    fired.add_argument("--token-file")
    fired.set_defaults(run=event)
    modes = commands.add_parser("mode")
    modes.add_argument("action", choices=("import-legacy",))
    modes.set_defaults(run=mode)
    commands.add_parser("migrate").set_defaults(run=migrate_command)
    commands.add_parser("purge").set_defaults(run=purge)
    first = commands.add_parser("preflight")
    first.add_argument("--database", required=True)
    first.set_defaults(run=preflight)
    args = parser.parse_args(argv)
    if args.command == "token" and args.action in ("create", "revoke") and not args.name:
        parser.error("give the token's name")
    if args.command == "token" and args.action == "create" and not args.client:
        parser.error(f"give the client: {', '.join(CLIENTS)}")
    if args.command == "secrets" and args.action in ("set", "unset") and not args.name:
        parser.error("give the secret's name")
    return int(args.run(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
