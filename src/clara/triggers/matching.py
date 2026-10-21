"""Which events a trigger wants: a filter of fields and operators, nothing else.

    {"from": {"contains": ["@billing.example"]}, "subject": {"contains": "invoice", "not_contains": ["newsletter"]},
     "has_attachment": {"eq": true}}

A bare value is shorthand for `contains` (text) or `eq` (a bool). All fields must hold; for one field, all its operators
must; for one operator, any of its values. Text is compared without case. There is no regular expression on purpose: the
subject of a mail is written by a stranger, and a pattern that backtracks without end would stop the server.
"""

from __future__ import annotations

from typing import Any

from .events import EventKind

TEXT_OPERATORS = ("eq", "contains", "starts_with", "ends_with", "not_contains")
MAX_FIELDS = 10
MAX_VALUES = 20
MAX_VALUE = 200


class FilterError(ValueError):
    pass


def _values(value: Any, where: str) -> list[str]:
    items = value if isinstance(value, list) else [value]
    found = [str(item).strip() for item in items if isinstance(item, (str, int, float)) and not isinstance(item, bool)]
    if not found or len(found) != len(items) or any(not item for item in found):
        raise FilterError(f"{where}: give a text or a list of texts.")
    if len(found) > MAX_VALUES or any(len(item) > MAX_VALUE for item in found):
        raise FilterError(f"{where}: at most {MAX_VALUES} values of {MAX_VALUE} characters.")
    return found


def check(kind: EventKind, raw: Any) -> dict:
    """The filter as stored (every field with its operators), or FilterError saying what is wrong."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise FilterError("A filter is an object: {field: {operator: value}}.")
    if len(raw) > MAX_FIELDS:
        raise FilterError(f"At most {MAX_FIELDS} fields in a filter.")
    clean: dict[str, dict] = {}
    for name, spec in raw.items():
        type_ = kind.fields.get(name)
        if type_ is None:
            raise FilterError(f"{name!r} is not a field of {kind.kind} (its fields: {', '.join(kind.fields)}).")
        if type_ == "bool":
            wanted = spec.get("eq") if isinstance(spec, dict) else spec
            if not isinstance(wanted, bool) or (isinstance(spec, dict) and set(spec) != {"eq"}):
                raise FilterError(f"{name}: true or false ({{\"eq\": true}}).")
            clean[name] = {"eq": wanted}
            continue
        operators = spec if isinstance(spec, dict) else {"contains": spec}
        if not operators:
            raise FilterError(f"{name}: say how to compare it ({', '.join(TEXT_OPERATORS)}).")
        clean[name] = {}
        for operator, value in operators.items():
            if operator not in TEXT_OPERATORS:
                raise FilterError(f"{name}: {operator!r} is not an operator ({', '.join(TEXT_OPERATORS)}).")
            clean[name][operator] = _values(value, f"{name}.{operator}")
    return clean


def _holds(operator: str, values: list[str], actual: str) -> bool:
    actual = actual.casefold()
    wanted = [value.casefold() for value in values]
    if operator == "eq":
        return actual in wanted
    if operator == "contains":
        return any(value in actual for value in wanted)
    if operator == "starts_with":
        return any(actual.startswith(value) for value in wanted)
    if operator == "ends_with":
        return any(actual.endswith(value) for value in wanted)
    return not any(value in actual for value in wanted)  # not_contains


def matches(filter_: dict, data: dict) -> bool:
    for name, spec in filter_.items():
        actual = data.get(name)
        if isinstance(spec.get("eq"), bool):
            if bool(actual) is not spec["eq"]:
                return False
            continue
        for operator, values in spec.items():
            if not _holds(operator, values, "" if actual is None else str(actual)):
                return False
    return True
