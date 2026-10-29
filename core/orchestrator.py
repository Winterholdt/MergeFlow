"""Central Orchestrator & State Machine controller for MergeFlow.

Coordinates the end-to-end autonomous lifecycle across all 7 phases:
- Phase 1: Foundation (Config, Provider, WorkflowState initialization)
- Phase 2: Security & Gatekeeper (Policy enforcement, command validation, shell runner)
- Phase 3: Requirements Discovery & Specification (Clarifications, SPEC.md generation & approval)
- Phase 4: Developer Agent Loop (Branching, coding, Gatekeeper execution, Conventional Commits, draft PR)
- Phase 5: Self-Healing CI Loop (Pipeline watching, failure diagnosis, self-healing, retry circuit breaker)
- Phase 6: Multi-Persona Code Review (Parallel reviews across 4 personas, finding consolidation)
- Phase 7: Consensus, Feedback Loop & Auto-Merge (Re-review loops up to 3 times, auto-merge or interactive confirmation)

Guarantees persistence of WorkflowState after every phase and step.
"""

import asyncio
from collections.abc import Callable
import inspect
import logging
from pathlib import Path
from typing import Any
import uuid

from rich.console import Console
from rich.panel import Panel

from core.config import AppConfig, load_config
from core.consensus import (
    ConsensusDecision,
    ConsensusError,
    ConsensusStatus,
    run_consensus_phase,
)
from core.dev_loop import DevLoopError, run_dev_phase
from core.discovery import (
    DiscoveryCancelledError,
    DiscoveryError,
    run_discovery_phase,
)
from core.healing_loop import CIHealingError, run_ci_healing_loop
from core.models import WorkflowPhase, WorkflowState
from core.provider import BaseLLMProvider, get_provider
from core.reviewer import ReviewError, ReviewSummary, run_review_phase
from core.skills import SkillManager
from core.state_machine import DEFAULT_STATE_FILE, determine_resume_phase
from security.gatekeeper import Gatekeeper
from vcs.ci_watcher import CIWatcher
from vcs.git_handler import GitHandler
from vcs.remote_vcs import RemoteVCS

logger = logging.getLogger(__name__)


