"""Behavior of the spec-driven ``opensre integrations setup <service>`` handlers.

One parametrized suite rather than a file per vendor: the handlers are now a
two-line delegation to :func:`integrations.cli._run_spec_setup`, so what is
worth pinning is the same for each — the prompt order and which answers are
masked, that nothing is written until verification passes, and that the
credentials reach the keyring and ``.env`` rather than the store alone.

That last one is the migration's point. These handlers previously called
``upsert_integration`` and stopped, which reads fine at runtime (the store is
resolved first) but leaves the deploy preflight — which reads env vars —
declaring a working integration missing.

Vendor-specific behavior stays with the vendor:
:mod:`tests.integrations.telegram.test_cli_setup_characterization` covers
Telegram's chat-id resolution, and
:mod:`tests.integrations.test_setup_spec_env_round_trip` covers the env var
names themselves.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

import integrations.cli as cli
import integrations.coralogix.setup as coralogix_setup
import integrations.datadog.setup as datadog_setup
import integrations.honeycomb.setup as honeycomb_setup
import integrations.setup_flow as setup_flow

_ANSWERS: dict[str, dict[str, str]] = {
    "datadog": {"api_key": "dd-api-key", "app_key": "dd-app-key", "site": "datadoghq.eu"},
    "honeycomb": {
        "api_key": "hc-api-key",
        "dataset": "checkout-prod",
        "base_url": "https://api.eu1.honeycomb.io",
    },
    "coralogix": {
        "api_key": "cx-api-key",
        "base_url": "https://api.eu2.coralogix.com",
        "application_name": "checkout",
        "subsystem_name": "api",
    },
}

# (spec module, spec attribute, CLI handler) — the attribute is patched rather
# than the spec object because ``_setup_*`` imports it inside the function body.
_CASES = [
    pytest.param(datadog_setup, "DATADOG_SETUP", cli._setup_datadog, id="datadog"),
    pytest.param(honeycomb_setup, "HONEYCOMB_SETUP", cli._setup_honeycomb, id="honeycomb"),
    pytest.param(coralogix_setup, "CORALOGIX_SETUP", cli._setup_coralogix, id="coralogix"),
]


@dataclasses.dataclass
class _Run:
    """Scripted verifier outcome for one run, plus everything the run did."""

    verify_status: str = "passed"
    verify_detail: str = "Connected."

    asked: list[tuple[str, str, bool]] = dataclasses.field(default_factory=list)
    verified: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    store: list[tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=list)
    keyring: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    env: list[dict[str, str]] = dataclasses.field(default_factory=list)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> _Run:
    state = _Run()
    monkeypatch.setattr(
        setup_flow,
        "upsert_integration",
        lambda service, payload: state.store.append((service, payload)),
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: state.keyring.append((key, value))
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_values", lambda values, **_kw: state.env.append(dict(values))
    )
    return state


def _install(
    monkeypatch: pytest.MonkeyPatch, module: Any, attr: str, state: _Run, blank: str = ""
) -> setup_flow.IntegrationSetupSpec:
    """Swap in a stub verifier and script ``_p`` with this vendor's answers.

    Pass *blank* to answer one field with an empty string instead.
    """

    def _fake_verify(_source: str, config: dict[str, Any]) -> dict[str, str]:
        state.verified.append(dict(config))
        return {"status": state.verify_status, "detail": state.verify_detail}

    spec = dataclasses.replace(getattr(module, attr), verify=_fake_verify)
    monkeypatch.setattr(module, attr, spec)

    answers = _ANSWERS[spec.service]
    queue = ["" if field.name == blank else answers[field.name] for field in spec.fields]

    def _fake_p(label: str, default: str = "", secret: bool = False) -> str:
        state.asked.append((label, default, secret))
        return queue.pop(0)

    monkeypatch.setattr(cli, "_p", _fake_p)
    return spec


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_prompts_follow_the_spec_and_mask_only_secret_fields(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    spec = _install(monkeypatch, module, attr, run)

    handler()

    assert [label for label, _default, _secret in run.asked] == [
        field.question for field in spec.fields
    ]
    assert [secret for _label, _default, secret in run.asked] == [
        field.secret for field in spec.fields
    ]


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_defaults_are_offered_as_prompt_prefills(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    """A user pressing enter should land on the documented default, not blank."""
    spec = _install(monkeypatch, module, attr, run)

    handler()

    assert [default for _label, default, _secret in run.asked] == [
        field.default for field in spec.fields
    ]


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_credentials_reach_the_keyring_and_env_not_just_the_store(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    spec = _install(monkeypatch, module, attr, run)
    answers = _ANSWERS[spec.service]

    handler()

    assert run.store == [(spec.service, {"credentials": dict(answers)})]
    secret_fields = {f.env_var: answers[f.name] for f in spec.fields if f.secret}
    assert dict(run.keyring) == secret_fields
    plain_fields = {f.env_var: answers[f.name] for f in spec.fields if f.env_var and not f.secret}
    assert run.env == [plain_fields]


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_failed_verification_exits_without_saving(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    """A bad credential must not overwrite a working integration."""
    run.verify_status = "failed"
    run.verify_detail = "Rejected."
    _install(monkeypatch, module, attr, run)

    with pytest.raises(SystemExit):
        handler()

    assert (run.store, run.keyring, run.env) == ([], [], [])


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_blank_required_field_exits_before_the_next_prompt(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    """Fail on the field that is blank, not after working through the rest."""
    spec = getattr(module, attr)
    first_required = next(f for f in spec.fields if f.required and not f.default)
    _install(monkeypatch, module, attr, run, blank=first_required.name)

    with pytest.raises(SystemExit):
        handler()

    assert len(run.asked) == 1 + [f.name for f in spec.fields].index(first_required.name)
    assert (run.verified, run.store) == ([], [])
