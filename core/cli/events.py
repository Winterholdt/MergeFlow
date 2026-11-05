"""Decoupled event system and agent registry for MergeFlow CLI and Orchestrator.

Provides event structures and asynchronous event emitter for:
- routing: Orchestrator analyzes input / selects workers
- agent_start / agent_end: A specific agent starts or finishes work
- tool_start / tool_end: An agent executes a specific tool or skill
- token: Streaming tokens for LLM responses
- done / error: Completion or failure

Also provides an AgentRegistry defining all system agents, their roles,
phases, and capabilities for visualization via `/agents`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import inspect
import logging
import time
from typing import Any, Callable, Coroutine, Union

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Categorized lifecycle events emitted during orchestration."""

    ROUTING = "routing"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOKEN = "token"
    STATUS = "status"
    USER_PROMPT_START = "user_prompt_start"
    USER_PROMPT_END = "user_prompt_end"
    DONE = "done"
    ERROR = "error"


@dataclass
class CLIEvent:
    """Slim, flexible event data structure for CLI feedback and streaming."""

    event_type: EventType | str
    message: str | None = None
    agent_name: str | None = None
    role: str | None = None
    tool_name: str | None = None
    content: str | None = None
    token: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def type(self) -> str:
        """String representation of event type."""
        if isinstance(self.event_type, EventType):
            return self.event_type.value
        return str(self.event_type)

    @property
    def agent(self) -> str | None:
        """Alias for agent_name."""
        return self.agent_name

    @property
    def tool(self) -> str | None:
        """Alias for tool_name."""
        return self.tool_name


EventCallback = Callable[[CLIEvent], Union[None, Coroutine[Any, Any, None]]]


