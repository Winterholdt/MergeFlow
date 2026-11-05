"""Unit tests for cli.py (Interactive Agent REPL & Terminal UI)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from rich.console import Console

from cli import (
    AgentCLI,
    TerminalUIEventHandler,
    build_cli_parser,
    run_cli,
)
from cli_events import CLIEvent, CLIEventEmitter, EventType
from core.models import WorkflowPhase, WorkflowState


def test_build_cli_parser():
    """Verify CLI parser options."""
    parser = build_cli_parser()
    args = parser.parse_args(["-c", "test.yaml", "-r", "./sub", "--debug"])
    assert args.config_path == "test.yaml"
    assert args.repo_path == "./sub"
    assert args.debug is True


def test_ui_event_handler_rendering():
    """Verify TerminalUIEventHandler renders various lifecycle events properly."""
    console = Console(record=True, width=120)
    handler = TerminalUIEventHandler(console=console)

    # Spinner lifecycle
    handler.start_spinner("Testing start...")
    assert handler._status is not None

    # Routing event
    handler.handle_event(CLIEvent(event_type=EventType.ROUTING, message="Scoping task"))

    # Agent start event
    handler.handle_event(
        CLIEvent(
            event_type=EventType.AGENT_START,
            agent_name="DiscoveryAgent",
            role="Architect",
            message="Analyzing specs",
        )
    )

    # Tool start & end
    handler.handle_event(
        CLIEvent(
            event_type=EventType.TOOL_START,
            tool_name="git_diff",
            message="Calculating changes",
        )
    )
    handler.handle_event(
        CLIEvent(
            event_type=EventType.TOOL_END,
            tool_name="git_diff",
            message="Diff computed",
        )
    )

    # Streaming token chunk
    handler.handle_event(
        CLIEvent(
            event_type=EventType.TOKEN,
            token="Hello token chunk",
        )
    )
    assert "Hello token chunk" in handler._accumulated_tokens

    # Status update
    handler.handle_event(
        CLIEvent(
            event_type=EventType.STATUS,
            message="Pipeline healthy",
        )
    )

    # Prompt event stops spinner
    handler.handle_event(
        CLIEvent(
            event_type=EventType.USER_PROMPT_START,
            message="Waiting for user input...",
        )
    )
    assert handler._status is None

    # Resume spinner for next test
    handler.start_spinner("Testing done...")

    # Done event stops spinner and renders message
    handler.handle_event(
        CLIEvent(
            event_type=EventType.DONE,
            message="Orchestration completed!",
        )
    )
    assert handler._status is None

    # Error event renders error
    handler.handle_event(
        CLIEvent(
            event_type=EventType.ERROR,
            message="Critical pipeline fault",
        )
    )

    out = console.export_text()
    assert "Orchestration completed!" in out
    assert "Critical pipeline fault" in out


@pytest.mark.asyncio
async def test_agent_cli_slash_agents():
    """Verify /agents renders the formatted table with all agents."""
    console = Console(record=True, width=120)
    cli = AgentCLI(console=console)
    handled = await cli.handle_slash_command("/agents")
    assert handled is True
    out = console.export_text()
    assert "Registrierte Orchestrator-Agenten & Personas" in out
    assert "DiscoveryAgent" in out
    assert "DevAgent" in out
    assert "ReviewerSecurity" in out
    assert "ConsensusAgent" in out


@pytest.mark.asyncio
async def test_agent_cli_slash_help():
    """Verify /help renders the command cheat sheet."""
    console = Console(record=True, width=120)
    cli = AgentCLI(console=console)
    handled = await cli.handle_slash_command("/help")
    assert handled is True
    out = console.export_text()
    assert "Verfügbare Slash-Commands & Shortcuts" in out
    assert "/agents" in out
    assert "/status" in out


@pytest.mark.asyncio
async def test_agent_cli_slash_provider():
    """Verify /provider gets and updates the active provider."""
    console = Console(record=True, width=120)
    cli = AgentCLI(console=console)

    # Query provider
    await cli.handle_slash_command("/provider")
    out = console.export_text()
    assert "Aktiver LLM-Provider:" in out

    # Switch provider
    await cli.handle_slash_command("/provider claude")
    assert cli.config.providers.default_provider == "claude"
    out2 = console.export_text()
    assert "Provider geändert auf: claude" in out2

    # Invalid provider
    await cli.handle_slash_command("/provider invalid_provider")
    out3 = console.export_text()
    assert "Ungültiger Provider" in out3


@pytest.mark.asyncio
async def test_agent_cli_slash_config():
    """Verify /config updates configuration values."""
    console = Console(record=True, width=120)
    cli = AgentCLI(console=console)

    await cli.handle_slash_command("/config auto-merge true")
    assert cli.config.workflow.auto_merge is True
    out = console.export_text()
    assert "Auto-Merge auf True gesetzt" in out


@pytest.mark.asyncio
async def test_agent_cli_slash_spec_and_diff(tmp_path: Path):
    """Verify /spec and /diff rendering."""
    console = Console(record=True, width=120)
    spec_path = tmp_path / "SPEC.md"
    spec_path.write_text("# Test Spec Markdown\n- Requirement 1", encoding="utf-8")

    cli = AgentCLI(repo_path=str(tmp_path), console=console)
    await cli.handle_slash_command("/spec")
    out = console.export_text()
    assert "Test Spec Markdown" in out

    # Diff
    with patch.object(cli.git_handler, "is_repo", AsyncMock(return_value=True)):
        with patch.object(cli.git_handler, "get_diff", AsyncMock(return_value="diff --git a/test b/test")):
            await cli.handle_slash_command("/diff")
    out_diff = console.export_text()
    assert "diff --git" in out_diff


@pytest.mark.asyncio
async def test_agent_cli_slash_exit():
    """Verify /exit raises SystemExit(0)."""
    console = Console(record=True)
    cli = AgentCLI(console=console)
    with pytest.raises(SystemExit) as exc_info:
        await cli.handle_slash_command("/exit")
    assert exc_info.value.code == 0


@pytest.mark.asyncio
async def test_agent_cli_repl_loop_exit():
    """Verify REPL loop gracefully exits on EOFError or /exit."""
    console = Console(record=True)
    cli = AgentCLI(console=console)

    with patch.object(cli.session, "prompt_async", AsyncMock(side_effect=["/exit"])):
        code = await cli.run()
    assert code == 0


@pytest.mark.asyncio
async def test_agent_cli_repl_loop_ctrl_c():
    """Verify REPL loop handles KeyboardInterrupt and continues until exit."""
    console = Console(record=True)
    cli = AgentCLI(console=console)

    # First Ctrl+C, then /exit
    with patch.object(cli.session, "prompt_async", AsyncMock(side_effect=[KeyboardInterrupt(), "/exit"])):
        code = await cli.run()
    assert code == 0
    assert "Eingabe abgebrochen" in console.export_text()


@pytest.mark.asyncio
async def test_agent_cli_executes_orchestration():
    """Verify running a prompt invokes orchestrator with event emitter."""
    console = Console(record=True)
    cli = AgentCLI(console=console)

    fake_state = WorkflowState(task_id="TASK-TEST", raw_requirements="Build feature")
    fake_state.current_phase = WorkflowPhase.DONE.value

    with patch("core.cli.repl.run_orchestrator", AsyncMock(return_value=fake_state)) as mock_run:
        await cli.execute_orchestration("Build feature")
        mock_run.assert_called_once()
        # Verify event_emitter was passed
        _, kwargs = mock_run.call_args
        assert "event_emitter" in kwargs
        assert isinstance(kwargs["event_emitter"], CLIEventEmitter)
        assert "input_func" in kwargs
        assert callable(kwargs["input_func"])

        with patch("rich.prompt.Prompt.ask", return_value="user answer") as mock_ask:
            answer = kwargs["input_func"]("Antwort zu Frage 1")
            assert answer == "user answer"
            mock_ask.assert_called_once_with("Antwort zu Frage 1", console=cli.console)

    out = console.export_text()
    assert "Workflow Erfolgreich" in out
    assert "TASK-TEST" in out


@pytest.mark.asyncio
async def test_orchestrator_emits_lifecycle_events():
    """Verify orchestrator triggers routing, agent_start, and done events during run."""
    emitter = CLIEventEmitter()
    events: list[CLIEvent] = []
    emitter.subscribe(lambda ev: events.append(ev))

    from core.orchestrator import Orchestrator
    console = Console(record=True)

    fake_state = WorkflowState(task_id="TEST-EMIT", raw_requirements="Task 1")
    fake_state.current_phase = WorkflowPhase.DONE.value

    with patch.object(WorkflowState, "load_from_file", return_value=fake_state):
        with patch.object(Path, "is_file", return_value=True):
            orchestrator = Orchestrator(console=console, event_emitter=emitter)
            res = await orchestrator.run(
                requirements="Task 1",
                task_id="TEST-EMIT",
                resume_from_state=True,
            )

    event_types = [e.event_type for e in events]
    assert EventType.ROUTING in event_types
    assert EventType.DONE in event_types
