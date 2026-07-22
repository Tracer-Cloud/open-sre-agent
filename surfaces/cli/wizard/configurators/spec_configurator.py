"""The onboarding wizard's configurator for any spec-driven integration.

This is the *collection* half of setup, and the counterpart to
:mod:`integrations.setup_flow`: that module takes values someone already
gathered and decides where they are persisted, while this one is what asks the
user for them.

Every configurator built on an :class:`~integrations.setup_flow.IntegrationSetupSpec`
does the same three things — prompt for each field (prefilled from whatever is
already stored, so re-running onboarding is not a retype), hand the answers to
:func:`~integrations.setup_flow.apply_setup`, and re-ask on failure instead of
dropping the user out of the wizard. Only the heading and the introductory
guidance differ, so those are the arguments.
"""

from __future__ import annotations

from integrations.setup_flow import IntegrationSetupSpec, apply_setup
from platform.terminal.theme import SECONDARY
from surfaces.cli.wizard._ui import (
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.integration_validators.shared import IntegrationHealthResult


def configure_from_spec(
    spec: IntegrationSetupSpec, *, title: str, intro: str = ""
) -> tuple[str, str]:
    """Prompt for *spec*'s fields until they verify, then persist them.

    Returns the pair the wizard's configurator table expects: the display name
    and the ``.env`` path that was written.
    """
    _, credentials = _integration_defaults(spec.service)
    if intro:
        _console.print(intro)
    while True:
        values = {
            field.name: _prompt_value(
                field.question,
                # A stored value wins over the spec's default, so re-running
                # onboarding is a series of enters rather than a retype.
                default=_string_value(credentials.get(field.name), field.default),
                secret=field.secret,
                # Only reached when the field has no default to fall back on:
                # _prompt_value substitutes the default before it consults this,
                # so a defaulted field never re-prompts and never returns blank.
                allow_empty=not field.required,
            )
            for field in spec.fields
        }
        with _console.status(f"Validating {title} credentials...", spinner="dots"):
            outcome = apply_setup(spec, values)
        _render_integration_result(
            title, IntegrationHealthResult(ok=outcome.ok, detail=outcome.detail)
        )
        if outcome.ok:
            # apply_setup always resolves an .env path on success; narrow for mypy
            # and fail loudly rather than returning the string "None" if it ever
            # stops doing so.
            assert outcome.env_path is not None, "apply_setup returned ok=True without an env_path"
            return title, str(outcome.env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