class CLIEventEmitter:
    """Thread-safe and async-compatible event emitter for CLI events."""

    def __init__(self) -> None:
        self._handlers: list[EventCallback] = []

    def subscribe(self, callback: EventCallback) -> None:
        """Register an event listener callback."""
        if callback not in self._handlers:
            self._handlers.append(callback)

    def unsubscribe(self, callback: EventCallback) -> None:
        """Remove a registered event listener callback."""
        if callback in self._handlers:
            self._handlers.remove(callback)

    def clear(self) -> None:
        """Remove all registered listeners."""
        self._handlers.clear()

    def emit(self, event: CLIEvent) -> None:
        """Emit an event synchronously to all registered listeners.

        If a listener is asynchronous (coroutine function), it is scheduled
        on the running event loop if available, or executed safely.
        """
        for handler in list(self._handlers):
            try:
                res = handler(event)
                if inspect.isawaitable(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        # No running loop, run in a temporary loop
                        asyncio.run(res)
            except Exception as exc:
                logger.error("Error in event listener %s: %s", handler, exc)

    async def emit_async(self, event: CLIEvent) -> None:
        """Emit an event asynchronously, awaiting any coroutine listeners."""
        tasks = []
        for handler in list(self._handlers):
            try:
                res = handler(event)
                if inspect.isawaitable(res):
                    tasks.append(res)
            except Exception as exc:
                logger.error("Error in event listener %s: %s", handler, exc)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -------------------------------------------------------------------------
    # Convenience helper methods
    # -------------------------------------------------------------------------

    def emit_routing(
        self,
        message: str = "Analyzing input and selecting worker agents...",
        target_agent: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger a routing event."""
        event = CLIEvent(
            event_type=EventType.ROUTING,
            message=message,
            agent_name=target_agent,
            data=data,
        )
        self.emit(event)
        return event

    def emit_agent_start(
        self,
        agent_name: str,
        role: str | None = None,
        message: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger an agent start event."""
        event = CLIEvent(
            event_type=EventType.AGENT_START,
            agent_name=agent_name,
            role=role,
            message=message or f"Agent '{agent_name}' started work.",
            data=data,
        )
        self.emit(event)
        return event

    def emit_agent_end(
        self,
        agent_name: str,
        message: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger an agent end event."""
        event = CLIEvent(
            event_type=EventType.AGENT_END,
            agent_name=agent_name,
            message=message or f"Agent '{agent_name}' finished work.",
            data=data,
        )
        self.emit(event)
        return event

    def emit_tool_start(
        self,
        tool_name: str,
        agent_name: str | None = None,
        message: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger a tool execution start event."""
        event = CLIEvent(
            event_type=EventType.TOOL_START,
            tool_name=tool_name,
            agent_name=agent_name,
            message=message or f"Executing tool: {tool_name}",
            data=data,
        )
        self.emit(event)
        return event

    def emit_tool_end(
        self,
        tool_name: str,
        agent_name: str | None = None,
        message: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger a tool execution end event."""
        event = CLIEvent(
            event_type=EventType.TOOL_END,
            tool_name=tool_name,
            agent_name=agent_name,
            message=message or f"Finished tool: {tool_name}",
            data=data,
        )
        self.emit(event)
        return event

    def emit_token(
        self,
        token: str,
        agent_name: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger a streaming token chunk event."""
        event = CLIEvent(
            event_type=EventType.TOKEN,
            token=token,
            agent_name=agent_name,
            data=data,
        )
        self.emit(event)
        return event

    def emit_status(
        self,
        message: str,
        agent_name: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger a general status update event."""
        event = CLIEvent(
            event_type=EventType.STATUS,
            message=message,
            agent_name=agent_name,
            data=data,
        )
        self.emit(event)
        return event

    def emit_prompt_start(
        self,
        message: str = "Waiting for user input...",
        prompt_label: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger interactive user prompt start event to pause spinners."""
        event = CLIEvent(
            event_type=EventType.USER_PROMPT_START,
            message=message,
            content=prompt_label,
            data=data,
        )
        self.emit(event)
        return event

    def emit_prompt_end(
        self,
        message: str = "User input received.",
        **data: Any,
    ) -> CLIEvent:
        """Trigger interactive user prompt completion event."""
        event = CLIEvent(
            event_type=EventType.USER_PROMPT_END,
            message=message,
            data=data,
        )
        self.emit(event)
        return event

    def emit_done(
        self,
        message: str = "Orchestration completed successfully.",
        content: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger workflow completion event."""
        event = CLIEvent(
            event_type=EventType.DONE,
            message=message,
            content=content,
            data=data,
        )
        self.emit(event)
        return event

    def emit_error(
        self,
        error: Exception | str,
        message: str | None = None,
        agent_name: str | None = None,
        **data: Any,
    ) -> CLIEvent:
        """Trigger workflow failure/error event."""
        err_msg = str(error)
        event = CLIEvent(
            event_type=EventType.ERROR,
            message=message or f"Error: {err_msg}",
            content=err_msg,
            agent_name=agent_name,
            data={"error": err_msg, **data},
        )
        self.emit(event)
        return event


# =============================================================================
# Agent Registry & Metadata
# =============================================================================


@dataclass
class AgentInfo:
    """Metadata describing a registered agent in the orchestrator."""

    name: str
    role: str
    phase: str
    description: str
    skills: list[str] = field(default_factory=list)
    icon: str = "🤖"


class AgentRegistry:
    """Registry maintaining active agents and personas for MergeFlow."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentInfo] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        defaults = [
            AgentInfo(
                name="DiscoveryAgent",
                role="Requirements Architect",
                phase="Phase 1: Spec",
                description="Scans repo context, identifies edge cases and blind spots, drafts and verifies SPEC.md.",
                skills=["spec-driven-development", "interview-me", "source-driven-development"],
                icon="📐",
            ),
            AgentInfo(
                name="DevAgent",
                role="Autonomous Developer",
                phase="Phase 2: Dev",
                description="Iteratively writes code, runs local unit tests, follows Conventional Commits.",
                skills=["test-driven-development", "incremental-implementation", "code-simplification"],
                icon="💻",
            ),
            AgentInfo(
                name="CIHealingAgent",
                role="CI / Reliability Engineer",
                phase="Phase 3: CI Loop",
                description="Monitors CI checks, analyzes failure stack traces, applies automated self-healing patches.",
                skills=["ci-cd-and-automation", "debugging-and-error-recovery"],
                icon="🩺",
            ),
            AgentInfo(
                name="ReviewerFunctional",
                role="Functional Reviewer",
                phase="Phase 4: Review",
                description="Audits PR changes for business logic correctness, spec adherence, and corner cases.",
                skills=["doubt-driven-development"],
                icon="🔍",
            ),
            AgentInfo(
                name="ReviewerArchitecture",
                role="Software Architect",
                phase="Phase 4: Review",
                description="Enforces clean architecture, DRY/SOLID principles, interfaces, and maintainability.",
                skills=["api-and-interface-design"],
                icon="🏛️",
            ),
            AgentInfo(
                name="ReviewerSecurity",
                role="Security Specialist",
                phase="Phase 4: Review",
                description="Scans for OWASP Top 10 vulnerabilities, unauthorized command execution, and leaks.",
                skills=["security-and-hardening"],
                icon="🛡️",
            ),
            AgentInfo(
                name="ReviewerQATesting",
                role="QA & Test Engineer",
                phase="Phase 4: Review",
                description="Verifies test quality, branch coverage, mocking integrity, and regression protection.",
                skills=["test-driven-development"],
                icon="🧪",
            ),
            AgentInfo(
                name="ConsensusAgent",
                role="Release Manager",
                phase="Phase 5: Consensus",
                description="Aggregates multi-persona reviews, evaluates blockers, triggers auto-merge or dev remediation.",
                skills=["code-review-and-quality"],
                icon="⚖️",
            ),
            AgentInfo(
                name="GatekeeperGuard",
                role="AST & Policy Enforcer",
                phase="Continuous",
                description="Performs AST syntax validation, bans hazardous shell commands, and prevents destructive actions.",
                skills=["constraint-driven-development"],
                icon="🚨",
            ),
        ]
        for a in defaults:
            self._agents[a.name.lower()] = a

    def register(self, agent: AgentInfo) -> None:
        """Register or override an agent definition."""
        self._agents[agent.name.lower()] = agent

    def get_agent(self, name: str) -> AgentInfo | None:
        """Lookup an agent by name (case-insensitive)."""
        return self._agents.get(name.lower())

    def get_all(self) -> list[AgentInfo]:
        """Return all registered agents."""
        return list(self._agents.values())


# Global default registry instance
DEFAULT_AGENT_REGISTRY = AgentRegistry()


def get_registered_agents() -> list[AgentInfo]:
    """Return list of all registered agents in the system."""
    return DEFAULT_AGENT_REGISTRY.get_all()


def register_agent(agent: AgentInfo) -> None:
    """Register a new agent in the global registry."""
    DEFAULT_AGENT_REGISTRY.register(agent)
