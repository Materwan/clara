"""Which events an event node wants: a filter of fields, each field a regular expression on the RE2 engine.

    {"subject": {"pattern": "invoice|receipt", "not": false, "case_sensitive": false},
     "from": [{"pattern": "@billing\\.example$"}, {"pattern": "newsletter", "not": true}],
     "has_attachment": {"eq": true}}

A text field holds a condition (or a list of them: all must hold). A condition **searches** the pattern anywhere in the field,
**without regard to case** unless `case_sensitive` is ticked, and `not` inverts it (« must not match »). A bool field holds
`{"eq": true}`. All fields must hold; a field the event does not have is the empty text.

The engine is RE2 (`google-re2`): it runs in time linear in the text, so a pattern written here cannot be made to run for ever by
a subject or a body written by a stranger. It has no lookahead and no back-reference, and says so when a pattern is saved.
A filter is still not a security boundary: a run an event starts is tainted and unattended whatever the filter was.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import re2

from .events import EventKind

MAX_FIELDS = 10
MAX_CONDITIONS = 5  # per field
MAX_PATTERN = 500
SHOWN = 60  # characters of a field's text a verdict quotes


class FilterError(ValueError):
    pass


def _options(case_sensitive: bool) -> re2.Options:
    options = re2.Options()
    options.case_sensitive = case_sensitive
    options.log_errors = False  # the message is ours to give, not stderr's
    return options


@lru_cache(maxsize=512)
def _compiled(pattern: str, case_sensitive: bool) -> Any:
    try:
        return re2.compile(pattern, _options(case_sensitive))
    except re2.error as error:
        message = error.args[0] if error.args else str(error)
        text = message.decode("utf-8", "replace") if isinstance(message, bytes) else str(message)
        if "perl operator" in text or "escape sequence" in text:
            text += " (RE2 has no lookahead and no back-reference)"
        raise FilterError(text) from None


def _condition(name: str, raw: Any) -> dict:
    if isinstance(raw, str):
        raw = {"pattern": raw}
    if not isinstance(raw, dict) or "pattern" not in raw:
        raise FilterError(f'{name}: give {{"pattern": "…"}} (and "not", "case_sensitive" if you need them).')
    unknown = sorted(set(raw) - {"pattern", "not", "case_sensitive"})
    if unknown:
        raise FilterError(f"{name}: {', '.join(unknown)} is not part of a condition (pattern, not, case_sensitive).")
    pattern = raw["pattern"]
    if not isinstance(pattern, str) or not pattern:
        raise FilterError(f"{name}: the pattern is a text that is not empty.")
    if len(pattern) > MAX_PATTERN:
        raise FilterError(f"{name}: a pattern is at most {MAX_PATTERN} characters long.")
    flags = {}
    for key in ("not", "case_sensitive"):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise FilterError(f"{name}: {key} is true or false.")
        flags[key] = value
    try:
        _compiled(pattern, flags["case_sensitive"])
    except FilterError as error:
        raise FilterError(f"{name}: {error}") from None
    return {"pattern": pattern, "not": flags["not"], "case_sensitive": flags["case_sensitive"]}


def check(kind: EventKind, raw: Any) -> dict:
    """The filter as stored, or FilterError saying what is wrong (a pattern the engine refuses is told with its words)."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise FilterError("A filter is an object: {field: {pattern, not, case_sensitive}}.")
    if len(raw) > MAX_FIELDS:
        raise FilterError(f"At most {MAX_FIELDS} fields in a filter.")
    clean: dict[str, Any] = {}
    for name, spec in raw.items():
        type_ = kind.fields.get(name)
        if type_ is None:
            raise FilterError(f"{name!r} is not a field of {kind.kind} (its fields: {', '.join(kind.fields)}).")
        if type_ == "bool":
            wanted = spec.get("eq") if isinstance(spec, dict) else spec
            if not isinstance(wanted, bool) or (isinstance(spec, dict) and set(spec) != {"eq"}):
                raise FilterError(f'{name}: true or false ({{"eq": true}}).')
            clean[name] = {"eq": wanted}
            continue
        items = spec if isinstance(spec, list) else [spec]
        if not items or len(items) > MAX_CONDITIONS:
            raise FilterError(f"{name}: one condition, or a list of at most {MAX_CONDITIONS}.")
        found = [_condition(name, item) for item in items]
        clean[name] = found[0] if len(found) == 1 else found
    return clean


@dataclass(frozen=True)
class Judgement:
    field: str
    ok: bool
    why: str

    def describe(self) -> dict:
        return {"field": self.field, "ok": self.ok, "why": self.why}


def _quote(text: str) -> str:
    flat = " ".join(text.split())
    return f"“{flat[:SHOWN]}{'…' if len(flat) > SHOWN else ''}”"


def _judge(name: str, spec: dict, actual: Any) -> Judgement:
    if "eq" in spec:
        ok = bool(actual) is spec["eq"]
        return Judgement(
            name, ok, f"{name} is {'yes' if bool(actual) else 'no'}; the filter wants {'yes' if spec['eq'] else 'no'}"
        )
    text = "" if actual is None else str(actual)
    found = _compiled(spec["pattern"], spec["case_sensitive"]).search(text) is not None
    ok = found is not spec["not"]
    if spec["not"]:
        why = (
            f"{name} {_quote(text)} does not match /{spec['pattern']}/, as asked"
            if ok
            else (f"{name} {_quote(text)} matches /{spec['pattern']}/, which it must not")
        )
    else:
        why = (
            f"{name} {_quote(text)} matches /{spec['pattern']}/"
            if ok
            else (f"{name} {_quote(text)} does not match /{spec['pattern']}/")
        )
    return Judgement(name, ok, why)


def judge(filter_: dict, data: dict) -> list[Judgement]:
    """Every condition of the filter against an event's fields, with the reason it holds or not."""
    found = []
    for name, spec in filter_.items():
        for condition in spec if isinstance(spec, list) else [spec]:
            found.append(_judge(name, condition, data.get(name)))
    return found


def matches(filter_: dict, data: dict) -> bool:
    return all(item.ok for item in judge(filter_, data))


def words(filter_: dict) -> str:
    """The filter in one readable phrase: `subject matches “invoice”, from must not match “news” (case matters)`."""
    parts = []
    for name, spec in filter_.items():
        for condition in spec if isinstance(spec, list) else [spec]:
            if "eq" in condition:
                parts.append(f"{name} is {'yes' if condition['eq'] else 'no'}")
                continue
            verb = "must not match" if condition["not"] else "matches"
            case = " (case matters)" if condition["case_sensitive"] else ""
            parts.append(f"{name} {verb} “{condition['pattern']}”{case}")
    return ", ".join(parts)
