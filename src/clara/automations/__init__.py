"""Automations: when a trigger holds (an event happens, a time comes, a time condition is met), Clara does what you said, within
what you allowed. See service.py for the pieces, tree.py for the trigger, matching.py for the filters."""

from .events import KINDS, SourceEvent, StoredEvent
from .service import Automation, AutomationError, Automations, Stopping

__all__ = ["KINDS", "Automation", "AutomationError", "Automations", "SourceEvent", "Stopping", "StoredEvent"]