def _safe_call(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Invoke callable filtering kwargs to only those accepted if no **kwargs."""
    target = func
    if hasattr(func, "side_effect") and callable(func.side_effect):
        target = func.side_effect
    try:
        sig = inspect.signature(target)
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if not has_varkw:
            kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
    except (ValueError, TypeError):
        pass
    return func(*args, **kwargs)


class OrchestratorError(Exception):
    """Base exception for orchestrator execution errors."""


class Orchestrator:
    """Central controller and state machine coordinating Phase 1 to Phase 7."""

    def __init__(
        self,
        config: AppConfig | None = None,
        provider: BaseLLMProvider | None = None,
        gatekeeper: Gatekeeper | None = None,
        git_handler: GitHandler | None = None,
        remote_vcs: RemoteVCS | None = None,
        ci_watcher: CIWatcher | None = None,
        repo_path: Path | str = ".",
        state_path: Path | str = DEFAULT_STATE_FILE,
        console: Console | None = None,
        input_func: Callable[[str], str] | None = None,
        auto_merge: bool | None = None,
        skill_manager: SkillManager | None = None,
    ) -> None:
        """Initialize the Orchestrator.

        Args:
            config: Optional pre-loaded AppConfig instance.
            provider: Optional pre-instantiated BaseLLMProvider.
            gatekeeper: Optional pre-instantiated Gatekeeper security engine.
            git_handler: Optional GitHandler instance.
            remote_vcs: Optional RemoteVCS instance.
            ci_watcher: Optional CIWatcher instance.
            repo_path: Path to target repository root (default: current directory).
            state_path: File path to save/load WorkflowState JSON (default: 'state.json').
            console: Optional Rich Console for UI outputs.
            input_func: Optional input function for mock testing terminal prompts.
            auto_merge: Optional explicit override for auto_merge flag.
        """
        self.repo_path = Path(repo_path).resolve()
        self.state_path = Path(state_path).resolve()
        self.console = console or Console()
        self.input_func = input_func
        self.auto_merge_override = auto_merge

        # Load or use configuration
        self.config = config or load_config()

        # Initialize Skill Manager
        self.skill_manager = skill_manager or SkillManager(config=self.config.skills)

        # Initialize LLM Provider (Phase 1)
        self.provider = provider or get_provider(self.config)

        # Initialize Security Gatekeeper (Phase 2)
        self.gatekeeper = gatekeeper or Gatekeeper(repo_root=self.repo_path)

        # Initialize VCS and CI tooling
        self.git = git_handler or GitHandler(repo_path=self.repo_path)
        if self.git.on_commit_callback is None:
            self.git.on_commit_callback = self.save_state
        self.remote_vcs = remote_vcs or RemoteVCS(repo_path=self.repo_path)
        self.ci_watcher = ci_watcher or CIWatcher(remote_vcs=self.remote_vcs)

        self.state: WorkflowState | None = None

    def save_state(self) -> Path:
        """Persist the current workflow state to disk.

        Returns:
            Resolved Path of the written state file.
        """
        if self.state is None:
            raise OrchestratorError("Cannot save uninitialized workflow state.")
        return self.state.save_to_file(self.state_path)

    async def run(
        self,
        requirements: str,
        task_id: str | None = None,
        resume_from_state: bool = False,
    ) -> WorkflowState:
        """Execute the end-to-end autonomous multi-agent workflow (Phases 1-7).

        Args:
            requirements: Raw user requirements or task description.
            task_id: Optional unique task identifier (generated if omitted).
            resume_from_state: Whether to restore existing state from state_path if available.

        Returns:
            Final WorkflowState after completion or failure.

        Raises:
            DiscoveryCancelledError: If user cancels during interactive spec discovery.
            OrchestratorError: On critical orchestration failure.
        """
        # =====================================================================
        # Phase 1: State Initialization & Persistence
        # =====================================================================
        resume_phase: WorkflowPhase | None = None
        if resume_from_state and self.state_path.is_file():
            logger.info("Resuming workflow from existing state file: %s", self.state_path)
            self.state = WorkflowState.load_from_file(self.state_path)
            resume_phase = determine_resume_phase(self.state)
            logger.info("Determined resume phase: %s", resume_phase.value)
        else:
            tid = task_id or f"TASK-{uuid.uuid4().hex[:8].upper()}"
            self.state = WorkflowState(
                task_id=tid,
                raw_requirements=requirements.strip(),
            )

        self.console.print(
            Panel(
                f"[bold cyan]Task ID:[/bold cyan] {self.state.task_id}\n"
                f"[bold cyan]Repo Root:[/bold cyan] {self.repo_path}\n"
                f"[bold cyan]Auto-Merge:[/bold cyan] "
                f"{self.auto_merge_override if self.auto_merge_override is not None else self.config.workflow.auto_merge}"
                + (f"\n[bold yellow]Resuming Phase:[/bold yellow] {resume_phase.value}" if resume_phase else ""),
                title="[bold green]MergeFlow Orchestrator Gestartet[/bold green]",
                border_style="green",
            )
        )
        self.save_state()

        if resume_phase == WorkflowPhase.DONE:
            self.console.print("[bold green]Workflow ist bereits abgeschlossen (DONE).[/bold green]")
            return self.state

        # =====================================================================
        # Phase 3: Requirements Discovery & Spec Generation
        # =====================================================================
        if not self.state.spec_markdown and resume_phase in (None, WorkflowPhase.SPEC_DISCOVERY):
            logger.info("Starting Phase 3: Requirements Discovery & Spec Generation...")
            self.state.transition_to(WorkflowPhase.SPEC_DISCOVERY)
            self.save_state()

            try:
                spec_markdown = await _safe_call(
                    run_discovery_phase,
                    raw_requirements=self.state.raw_requirements,
                    provider=self.provider,
                    repo_path=self.repo_path,
                    console=self.console,
                    input_func=self.input_func,
                    skill_manager=self.skill_manager,
                )
                self.state.spec_markdown = spec_markdown
                self.save_state()
                logger.info("Phase 3 complete. SPEC.md created and approved.")
            except DiscoveryCancelledError:
                logger.warning("Discovery phase was cancelled by user.")
                self.state.transition_to(WorkflowPhase.FAILED)
                self.save_state()
                raise
            except Exception as exc:
                logger.error("Error in Phase 3 Discovery: %s", exc)
                self.state.transition_to(WorkflowPhase.FAILED)
                self.save_state()
                raise OrchestratorError(f"Phase 3 Discovery failed: {exc}") from exc
        else:
            logger.info("Existing SPEC.md found or resuming later phase. Skipping Phase 3 Discovery.")

        # =====================================================================
        # Phases 4 - 7: Dev Loop, CI Healing, Code Review & Consensus Loop
        # =====================================================================
        feedback_for_dev: str | None = None
        skip_to_phase: WorkflowPhase | None = resume_phase

        while True:
            # -----------------------------------------------------------------
            # Phase 4: Developer Agent Loop
            # -----------------------------------------------------------------
            if skip_to_phase not in (WorkflowPhase.CI_LOOP, WorkflowPhase.REVIEW, WorkflowPhase.CONSENSUS):
                logger.info(
                    "Starting Phase 4: Dev-Agent Loop (Branch: %s, Review Round: %d)...",
                    self.state.branch_name or "new",
                    self.state.review_retry_count,
                )
                self.state.transition_to(WorkflowPhase.CODING)
                self.save_state()

                try:
                    await _safe_call(
                        run_dev_phase,
                        spec=self.state.spec_markdown or "",
                        state=self.state,
                        gatekeeper=self.gatekeeper,
                        provider=self.provider,
                        git_handler=self.git,
                        remote_vcs=self.remote_vcs,
                        feedback=feedback_for_dev,
                        skill_manager=self.skill_manager,
                    )
                    self.save_state()
                    logger.info("Phase 4 Dev-Loop finished. Transitioned to CI_LOOP.")
                except Exception as exc:
                    logger.error("Phase 4 Dev-Loop failed: %s", exc)
                    self.state.transition_to(WorkflowPhase.FAILED)
                    self.save_state()
                    raise OrchestratorError(f"Phase 4 Dev-Loop failed: {exc}") from exc
            else:
                logger.info("Resuming: Skipping Phase 4 Dev-Loop to resume at %s.", skip_to_phase.value)

            # -----------------------------------------------------------------
            # Phase 5: Self-Healing CI Loop
            # -----------------------------------------------------------------
            if skip_to_phase not in (WorkflowPhase.REVIEW, WorkflowPhase.CONSENSUS):
                logger.info("Starting Phase 5: Self-Healing CI Loop...")
                self.state.transition_to(WorkflowPhase.CI_LOOP)
                self.save_state()

                try:
                    ci_passed = await _safe_call(
                        run_ci_healing_loop,
                        state=self.state,
                        gatekeeper=self.gatekeeper,
                        provider=self.provider,
                        git_handler=self.git,
                        remote_vcs=self.remote_vcs,
                        ci_watcher=self.ci_watcher,
                        max_retries=self.config.workflow.ci_max_retries,
                        skill_manager=self.skill_manager,
                        console=self.console,
                        input_func=self.input_func,
                    )
                    self.save_state()

                    if not ci_passed:
                        logger.error("Phase 5 CI loop failed or circuit breaker triggered.")
                        if self.state.current_phase != WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value:
                            self.state.transition_to(WorkflowPhase.FAILED)
                        self.save_state()
                        return self.state
                except Exception as exc:
                    logger.error("Phase 5 CI Healing encountered fatal error: %s", exc)
                    self.state.transition_to(WorkflowPhase.FAILED)
                    self.save_state()
                    raise OrchestratorError(f"Phase 5 CI Healing failed: {exc}") from exc
            else:
                logger.info("Resuming: Skipping Phase 5 CI Loop to resume at %s.", skip_to_phase.value)

            # -----------------------------------------------------------------
            # Phase 6: Parallel Multi-Persona Code Review
            # -----------------------------------------------------------------
            if skip_to_phase != WorkflowPhase.CONSENSUS:
                logger.info("Starting Phase 6: Parallel Multi-Persona Code Review...")
                self.state.transition_to(WorkflowPhase.REVIEW)
                self.save_state()

                try:
                    diff = await self.git.get_diff(
                        target_branch=self.config.workflow.default_branch,
                        protect_tokens=True,
                    )
                    review_summary = await _safe_call(
                        run_review_phase,
                        diff=diff,
                        spec=self.state.spec_markdown or "",
                        provider=self.provider,
                        skill_manager=self.skill_manager,
                    )
                    self.save_state()
                    logger.info(
                        "Phase 6 Review completed. All approved=%s, Blockers=%d, Warnings=%d",
                        review_summary.all_approved,
                        len(review_summary.blockers),
                        len(review_summary.warnings),
                    )
                except Exception as exc:
                    logger.error("Phase 6 Code Review failed: %s", exc)
                    self.state.transition_to(WorkflowPhase.FAILED)
                    self.save_state()
                    raise OrchestratorError(f"Phase 6 Code Review failed: {exc}") from exc
            else:
                logger.info("Resuming: Skipping Phase 6 Code Review to resume at %s.", skip_to_phase.value)
                review_summary = ReviewSummary(
                    all_approved=False,
                    has_blockers=any(f.severity == Severity.BLOCKER for f in self.state.findings),
                    is_approved=False,
                    reviews=[],
                )

            # -----------------------------------------------------------------
            # Phase 7: Consensus, Feedback Loop & Auto-Merge
            # -----------------------------------------------------------------
            skip_to_phase = None
            logger.info("Starting Phase 7: Consensus Evaluation & Auto-Merge...")
            self.state.transition_to(WorkflowPhase.CONSENSUS)
            self.save_state()

            try:
                decision = await run_consensus_phase(
                    summary=review_summary,
                    state=self.state,
                    config=self.config,
                    remote_vcs=self.remote_vcs,
                    auto_merge=self.auto_merge_override,
                    max_retries=self.config.workflow.review_max_retries,
                    console=self.console,
                    input_func=self.input_func,
                )
                self.save_state()

                if decision.status == ConsensusStatus.APPROVED:
                    logger.info("Consensus reached! Workflow completed successfully.")
                    self.state.transition_to(WorkflowPhase.DONE)
                    self.save_state()
                    break

                elif decision.status == ConsensusStatus.NEEDS_REVISION:
                    logger.info(
                        "Review findings detected. Looping back to Dev-Agent for remediation (Iteration %d/%d).",
                        self.state.review_retry_count,
                        self.config.workflow.review_max_retries,
                    )
                    feedback_for_dev = decision.feedback_markdown
                    # Continue while loop back to Phase 4
                    continue

                else:
                    # ConsensusStatus.REJECTED (Circuit breaker triggered or aborted)
                    logger.error("Consensus circuit breaker triggered or workflow aborted.")
                    if self.state.current_phase != WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value:
                        self.state.transition_to(WorkflowPhase.FAILED)
                    self.save_state()
                    break

            except Exception as exc:
                logger.error("Phase 7 Consensus encountered error: %s", exc)
                self.state.transition_to(WorkflowPhase.FAILED)
                self.save_state()
                raise OrchestratorError(f"Phase 7 Consensus failed: {exc}") from exc

        self.save_state()
        return self.state


async def run_orchestrator(
    requirements: str = "",
    config_path: Path | str | None = None,
    repo_path: Path | str = ".",
    state_path: Path | str = DEFAULT_STATE_FILE,
    auto_merge: bool | None = None,
    provider_type: str | None = None,
    console: Console | None = None,
    input_func: Callable[[str], str] | None = None,
    skill_manager: SkillManager | None = None,
    task_id: str | None = None,
    resume_from_state: bool = False,
) -> WorkflowState:
    """Convenience helper to initialize and run the Orchestrator.

    Args:
        requirements: Requirements text or prompt.
        config_path: Path to config.yaml.
        repo_path: Target repository path.
        state_path: Path to state.json.
        auto_merge: Explicit auto_merge flag override.
        provider_type: Provider identifier override ('cli', 'api', 'claude', 'antigravity').
        console: Optional Rich Console.
        input_func: Optional input function for terminal prompts.
        skill_manager: Optional SkillManager instance.
        task_id: Optional unique task identifier.
        resume_from_state: Whether to resume from persisted state.

    Returns:
        Final WorkflowState instance.
    """
    config = load_config(config_path)
    if provider_type:
        config.providers.default_provider = provider_type

    provider = get_provider(config)
    orchestrator = Orchestrator(
        config=config,
        provider=provider,
        repo_path=repo_path,
        state_path=state_path,
        console=console,
        input_func=input_func,
        auto_merge=auto_merge,
        skill_manager=skill_manager,
    )
    return await orchestrator.run(
        requirements=requirements,
        task_id=task_id,
        resume_from_state=resume_from_state,
    )
