"""Typed fake planner/registry harness for routing execution tests."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions import (
    ActionExecutionDeps,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)


@dataclass
class FakePlanner:
    result: Any
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def __call__(self, message: str, *, session: Any | None = None) -> Any:
        self.calls.append((message, session))
        return self.result


@dataclass
class FakeDispatcher:
    should_succeed: bool = True
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, *, tool_name: str, args: dict[str, Any], ctx: Any) -> bool:
        _ = ctx
        self.calls.append((tool_name, dict(args)))
        return self.should_succeed


@dataclass
class RoutingHarness:
    planner: FakePlanner
    dispatcher: FakeDispatcher
    console_buffer: io.StringIO = field(default_factory=io.StringIO)

    @property
    def console(self) -> Console:
        return Console(file=self.console_buffer, force_terminal=False, highlight=False, width=100)

    @property
    def deps(self) -> ActionExecutionDeps:
        return ActionExecutionDeps(planner=self.planner, dispatch=self.dispatcher)


def planned_action(kind: str, content: str) -> PlannedAction:
    return PlannedAction(kind=kind, content=content, position=0, source="llm")
