"""Decoupled event system and agent registry for MergeFlow CLI and Orchestrator.

Backward-compatibility proxy module that imports and re-exports everything
from core.cli.events.
"""

from __future__ import annotations

from core.cli.events import (
    AgentInfo,
    AgentRegistry,
    CLIEvent,
    CLIEventEmitter,
    EventType,
    get_registered_agents,
    register_agent,
)

__all__ = [
    "AgentInfo",
    "AgentRegistry",
    "CLIEvent",
    "CLIEventEmitter",
    "EventType",
    "get_registered_agents",
    "register_agent",
]
