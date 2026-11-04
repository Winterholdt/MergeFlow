"""Unit tests for cli_events.py (decoupled event architecture & agent registry)."""

import asyncio
from unittest.mock import MagicMock
import pytest

from cli_events import (
    AgentInfo,
    AgentRegistry,
    CLIEvent,
    CLIEventEmitter,
    EventType,
    get_registered_agents,
    register_agent,
)


def test_cli_event_creation_and_properties():
    """Verify event initialization, type mapping, and alias properties."""
    event = CLIEvent(
        event_type=EventType.ROUTING,
        message="Routing task",
        agent_name="DiscoveryAgent",
        tool_name="git_diff",
        token="token_abc",
        data={"target": "spec"},
    )
    assert event.type == "routing"
    assert event.agent == "DiscoveryAgent"
    assert event.tool == "git_diff"
    assert event.token == "token_abc"
    assert event.data["target"] == "spec"
    assert event.timestamp > 0


def test_cli_event_string_type():
    """Verify CLIEvent accepts arbitrary string event types."""
    event = CLIEvent(event_type="custom_event", message="custom")
    assert event.type == "custom_event"
    assert event.message == "custom"


def test_emitter_subscribe_unsubscribe():
    """Verify adding, removing, and clearing listeners."""
    emitter = CLIEventEmitter()
    mock_cb = MagicMock()

    emitter.subscribe(mock_cb)
    assert mock_cb in emitter._handlers

    # Duplicate subscriptions ignored
    emitter.subscribe(mock_cb)
    assert len(emitter._handlers) == 1

    emitter.unsubscribe(mock_cb)
    assert mock_cb not in emitter._handlers

    # Unsubscribing non-existent is a no-op
    emitter.unsubscribe(mock_cb)

    emitter.subscribe(mock_cb)
    emitter.clear()
    assert len(emitter._handlers) == 0


def test_emitter_sync_dispatch():
    """Verify sync emit delivers events to all subscribers."""
    emitter = CLIEventEmitter()
    events_received: list[CLIEvent] = []

    def listener(ev: CLIEvent) -> None:
        events_received.append(ev)

    emitter.subscribe(listener)
    ev = emitter.emit_routing("Analyzing...")
    assert len(events_received) == 1
    assert events_received[0] == ev
    assert events_received[0].event_type == EventType.ROUTING


def test_emitter_handles_listener_error(caplog):
    """Verify that an exception in one listener does not crash the emitter."""
    emitter = CLIEventEmitter()
    events_received: list[CLIEvent] = []

    def bad_listener(ev: CLIEvent) -> None:
        raise ValueError("Listener crash")

    def good_listener(ev: CLIEvent) -> None:
        events_received.append(ev)

    emitter.subscribe(bad_listener)
    emitter.subscribe(good_listener)

    emitter.emit_status("Operational")
    assert len(events_received) == 1
    assert events_received[0].message == "Operational"


@pytest.mark.asyncio
async def test_emitter_async_dispatch():
    """Verify emit_async awaits coroutine listeners."""
    emitter = CLIEventEmitter()
    events_received: list[CLIEvent] = []

    async def async_listener(ev: CLIEvent) -> None:
        await asyncio.sleep(0.01)
        events_received.append(ev)

    def sync_listener(ev: CLIEvent) -> None:
        events_received.append(ev)

    emitter.subscribe(async_listener)
    emitter.subscribe(sync_listener)

    event = CLIEvent(event_type=EventType.TOKEN, token="chunk-1")
    await emitter.emit_async(event)

    assert len(events_received) == 2


def test_emitter_convenience_methods():
    """Verify all convenience methods emit correct event types and payloads."""
    emitter = CLIEventEmitter()
    emitted: list[CLIEvent] = []
    emitter.subscribe(lambda ev: emitted.append(ev))

    ev_route = emitter.emit_routing("Route info", target_agent="DevAgent")
    assert ev_route.event_type == EventType.ROUTING
    assert ev_route.agent == "DevAgent"

    ev_start = emitter.emit_agent_start("DevAgent", role="Coder", message="Working")
    assert ev_start.event_type == EventType.AGENT_START
    assert ev_start.role == "Coder"

    ev_end = emitter.emit_agent_end("DevAgent", message="Done")
    assert ev_end.event_type == EventType.AGENT_END

    ev_tool_s = emitter.emit_tool_start("pytest", agent_name="DevAgent")
    assert ev_tool_s.event_type == EventType.TOOL_START
    assert ev_tool_s.tool == "pytest"

    ev_tool_e = emitter.emit_tool_end("pytest", agent_name="DevAgent")
    assert ev_tool_e.event_type == EventType.TOOL_END

    ev_tok = emitter.emit_token("streamed_text", agent_name="DevAgent")
    assert ev_tok.event_type == EventType.TOKEN
    assert ev_tok.token == "streamed_text"

    ev_stat = emitter.emit_status("Waiting on CI")
    assert ev_stat.event_type == EventType.STATUS

    ev_done = emitter.emit_done("All set", content="# Report")
    assert ev_done.event_type == EventType.DONE
    assert ev_done.content == "# Report"

    ev_err = emitter.emit_error(ValueError("Failed action"), message="Something broke")
    assert ev_err.event_type == EventType.ERROR
    assert "Failed action" in ev_err.content

    assert len(emitted) == 9


def test_agent_registry_defaults():
    """Verify agent registry initializes with expected core agents."""
    registry = AgentRegistry()
    agents = registry.get_all()
    assert len(agents) >= 9

    names = [a.name.lower() for a in agents]
    assert "discoveryagent" in names
    assert "devagent" in names
    assert "cihealingagent" in names
    assert "reviewerfunctional" in names
    assert "reviewersecurity" in names
    assert "consensusagent" in names
    assert "gatekeeperguard" in names

    disc = registry.get_agent("DiscoveryAgent")
    assert disc is not None
    assert "spec" in disc.phase.lower()
    assert len(disc.skills) > 0


def test_agent_registry_custom_registration():
    """Verify custom agent registration and lookup."""
    registry = AgentRegistry()
    custom_agent = AgentInfo(
        name="CustomTester",
        role="Test Runner",
        phase="Phase 2.5",
        description="Executes load and fuzz tests",
        skills=["browser-testing-with-devtools"],
        icon="⚡",
    )
    registry.register(custom_agent)
    retrieved = registry.get_agent("customtester")
    assert retrieved is not None
    assert retrieved.name == "CustomTester"
    assert retrieved.icon == "⚡"


def test_global_registry_helpers():
    """Verify global get_registered_agents and register_agent."""
    agents = get_registered_agents()
    assert len(agents) >= 9
    initial_len = len(agents)

    test_agent = AgentInfo(
        name="UniqueTestWorker",
        role="Worker",
        phase="Custom",
        description="Test desc",
    )
    register_agent(test_agent)
    updated = get_registered_agents()
    assert len(updated) == initial_len + 1
    assert any(a.name == "UniqueTestWorker" for a in updated)
