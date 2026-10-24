"""Which day a tool means when it is not told: the day of the scheduled run it is in (the local date of the moment the run
was due, so a run due at 23:59 and started at 00:03 after a stop still means the day that ended), else today."""

from __future__ import annotations

from datetime import date, datetime

from ..timeutil import parse_iso, zone
from .base import ToolContext, ToolError


def resolve_day(context: ToolContext, text: object = "") -> date:
    tz = zone(context.turn.timezone)
    given = str(text or "").strip().lower()
    today = datetime.now(tz).date()
    if given:
        from ..packs.history import read_day

        try:
            return read_day(given, today)
        except ValueError as error:
            raise ToolError(str(error)) from None
    run = context.services.packs.run_of(context.turn.conversation_id) if context.services.packs is not None else None
    if run is not None:
        return parse_iso(run["due_at"]).astimezone(tz).date()
    return today
