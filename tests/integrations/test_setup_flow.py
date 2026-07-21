"""Behavior of the shared integration setup flow.

The contract worth protecting here is tier coverage: whatever surface collected
the values, a successful setup must land in the integration store *and* the
keyring *and* ``.env``. Divergence there is invisible locally — runtime resolves
the store first — and only shows up in the deploy preflight, which reads env
vars.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
from integrations.setup_flow import (
    IntegrationSetupSpec,
    ResolvedCredentials,
    SetupField,
    apply_setup,
)

_ENV_PATH = Path("/tmp/opensre-test/.env")

_FIELDS = (
    SetupField(name="api_token", label="Demo API token", env_var="DEMO_API_TOKEN", secret=True),
    SetupField(name="room", label="Demo room", env_var="DEMO_ROOM"),
    SetupField(name="note", label="Demo note", required=False),
)


def _passing(_source: str, _config: dict[str, str]) -> dict[str, str]:
    return {"status": "passed", "detail": "Demo connected."}


_SPEC = IntegrationSetupSpec(service="demo", fields=_FIELDS, verify=_passing)


class _Recorder:
    """Captures every write the flow performs."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.keyring: list[tuple[str, str]] = []
        self.env_values: list[dict[str, str]] = []


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()

    def _sync_env_values(values: dict[str, str], **_kwargs: Any) -> Path:
        rec.env_values.append(dict(values))
        return _ENV_PATH

    monkeypatch.setattr(
        setup_flow,
        "upsert_integration",
        lambda service, payload: rec.saved.append((service, payload)),
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: rec.keyring.append((key, value))
    )
    monkeypatch.setattr(setup_flow, "sync_env_values", _sync_env_values)
    return rec


def test_success_writes_store_keyring_and_env(recorder: _Recorder) -> None:
    outcome = apply_setup(_SPEC, {"api_token": "tok-1", "room": "ops", "note": "hi"})

    assert outcome.ok is True
    assert outcome.saved is True
    assert outcome.env_path == _ENV_PATH
    assert recorder.saved == [
        ("demo", {"credentials": {"api_token": "tok-1", "room": "ops", "note": "hi"}})
    ]
    # Routing is by env var name: the token is a secret, the room is not, and
    # the store-only field reaches neither tier.
    assert recorder.keyring == [("DEMO_API_TOKEN", "tok-1")]
    assert recorder.env_values == [{"DEMO_ROOM": "ops"}]


def test_missing_required_field_fails_before_any_write(recorder: _Recorder) -> None:
    outcome = apply_setup(_SPEC, {"api_token": "tok-1", "room": "  "})

    assert outcome.ok is False
    assert outcome.saved is False
    assert outcome.detail == "Demo room is required."
    assert (recorder.saved, recorder.keyring, recorder.env_values) == ([], [], [])


def test_optional_field_left_blank_is_stored_as_none(recorder: _Recorder) -> None:
    apply_setup(_SPEC, {"api_token": "tok-1", "room": "ops"})

    assert recorder.saved[0][1]["credentials"]["note"] is None


def test_failed_verification_persists_nothing(recorder: _Recorder) -> None:
    def _rejecting(_source: str, _config: dict[str, str]) -> dict[str, str]:
        return {"status": "failed", "detail": "Demo rejected the token."}

    spec = dataclasses.replace(_SPEC, verify=_rejecting)

    outcome = apply_setup(spec, {"api_token": "bad", "room": "ops"})

    assert outcome.ok is False
    assert outcome.detail == "Demo rejected the token."
    assert (recorder.saved, recorder.keyring, recorder.env_values) == ([], [], [])


def test_resolve_step_rewrites_credentials_before_they_are_stored(recorder: _Recorder) -> None:
    spec = dataclasses.replace(
        _SPEC,
        resolve=lambda creds: ResolvedCredentials(
            credentials={**creds, "room": "-100999"}, note="Delivering to Ops (channel)."
        ),
    )

    outcome = apply_setup(spec, {"api_token": "tok-1", "room": "@ops"})

    assert outcome.ok is True
    assert outcome.detail == "Demo connected. Delivering to Ops (channel)."
    # The resolved value, not the typed one, reaches both the store and .env.
    assert recorder.saved[0][1]["credentials"]["room"] == "-100999"
    assert recorder.env_values == [{"DEMO_ROOM": "-100999"}]


def test_resolve_failure_aborts_setup(recorder: _Recorder) -> None:
    spec = dataclasses.replace(
        _SPEC,
        resolve=lambda _creds: ResolvedCredentials(credentials={}, error="Cannot reach @ops."),
    )

    outcome = apply_setup(spec, {"api_token": "tok-1", "room": "@ops"})

    assert outcome.ok is False
    assert outcome.detail == "Cannot reach @ops."
    assert (recorder.saved, recorder.keyring, recorder.env_values) == ([], [], [])


def test_spec_without_a_verifier_still_configures(recorder: _Recorder) -> None:
    """An integration with nothing to verify against must not be unconfigurable."""
    spec = dataclasses.replace(_SPEC, verify=None)

    outcome = apply_setup(spec, {"api_token": "tok-1", "room": "ops"})

    assert outcome.ok is True
    assert outcome.detail == ""
    assert recorder.saved != []
