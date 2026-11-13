"""Helpers for tests that need a mode to say something other than what the built-in default mode says."""

from __future__ import annotations

from clara.gate import Line


def line(verdict: str, tool: str = "", klass: str = "", target: str = "", tainted: str = "") -> Line:
    return Line(verdict, tool, klass, target, tainted)


def default(services, *lines: Line) -> None:
    """Put these lines in the default mode of this test's server (the file is not written)."""
    services.modes.default = services.modes.default.with_lines(*lines)


def make(services, name: str, *lines: Line, packs=()):
    return services.modes.create(name, packs, lines)


def only(services, *lines: Line) -> None:
    """Replace the lines of the default mode of this test's server by these (a class with no line takes the built-in one)."""
    from clara.gate.modes import DEFAULT, Mode, complete

    services.modes.default = complete(Mode(DEFAULT, (), lines))[0]


def own(services, conversation_id: str, mode: str | None) -> None:
    services.conversations.update(conversation_id, mode=mode)


def level_lines(level: int) -> tuple[Line, ...]:
    """What the old auto level `level` (0 to 5 of the default ladder) made every class do: the verdicts of the thresholds at that
    level, `(if tainted)` raised for the classes the tainted-turn rule raised, and (at the last level) not raised."""
    from clara.gate.actions import CLASSES, LEVEL_KEYS
    from clara.gate.modes import tainted_flavour, verdict_at

    position = {key: index for index, key in enumerate(LEVEL_KEYS)}
    top = len(LEVEL_KEYS) - 1
    lines = []
    for klass, (ask_key, approve_key, _) in CLASSES.items():
        verdict = verdict_at(level, top, position[ask_key], position[approve_key])
        lines.append(Line(verdict, klass=klass, tainted=verdict if level >= top else tainted_flavour(klass, verdict)))
    return tuple(lines)


def at_level(services, level: int) -> None:
    """The default mode of this test's server, as the old auto level `level` made the tools behave."""
    only(services, *level_lines(level))


def level_mode(services, name: str, level: int):
    """A mode that makes the tools behave as the old auto level `level` did."""
    return services.modes.create(name, (), level_lines(level))
