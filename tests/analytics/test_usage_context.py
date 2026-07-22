"""Tests for org/surface/session usage analytics context."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from platform.analytics import provider
from platform.analytics.events import Event
from platform.analytics.repl_context import bound_repl_turn_context
from platform.analytics.usage_context import (
    ORGANIZATION_GROUP_TYPE,
    SURFACE_CLI,
    SURFACE_SLACK,
    bound_usage_context,
    build_usage_enrichment,
    merge_usage_enrichment,
)


@pytest.fixture(autouse=True)
def _reset_analytics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    provider.shutdown_analytics(flush=False)
    provider._instance = None
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    provider._cached_anonymous_id = None
    provider._cached_identity_persistence = "unknown"
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    yield
    provider.shutdown_analytics(flush=False)
    provider._instance = None


def _stub_httpx_client(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    posted_payloads: list[dict[str, object]] = []

    class _StubResponse:
        def raise_for_status(self) -> None:
            return None

    class _StubClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            return None

        def post(self, url: str, json: dict[str, object]) -> _StubResponse:
            posted_payloads.append({"url": url, "json": json})
            return _StubResponse()

    monkeypatch.setattr(provider.httpx, "Client", _StubClient)
    return posted_payloads


def test_build_usage_enrichment_from_context_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_abc")
    with bound_usage_context(
        surface=SURFACE_SLACK,
        session_id="sess-1",
        user_id="U123",
    ):
        props = build_usage_enrichment()
    assert props["organization_id"] == "org_abc"
    assert props["$groups"] == {ORGANIZATION_GROUP_TYPE: "org_abc"}
    assert props["surface"] == SURFACE_SLACK
    assert props["session_id"] == "sess-1"
    assert props["user_id"] == "U123"


def test_session_id_falls_back_to_cli_session() -> None:
    with bound_repl_turn_context(session_id="repl-sess"):
        props = build_usage_enrichment()
    assert props["session_id"] == "repl-sess"


def test_merge_usage_enrichment_caller_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_env")
    with bound_usage_context(surface=SURFACE_CLI, organization_id="org_ctx"):
        merged = merge_usage_enrichment({"organization_id": "org_caller", "surface": "cli"})
    assert merged["organization_id"] == "org_caller"
    assert merged["$groups"] == {ORGANIZATION_GROUP_TYPE: "org_caller"}
    assert merged["surface"] == "cli"


def test_capture_stamps_org_groups_and_emits_groupidentify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _stub_httpx_client(monkeypatch)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_prod")

    analytics = provider.Analytics()
    with bound_usage_context(surface=SURFACE_CLI, session_id="s1"):
        analytics.capture(Event.CLI_INVOKED, {"entrypoint": "opensre"})
    analytics.shutdown(flush=True)

    events = [p["json"]["event"] for p in posted]
    assert "$groupidentify" in events
    assert Event.CLI_INVOKED.value in events

    group_payload = next(p["json"] for p in posted if p["json"]["event"] == "$groupidentify")
    assert group_payload["properties"]["$group_type"] == ORGANIZATION_GROUP_TYPE
    assert group_payload["properties"]["$group_key"] == "org_prod"
    assert group_payload["properties"]["$group_set"]["organization_id"] == "org_prod"

    capture_payload = next(
        p["json"] for p in posted if p["json"]["event"] == Event.CLI_INVOKED.value
    )
    props = capture_payload["properties"]
    assert props["organization_id"] == "org_prod"
    assert props["$groups"] == {ORGANIZATION_GROUP_TYPE: "org_prod"}
    assert props["surface"] == SURFACE_CLI
    assert props["session_id"] == "s1"


def test_group_identify_once_per_org(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = _stub_httpx_client(monkeypatch)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_once")

    analytics = provider.Analytics()
    analytics.capture(Event.CLI_INVOKED)
    analytics.capture(Event.ONBOARD_STARTED)
    analytics.shutdown(flush=True)

    group_events = [p for p in posted if p["json"]["event"] == "$groupidentify"]
    assert len(group_events) == 1


def test_group_identify_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = _stub_httpx_client(monkeypatch)
    analytics = provider.Analytics()
    analytics.group_identify(ORGANIZATION_GROUP_TYPE, "org_x", {"name": "Acme"})
    analytics.shutdown(flush=True)

    assert len(posted) == 1
    props = posted[0]["json"]["properties"]
    assert props["$group_key"] == "org_x"
    assert props["$group_set"] == {"name": "Acme"}
