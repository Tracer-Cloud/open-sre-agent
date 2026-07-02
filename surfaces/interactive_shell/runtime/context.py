"""Validated runtime context for interactive shell sessions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Self

import click
from prompt_toolkit import PromptSession
from pydantic import BaseModel, ConfigDict, Field, InstanceOf, field_validator, model_validator

from core.agent_harness.session import SessionManager
from core.agent_harness.session.state import Session
from core.domain.alerts import inbox as _alert_inbox
from surfaces.interactive_shell.runtime.core.state import (
    ReplState,
    SpinnerState,
    create_repl_mutable_state,
)


class SessionBootstrapSpec(BaseModel):
    """Pydantic-enforced inputs for preparing a REPL session."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    session: InstanceOf[Session] = Field(default_factory=Session)
    pt_session: PromptSession[str] | None = None
    active_theme_name: str | None = None
    hydrate_integrations: bool = True
    persistent_tasks: bool = True

    @field_validator("active_theme_name")
    @classmethod
    def _active_theme_name_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("active_theme_name must not be blank")
        return value

    @model_validator(mode="after")
    def apply_to_session(self) -> Self:
        """Apply the canonical startup mutations to the validated session.

        Core bootstrap (persistent task registry + integration hydration) is
        delegated to :class:`SessionManager`; the shell layers its own UI
        concerns (theme, grounding providers, prompt history) on top.
        """
        SessionManager().bootstrap(
            self.session,
            hydrate_integrations=self.hydrate_integrations,
            persistent_tasks=self.persistent_tasks,
        )
        self.session.active_theme_name = self.active_theme_name or _current_theme_name()
        _bind_shell_grounding(self.session)
        if self.pt_session is not None:
            self.session.prompt_history_backend = self.pt_session.history
        return self


class ReplRuntimeContext(BaseModel):
    """Validated bundle shared by REPL entrypoints and the controller."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
    )

    session: InstanceOf[Session]
    state: InstanceOf[ReplState]
    spinner: InstanceOf[SpinnerState]
    pt_session: PromptSession[str] | None = None
    inbox: _alert_inbox.AlertInbox | None = None

    @model_validator(mode="before")
    @classmethod
    def apply_initial_mutable_state(cls, data: object) -> object:
        """Set the paired mutable state defaults through one canonical factory."""
        if not isinstance(data, dict):
            return data
        if "state" in data and "spinner" in data:
            return data
        mutable_state = create_repl_mutable_state(
            state=data.get("state"),
            spinner=data.get("spinner"),
        )
        return {
            **data,
            "state": mutable_state.state,
            "spinner": mutable_state.spinner,
        }

    @model_validator(mode="after")
    def bind_prompt_history_backend(self) -> Self:
        """Keep session prompt-history state aligned with the prompt session."""
        if self.pt_session is not None:
            self.session.prompt_history_backend = self.pt_session.history
        return self


def _current_theme_name() -> str:
    from platform.terminal.theme import get_active_theme_name

    return get_active_theme_name()


def _bind_shell_grounding(session: Session) -> None:
    def _slash_commands() -> Mapping[str, object]:
        from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

        return SLASH_COMMANDS

    def _cli_command_group() -> click.Command | None:
        from surfaces.cli.__main__ import cli

        return cli

    session.grounding.set_slash_commands_provider(_slash_commands)
    session.grounding.set_command_group_provider(_cli_command_group)


def prepare_repl_session(
    session: Session | None = None,
    *,
    pt_session: PromptSession[str] | None = None,
    active_theme_name: str | None = None,
    hydrate_integrations: bool = True,
    persistent_tasks: bool = True,
) -> Session:
    """Return a session with the same defaults used by REPL boot."""
    spec = SessionBootstrapSpec(
        session=session or Session(),
        pt_session=pt_session,
        active_theme_name=active_theme_name,
        hydrate_integrations=hydrate_integrations,
        persistent_tasks=persistent_tasks,
    )
    return spec.session


def create_repl_runtime_context(
    session: Session | None = None,
    *,
    state: ReplState | None = None,
    spinner: SpinnerState | None = None,
    pt_session: PromptSession[str] | None = None,
    inbox: _alert_inbox.AlertInbox | None = None,
    active_theme_name: str | None = None,
    hydrate_integrations: bool = True,
    persistent_tasks: bool = True,
) -> ReplRuntimeContext:
    """Create the canonical validated context for a REPL controller."""
    prepared_session = prepare_repl_session(
        session,
        pt_session=pt_session,
        active_theme_name=active_theme_name,
        hydrate_integrations=hydrate_integrations,
        persistent_tasks=persistent_tasks,
    )
    mutable_state = create_repl_mutable_state(state=state, spinner=spinner)
    return ReplRuntimeContext(
        session=prepared_session,
        state=mutable_state.state,
        spinner=mutable_state.spinner,
        pt_session=pt_session,
        inbox=inbox,
    )


__all__ = [
    "ReplRuntimeContext",
    "SessionBootstrapSpec",
    "create_repl_runtime_context",
    "prepare_repl_session",
]
