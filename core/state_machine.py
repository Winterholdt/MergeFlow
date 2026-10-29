"""State machine and persistence management for MergeFlow orchestrator.

Provides crash-resilience, atomic state serialization into .orchestrator/state.json,
and workflow resumption tracking.
"""

from collections.abc import Callable
import logging
from pathlib import Path
from typing import Any

from core.models import WorkflowPhase, WorkflowState

logger = logging.getLogger(__name__)

DEFAULT_ORCHESTRATOR_DIR = Path(".orchestrator")
DEFAULT_STATE_FILE = Path(".orchestrator/state.json")


def save_atomic_state(state: WorkflowState, path: Path | str = DEFAULT_STATE_FILE) -> Path:
    """Persist workflow state atomically to disk.

    Args:
        state: Active WorkflowState instance.
        path: Destination file path (defaults to .orchestrator/state.json).

    Returns:
        Resolved Path of the saved file.
    """
    return state.save_to_file(path)


def load_state(path: Path | str = DEFAULT_STATE_FILE, task_id: str | None = None) -> WorkflowState:
    """Load workflow state from file and optionally verify task_id.

    Args:
        path: Path to the JSON state file.
        task_id: Optional expected task_id to validate.

    Returns:
        WorkflowState restored from disk.

    Raises:
        FileNotFoundError: If state file does not exist.
        ValueError: If task_id does not match the saved state.
    """
    state_file = Path(path).resolve()
    if not state_file.is_file():
        raise FileNotFoundError(f"State file not found at: {state_file}")

    state = WorkflowState.load_from_file(state_file)
    if task_id and task_id != "LAST" and state.task_id != task_id:
        raise ValueError(f"State task_id '{state.task_id}' does not match requested task_id '{task_id}'.")

    return state


def determine_resume_phase(state: WorkflowState) -> WorkflowPhase:
    """Determine the last incomplete phase to resume execution from.

    Args:
        state: Restored WorkflowState.

    Returns:
        WorkflowPhase where execution should resume.
    """
    phase_str = state.current_phase

    if phase_str == WorkflowPhase.DONE.value:
        return WorkflowPhase.DONE

    # If the workflow crashed or stopped in FAILED or HUMAN_INTERVENTION_REQUIRED,
    # determine where it stopped
    if phase_str in (WorkflowPhase.FAILED.value, WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value):
        recorded_phase = state.metadata.get("failed_phase")
        if recorded_phase:
            try:
                return WorkflowPhase(recorded_phase)
            except ValueError:
                pass

        if not state.spec_markdown:
            return WorkflowPhase.SPEC_DISCOVERY
        if state.review_retry_count > 0:
            return WorkflowPhase.REVIEW
        if state.ci_retry_count > 0:
            return WorkflowPhase.CI_LOOP
        return WorkflowPhase.CODING

    try:
        return WorkflowPhase(phase_str)
    except ValueError:
        return WorkflowPhase.CODING


class StateMachine:
    """Manages workflow transitions and guarantees atomic persistence."""

    def __init__(
        self,
        state: WorkflowState,
        state_path: Path | str = DEFAULT_STATE_FILE,
        on_transition: Callable[[WorkflowState], Any] | None = None,
    ) -> None:
        """Initialize StateMachine with state and path.

        Args:
            state: WorkflowState instance.
            state_path: Path to state.json.
            on_transition: Optional callback invoked after phase transition.
        """
        self.state = state
        self.state_path = Path(state_path).resolve()
        self.on_transition = on_transition

    def save(self) -> Path:
        """Atomically persist state to disk."""
        return save_atomic_state(self.state, self.state_path)

    def transition_to(self, phase: WorkflowPhase | str) -> None:
        """Transition to a new phase and persist immediately.

        Args:
            phase: Target WorkflowPhase or string.
        """
        self.state.transition_to(phase)
        self.save()
        if self.on_transition:
            try:
                self.on_transition(self.state)
            except Exception as exc:
                logger.debug("on_transition callback error: %s", exc)

    def notify_commit(self, commit_hash: str | None = None) -> None:
        """Invoked after git commits to guarantee persistence.

        Args:
            commit_hash: Optional new commit SHA.
        """
        if commit_hash:
            self.state.metadata["last_commit"] = commit_hash
        self.save()
