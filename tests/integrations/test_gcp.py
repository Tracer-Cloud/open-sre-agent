"""GCP integration: config normalization, project scope, and the verifier."""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.tool_framework.utils.integration_sources import availability_view
from integrations._catalog_impl import classify_integrations, load_env_integrations
from integrations.config_models import GCPIntegrationConfig
from integrations.gcp import classify
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import GCPClientError, api_not_enabled, describe_api_error
from integrations.gcp.projects import group_projects, resolve_projects, resource_names
from integrations.gcp.tool_params import gcp_tool_params

# --- config model ------------------------------------------------------------


def test_additional_projects_accepts_comma_string() -> None:
    config = GCPIntegrationConfig.model_validate(
        {"project_id": "primary", "additional_projects": " one , two ,, three "}
    )

    assert config.additional_projects == ["one", "two", "three"]
    assert config.all_projects == ["primary", "one", "two", "three"]


def test_all_projects_deduplicates_the_primary() -> None:
    config = GCPIntegrationConfig.model_validate(
        {"project_id": "primary", "additional_projects": "primary,other"}
    )

    assert config.all_projects == ["primary", "other"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("not-a-number", 100), (0, 1), (5000, 1000), ("250", 250)],
)
def test_max_results_is_clamped(raw: object, expected: int) -> None:
    config = GCPIntegrationConfig.model_validate({"project_id": "p", "max_results": raw})

    assert config.max_results == expected


def test_classify_requires_a_project_id() -> None:
    assert classify({"service_account_key": "{}"}, "rec-1") == (None, None)


def test_classify_returns_config_and_service() -> None:
    config, service = classify({"project_id": "acme"}, "rec-1")

    assert service == "gcp"
    assert config is not None
    assert config.integration_id == "rec-1"


# --- env loading -------------------------------------------------------------


