from __future__ import annotations

import pytest

from app.analytics.source import (
    INVESTIGATION_EVENT_SCHEMA_VERSION,
    EntrypointSource,
    TriggerMode,
    build_source_properties,
    is_test_run,
    resolve_environment_tag,
)


def test_is_test_run_true_for_explicit_source_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_INVESTIGATION_SOURCE", "test")
    monkeypatch.delenv("OPENSRE_IS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)

    assert is_test_run() is True


def test_is_test_run_true_for_explicit_boolean_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_INVESTIGATION_SOURCE", raising=False)
    monkeypatch.setenv("OPENSRE_IS_TEST", "1")

    assert is_test_run() is True


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("PYTEST_CURRENT_TEST", "tests/suite.py::test_case"),
        ("GITHUB_ACTIONS", "true"),
        ("CI", "true"),
    ],
)
def test_is_test_run_true_for_auto_detected_env(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
) -> None:
    monkeypatch.delenv("OPENSRE_INVESTIGATION_SOURCE", raising=False)
    monkeypatch.delenv("OPENSRE_IS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(env_name, env_value)

    assert is_test_run() is True


def test_is_test_run_false_without_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_INVESTIGATION_SOURCE", raising=False)
    monkeypatch.delenv("OPENSRE_IS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)

    assert is_test_run() is False


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("production", "prod"),
        ("prod", "prod"),
        ("staging", "staging"),
        ("stage", "staging"),
        ("development", "dev"),
        ("dev", "dev"),
        ("local", "dev"),
        ("preview", "unknown"),
    ],
)
def test_resolve_environment_tag_maps_known_values(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected: str,
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_ENV", raising=False)
    monkeypatch.setenv("ENV", env_value)

    assert resolve_environment_tag() == expected


def test_build_source_properties_for_api_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_INVESTIGATION_SOURCE", raising=False)
    monkeypatch.delenv("OPENSRE_IS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("ENV", "production")

    properties = build_source_properties(
        entrypoint=EntrypointSource.SDK,
        trigger_mode=TriggerMode.SERVICE_RUNTIME,
        investigation_id="inv-123",
    )

    assert properties == {
        "source": "sdk",
        "entrypoint_source": "sdk",
        "category": "api",
        "trigger_mode": "service_runtime",
        "is_test": False,
        "environment": "prod",
        "investigation_id": "inv-123",
        "investigation_event_schema_version": INVESTIGATION_EVENT_SCHEMA_VERSION,
    }


def test_build_source_properties_for_test_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_INVESTIGATION_SOURCE", "test")
    monkeypatch.setenv("ENV", "development")

    properties = build_source_properties(
        entrypoint=EntrypointSource.CLI_PASTE,
        trigger_mode=TriggerMode.PASTE,
        investigation_id="inv-abc",
    )

    assert properties["source"] == "test"
    assert properties["entrypoint_source"] == "cli_paste"
    assert properties["category"] == "test"
    assert properties["is_test"] is True
    assert properties["trigger_mode"] == "paste"
    assert properties["environment"] == "dev"
