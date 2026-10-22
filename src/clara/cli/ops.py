"""`clara`: operations on this machine, run as the service user (`sudo -u clara clara …`).

clara token create <name> <client> [--approvals all|safe|none]    a client token (shown once)
clara token list | revoke <name>
clara secrets new-key                       a fresh raw key for the systemd credential
clara secrets list | set <name> | unset <name>
clara secrets rotate --new-key-file <path>  re-encrypt every secret under a new key
clara doctor [--pull]                       check what Clara needs (rootless Podman, Tailscale, providers…)
clara backup [init-remote]                  a backup now (and the remote copy), or create the restic repository
clara restore --verify <file>               open a backup, check it, count its rows
clara restore <file>                        put a backup back (the server must be stopped)
clara migrate | purge                       bring the schema up to date | vacuum the file (server stopped)
clara preflight --database <copy>           what the updater runs on a copy before switching releases
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import shutil
import sys
from pathlib import Path

import httpx

from .. import __version__
from ..auth import APPROVALS, CLIENTS, TokenError, Tokens
from ..db import AlreadyRunning, Database, InstanceLock, SchemaTooNew, backup_database, migrate
from ..ops.backups import verify
from ..ops.private import check_private
from ..secrets import KNOWN_SECRETS, KeyMaterial, SecretError, SecretsStore, load_key_material
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


# ----------------------------------------------------------------------
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
        results.append(("secret key", FAIL, str(error)))
    if settings.run == "auto":
        from ..workspace.sandbox import Sandbox
        from ..workspace.store import Workspace

        workspace = Workspace(settings.workspace_dir, settings.workspace_max_bytes, settings.workspace_max_file_bytes)
        sandbox = Sandbox(settings, workspace, None, settings.sandbox_image)  # type: ignore[arg-type]
        if pull and shutil.which(settings.podman):
            code, out = await sandbox._exec(settings.podman, "pull", settings.sandbox_image, timeout=1200)
            results.append(
                ("image pull", OK if code == 0 else FAIL, out.splitlines()[-1] if out else settings.sandbox_image)
            )
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