def _gcp_sources(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> dict[str, Any]:
    """Load GCP integration records from a clean env and return the tool view."""
    for name in (
        "GCP_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        "GCP_ADDITIONAL_PROJECTS",
        "GCP_SERVICE_ACCOUNT_KEY",
        "GCP_IMPERSONATE_SERVICE_ACCOUNT",
        "GCP_MAX_RESULTS",
        "GCP_INSTANCES",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    records = [rec for rec in load_env_integrations() if rec["service"] == "gcp"]
    return availability_view(classify_integrations(records))


def test_env_loader_falls_back_to_google_cloud_project(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = _gcp_sources(monkeypatch, {"GOOGLE_CLOUD_PROJECT": "from-google-var"})

    assert gcp_available(sources) is True
    assert gcp_tool_params(sources)["default_project"] == "from-google-var"


def test_env_loader_ignores_gcp_without_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = _gcp_sources(monkeypatch, {"GCP_MAX_RESULTS": "500"})

    assert gcp_available(sources) is False


def test_tool_params_expose_one_credential_many_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _gcp_sources(
        monkeypatch,
        {
            "GCP_PROJECT_ID": "acme-prod",
            "GCP_ADDITIONAL_PROJECTS": "acme-staging,acme-data",
            "GCP_MAX_RESULTS": "250",
        },
    )

    params = gcp_tool_params(sources)

    assert params["default_project"] == "acme-prod"
    assert params["available_projects"] == ["acme-prod", "acme-staging", "acme-data"]
    assert params["limit"] == 250
    # One credential reaches all three, so they share a config object.
    assert len(group_projects(params["available_projects"], params["project_configs"])) == 1


def test_tool_params_merge_many_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = _gcp_sources(
        monkeypatch,
        {
            "GCP_INSTANCES": json.dumps(
                [
                    {
                        "name": "prod",
                        "project_id": "acme-prod",
                        "additional_projects": "acme-shared",
                    },
                    {
                        "name": "research",
                        "project_id": "acme-research",
                        "impersonate_service_account": "ro@acme-research.iam.gserviceaccount.com",
                    },
                ]
            )
        },
    )

    params = gcp_tool_params(sources)

    assert params["default_project"] == "acme-prod"
    assert params["available_projects"] == ["acme-prod", "acme-shared", "acme-research"]

    groups = group_projects(params["available_projects"], params["project_configs"])
    assert [projects for _config, projects in groups] == [
        ["acme-prod", "acme-shared"],
        ["acme-research"],
    ]
    assert groups[0][0]["impersonate_service_account"] == ""
    assert groups[1][0]["impersonate_service_account"] == (
        "ro@acme-research.iam.gserviceaccount.com"
    )


def test_tool_params_are_empty_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    params = gcp_tool_params(_gcp_sources(monkeypatch, {}))

    assert params == {
        "default_project": "",
        "available_projects": [],
        "project_configs": {},
        "limit": 100,
    }


# --- project resolution ------------------------------------------------------


def test_resolve_projects_defaults_to_the_primary() -> None:
    projects, error = resolve_projects(
        "", default_project="acme-prod", available_projects=["acme-prod", "acme-staging"]
    )

    assert (projects, error) == (["acme-prod"], None)


@pytest.mark.parametrize("token", ["*", "all", "ALL"])
def test_resolve_projects_expands_the_all_token(token: str) -> None:
    projects, error = resolve_projects(
        token, default_project="acme-prod", available_projects=["acme-prod", "acme-staging"]
    )

    assert (projects, error) == (["acme-prod", "acme-staging"], None)


def test_resolve_projects_accepts_a_list() -> None:
    projects, error = resolve_projects(
        "acme-staging, acme-prod",
        default_project="acme-prod",
        available_projects=["acme-prod", "acme-staging"],
    )

    assert (projects, error) == (["acme-staging", "acme-prod"], None)


def test_resolve_projects_rejects_an_unconfigured_project() -> None:
    projects, error = resolve_projects(
        "hallucinated", default_project="acme-prod", available_projects=["acme-prod"]
    )

    assert projects == []
    assert error is not None
    # The message has to tell the agent how to recover, not just that it failed.
    assert "hallucinated" in error
    assert "gcp_list_projects" in error


def test_resolve_projects_reports_missing_configuration() -> None:
    projects, error = resolve_projects("", default_project="", available_projects=[])

    assert projects == []
    assert error is not None
    assert "GCP_PROJECT_ID" in error


def test_group_projects_without_configs_yields_one_empty_group() -> None:
    assert group_projects(["a", "b"], None) == [({}, ["a", "b"])]


def test_resource_names_render_for_cloud_logging() -> None:
    assert resource_names(["a", "b"]) == ["projects/a", "projects/b"]


# --- client error rendering --------------------------------------------------


class _FakeResponse:
    status = 403


class _FakeHttpError(Exception):
    resp = _FakeResponse()
    content = json.dumps(
        {"error": {"message": "Permission 'logging.logEntries.list' denied"}}
    ).encode()


def test_describe_api_error_keeps_the_permission_name() -> None:
    detail = describe_api_error(_FakeHttpError())

    assert detail == "HTTP 403: Permission 'logging.logEntries.list' denied"


def test_describe_api_error_falls_back_to_the_exception_type() -> None:
    assert describe_api_error(ValueError("boom")) == "ValueError calling the Google API"


# --- disabled API vs denied permission ---------------------------------------
#
# Google returns HTTP 403 PERMISSION_DENIED for both, so these fixtures differ
# only in ``details[].reason`` — the one field that separates them.

_DISABLED_MESSAGE = (
    "Kubernetes Engine API has not been used in project acme before or it is disabled."
)


def _http_error(payload: dict[str, Any]) -> Exception:
    error = Exception()
    error.resp = _FakeResponse()  # type: ignore[attr-defined]
    error.content = json.dumps({"error": payload}).encode()  # type: ignore[attr-defined]
    return error


def _service_disabled() -> Exception:
    return _http_error(
        {
            "code": 403,
            "message": _DISABLED_MESSAGE,
            "status": "PERMISSION_DENIED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "SERVICE_DISABLED",
                    "domain": "googleapis.com",
                    "metadata": {"service": "container.googleapis.com"},
                }
            ],
        }
    )


def test_api_not_enabled_recognises_a_disabled_service() -> None:
    assert api_not_enabled(_service_disabled()) is True


def test_api_not_enabled_rejects_a_real_permission_denial() -> None:
    """A denial carries the same 403 and status, so only the reason may decide."""
    denied = _http_error(
        {
            "code": 403,
            "message": "Required 'container.clusters.list' permission",
            "status": "PERMISSION_DENIED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "IAM_PERMISSION_DENIED",
                }
            ],
        }
    )

    assert api_not_enabled(denied) is False


def test_api_not_enabled_rejects_an_unrecognisable_error() -> None:
    """Fail safe: anything this cannot positively identify stays an error."""
    assert api_not_enabled(_FakeHttpError()) is False
    assert api_not_enabled(ValueError("boom")) is False


# --- verifier ----------------------------------------------------------------


