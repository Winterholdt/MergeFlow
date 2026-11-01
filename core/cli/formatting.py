"""Formatting utilities and phase visualizers for MergeFlow CLI."""

from rich.text import Text
from rich.table import Table
from rich.panel import Panel

from core.models import WorkflowPhase

PHASES = [
    (1, "Spec", [WorkflowPhase.SPEC_DISCOVERY]),
    (2, "Dev", [WorkflowPhase.CODING]),
    (3, "CI", [WorkflowPhase.CI_LOOP]),
    (4, "Review", [WorkflowPhase.REVIEW]),
    (5, "Merge", [WorkflowPhase.CONSENSUS]),
]

PHASE_ORDER = {
    WorkflowPhase.SPEC_DISCOVERY.value: 1,
    WorkflowPhase.CODING.value: 2,
    WorkflowPhase.CI_LOOP.value: 3,
    WorkflowPhase.REVIEW.value: 4,
    WorkflowPhase.CONSENSUS.value: 5,
    WorkflowPhase.DONE.value: 6,
}


def format_phase_breadcrumbs(current_phase: str | WorkflowPhase | None) -> Text:
    """Render a visual pipeline breadcrumb indicator for the 5 phases.

    Example:
        [✓ 1.Spec] ──> [● 2.Dev] ──> [○ 3.CI] ──> [○ 4.Review] ──> [○ 5.Merge]

    Args:
        current_phase: The active WorkflowPhase string or enum.

    Returns:
        Rich Text instance with styled breadcrumbs.
    """
    phase_val = current_phase.value if isinstance(current_phase, WorkflowPhase) else (current_phase or "")
    current_num = PHASE_ORDER.get(phase_val, 0)
    is_done = phase_val == WorkflowPhase.DONE.value
    is_failed = phase_val == WorkflowPhase.FAILED.value
    is_paused = phase_val == WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value

    breadcrumb = Text()

    for idx, (p_num, p_name, _) in enumerate(PHASES):
        if idx > 0:
            breadcrumb.append(" ➔ ", style="dim white")

        if is_done or (current_num > p_num and not is_failed):
            # Completed
            breadcrumb.append(f"✓ {p_num}.{p_name}", style="bold green")
        elif current_num == p_num:
            # Currently active
            if is_failed:
                breadcrumb.append(f"✗ {p_num}.{p_name}", style="bold red")
            elif is_paused:
                breadcrumb.append(f"⏸ {p_num}.{p_name}", style="bold yellow")
            else:
                breadcrumb.append(f"● {p_num}.{p_name}", style="bold cyan")
        else:
            # Future / pending
            breadcrumb.append(f"○ {p_num}.{p_name}", style="dim")

    return breadcrumb


def get_badge_style(category: str) -> tuple[str, str]:
    """Get bracket text and Rich style for a category badge.

    Args:
        category: Log or event category.

    Returns:
        Tuple of (badge_text, style).
    """
    cat_upper = category.upper()
    badges = {
        "SPEC": ("[Phase 1: Spec]", "bold cyan"),
        "GATE": ("[Gatekeeper]", "bold magenta"),
        "SECURITY": ("[Security]", "bold red"),
        "DEV": ("[Phase 2: Dev]", "bold blue"),
        "CI": ("[Phase 3: CI]", "bold yellow"),
        "REVIEW": ("[Phase 4: Review]", "bold magenta"),
        "CONSENSUS": ("[Phase 5: Consensus]", "bold green"),
        "MERGE": ("[Auto-Merge]", "bold green"),
        "VCS": ("[VCS/Git]", "cyan"),
        "ORCH": ("[Orchestrator]", "bold purple"),
    }
    return badges.get(cat_upper, (f"[{category}]", "bold white"))
