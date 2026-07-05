"""Canonical Event -> ControlCommand decision engine.

`core.orchestrator` is the stable import path used by the control runner and
tests.  The implementation lives in `core.orchestrator_v2`; keep this module as
the compatibility shim so there is only one active orchestrator behavior.
"""

from __future__ import annotations

from typing import Optional

from core.event import ControlCommand, Event
from core.orchestrator_v2 import Orchestrator


def make_system_command(name: str, source: str = "system") -> Optional[ControlCommand]:
    """Create system commands through the canonical orchestrator."""
    return Orchestrator().handle_event(Event.make("system", name, source))


__all__ = ["Orchestrator", "make_system_command"]