def test_verify_gcp_missing_project() -> None:
    from integrations.gcp.verifier import verify_gcp

    result = verify_gcp("local env", {"project_id": ""})

    assert result["status"] == "missing"
    assert result["detail"] == "Missing project_id."


def _raise_client_error(_config: Any, _api: tuple[str, str]) -> Any:
    raise GCPClientError("no Google credentials available")


def test_verify_gcp_reports_a_credential_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.gcp import verifier

    monkeypatch.setattr(verifier, "build_service", _raise_client_error)

    result = verifier.verify_gcp("local env", {"project_id": "acme-prod"})

    assert result["status"] == "failed"
    assert result["detail"] == "no Google credentials available"


class _StubProjects:
    def get(self, projectId: str) -> _StubProjects:  # noqa: N803 — Google API kwarg
        self._project = projectId
        return self

    def execute(self) -> dict[str, str]:
        return {"name": "Acme Prod", "projectId": self._project}


class _StubService:
    def projects(self) -> _StubProjects:
        return _StubProjects()


def _build_stub_service(_config: Any, _api: tuple[str, str]) -> _StubService:
    return _StubService()


def test_verify_gcp_reports_auth_mode_and_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.gcp import verifier

    monkeypatch.setattr(verifier, "build_service", _build_stub_service)

    result = verifier.verify_gcp(
        "local env",
        {
            "project_id": "acme-prod",
            "additional_projects": "acme-staging",
            "impersonate_service_account": "ro@acme-prod.iam.gserviceaccount.com",
        },
    )

    assert result["status"] == "passed"
    assert "Acme Prod (acme-prod)" in result["detail"]
    assert "via impersonation" in result["detail"]
    assert "2 projects" in result["detail"]


# --- scope under GCP_ADDITIONAL_PROJECTS=discover -----------------------------
#
# The configured list is one project; the credential reads a whole folder. What
# the verifier must not do is report the former as the reach.


def _stub_discovery(monkeypatch: pytest.MonkeyPatch, found: Any) -> None:
    from integrations.gcp import verifier

    def _discover(_config: Any) -> Any:
        return found

    monkeypatch.setattr(verifier, "build_service", _build_stub_service)
    monkeypatch.setattr(verifier, "discover", _discover)


def _discovered(*projects: str, error: str = "", truncated: bool = False) -> Any:
    from integrations.gcp.project_discovery import DiscoveryResult

    return DiscoveryResult(projects=projects, error=error, truncated=truncated)


def test_verify_gcp_counts_discovered_projects_not_configured_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.gcp import verifier

    _stub_discovery(monkeypatch, _discovered("acme-prod", "acme-staging", "acme-data"))

    result = verifier.verify_gcp(
        "local env", {"project_id": "acme-prod", "additional_projects": "discover"}
    )

    assert result["status"] == "passed"
    assert "3 discovered projects" in result["detail"]


def test_verify_gcp_says_so_when_discovery_could_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still passed — the tools work, on a narrower estate than was asked for."""
    from integrations.gcp import verifier

    _stub_discovery(monkeypatch, _discovered(error="HTTP 403: projects.list denied"))

    result = verifier.verify_gcp(
        "local env", {"project_id": "acme-prod", "additional_projects": "discover"}
    )

    assert result["status"] == "passed"
    assert "1 configured project" in result["detail"]
    assert "project discovery unavailable (HTTP 403: projects.list denied)" in result["detail"]


def test_verify_gcp_flags_a_capped_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A capped reach is not the real reach, and nothing else tells the operator."""
    from integrations.gcp import verifier
    from integrations.gcp.project_discovery import MAX_DISCOVERED

    _stub_discovery(monkeypatch, _discovered("acme-prod", "acme-staging", truncated=True))

    result = verifier.verify_gcp(
        "local env", {"project_id": "acme-prod", "additional_projects": "discover"}
    )

    assert f"2 discovered projects, capped at {MAX_DISCOVERED}" in result["detail"]


def test_verify_gcp_does_not_list_projects_when_discovery_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round trip is only owed when configuration asked for it."""
    from integrations.gcp import verifier

    called: list[object] = []

    def _discover(config: Any) -> Any:
        called.append(config)
        raise AssertionError("discovery must not run without the token")

    monkeypatch.setattr(verifier, "build_service", _build_stub_service)
    monkeypatch.setattr(verifier, "discover", _discover)

    result = verifier.verify_gcp("local env", {"project_id": "acme-prod"})

    assert result["status"] == "passed"
    assert "1 project" in result["detail"]
    assert called == []
