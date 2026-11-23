"""Installs the dictation service (`app.extend`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import Dictation
from .process import ProcessTranscriber

if TYPE_CHECKING:
    from ..services import Services


def install(s: Services) -> None:
    s.dictation = Dictation(s, ProcessTranscriber(s.settings.models_dir))
