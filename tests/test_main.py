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
async def test_async_main_missing_requirements():
    """Missing requirements returns exit code 1."""
    parser = build_parser()
    args = parser.parse_args([])
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
