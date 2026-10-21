"""Hooks: when something happens outside (a mail arrives), Clara does what you said, within what you allowed. See service.py."""

from .events import KINDS, SourceEvent, StoredEvent
from .service import Trigger, TriggerError, Triggers

__all__ = ["KINDS", "SourceEvent", "StoredEvent", "Trigger", "TriggerError", "Triggers"]
