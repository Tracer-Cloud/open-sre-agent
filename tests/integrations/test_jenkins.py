"""Unit tests for the Jenkins integration.

Covers the config layer, the REST client (against ``httpx.MockTransport`` —
no live Jenkins), the response-shaping helpers, and the investigation tools.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.integrations import jenkins as jenkins_module
from app.integrations.jenkins import (
    JenkinsConfig,
    build_jenkins_config,
    jenkins_config_from_env,
    validate_jenkins_config,
)
from app.services.jenkins import make_jenkins_client
from app.services.jenkins.client import (
    JenkinsClient,
    _iso_from_ms,
    _safe_job_name,
    _status_from_color,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


class _FakeResponse:
    """Minimal stand-in for httpx.Response used to mock module-level httpx.request."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://jenkins.local"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._payload


def _client_with_handler(handler: Handler, monkeypatch: pytest.MonkeyPatch) -> JenkinsClient:
    """Build a JenkinsClient whose HTTP calls route through a MockTransport."""
    config = JenkinsConfig(base_url="http://jenkins.local", username="u", api_token="t")
    client = JenkinsClient(config)
    mock = httpx.Client(
        base_url=config.api_base_url,
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(client, "_get_client", lambda: mock)
    return client


# ---------------------------------------------------------------------------
# Config layer
# ---------------------------------------------------------------------------


class TestJenkinsConfig:
    def test_api_base_url_strips_trailing_slash(self) -> None:
        cfg = JenkinsConfig(base_url="http://jenkins.local/")
        assert cfg.api_base_url == "http://jenkins.local"

    def test_base_url_whitespace_normalized(self) -> None:
        cfg = JenkinsConfig(base_url="  http://jenkins.local  ")
        assert cfg.api_base_url == "http://jenkins.local"

    def test_auth_is_username_token_tuple(self) -> None:
        cfg = JenkinsConfig(base_url="http://x", username="alice", api_token="secret")
        assert cfg.auth == ("alice", "secret")

    def test_is_configured_requires_url_and_token(self) -> None:
        assert JenkinsConfig(base_url="http://x", api_token="t").is_configured
        assert not JenkinsConfig(base_url="http://x").is_configured
        assert not JenkinsConfig(base_url="", api_token="t").is_configured

    def test_timeout_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            JenkinsConfig(base_url="http://x", timeout_seconds=0)

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JenkinsConfig.model_validate({"base_url": "http://x", "bad_field": 1})


class TestBuildAndEnvConfig:
    def test_build_from_empty(self) -> None:
        cfg = build_jenkins_config(None)
        assert cfg.base_url == ""

    def test_env_returns_none_without_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JENKINS_URL", raising=False)
        monkeypatch.setenv("JENKINS_API_TOKEN", "t")
        assert jenkins_config_from_env() is None

    def test_env_returns_none_without_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JENKINS_URL", "http://jenkins.local")
        monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
        assert jenkins_config_from_env() is None

    def test_env_loads_full_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JENKINS_URL", "http://jenkins.local")
        monkeypatch.setenv("JENKINS_USER", "alice")
        monkeypatch.setenv("JENKINS_API_TOKEN", "tok")
        cfg = jenkins_config_from_env()
        assert cfg is not None
        assert cfg.api_base_url == "http://jenkins.local"
        assert cfg.username == "alice"
        assert cfg.api_token == "tok"


class TestValidateConfig:
    def test_fails_without_base_url(self) -> None:
        result = validate_jenkins_config(JenkinsConfig(base_url="", api_token="t"))
        assert not result.ok
        assert "base URL" in result.detail

    def test_fails_without_token(self) -> None:
        result = validate_jenkins_config(JenkinsConfig(base_url="http://x"))
        assert not result.ok
        assert "token" in result.detail

    def test_passes_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            jenkins_module.httpx,
            "request",
            lambda *_, **__: _FakeResponse({"nodeName": "controller"}),
        )
        result = validate_jenkins_config(
            JenkinsConfig(base_url="http://jenkins.local", api_token="t")
        )
        assert result.ok
        assert "controller" in result.detail

    def test_fails_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            jenkins_module.httpx,
            "request",
            lambda *_, **__: _FakeResponse({}, status_code=401),
        )
        result = validate_jenkins_config(
            JenkinsConfig(base_url="http://jenkins.local", api_token="bad")
        )
        assert not result.ok


