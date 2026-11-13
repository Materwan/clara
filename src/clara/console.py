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
from .connectors.auriga import lapse_phrase, lapses
from .connectors.school import LABELS as SCHOOL_LABELS
from .connectors.school import SECRETS as SCHOOL_SECRETS
from .connectors.school import SITES as SCHOOL_SITES
from .gate import ANSWERS, AlreadyAnswered, ApprovalError, Line, ModeError
from .gate.modes import NeedsConfirmation
from .llm import NoModel, ProviderError, split_ref
from .memory.conversations import ConversationError
from .secrets import KNOWN_SECRETS, LOGIN_SECRETS, SecretError

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
    lines = [
        f"{'*' if info.shown else ' '} {info.ref:<50} {'local' if info.local else 'hosted':<6} window {info.window:,}"
        + ("" if info.listed else " (not listed now)")
        + (f" (does not work: {info.fit.why})" if info.fit.works is False else "")
        for info in found
    ]
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
    try:
        s.runtime.set("upkeep_model", ref)
    except ValueError as error:
        return f"Error: {error}"
    return f"Side jobs model: {ref or '(the default model)'}"


def _tokens(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args or args[0] == "list":
        return "\n".join(
            f"{t.name:<20} {t.client:<8} approvals={t.approvals:<5} last used {t.last_used_at or 'never'}"
            + (f" REVOKED {t.revoked_at}" if t.revoked_at else "")
            for t in s.tokens.list()
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
    return (
        f"Usage: /tokens [list] | create <name> <{'|'.join(CLIENTS)}> [{'|'.join(APPROVALS)}] | revoke <name> | "
        "approvals <name> <scope>"
    )


# an alert that is over once its secret is replaced or removed (the key of a school site's alert is the site)
ALERT_OF_SECRET = {"brave_api_key": "brave", **{secret: site for site, secret in SCHOOL_SECRETS.items()}}


def secret_changed(s: Services, name: str) -> None:
    """A secret was set or removed (from the Admin page or the console): what depends on it looks again."""
    s.providers.forget_backends()
    if name in ALERT_OF_SECRET:
        s.alerts.clear(ALERT_OF_SECRET[name])
        if name in SCHOOL_SECRETS.values() and s.secrets.has(name) and s.connections is not None:
            s.connections.mark(ALERT_OF_SECRET[name], "ok")  # a new token to try (no-op without a connection)


def _secrets(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args or args[0] == "list":
        return "\n".join(
            f"{item['name']:<24} {'set' if item['set'] else '-':<4} {item['purpose']}" for item in s.secrets.status()
        )
    if args[0] == "unset" and len(args) == 2:
        removed = s.secrets.unset(args[1])
        secret_changed(s, args[1])
        return f"{args[1]}: {'removed' if removed else 'was not set'}"
    if args[0] == "set" and len(args) == 3:
        if args[1] not in KNOWN_SECRETS:
            return f"Unknown secret {args[1]}: {', '.join(KNOWN_SECRETS)}"
        if args[1] in LOGIN_SECRETS:  # a value typed on this line would be seen: only `clara auriga connect` keeps them
            return f"{args[1]} is kept only by `clara auriga connect` (a hidden prompt), never typed here."
        try:
            s.secrets.set(args[1], args[2])
        except SecretError as error:
            return str(error)
        secret_changed(s, args[1])
        return f"{args[1]}: set"
    return "Usage: /secrets [list] | set <name> <value> | unset <name>   (clara-admin asks for the value hidden)"


def _school(s: Services, args: list[str], token: TokenInfo) -> str:
    if s.connectors is None:
        return "Moodle and Auriga are not available."
    if args and args[0] == "disconnect" and len(args) == 2 and args[1] in SCHOOL_SITES:
        forgotten = s.connectors.school_disconnect(args[1])
        return f"{SCHOOL_LABELS[args[1]]}: {'disconnected, its token forgotten' if forgotten else 'was not connected'}"
    if args == ["forget-password"]:  # the console only removes the school password: never sets it, never shows it
        if token.approvals != "all":  # it takes away what Clara does alone: as for a trusted file, only scope `all`
            return "Error: this token may not remove the school password."
        return "School password: " + ("forgotten" if s.connectors.school_password_forget() else "none was kept")
    if args and args[0] != "status":
        sites = "|".join(SCHOOL_SITES)
        return (
            f"Usage: /school [status] | disconnect <{sites}> | forget-password   "
            f"(sign in on the Connections page, or `clara <{sites}> connect`)"
        )
    lines = []
    for site in SCHOOL_SITES:
        state = s.connectors.school_state(site)
        found = s.connections.get(site) or {}
        ends = note = ""
        if site == "auriga" and state != "off":  # whether a school password is kept, never what it is
            kept = s.connectors.school_password_kept()
            if state == "ok":
                note = f", school password {'kept' if kept else 'not kept'}"
            elif kept:
                note = ", the kept school password was tried once and did not work"
        if site == "auriga" and state == "ok":  # Auriga's sign-in ends on a fixed date, which the token itself says
            phrase = lapse_phrase(lapses(s.secrets.get("auriga_refresh_token") or ""), s.settings.zone())
            ends = f", sign-in {phrase}" if phrase else ""
        lines.append(
            f"{SCHOOL_LABELS[site]:<8} "
            + {"ok": f"connected as {found.get('account') or '?'}{ends}{note}", "off": "not connected"}.get(
                state, f"must be signed in again (was {found.get('account') or '?'}){note}"
            )
        )
    return "\n".join(lines)


def _automation(s: Services, ref: str):
    from .automations import AutomationError

    found = s.automations.find(int(ref)) if ref.isdigit() else s.automations.by_name(ref)
    if found is None:
        raise AutomationError(f"No automation {ref}.")
    return found


def _automation_line(s: Services, item) -> str:
    info = s.automations.describe(item)
    last = info["last"]
    return (
        f"#{item.id} {item.name}  {info['when_words']}  {item.action}{'' if item.enabled else ' (off)'}  "
        f"catch-up {item.catch_up}, {item.max_per_hour}/h  queued {info['queued']}  "
        f"last: {last['status'] + ' ' + (last['finished_at'] or '')[:16] if last else 'never'}"
    )


def _automations(s: Services, args: list[str], token: TokenInfo) -> str:
    from .automations import KINDS, AutomationError

    usage = (
        "Usage: /automations [list] | show <id|name> | add <name> '<json>' | edit <id|name> '<json>' | on|off <id|name> | "
        "run <id|name> | del <id|name> | test <id|name> [event id] | runs <id|name> [n] | events [n]"
    )
    if not args or args[0] == "list":
        return "\n".join(_automation_line(s, item) for item in s.automations.listing()) or (
            f"(no automation) Kinds of event: {', '.join(KINDS)}. {usage}"
        )
    action, *rest = args
    try:
        if action == "show" and rest:
            item = _automation(s, rest[0])
            return json.dumps(s.automations.describe(item), indent=2, ensure_ascii=False)
        if action in ("add", "edit") and len(rest) >= 2:
            try:
                fields = json.loads(" ".join(rest[1:]))
            except ValueError as error:
                return f'The fields are not JSON ({error}). Quote them: \'{{"when": {{"type": "cron", ...}}, ...}}\'.'
            if not isinstance(fields, dict):
                return "The fields are a JSON object."
            if action == "add":
                made = s.automations.create(rest[0], fields.pop("when", None), **fields)
                return (
                    f"Made automation #{made.id}. Its runs are listed under it in the history once you set in_history.\n"
                    f"{_automation_line(s, made)}"
                )
            return "Changed.\n" + _automation_line(s, s.automations.update(_automation(s, rest[0]).id, **fields))
        if action in ("on", "off") and rest:
            return _automation_line(s, s.automations.update(_automation(s, rest[0]).id, enabled=action == "on"))
        if action == "run" and rest:
            return _automation_line(s, s.automations.run_now(_automation(s, rest[0]).id))
        if action in ("del", "delete") and rest:
            return "Deleted." if s.automations.delete(_automation(s, rest[0]).id) else "Nothing deleted."
        if action == "test" and rest:
            item = _automation(s, rest[0])
            if len(rest) > 1:
                event = s.automations.event(int(rest[1]))
            else:
                event = max(
                    (e for kind in item.kinds for e in s.automations.recent_events(1, kind)),
                    key=lambda e: e.id,
                    default=None,
                )
            if event is None and item.kinds:
                return "No event of its kinds was stored yet (/automations events)."
            shown = s.automations.test(item, event)
            head = f"Event #{event.id}: {event.title}\n" if event else "At this moment\n"
            return (
                f"{head}{'MATCHES' if shown['matches'] else 'does not match: ' + shown['why_not']}\n"
                f"Action: {shown['action']}; tools: {', '.join(shown['tools']) or '(none)'}; tainted: {shown['tainted']}\n"
                f"--- what Clara would receive ---\n{shown['message']}"
            )
        if action == "runs" and rest:
            item = _automation(s, rest[0])
            runs = s.automations.runs(item.id, int(rest[1]) if len(rest) > 1 else 10)
            return (
                "\n".join(
                    f"run {r['id']} {r['status']:<11} {r['queued_at'][:16]}  {r['event_count']} event(s)  "
                    f"{(r['summary'] or r['note'])[:90]}"
                    for r in runs
                )
                or "No run yet."
            )
        if action == "events":
            found = s.automations.recent_events(int(rest[0]) if rest else 10)
            return (
                "\n".join(
                    f"#{e.id} {e.kind} {e.occurred_at[:16]}{' (self)' if e.self_caused else ''}  {e.title[:90]}"
                    for e in found
                )
                or "No event yet."
            )
    except (AutomationError, ValueError) as error:
        return f"Error: {error}"
    return usage


MODE_USAGE = (
    "Usage: /mode [list] | show <name|default> | conversation|project|automation <id> [<name>|follow]"
    " | new <name> [<pack,pack>] | packs <name> <pack,pack> | delete <name> [<conversations> <automation conversations> <automations> <projects>]"
    " | verdict <name> <approve|ask|refuse> [tool=…] [class=…] [target=…] [tainted=<verdict>] | unverdict <name> [tool=…] [class=…] [target=…]"
    " | test <tool> <json> [mode=<name>] [tainted] | import-legacy"
)
MODE_TABLES = {"conversation": "conversations", "project": "projects", "automation": "automations"}


def _mode_text(mode) -> str:
    lines = [f"{mode.name}: packs {', '.join(mode.packs) or '(none)'}"]
    return "\n".join(
        [*lines, *(f"  {line.text()}" for line in mode.lines)]
        if mode.lines
        else [*lines, "  (no lines: the default mode decides)"]
    )


def _fields(words: list[str]) -> dict[str, str]:
    fields = dict(part.split("=", 1) for part in words if "=" in part)
    unknown = set(fields) - {"tool", "class", "target", "tainted"}
    if unknown:
        raise ModeError(f"Unknown fields: {', '.join(sorted(unknown))} (tool= class= target= tainted=)")
    return fields


def _mode(s: Services, args: list[str], token: TokenInfo) -> str:
    modes = s.modes
    try:
        if not args or args[0] == "list":
            return "\n".join(
                [f"default (the file {s.settings.default_mode_file}): {len(modes.default.lines)} lines"]
                + [
                    f"{name}: packs {', '.join(modes.require(name).packs) or '(none)'}, {len(modes.require(name).lines)} lines, "
                    + ", ".join(f"{count} {what.replace('_', ' ')}" for what, count in modes.uses(name).items())
                    for name in modes.names()
                ]
            )
        action, *rest = args
        changes = action in ("new", "packs", "verdict", "unverdict", "delete", "import-legacy") or (
            action in MODE_TABLES and len(rest) > 1
        )
        if (
            changes and token.approvals != "all"
        ):  # a mode is an answer given in advance: as for the API, only scope `all`
            return "Error: this token may not change modes: they decide what runs without your answer."
        if action == "show" and rest:
            return _mode_text(modes.require(rest[0]))
        if action in MODE_TABLES and rest:
            if len(rest) == 1:
                if action != "conversation":
                    return MODE_USAGE
                name, source = modes.where(rest[0])
                return f"Mode {name} (from {source})"
            name = modes.check_mode(None if rest[1] == "follow" else rest[1])
            if action == "conversation":
                conversation = s.conversations.update(rest[0], mode=name)
                if s.packs is not None:
                    s.packs.with_mode(conversation)
            else:
                modes.set_stored(MODE_TABLES[action], int(rest[0]), name)
            modes.log(action, f"{token.name} set the mode of {action} {rest[0]} to {name or 'follow'}")
            return f"{action} {rest[0]}: " + ("follows" if name is None else f"mode {name}")
        if action == "new" and rest:
            made = modes.create(rest[0], rest[1].split(",") if len(rest) > 1 else [])
            modes.log("mode", f"{token.name} made the mode {made.name}")
            return f"Made the mode {made.name}. /mode verdict {made.name} … adds its lines."
        if action == "packs" and len(rest) == 2:
            made = modes.update(rest[0], packs=[item for item in rest[1].split(",") if item])
            modes.log("mode", f"{token.name} set the packs of the mode {made.name}")
            return _mode_text(made)
        if action == "verdict" and len(rest) >= 2:
            fields = _fields(rest[2:])
            line = Line(
                rest[1],
                fields.get("tool", ""),
                fields.get("class", ""),
                fields.get("target", ""),
                fields.get("tainted", ""),
            )
            made = modes.set_line(rest[0], line)
            modes.log("mode", f"{token.name} set {line.text()} in the mode {made.name}")
            return f"{made.name}: {line.text()}"
        if action == "unverdict" and len(rest) >= 2:
            fields = _fields(rest[1:])
            made = modes.remove_line(rest[0], fields.get("tool", ""), fields.get("class", ""), fields.get("target", ""))
            modes.log("mode", f"{token.name} removed a line of the mode {made.name}")
            return _mode_text(made)
        if action == "delete" and rest:
            try:
                moved = modes.delete(
                    rest[0],
                    dict(
                        zip(
                            ("conversations", "automation_conversations", "automations", "projects"),
                            map(int, rest[1:5]),
                        )
                    ),
                )
            except NeedsConfirmation as error:
                numbers = " ".join(str(count) for count in error.uses.values())
                return f"{error}\nTo delete it: /mode delete {rest[0]} {numbers}"
            modes.log("mode", f"{token.name} deleted the mode {rest[0]}: {moved}")
            return f"Deleted {rest[0]}: {moved['conversations']} conversation(s), {moved['automation_conversations']} automation conversation(s), {moved['automations']} automation(s), {moved['projects']} project(s) follow the default mode."
        if action == "test" and rest:
            from .gate import Situation
            from .tools.base import ToolContext, TurnState

            words = [word for word in rest[1:] if word != "tainted" and not word.startswith("mode=")]
            state = TurnState(turn_id="test", conversation_id="", client="console", timezone=s.settings.timezone)
            tested = s.toolbox.action(rest[0], ToolContext(s, state), json.loads(" ".join(words) or "{}"))
            named = next((word[5:] for word in rest if word.startswith("mode=") and word[5:] != "default"), None)
            explained = s.gate.explain(
                tested, Situation("interactive", "tainted" in rest, False, modes.check_mode(named))
            )
            return (
                f"{tested.tool} [{tested.klass}, {tested.level}] {' '.join(tested.targets)}\n"
                f"→ {explained['effect']} ({explained['reason']})"
                + (f"; raised: {', '.join(explained['raised'])}" if explained["raised"] else "")
            )
        if action == "import-legacy":
            from .app import static_tools
            from .gate.legacy import import_legacy

            return import_legacy(s.db, {tool.name: tool.classes for tool in static_tools()})
    except (ModeError, ValueError, KeyError, ConversationError) as error:
        return f"Error: {error}"
    return MODE_USAGE


async def _approvals(s: Services, args: list[str], token: TokenInfo) -> str:
    if not args or args[0] == "list":
        found = s.approvals.list("pending")
        return (
            "\n".join(f"#{a.id} [{a.level}] {a.summary} ({a.reason}; expires {a.expires_at})" for a in found)
            or "Nothing waits."
        )
    words = {"allow": "allow_once", "refuse": "refuse_once"}
    answer = words.get(args[0], args[0])
    if answer not in ANSWERS or len(args) != 2:
        return "Usage: /approvals [list] | allow|refuse <id>"
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
    lines = [
        f"{row['key']:<45} {row['calls']:>6} calls {row['prompt']:>12,} in {row['completion']:>10,} out"
        for row in stats["rows"]
    ]
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
    return (
        "\n".join(f"{item['name']}  {item['size_mb']} MB  {item['kind']}" for item in s.backups.listing())
        or "No backup yet."
    )


async def _update(s: Services, args: list[str], token: TokenInfo) -> str:
    if s.updater is None:
        return "Updates are not available."
    if not args or args[0] == "list":
        releases = await s.updater.releases()
        return (
            "\n".join(
                f"{r['tag']:<12} {r['published_at'] or ''}  {r['name']}" + ("  (installed)" if r["current"] else "")
                for r in releases
            )
            or "No release found."
        )
    return await s.updater.start(args[0])


def _trust(s: Services, args: list[str], token: TokenInfo) -> str:
    """Files that came from outside or were written after untrusted content, and your word that one is fine."""
    marks = s.provenance
    if marks is None:
        return "The workspace is not available."
    if not args or args[0] == "list":
        found = marks.listing()
        if not found:
            return "(no marked file: nothing came from outside)"
        return "\n".join(
            f"{'trusted  ' if item['trusted'] else 'untrusted'} {item['kind']:<10} {item['path']}  ({item['source']})"
            for item in found
        )
    action, *rest = args
    path = rest[0] if action == "untrust" and rest else action
    if token.approvals != "all":  # trusting a file is an answer given in advance: as for the API, only scope `all`
        return "Error: this token may not trust a file: it decides what Clara reads without care."
    try:
        if action == "untrust":
            return (
                f"Not trusted any more: {marks.untrust(path)} file(s)."
                if rest
                else "Usage: /trust [list] | <path> | untrust <path>"
            )
        count = marks.trust(path)
    except ValueError as error:
        return f"Error: {error}"
    return f"Trusted {count} file(s)." if count else f"Error: {path} is not a marked file (or it cannot be read)."


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
    return (
        "\n".join(
            f"{e['at']} {e['tool']:<16} {e['effect']:<6} {e['outcome']:<8} {e['target'][:60]}"
            for e in s.audit.recent(limit)
        )
        or "Empty."
    )


COMMANDS: list[Command] = [
    Command("status", "/status", "Version, state, models, storage, Tailscale, backups, alerts.", _status),
    Command("provider", "/provider [list] | enable|disable|check <id>", "Model providers.", _provider),
    Command("model", "/model [provider:model]", "Show or set the default model.", _model),
    Command(
        "models", "/models [refresh] | show|hide <ref>...", "The catalogue; * marks models shown in pickers.", _models
    ),
    Command("fallbacks", "/fallbacks [<ref>... | clear]", "Models asked in turn when one fails.", _fallbacks),
    Command("upkeep", "/upkeep [<ref> | none]", "The model of titles and summaries.", _upkeep),
    Command(
        "tokens", "/tokens [list] | create | revoke | approvals", "Client tokens (revoking works at once).", _tokens
    ),
    Command("secrets", "/secrets [list] | set <name> <value> | unset <name>", "The encrypted secrets store.", _secrets),
    Command(
        "school",
        "/school [status] | disconnect <moodle|auriga> | forget-password",
        "Moodle and Auriga: who is connected, forget a site's token, or forget the school password Clara keeps for Auriga.",
        _school,
    ),
    Command(
        "mode",
        MODE_USAGE.removeprefix("Usage: "),
        "The modes: what each conversation may use, and what each tool does.",
        _mode,
    ),
    Command(
        "automations",
        "/automations [list] | show | add | edit | on | off | run | del | test | runs | events",
        "Automations: what Clara does when a trigger holds (a mail arrives, a time comes).",
        _automations,
    ),
    Command("approvals", "/approvals [list] | allow|refuse <id>", "Pending approval requests.", _approvals),
    Command("debug", "/debug [on|off]", "Full prompts and answers to the debug log for one hour.", _debug),
    Command("usage", "/usage [24h|7d|30d|90d|365d|all] [day|model|kind|provider|client]", "Token statistics.", _usage),
    Command("alerts", "/alerts [clear <key>]", "Active and recent alerts.", _alerts),
    Command("audit", "/audit [n]", "The last gate decisions.", _audit),
    Command(
        "trust",
        "/trust [list] | <path> | untrust <path>",
        "Workspace files that came from outside: trust one (Clara reads it as your own) or take it back.",
        _trust,
    ),
    Command("undo", "/undo [commit]", "Revert the last workspace change (or a given commit).", _undo),
    Command("vault", "/vault [status|sync|reindex]", "The Obsidian vault.", _vault),
    Command("backup", "/backup [now]", "Local backups (and the remote copy).", _backup),
    Command(
        "update", "/update [list | <tag>]", "Tagged releases: list, or install one (preflight, then restart).", _update
    ),
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
