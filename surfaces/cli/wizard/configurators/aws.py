"""Configurator handler for the AWS integration.

The role mode's prerequisite check lives in :mod:`integrations.aws.role_mode_gate`
so the CLI and this wizard steer identically; this module only supplies the
wizard's way of asking.
"""

from __future__ import annotations

from infrastructure.terminal.theme import SECONDARY, WARNING
from integrations.aws.role_mode_gate import (
    CONFIGURE_FIRST_INSTRUCTION,
    GATE_OPTIONS,
    GATE_QUESTION,
    NO_AMBIENT_CREDENTIALS_NOTICE,
    ConfigureCredentialsFirst,
    RoleGateChoice,
    gate_role_mode,
)
from integrations.aws.setup import AWS_SETUP
from surfaces.cli.wizard._ui import Choice, WizardBack, _choose, _console
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _ask_gate() -> RoleGateChoice | None:
    _console.print(f"[{WARNING}]{NO_AMBIENT_CREDENTIALS_NOTICE}[/]")
    picked = _choose(
        GATE_QUESTION,
        [Choice(value=str(o.value), label=o.label, hint=o.hint) for o in GATE_OPTIONS],
        default=str(RoleGateChoice.USE_KEYS),
    )
    return RoleGateChoice(picked)


def _gate_aws_mode(mode: str) -> str:
    try:
        return gate_role_mode(mode, ask=_ask_gate)
    except ConfigureCredentialsFirst:
        _console.print(f"[{SECONDARY}]{CONFIGURE_FIRST_INSTRUCTION}[/]")
        # The wizard reports the service as skipped and moves on.
        raise WizardBack from None


def _configure_aws() -> tuple[str, str]:
    return configure_from_spec(AWS_SETUP, title="AWS", on_mode_chosen=_gate_aws_mode)