# ---------------------------------------------------------------------------
# Shaping helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_iso_from_ms_converts(self) -> None:
        # 1780150032692 ms -> 2026-05-30 (verifies ms, not seconds)
        assert _iso_from_ms(1780150032692).startswith("2026-05-30")

    def test_iso_from_ms_invalid_returns_empty(self) -> None:
        assert _iso_from_ms(None) == ""
        assert _iso_from_ms("not-a-number") == ""
        assert _iso_from_ms(0) == ""

    def test_status_from_color(self) -> None:
        assert _status_from_color("blue") == ("SUCCESS", False)
        assert _status_from_color("red") == ("FAILURE", False)
        assert _status_from_color("yellow") == ("UNSTABLE", False)

    def test_status_from_color_anime_means_building(self) -> None:
        status, building = _status_from_color("blue_anime")
        assert status == "SUCCESS"
        assert building is True

    def test_status_from_color_unknown(self) -> None:
        assert _status_from_color("") == ("UNKNOWN", False)

    def test_safe_job_name_rejects_traversal(self) -> None:
        assert _safe_job_name("demo-fail") == "demo-fail"
        assert _safe_job_name("../etc") is None
        assert _safe_job_name("a/b") is None
        assert _safe_job_name("") is None


# ---------------------------------------------------------------------------
# Service client
# ---------------------------------------------------------------------------


_BUILDS_PAYLOAD = {
    "builds": [
        {"number": 4, "result": "FAILURE", "timestamp": 1780150032692, "duration": 11, "url": "u4"},
        {"number": 3, "result": "SUCCESS", "timestamp": 1780150031599, "duration": 10, "url": "u3"},
        {"number": 2, "result": None, "timestamp": 1780150030571, "building": True, "url": "u2"},
    ]
}


class TestListBuilds:
    def test_returns_shaped_builds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/job/demo/api/json"
            return httpx.Response(200, json=_BUILDS_PAYLOAD)

        client = _client_with_handler(handler, monkeypatch)
        result = client.list_builds("demo")
        assert result["success"]
        assert result["total"] == 3
        assert result["builds"][0]["status"] == "FAILURE"
        assert result["builds"][2]["status"] == "RUNNING"  # null result -> RUNNING
        assert len(result["failed_builds"]) == 1

    def test_status_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(
            lambda _: httpx.Response(200, json=_BUILDS_PAYLOAD), monkeypatch
        )
        result = client.list_builds("demo", status="success")
        assert result["total"] == 1
        assert result["builds"][0]["status"] == "SUCCESS"

    def test_invalid_job_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(200, json={}), monkeypatch)
        result = client.list_builds("../evil")
        assert not result["success"]
        assert "invalid job name" in result["error"]

    def test_http_error_returns_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(404, text="nope"), monkeypatch)
        result = client.list_builds("demo")
        assert not result["success"]
        assert "404" in result["error"]


class TestGetBuildLog:
    def test_returns_log(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/job/demo/4/consoleText"
            return httpx.Response(200, text="ERROR: boom\nFinished: FAILURE")

        client = _client_with_handler(handler, monkeypatch)
        result = client.get_build_log("demo", 4)
        assert result["success"]
        assert "ERROR: boom" in result["log"]
        assert result["truncated"] is False

    def test_tail_truncation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = "x" * 10 + "TAIL_MARKER"
        client = _client_with_handler(lambda _: httpx.Response(200, text=body), monkeypatch)
        result = client.get_build_log("demo", 1, max_chars=5)
        assert result["truncated"] is True
        assert result["log"] == "ARKER"  # keeps the tail

    def test_invalid_build_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_with_handler(lambda _: httpx.Response(200, text=""), monkeypatch)
        result = client.get_build_log("demo", "abc")  # type: ignore[arg-type]
        assert not result["success"]
        assert "invalid build number" in result["error"]


class TestListJobs:
    def test_decodes_color_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "jobs": [
                {
                    "name": "demo-fail",
                    "url": "uf",
                    "color": "red",
                    "lastBuild": {"number": 4, "timestamp": 1780150032692},
                },
                {"name": "demo-pass", "url": "up", "color": "blue", "lastBuild": {"number": 3}},
            ]
        }
        client = _client_with_handler(lambda _: httpx.Response(200, json=payload), monkeypatch)
        result = client.list_jobs()
        assert result["success"]
        statuses = {j["name"]: j["status"] for j in result["jobs"]}
        assert statuses == {"demo-fail": "FAILURE", "demo-pass": "SUCCESS"}


