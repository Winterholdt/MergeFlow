"""Tests for the main CLI entry point."""

from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from core.models import WorkflowPhase, WorkflowState
from main import async_main, build_parser


def test_build_parser_arguments():
    """Verify CLI argument parsing behavior."""
    parser = build_parser()

    # Positional requirement
    args = parser.parse_args(["Build payment service"])
    assert args.requirements == "Build payment service"
    assert args.file_path is None
    assert args.config_path == "config.yaml"
    assert args.auto_merge is None

    # File option
    args_file = parser.parse_args(["--file", "reqs.txt", "--config", "custom.yaml", "--auto-merge"])
    assert args_file.file_path == "reqs.txt"
    assert args_file.config_path == "custom.yaml"
    assert args_file.auto_merge is True

    # No auto merge
    args_no_merge = parser.parse_args(["reqs", "--no-auto-merge"])
    assert args_no_merge.auto_merge is False


@pytest.mark.asyncio
async def test_async_main_missing_requirements_non_tty():
    """Missing requirements in non-tty mode returns exit code 1."""
    parser = build_parser()
    args = parser.parse_args([])
    with patch("sys.stdin.isatty", return_value=False):
        code = await async_main(args)
    assert code == 1


@pytest.mark.asyncio
async def test_async_main_missing_file():
    """Non-existent requirements file returns exit code 1."""
    parser = build_parser()
    args = parser.parse_args(["--file", "non_existent_file_98765.txt"])
    code = await async_main(args)
    assert code == 1


@pytest.mark.asyncio
async def test_async_main_file_input(tmp_path: Path):
    """File contents are read and passed to orchestrator."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("Anforderung aus Datei: CSV Export.", encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["--file", str(req_file)])

    mock_state = WorkflowState(
        task_id="TASK-CLI-1",
        raw_requirements="Anforderung aus Datei: CSV Export.",
        current_phase=WorkflowPhase.DONE.value,
    )

    with patch("main.run_orchestrator", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_state
        code = await async_main(args)

    assert code == 0
    mock_run.assert_called_once()
    assert "Anforderung aus Datei: CSV Export." in mock_run.call_args.kwargs["requirements"]


@pytest.mark.asyncio
async def test_async_main_orchestration_failed():
    """When orchestrator completes in FAILED phase, CLI exits with code 1."""
    parser = build_parser()
    args = parser.parse_args(["Some requirement"])

    failed_state = WorkflowState(
        task_id="TASK-CLI-FAIL",
        raw_requirements="Some requirement",
        current_phase=WorkflowPhase.FAILED.value,
    )

    with patch("main.run_orchestrator", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = failed_state
        code = await async_main(args)

    assert code == 1


@pytest.mark.asyncio
async def test_async_main_unquoted_multi_word_requirements():
    """CLI automatically joins multiple words passed without quotes."""
    parser = build_parser()
    args = parser.parse_args(["Build", "user", "authentication", "system"])

    mock_state = WorkflowState(
        task_id="TASK-UNQUOTED",
        raw_requirements="Build user authentication system",
        current_phase=WorkflowPhase.DONE.value,
    )

    with patch("main.run_orchestrator", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_state
        code = await async_main(args)

    assert code == 0
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["requirements"] == "Build user authentication system"


@pytest.mark.asyncio
async def test_async_main_tty_no_args_launches_repl():
    """When no args are given in tty mode, launches the interactive Agent REPL directly."""
    parser = build_parser()
    args = parser.parse_args([])

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("main.run_interactive_session", new_callable=AsyncMock) as mock_interactive,
    ):
        mock_interactive.return_value = 0
        code = await async_main(args)

    assert code == 0
    mock_interactive.assert_called_once()


@pytest.mark.asyncio
async def test_async_main_interactive_flag():
    """Verify that --interactive launches run_interactive_session."""
    parser = build_parser()
    args = parser.parse_args(["--interactive"])

    with patch("main.run_interactive_session", new_callable=AsyncMock) as mock_interactive:
        mock_interactive.return_value = 0
        code = await async_main(args)

    assert code == 0
    mock_interactive.assert_called_once()


@pytest.mark.asyncio
async def test_subcommand_agents():
    """Verify 'orch agents' prints agents table and exits 0."""
    parser = build_parser()
    args = parser.parse_args(["agents"])

    with patch("main.show_agents_table") as mock_show:
        code = await async_main(args)

    assert code == 0
    mock_show.assert_called_once()


@pytest.mark.asyncio
async def test_subcommand_status():
    """Verify 'orch status' displays status panel and exits 0."""
    parser = build_parser()
    args = parser.parse_args(["status"])

    with patch("main.show_status_panel") as mock_status:
        code = await async_main(args)

    assert code == 0
    mock_status.assert_called_once()


@pytest.mark.asyncio
async def test_subcommand_spec():
    """Verify 'orch spec' displays SPEC.md preview and exits 0."""
    parser = build_parser()
    args = parser.parse_args(["spec"])

    with patch("main.show_spec_preview") as mock_spec:
        code = await async_main(args)

    assert code == 0
    mock_spec.assert_called_once()


@pytest.mark.asyncio
async def test_subcommand_diff():
    """Verify 'orch diff' displays git diff and exits 0."""
    parser = build_parser()
    args = parser.parse_args(["diff"])

    with patch("main.show_diff_preview", new_callable=AsyncMock) as mock_diff:
        code = await async_main(args)

    assert code == 0
    mock_diff.assert_called_once()
