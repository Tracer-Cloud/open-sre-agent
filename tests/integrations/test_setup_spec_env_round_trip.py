"""Every ``SetupField.env_var`` must be a name the catalog actually reads back.

:func:`integrations.setup_flow.apply_setup` writes credentials to ``.env`` and
the keyring; :func:`integrations._catalog_impl.load_env_integrations` is what
reads them again — and the two sides name the same value differently
(``base_url`` is written as ``HONEYCOMB_API_URL``, ``endpoint`` as
``GRAFANA_INSTANCE_URL``). A spec that declares an env var nothing reads still
passes every test in :mod:`tests.integrations.test_setup_flow`, because those
mock the writers: the value lands in ``.env`` and is silently never resolved
again. The failure surfaces only as a deploy preflight calling a fully
configured integration missing.

So this closes the loop end to end — persist through the real ``.env`` writer,
load the result into the environment, and require the catalog to hand back the
same credentials.
"""

from __future__ import annotations

import dataclasses
import functools
from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
from config.env_file import env_assignment_key, read_env_lines, sync_env_values
from integrations._catalog_impl import load_env_integrations
from integrations.coralogix.setup import CORALOGIX_SETUP
from integrations.datadog.setup import DATADOG_SETUP
from integrations.gitlab.setup import GITLAB_SETUP
from integrations.groundcover.setup import GROUNDCOVER_SETUP
from integrations.honeycomb.setup import HONEYCOMB_SETUP
from integrations.posthog.setup import POSTHOG_SETUP
from integrations.sentry.setup import SENTRY_SETUP
from integrations.telegram.setup import TELEGRAM_SETUP
from integrations.vercel.setup import VERCEL_SETUP

# A distinct, recognizable value per field, so two fields of the same
# integration swapping places fails instead of coincidentally matching. Values
# are deliberately non-default (EU hosts, a named dataset) — a default would
# still "round-trip" through a spec that wrote nothing at all.
_SUBMITTED: dict[str, dict[str, str]] = {
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
    "telegram": {"bot_token": "123456:tg-bot-token", "default_chat_id": "-1001234567890"},
}

_SPECS = [
    CORALOGIX_SETUP,
    DATADOG_SETUP,
    GITLAB_SETUP,
    GROUNDCOVER_SETUP,
    HONEYCOMB_SETUP,
    POSTHOG_SETUP,
    SENTRY_SETUP,
    TELEGRAM_SETUP,
    VERCEL_SETUP,
]


@dataclasses.dataclass
class _Persisted:
    """Where a run's credentials ended up."""

    env_path: Path
    secrets: dict[str, str]


@pytest.fixture
def persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Persisted:
    """Point the flow's writers at a throwaway ``.env`` and an in-memory keyring.

    The real :func:`config.env_file.sync_env_values` is kept and only its target
    moves, so its refusal to write a sensitive key to disk still applies. Only
    the keyring backend is replaced — a test must not touch the real one.
    """
    written = _Persisted(env_path=tmp_path / ".env", secrets={})
    monkeypatch.setattr(
        setup_flow,
        "sync_env_values",
        functools.partial(sync_env_values, env_path=written.env_path),
    )
    monkeypatch.setattr(setup_flow, "sync_env_secret", written.secrets.__setitem__)
    monkeypatch.setattr(setup_flow, "upsert_integration", lambda _service, _payload: None)
    return written


def _restore_environment(written: _Persisted, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduce the environment a later process would start with.

    Keyring secrets are seeded straight into ``os.environ`` because
    ``resolve_env_credential`` checks the environment first — which is what a
    deploy, and this assertion, ultimately depend on.
    """
    for key, value in written.secrets.items():
        monkeypatch.setenv(key, value)
    for line in read_env_lines(written.env_path):
        key = env_assignment_key(line)
        if key:
            monkeypatch.setenv(key, line.split("=", 1)[1].strip())


def _catalog_credentials(service: str) -> dict[str, Any]:
    for record in load_env_integrations():
        if record.get("service") == service:
            credentials = record.get("credentials")
            assert isinstance(credentials, dict)
            return credentials
    raise AssertionError(f"{service} was not discovered from the environment")


@pytest.mark.parametrize("spec", _SPECS, ids=lambda spec: spec.service)
def test_persisted_credentials_are_read_back_by_the_catalog(
    spec: setup_flow.IntegrationSetupSpec, persisted: _Persisted, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = _SUBMITTED[spec.service]
    assert {field.name for field in spec.fields} == set(submitted), (
        f"{spec.service} spec fields changed; update this test's submitted values"
    )

    # Verification and reference resolution are each integration's own concern
    # and covered per vendor. Dropping them here keeps the test on one question:
    # do the values come back out under the names the spec wrote them?
    outcome = setup_flow.apply_setup(
        dataclasses.replace(spec, verify=None, resolve=None), submitted
    )
    assert outcome.ok, outcome.detail

    _restore_environment(persisted, monkeypatch)

    resolved = _catalog_credentials(spec.service)
    for field in spec.fields:
        assert resolved.get(field.name) == submitted[field.name], (
            f"{spec.service}.{field.name} was persisted as {field.env_var!r}, "
            "which the catalog does not read back into that credential"
        )
