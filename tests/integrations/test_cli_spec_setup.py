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
import integrations.dagster.setup as dagster_setup
import integrations.datadog.setup as datadog_setup
import integrations.gitlab.setup as gitlab_setup
import integrations.groundcover.setup as groundcover_setup
import integrations.helm.setup as helm_setup
import integrations.honeycomb.setup as honeycomb_setup
import integrations.incident_io.setup as incident_io_setup
import integrations.jenkins.setup as jenkins_setup
import integrations.mongodb_atlas.setup as mongodb_atlas_setup
import integrations.pagerduty.setup as pagerduty_setup
import integrations.posthog.setup as posthog_setup
import integrations.sentry.setup as sentry_setup
import integrations.setup_flow as setup_flow
import integrations.signoz.setup as signoz_setup
import integrations.smtp.setup as smtp_setup
import integrations.tempo.setup as tempo_setup
import integrations.temporal.setup as temporal_setup
import integrations.tracer.setup as tracer_setup
import integrations.vercel.setup as vercel_setup
import integrations.whatsapp.setup as whatsapp_setup

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
    "groundcover": {
        "api_key": "gc-api-key",
        "mcp_url": "https://mcp.eu.groundcover.com/api/mcp",
        "tenant_uuid": "11111111-2222-3333-4444-555555555555",
        "backend_id": "gc-backend-7",
        "timezone": "Europe/Berlin",
    },
    "gitlab": {
        "base_url": "https://gitlab.example.com/api/v4",
        "auth_token": "glpat-gitlab-token",
    },
    "sentry": {
        "base_url": "https://sentry.example.com",
        "organization_slug": "checkout-org",
        "auth_token": "sntrys-sentry-token",
        "project_slug": "checkout-api",
    },
    "posthog": {
        "base_url": "https://eu.i.posthog.com",
        "project_id": "40182",
        "personal_api_key": "phx-posthog-key",
    },
    "vercel": {"api_token": "vercel-api-token", "team_id": "team_abc123"},
    "incident_io": {"api_key": "iio-api-key", "base_url": "https://api.eu.incident.io"},
    "tracer": {"base_url": "https://tracer.example.com", "jwt_token": "tracer-jwt-token"},
    "mongodb_atlas": {
        "api_public_key": "atlas-public-key",
        "api_private_key": "atlas-private-key",
        "project_id": "60f1a2b3c4d5e6f7a8b9c0d1",
        "base_url": "https://cloud-eu.mongodb.com/api/atlas/v2",
    },
    "signoz": {"url": "https://signoz.example.com", "api_key": "signoz-api-key"},
    "jenkins": {
        "base_url": "https://jenkins.example.com",
        "username": "ci-bot",
        "api_token": "jenkins-api-token",
    },
    "pagerduty": {"api_key": "pd-api-key", "base_url": "https://api.eu.pagerduty.com"},
    "dagster": {"endpoint": "https://checkout.dagster.cloud/prod", "api_token": "dagster-token"},
    "temporal": {
        "base_url": "https://temporal.example.com",
        "namespace": "checkout-prod",
        "api_key": "temporal-api-key",
    },
    "helm": {
        "helm_path": "/opt/homebrew/bin/helm",
        "kube_context": "checkout-prod",
        "kubeconfig": "/home/ci/.kube/config",
        "default_namespace": "checkout",
    },
    "smtp": {
        "host": "smtp.eu.example.com",
        "from_address": "reports@example.com",
        "port": "2525",
        "security": "ssl",
        "username": "reports@example.com",
        "password": "smtp-secret",
        "default_to": "oncall@example.com",
    },
    "whatsapp": {
        "account_sid": "AC-checkout-sid",
        "auth_token": "twilio-auth-token",
        "from_number": "whatsapp:+14155238886",
        "default_to": "+15551234567",
    },
    "tempo": {
        "url": "https://tempo.eu.example.com",
        "api_key": "tempo-bearer-token",
        "username": "tempo-user",
        "password": "tempo-password",
        "org_id": "checkout-tenant",
    },
}

# (spec module, spec attribute, CLI handler) — the attribute is patched rather
# than the spec object because ``_setup_*`` imports it inside the function body.
_CASES = [
    pytest.param(datadog_setup, "DATADOG_SETUP", cli._setup_datadog, id="datadog"),
    pytest.param(honeycomb_setup, "HONEYCOMB_SETUP", cli._setup_honeycomb, id="honeycomb"),
    pytest.param(coralogix_setup, "CORALOGIX_SETUP", cli._setup_coralogix, id="coralogix"),
    pytest.param(groundcover_setup, "GROUNDCOVER_SETUP", cli._setup_groundcover, id="groundcover"),
    pytest.param(gitlab_setup, "GITLAB_SETUP", cli._setup_gitlab, id="gitlab"),
    pytest.param(sentry_setup, "SENTRY_SETUP", cli._setup_sentry, id="sentry"),
    pytest.param(posthog_setup, "POSTHOG_SETUP", cli._setup_posthog, id="posthog"),
    pytest.param(vercel_setup, "VERCEL_SETUP", cli._setup_vercel, id="vercel"),
    pytest.param(incident_io_setup, "INCIDENT_IO_SETUP", cli._setup_incident_io, id="incident_io"),
    pytest.param(tracer_setup, "TRACER_SETUP", cli._setup_tracer, id="tracer"),
    pytest.param(
        mongodb_atlas_setup, "MONGODB_ATLAS_SETUP", cli._setup_mongodb_atlas, id="mongodb_atlas"
    ),
    pytest.param(signoz_setup, "SIGNOZ_SETUP", cli._setup_signoz, id="signoz"),
    pytest.param(jenkins_setup, "JENKINS_SETUP", cli._setup_jenkins, id="jenkins"),
    pytest.param(pagerduty_setup, "PAGERDUTY_SETUP", cli._setup_pagerduty, id="pagerduty"),
    pytest.param(dagster_setup, "DAGSTER_SETUP", cli._setup_dagster, id="dagster"),
    pytest.param(temporal_setup, "TEMPORAL_SETUP", cli._setup_temporal, id="temporal"),
    pytest.param(helm_setup, "HELM_SETUP", cli._setup_helm, id="helm"),
    pytest.param(smtp_setup, "SMTP_SETUP", cli._setup_smtp, id="smtp"),
    pytest.param(whatsapp_setup, "WHATSAPP_SETUP", cli._setup_whatsapp, id="whatsapp"),
    pytest.param(tempo_setup, "TEMPO_SETUP", cli._setup_tempo, id="tempo"),
]

# HELM_SETUP has no field that is both required and defaultless (``helm_path``
# defaults to "helm"; everything else is optional). DAGSTER_SETUP's ``endpoint``
# now carries the same default the wizard configurator always offered
# ("http://localhost:3000"), and ``api_token`` is optional. Neither spec can
# produce the "blank required field with no default" scenario the last test
# below exercises — excluded there only.
_BLANK_REQUIRED_CASES = [case for case in _CASES if case.id not in {"helm", "dagster"}]


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


@pytest.mark.parametrize(("module", "attr", "handler"), _BLANK_REQUIRED_CASES)
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


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_handler_is_registered_for_the_service(module: Any, attr: str, handler: Any) -> None:
    """The dispatch entry is what makes `integrations setup <service>` reachable."""
    spec = getattr(module, attr)
    assert cli._HANDLERS[spec.service] is handler
