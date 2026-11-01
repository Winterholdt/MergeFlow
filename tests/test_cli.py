"""Unit tests for MergeFlow enhanced CLI components, slash commands, and interactive session."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from rich.console import Console

from core.cli.banner import print_banner
from core.cli.formatting import format_phase_breadcrumbs, get_badge_style
from core.cli.interactive import print_help_table, run_interactive_session
from core.cli.logger import render_phase_header, setup_cli_logging
from core.cli.prompter import CLIPrompter
from core.cli.settings_menu import (
    interactive_resume_menu,
    interactive_settings_menu,
    save_config_to_yaml,
    show_settings_table,
    show_skills_overview,
)
from core.config import AppConfig, load_config
from core.models import WorkflowPhase, WorkflowState
from core.skills import SkillManager


def test_format_phase_breadcrumbs():
    """Verify phase breadcrumbs formatting across all 5 phases."""
    b1 = format_phase_breadcrumbs(WorkflowPhase.SPEC_DISCOVERY)
    assert "1.Spec" in b1.plain
    assert "●" in b1.plain

    b2 = format_phase_breadcrumbs(WorkflowPhase.CODING)
    assert "✓ 1.Spec" in b2.plain
    assert "● 2.Dev" in b2.plain

    b3 = format_phase_breadcrumbs(WorkflowPhase.CI_LOOP)
    assert "✓ 1.Spec" in b3.plain
    assert "✓ 2.Dev" in b3.plain
    assert "● 3.CI" in b3.plain

    b4 = format_phase_breadcrumbs(WorkflowPhase.REVIEW)
    assert "● 4.Review" in b4.plain

    b5 = format_phase_breadcrumbs(WorkflowPhase.CONSENSUS)
    assert "● 5.Merge" in b5.plain

    b_done = format_phase_breadcrumbs(WorkflowPhase.DONE)
    assert "✓ 1.Spec" in b_done.plain
    assert "✓ 5.Merge" in b_done.plain

    b_fail = format_phase_breadcrumbs(WorkflowPhase.FAILED)
    assert "1.Spec" in b_fail.plain


def test_get_badge_style():
    """Verify category badge styles."""
    badge, style = get_badge_style("SPEC")
    assert "Phase 1: Spec" in badge
    badge_dev, _ = get_badge_style("DEV")
    assert "Phase 2: Dev" in badge_dev
    badge_ci, _ = get_badge_style("CI")
    assert "Phase 3: CI" in badge_ci
    badge_rev, _ = get_badge_style("REVIEW")
    assert "Phase 4: Review" in badge_rev
    badge_con, _ = get_badge_style("CONSENSUS")
    assert "Phase 5: Consensus" in badge_con


def test_print_banner_and_headers():
    """Test banner and phase header rendering without errors."""
    console = Console(record=True, width=120)
    config = load_config()
    print_banner(console=console, config=config, repo_path=".", version="0.1.0")
    output = console.export_text()
    assert "Autonomous Multi-Agent Development Pipeline" in output
    assert "Provider:" in output

    render_phase_header(
        console=console,
        phase_num=1,
        phase_name="Requirements Discovery & Spec Generation",
        description="Scans repo and prepares specification",
    )
    header_output = console.export_text()
    assert "Pipeline Step 1/5" in header_output


def test_setup_cli_logging():
    """Test setting up structured logging."""
    console = Console(record=True)
    setup_cli_logging(console=console, debug=True)
    import logging
    logger = logging.getLogger("test_logger")
    logger.info("Test log entry")
    out = console.export_text()
    assert "Test log entry" in out


def test_settings_menu_table_and_save(tmp_path: Path):
    """Test displaying settings table and saving to YAML."""
    console = Console(record=True, width=120)
    config = load_config()
    show_settings_table(config, console)
    out = console.export_text()
    assert "Standard-Provider" in out

    out_yaml = tmp_path / "test_config.yaml"
    save_config_to_yaml(config, out_yaml)
    assert out_yaml.is_file()
    assert "default_provider" in out_yaml.read_text(encoding="utf-8")


def test_show_skills_overview():
    """Test skill overview rendering."""
    console = Console(record=True, width=120)
    mgr = SkillManager()
    show_skills_overview(mgr, console)
    out = console.export_text()
    assert "Phase 1: Discovery" in out
    assert "Phase 2: Dev Loop" in out


def test_prompter_fallback(tmp_path: Path):
    """Test prompter fallback behavior in non-tty or mock."""
    prompter = CLIPrompter(history_dir=tmp_path)
    console = Console()
    with patch("sys.stdin.isatty", return_value=False), patch.object(console, "input", return_value="custom task"):
        res = prompter.get_input(console=console)
        assert res == "custom task"


@pytest.mark.asyncio
async def test_interactive_session_slash_exit():
    """Interactive session exits cleanly on /exit."""
    console = Console(record=True)
    with patch("core.cli.interactive.CLIPrompter.get_input", side_effect=["/exit"]):
        code = await run_interactive_session(console=console)
    assert code == 0
    assert "Auf Wiedersehen" in console.export_text()


@pytest.mark.asyncio
async def test_interactive_session_slash_commands(tmp_path: Path):
    """Test interactive session handling various slash commands."""
    console = Console(record=True)
    commands = [
        "/help",
        "/provider",
        "/provider antigravity",
        "/config auto-merge on",
        "/status",
        "/multiline",
        "/exit",
    ]
    with patch("core.cli.interactive.CLIPrompter.get_input", side_effect=commands):
        code = await run_interactive_session(console=console, repo_path=str(tmp_path))
    assert code == 0
    out = console.export_text()
    assert "Verfügbare Befehle" in out
    assert "Provider geändert auf: antigravity" in out
    assert "Auto-Merge auf True gesetzt" in out
