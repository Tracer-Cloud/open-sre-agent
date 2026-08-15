"""Configurator handler for the AWS integration.

The role mode assumes the ARN with boto3's ambient credential chain and never
collects base credentials itself. On a machine with none, letting the user
type an ARN and then failing STS is a dead end — so the mode gate checks the
chain the moment "IAM Role ARN" is picked and offers the ways forward.
"""

from __future__ import annotations

from integrations.aws.credential_chain import AMBIENT_SOURCES_HINT, has_ambient_credentials
from integrations.aws.setup import AWS_SETUP
from platform.terminal.theme import SECONDARY, WARNING
from surfaces.cli.wizard._ui import Choice, WizardBack, _choose, _console
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec

ROLE_MODE = "role"
KEYS_MODE = "keys"

_CONTINUE_ROLE = "continue-role"
_CONFIGURE_ELSEWHERE = "configure-elsewhere"

NO_AMBIENT_CREDENTIALS_NOTICE = (
    "IAM Role ARN is assumed with base AWS credentials already on this machine — "
    f"{AMBIENT_SOURCES_HINT}. None were found, so validation would fail with "
    '"Unable to locate credentials" before reaching your role.'
)


def _resolve_missing_base_credentials() -> str:
    """Steer the role mode when the ambient credential chain is empty.

    Returns the mode to continue with, or raises :class:`WizardBack` when the
    user chooses to configure base credentials first — the wizard reports the
    service as skipped and they re-run setup once the chain exists.
    """
    if has_ambient_credentials():
        return ROLE_MODE
    _console.print(f"[{WARNING}]{NO_AMBIENT_CREDENTIALS_NOTICE}[/]")
    choice = _choose(
        "How would you like to continue?",
        [
            Choice(
                value=KEYS_MODE,
                label="Use an access key + secret instead",
                hint="Simplest on a laptop. The key needs the read-only policies from the AWS docs.",
            ),
            Choice(
                value=_CONFIGURE_ELSEWHERE,
                label="I'll configure base credentials in this shell first",
                hint="Run `aws configure` (or export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) "
                "for an identity allowed sts:AssumeRole on the role, then re-run setup.",
            ),
            Choice(
                value=_CONTINUE_ROLE,
                label="Continue with the role anyway",
                hint="Only useful if OpenSRE will run on EC2/ECS/Lambda with an attached role.",
            ),
        ],
        default=KEYS_MODE,
    )
    if choice == KEYS_MODE:
        return KEYS_MODE
    if choice == _CONFIGURE_ELSEWHERE:
        _console.print(
            f"[{SECONDARY}]Configure base credentials in this shell, then run "
            "`opensre onboard` (or `/integrations` → aws) again and pick IAM Role ARN.[/]"
        )
        raise WizardBack
    return ROLE_MODE


def _gate_aws_mode(mode: str) -> str:
    """Mode gate for :func:`configure_from_spec`; only the role mode needs one."""
    if mode != ROLE_MODE:
        return mode
    return _resolve_missing_base_credentials()


def _configure_aws() -> tuple[str, str]:
    return configure_from_spec(AWS_SETUP, title="AWS", on_mode_chosen=_gate_aws_mode)


__all__ = ["NO_AMBIENT_CREDENTIALS_NOTICE", "ROLE_MODE", "KEYS_MODE"]
