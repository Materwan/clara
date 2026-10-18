"""The daily recap: a check-in at the end of the day, then the day written into the vault's daily note."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .digest import Digest, DigestBuilder, day_bounds, french_date
from .service import Recap, RecapError, Recaps, read_day
from .tools import WRITE_RECAP, recap_tool

if TYPE_CHECKING:
    from ..services import Services

__all__ = ["WRITE_RECAP", "Digest", "DigestBuilder", "Recap", "RecapError", "Recaps", "day_bounds", "french_date",
           "install", "read_day", "recap_tool"]


def install(s: Services) -> None:
    """Adds the recap when there is a vault to write it in (it runs by itself only with CLARA_RECAP)."""
    if s.vault is None:
        return
    recaps = Recaps(s)
    s.recaps = recaps
    s.toolbox.add(recap_tool())
    s.agent.tool_conditions[WRITE_RECAP] = lambda state: recaps.is_checkin(state.conversation_id)
    s.agent.context_blocks.append(recaps.context_block)
