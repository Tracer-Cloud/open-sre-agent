"""Behavior of the wizard's shared spec-driven collection loop.

``configure_from_spec`` is what every spec-backed configurator delegates to, so
the prompt-level rules live here rather than being re-asserted per vendor: what
each field is prefilled with, that a blank answer to a defaulted field is
accepted rather than re-prompted, and that a failed verification re-asks instead
of dropping the user out of onboarding.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
import surfaces.cli.wizard.configurators.spec_configurator as spec_configurator

_ENV_PATH = Path("/tmp/opensre-test/.env")

_SPEC = setup_flow.IntegrationSetupSpec(
    service="demo",
    fields=(
        setup_flow.SetupField(
            name="api_key", label="Demo API key", env_var="DEMO_API_KEY", secret=True
        ),
        setup_flow.SetupField(
            name="site", label="Demo site", env_var="DEMO_SITE", default="demo.example.com"
        ),
        setup_flow.SetupField(name="note", label="Demo note", env_var="DEMO_NOTE", required=False),
    ),
    verify=lambda _source, _config: {"status": "passed", "detail": "Demo connected."},
)


@dataclasses.dataclass
class _Run:
    """What the loop asked for, and what it answered with."""

    answers: dict[str, str] = dataclasses.field(default_factory=dict)
    stored: dict[str, Any] = dataclasses.field(default_factory=dict)
    asked: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> _Run:
    state = _Run()

    def _fake_prompt_value(
        label: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = False,
        **_kw: Any,
    ) -> str:
        state.asked.append(
            {"label": label, "default": default, "secret": secret, "allow_empty": allow_empty}
        )
        # Mirror the real _prompt_value: a blank answer falls back to the default.
        return state.answers.get(label, "") or default

    monkeypatch.setattr(spec_configurator, "_prompt_value", _fake_prompt_value)
    monkeypatch.setattr(spec_configurator, "_integration_defaults", lambda _s: ({}, state.stored))
    monkeypatch.setattr(spec_configurator, "_render_integration_result", lambda *_a: None)
    monkeypatch.setattr(setup_flow, "upsert_integration", lambda *_a: None)
    monkeypatch.setattr(setup_flow, "sync_env_secret", lambda *_a: None)
    monkeypatch.setattr(setup_flow, "sync_env_values", lambda *_a, **_kw: _ENV_PATH)
    return state


def test_blank_answer_to_a_defaulted_field_is_accepted(run: _Run) -> None:
    """The rule the ``allow_empty`` argument depends on.

    ``allow_empty=False`` is passed for required fields, defaulted or not, and is
    only consulted when there is no default to fall back on. Pressing enter on a
    defaulted field must therefore succeed rather than loop on "Required.".
    """
    run.answers = {"Demo API key": "key-1"}

    title, env_path = spec_configurator.configure_from_spec(_SPEC, title="Demo")

    assert (title, env_path) == ("Demo", str(_ENV_PATH))
    site = next(entry for entry in run.asked if entry["label"] == "Demo site")
    assert site["default"] == "demo.example.com"
    assert site["allow_empty"] is False


def test_optional_field_without_a_default_may_be_left_empty(run: _Run) -> None:
    run.answers = {"Demo API key": "key-1"}

    spec_configurator.configure_from_spec(_SPEC, title="Demo")

    note = next(entry for entry in run.asked if entry["label"] == "Demo note")
    assert note["allow_empty"] is True
    assert note["default"] == ""


def test_a_stored_value_is_prefilled_over_the_spec_default(run: _Run) -> None:
    """Re-running onboarding should be a series of enters, not a retype."""
    run.stored = {"api_key": "stored-key", "site": "stored.example.com"}
    run.answers = {}

    spec_configurator.configure_from_spec(_SPEC, title="Demo")

    prefilled = {entry["label"]: entry["default"] for entry in run.asked}
    assert prefilled["Demo API key"] == "stored-key"
    assert prefilled["Demo site"] == "stored.example.com"


def test_secret_fields_are_marked_for_masking(run: _Run) -> None:
    run.answers = {"Demo API key": "key-1"}

    spec_configurator.configure_from_spec(_SPEC, title="Demo")

    assert {entry["label"]: entry["secret"] for entry in run.asked} == {
        "Demo API key": True,
        "Demo site": False,
        "Demo note": False,
    }


def test_failed_verification_re_asks_instead_of_leaving_the_wizard(run: _Run) -> None:
    """Onboarding must survive a typo; the user gets another go at the prompts."""
    outcomes = iter([("failed", "Demo rejected the key."), ("passed", "Demo connected.")])
    spec = dataclasses.replace(
        _SPEC, verify=lambda _source, _config: dict(zip(("status", "detail"), next(outcomes)))
    )
    run.answers = {"Demo API key": "key-1"}

    title, _env_path = spec_configurator.configure_from_spec(spec, title="Demo")

    assert title == "Demo"
    # Three fields asked twice: the first round failed, the second succeeded.
    assert len(run.asked) == 6