class TestListRunningBuilds:
    def test_filters_building(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "jobs": [
                {
                    "name": "demo",
                    "builds": [
                        {"number": 5, "building": True, "timestamp": 1780150032692, "url": "u5"},
                        {"number": 4, "building": False, "result": "SUCCESS", "url": "u4"},
                    ],
                }
            ]
        }
        client = _client_with_handler(lambda _: httpx.Response(200, json=payload), monkeypatch)
        result = client.list_running_builds()
        assert result["total"] == 1
        assert result["running_builds"][0]["number"] == 5
        assert result["running_builds"][0]["status"] == "RUNNING"


class TestMakeClient:
    def test_returns_none_without_creds(self) -> None:
        assert make_jenkins_client("", api_token="") is None
        assert make_jenkins_client("http://x", api_token="") is None
        assert make_jenkins_client("", api_token="t") is None

    def test_builds_client_with_creds(self) -> None:
        client = make_jenkins_client("http://jenkins.local", "alice", "tok")
        assert isinstance(client, JenkinsClient)
        assert client.config.auth == ("alice", "tok")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class _FakeToolClient:
    """Context-managed fake client returning canned method results for tool tests."""

    def __init__(self, **results: Any) -> None:
        self._results = results

    def __enter__(self) -> _FakeToolClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def list_builds(self, *_: Any, **__: Any) -> dict[str, Any]:
        return self._results["list_builds"]

    def list_jobs(self, *_: Any, **__: Any) -> dict[str, Any]:
        return self._results["list_jobs"]


class TestTools:
    def test_availability_requires_verified_connection(self) -> None:
        from app.tools.JenkinsTool import _jenkins_available

        assert not _jenkins_available({"jenkins": {}})
        assert not _jenkins_available({"jenkins": {"connection_verified": False}})
        assert _jenkins_available({"jenkins": {"connection_verified": True}})

    def test_build_tool_extract_params_soft_defaults_job_name(self) -> None:
        from app.tools.JenkinsTool import _list_jenkins_builds_extract_params

        # job_name absent from sources -> empty default (LLM supplies it as a tool arg)
        params = _list_jenkins_builds_extract_params({"jenkins": {"connection_verified": True}})
        assert params["job_name"] == ""

    def test_creds_mapping_from_source_dict(self) -> None:
        from app.tools.JenkinsTool import _jenkins_creds

        creds = _jenkins_creds({"base_url": "http://x", "username": "u", "api_token": "t"})
        assert creds == {"jenkins_url": "http://x", "jenkins_user": "u", "jenkins_token": "t"}

    def test_not_configured_when_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.tools import JenkinsTool

        monkeypatch.setattr(JenkinsTool, "_resolve_client", lambda *_a, **_k: None)
        result = JenkinsTool.list_jenkins_builds("demo")
        assert result["available"] is False
        assert "not configured" in result["error"]

    def test_list_builds_tool_shapes_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.tools import JenkinsTool

        fake = _FakeToolClient(
            list_builds={
                "success": True,
                "job": "demo",
                "builds": [{"number": 4, "status": "FAILURE"}],
                "failed_builds": [{"number": 4, "status": "FAILURE"}],
                "total": 1,
            }
        )
        monkeypatch.setattr(JenkinsTool, "_resolve_client", lambda *_a, **_k: fake)
        result = JenkinsTool.list_jenkins_builds("demo", jenkins_url="http://x", jenkins_token="t")
        assert result["available"] is True
        assert result["source"] == "jenkins"
        assert result["total"] == 1
        assert result["failed_builds"][0]["number"] == 4

    def test_tools_registered_in_registry(self) -> None:
        from app.tools.registry import get_registered_tools

        names = {t.name for t in get_registered_tools() if t.source == "jenkins"}
        assert names == {
            "list_jenkins_builds",
            "get_jenkins_build_log",
            "list_jenkins_jobs",
            "list_jenkins_running_builds",
        }
