"""The admin console: slash commands run on the server, for `clara-admin` and the web Admin page alike.

Each command answers with text. The leading `/` is optional. Commands that change something say what they did.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .auth import APPROVALS, CLIENTS, TokenError, TokenInfo
from .gate import ANSWERS, AlreadyAnswered, ApprovalError, RuleError
from .llm import NoModel, ProviderError, split_ref
from .secrets import KNOWN_SECRETS, SecretError

if TYPE_CHECKING:
    from .services import Services


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    help: str
    run: Callable[[Services, list[str], TokenInfo], Awaitable[str] | str]


def _uptime(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"


async def _status(s: Services, args: list[str], token: TokenInfo) -> str:
    from .ops.status import status

    info = await status(s)
    lines = [
        f"Clara {info['version']} ({info['release'] or 'source tree'}), {info['state']}, up {_uptime(info['uptime'])}",
        f"Default model: {info['models']['default'] or '(none)'}; side jobs: {info['models']['upkeep'] or '(default)'}",
        f"Turns running: {info['agent']['active']}, since start: {info['agent']['turns']}",
        f"Database: {info['storage']['database_mb']} MB; workspace: {info['storage']['workspace_mb']} MB; "
        f"disk {info['storage']['disk_percent']}% used",
        f"Scripts: {'ready' if info['workspace']['run_ready'] else 'off (' + info['workspace']['run_problem'] + ')'}",
        f"Tailscale: {info['tailscale']}",
        f"Backups: last {info['backups']['last'] or 'never'}{'; remote: ' + info['backups']['remote'] if info['backups']['remote'] else ''}",
        f"Event listeners: {info['events']['listeners']}; Discord bot: {info['clients'].get('discord', 'not seen')}",
    ]
    for provider in info["providers"]:
        state = "usable" if provider["usable"] else ("enabled, no key" if provider["enabled"] else "off")
        lines.append(f"  provider {provider['id']:<10} {'local ' if provider['local'] else 'hosted'} {state}")
    if info["alerts"]:
        lines.append("Active alerts:")
        lines += [f"  ! {text}" for text in info["alerts"].values()]
    if info["lifecycle"]["state"] == "stopping":
        lines.append(f"STOPPING: waited {info['lifecycle']['waited']} s of {info['lifecycle']['limit']} s")
    return "\n".join(lines)


def _provider(s: Services, args: list[str], token: TokenInfo) -> str | Awaitable[str]:
    if not args or args[0] == "list":
        return "\n".join(
            f"{p['id']:<10} {p['label']:<18} {'local ' if p['local'] else 'hosted'} "
            f"{'enabled' if p['enabled'] else 'off':<8} {'key set' if p['key_set'] else 'NO KEY'} {p['host']}"
            for p in s.providers.status()
        )
    action, *rest = args
    if not rest:
        return "Usage: /provider enable|disable|check <id>"
    provider_id = rest[0]
    try:
        if action in ("enable", "disable"):
            enabled = s.providers.set_enabled(provider_id, action == "enable")
            s.providers.forget_backends(provider_id)
            return f"Enabled providers: {', '.join(enabled) or '(none)'}"
        if action == "check":
            async def checked() -> str:
                problem = await s.providers.check(provider_id)
                return f"{provider_id}: {'OK' if problem is None else problem}"
            return checked()
    except ProviderError as error:
        return str(error)
    return "Usage: /provider [list] | enable|disable|check <id>"


def _model(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args:
        return f"Default model: {s.runtime.get('default_model') or '(none)'}"
    ref = args[0]
    try:
        provider_id, _ = split_ref(ref)
        if not s.providers.usable(provider_id):
            return s.providers.why_not(provider_id)
    except ProviderError as error:
        return str(error)
    s.runtime.set("default_model", ref)
    return f"Default model: {ref}"


async def _models(s: Services, args: list[str], token: TokenInfo) -> str:
    if args and args[0] in ("show", "hide"):
        if len(args) < 2:
            return "Usage: /models show|hide <provider:model>..."
        try:
            s.catalog.set_shown(args[1:], args[0] == "show")
        except ProviderError as error:
            return str(error)
        return f"{'Shown' if args[0] == 'show' else 'Hidden'} in the pickers: {', '.join(args[1:])}"
    found = await s.catalog.listing(force=bool(args and args[0] == "refresh"))
    lines = [f"{'*' if info.shown else ' '} {info.ref:<50} {'local' if info.local else 'hosted':<6} window {info.window:,}"
             + ("" if info.listed else " (not listed now)") for info in found]
    lines += [f"! {provider}: {problem}" for provider, problem in s.catalog.problems.items()]
    return "\n".join(lines) or "No model found: is a provider enabled and reachable?"


def _fallbacks(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args:
        return "Fallbacks: " + (", ".join(s.runtime.get("fallbacks")) or "(none)")
    refs = [] if args == ["clear"] else args
    for ref in refs:
        try:
            if not s.providers.usable(split_ref(ref)[0]):
                return s.providers.why_not(split_ref(ref)[0])
        except ProviderError as error:
            return str(error)
    s.runtime.set("fallbacks", refs)
    return "Fallbacks: " + (", ".join(refs) or "(none)")


def _upkeep(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args:
        return f"Side jobs model: {s.runtime.get('upkeep_model') or '(the default model)'}"
    ref = "" if args[0] in ("none", "default") else args[0]
    s.runtime.set("upkeep_model", ref)
    return f"Side jobs model: {ref or '(the default model)'}"


def _tokens(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args or args[0] == "list":
        return "\n".join(
            f"{t.name:<20} {t.client:<8} approvals={t.approvals:<5} last used {t.last_used_at or 'never'}"
            + (f" REVOKED {t.revoked_at}" if t.revoked_at else "") for t in s.tokens.list()
        )
    action, *rest = args
    try:
        if action == "create" and len(rest) >= 2:
            info, secret = s.tokens.create(rest[0], rest[1], rest[2] if len(rest) > 2 else None)
            return f"Token {info.name} ({info.client}, approvals={info.approvals}):\n{secret}\nIt is shown once: copy it now."
        if action == "revoke" and rest:
            if rest[0] == token.name:
                return "This is the token you are using: revoke it from another client."
            return f"Revoked {s.tokens.revoke(rest[0]).name}: it is refused from now on."
        if action == "approvals" and len(rest) == 2:
            return f"{s.tokens.set_approvals(rest[0], rest[1]).name} may answer: {rest[1]}"
    except TokenError as error:
        return str(error)
    return (f"Usage: /tokens [list] | create <name> <{'|'.join(CLIENTS)}> [{'|'.join(APPROVALS)}] | revoke <name> | "
            "approvals <name> <scope>")


def _secrets(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args or args[0] == "list":
        return "\n".join(f"{item['name']:<24} {'set' if item['set'] else '-':<4} {item['purpose']}" for item in s.secrets.status())
    if args[0] == "unset" and len(args) == 2:
        removed = s.secrets.unset(args[1])
        s.providers.forget_backends()
        return f"{args[1]}: {'removed' if removed else 'was not set'}"
    if args[0] == "set" and len(args) == 3:
        if args[1] not in KNOWN_SECRETS:
            return f"Unknown secret {args[1]}: {', '.join(KNOWN_SECRETS)}"
        try:
            s.secrets.set(args[1], args[2])
        except SecretError as error:
            return str(error)
        s.providers.forget_backends()
        return f"{args[1]}: set"
    return "Usage: /secrets [list] | set <name> <value> | unset <name>   (clara-admin asks for the value hidden)"


def _rules(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args or args[0] == "list":
        lines = [rule.line() for rule in s.rules.list()] or ["(no rules: the defaults of each action class apply)"]
        lines.append(f"Tainted-turn rule: {'on' if s.runtime.get('tainted_rule') else 'off'}")
        return "\n".join(lines)
    action, *rest = args
    try:
        if action == "add" and rest:
            effect, fields = rest[0], dict(part.split("=", 1) for part in rest[1:] if "=" in part)
            unknown = set(fields) - {"tool", "target", "situation", "level", "note"}
            if unknown:
                return f"Unknown fields: {', '.join(sorted(unknown))} (tool= target= situation= level= note=)"
            return f"Added {s.rules.add(effect, **fields).line()}"
        if action in ("del", "delete") and rest:
            return "Deleted." if s.rules.delete(int(rest[0])) else f"No rule {rest[0]}."
        if action in ("on", "off") and rest:
            return s.rules.update(int(rest[0]), enabled=action == "on").line()
        if action == "test" and len(rest) >= 1:
            from .gate import Situation
            from .tools.base import ToolContext, TurnState

            arguments = json.loads(" ".join(rest[1:]) or "{}")
            state = TurnState(turn_id="test", conversation_id="", client="console", timezone=s.settings.timezone)
            action_ = s.toolbox.action(rest[0], ToolContext(s, state), arguments)
            explained = s.gate.explain(action_, Situation("interactive"))
            return (f"{action_.tool} [{action_.klass}, {action_.level}] {' '.join(action_.targets)}\n"
                    f"→ {explained['effect']} ({explained['reason']})"
                    + (f"; raised: {', '.join(explained['raised'])}" if explained["raised"] else ""))
    except (RuleError, ValueError, KeyError) as error:
        return f"Error: {error}"
    return "Usage: /rules [list] | add <allow|ask|refuse> tool=… target=… situation=… level=… note=… | del <id> | on|off <id> | test <tool> <json>"


def _tainted(s: Services, args: list[str], token: TokenInfo) -> str:
    if args and args[0] in ("on", "off"):
        s.runtime.set("tainted_rule", args[0] == "on")
    return f"Tainted-turn rule: {'on' if s.runtime.get('tainted_rule') else 'off'}"


async def _approvals(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args or args[0] == "list":
        found = s.approvals.list("pending")
        return "\n".join(f"#{a.id} [{a.level}] {a.summary} ({a.reason}; expires {a.expires_at})" for a in found) or "Nothing waits."
    words = {"allow": "allow_once", "always": "allow_always", "refuse": "refuse_once", "never": "refuse_always"}
    answer = words.get(args[0], args[0])
    if answer not in ANSWERS or len(args) != 2:
        return "Usage: /approvals [list] | allow|always|refuse|never <id>"
    try:
        settled = await s.approvals.decide(int(args[1]), answer, token)
    except (AlreadyAnswered, ApprovalError, ValueError) as error:
        return str(error)
    return f"#{settled.id}: {settled.status}" + (f"\n{settled.result[:500]}" if settled.result else "")


def _debug(s: Services, args: list[str], token: TokenInfo) -> str:
    from .ops.status import set_debug

    if args and args[0] in ("on", "off"):
        until = set_debug(s, args[0] == "on")
        return f"Debug content log {'on until ' + until if until else 'off (and its file deleted)'}."
    until = s.runtime.get("debug_until")
    return f"Debug content log: {'on until ' + until if until and s.traffic.debugging else 'off'}"


def _usage(s: Services, args: list[str], token: TokenInfo) -> str:
    period = args[0] if args else "7d"
    group = args[1] if len(args) > 1 else "model"
    try:
        stats = s.usage.stats(period, group)
    except ValueError as error:
        return str(error)
    lines = [f"{row['key']:<45} {row['calls']:>6} calls {row['prompt']:>12,} in {row['completion']:>10,} out" for row in stats["rows"]]
    totals = stats["totals"]
    lines.append(f"{'total':<45} {totals['calls']:>6} calls {totals['prompt']:>12,} in {totals['completion']:>10,} out")
    return "\n".join(lines)


def _alerts(s: Services, args: list[str], token: TokenInfo) -> str:
    if args and args[0] == "clear" and len(args) == 2:
        s.alerts.clear(args[1])
        return f"Cleared {args[1]}."
    recent = s.events.recent(20, ["alert"])
    active = [f"! {key}: {text}" for key, text in s.alerts.active.items()]
    return "\n".join(active + [f"{e.created_at} {e.title}: {e.text}" for e in recent]) or "No alert."


def _stop(s: Services, args: list[str], token: TokenInfo) -> str:
    return s.lifecycle.stop(force=bool(args and args[0] == "now"))


def _restart(s: Services, args: list[str], token: TokenInfo) -> str:
    from .ops.lifecycle import EXIT_RESTART

    if not s.settings.releases_dir or s.updater is None or not s.updater.launched:
        return "Restart needs the launcher (clara-launcher under systemd): use systemctl restart clara instead."
    return s.lifecycle.stop(force=bool(args and args[0] == "now"), exit_code=EXIT_RESTART)


async def _backup(s: Services, args: list[str], token: TokenInfo) -> str:
    if s.backups is None:
        return "Backups are not available."
    if args and args[0] == "now":
        result = await s.backups.run(manual=True)
        return result
    return "\n".join(f"{item['name']}  {item['size_mb']} MB  {item['kind']}" for item in s.backups.listing()) or "No backup yet."


async def _update(s: Services, args: list[str], token: TokenInfo) -> str:
    if s.updater is None:
        return "Updates are not available."
    if not args or args[0] == "list":
        releases = await s.updater.releases()
        return "\n".join(f"{r['tag']:<12} {r['published_at'] or ''}  {r['name']}" + ("  (installed)" if r["current"] else "")
                         for r in releases) or "No release found."
    return await s.updater.start(args[0])


async def _undo(s: Services, args: list[str], token: TokenInfo) -> str:
    if s.workspace is None:
        return "The workspace is not available."
    try:
        commit = await s.workspace.undo(args[0] if args else None)
    except ValueError as error:
        return str(error)
    return f"Reverted: {commit}"


async def _vault(s: Services, args: list[str], token: TokenInfo) -> str:
    if s.vault is None:
        return "No vault (CLARA_VAULT_PATH is not set)."
    if args and args[0] == "sync":
        return await s.vault.sync_now()
    if args and args[0] == "reindex":
        count = await s.vault.reindex(full=True)
        return f"Indexed {count} notes."
    return json.dumps(s.vault.status(), indent=2)


def _audit(s: Services, args: list[str], token: TokenInfo) -> str:
    limit = int(args[0]) if args and args[0].isdigit() else 20
    return "\n".join(f"{e['at']} {e['tool']:<16} {e['effect']:<6} {e['outcome']:<8} {e['target'][:60]}" for e in s.audit.recent(limit)) or "Empty."


COMMANDS: list[Command] = [
    Command("status", "/status", "Version, state, models, storage, Tailscale, backups, alerts.", _status),
    Command("provider", "/provider [list] | enable|disable|check <id>", "Model providers.", _provider),
    Command("model", "/model [provider:model]", "Show or set the default model.", _model),
    Command("models", "/models [refresh] | show|hide <ref>...", "The catalogue; * marks models shown in pickers.", _models),
    Command("fallbacks", "/fallbacks [<ref>... | clear]", "Models asked in turn when one fails.", _fallbacks),
    Command("upkeep", "/upkeep [<ref> | none]", "The model of titles and summaries.", _upkeep),
    Command("tokens", "/tokens [list] | create | revoke | approvals", "Client tokens (revoking works at once).", _tokens),
    Command("secrets", "/secrets [list] | set <name> <value> | unset <name>", "The encrypted secrets store.", _secrets),
    Command("rules", "/rules [list] | add | del | on | off | test", "The rule gate.", _rules),
    Command("tainted", "/tainted [on|off]", "The tainted-turn rule.", _tainted),
    Command("approvals", "/approvals [list] | allow|always|refuse|never <id>", "Pending approval requests.", _approvals),
    Command("debug", "/debug [on|off]", "Full prompts and answers to the debug log for one hour.", _debug),
    Command("usage", "/usage [24h|7d|30d|90d|365d|all] [day|model|kind|provider|client]", "Token statistics.", _usage),
    Command("alerts", "/alerts [clear <key>]", "Active and recent alerts.", _alerts),
    Command("audit", "/audit [n]", "The last gate decisions.", _audit),
    Command("undo", "/undo [commit]", "Revert the last workspace change (or a given commit).", _undo),
    Command("vault", "/vault [status|sync|reindex]", "The Obsidian vault.", _vault),
    Command("backup", "/backup [now]", "Local backups (and the remote copy).", _backup),
    Command("update", "/update [list | <tag>]", "Tagged releases: list, or install one (preflight, then restart).", _update),
    Command("restart", "/restart [now]", "Stop and start again (the same release).", _restart),
    Command("stop", "/stop [now]", "Graceful stop (now: cut running answers).", _stop),
]
BY_NAME = {command.name: command for command in COMMANDS}


def help_text(name: str = "") -> str:
    if name:
        command = BY_NAME.get(name.lstrip("/"))
        return f"{command.usage}\n{command.help}" if command else f"No command {name}."
    return "\n".join(f"{command.usage:<70} {command.help}" for command in COMMANDS) + "\n/help [command]"


async def run(services: Services, line: str, token: TokenInfo) -> str:
    line = line.strip()
    if not line:
        return ""
    try:
        words = shlex.split(line)
    except ValueError as error:
        return f"Cannot read the command: {error}"
    name = words[0].lstrip("/").lower()
    if name in ("help", "?"):
        return help_text(words[1] if len(words) > 1 else "")
    command = BY_NAME.get(name)
    if command is None:
        return f"Unknown command /{name}. /help lists them."
    try:
        result = command.run(services, words[1:], token)
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[misc]
        return str(result)
    except NoModel as error:
        return str(error)
