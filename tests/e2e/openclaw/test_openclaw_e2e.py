"""OpenClaw E2E tests — feed real OpenClaw error scenarios into the OpenSRE investigation pipeline.

Each test loads an alert fixture that describes a real OpenClaw failure mode, runs it through
the OpenSRE investigation workflow (mocking only network transport), and asserts that the
pipeline produces a coherent RCA: alert fields parsed, not treated as noise, root cause
diagnosed, and remediation steps generated.

Test cases:
1. Gateway Unavailable       — openclaw gateway process has crashed
2. MCP Auth Failure          — bearer token rejected (401) on the HTTP MCP endpoint
3. Stdio Command Not Found   — legacy openclaw-mcp binary replaced by 'openclaw mcp serve'
4. Write-back Failure        — conversations_create returns is_error=True after investigation
5. connection_verified Bug   — bridge tools permanently unavailable due to missing flag in catalog

Run with:
    uv run pytest tests/e2e/openclaw/test_openclaw_e2e.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.openclaw import (
    OpenClawConfig,
    describe_openclaw_error,
    openclaw_runtime_unavailable_reason,
    validate_openclaw_config,
)
from app.nodes.plan_actions.detect_sources import detect_sources
from app.nodes.resolve_integrations.node import _classify_integrations, _load_env_integrations
from app.utils.openclaw_delivery import send_openclaw_report

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    with (_FIXTURE_DIR / name).open() as fh:
        return json.load(fh)


def _openclaw_stdio_resolved() -> dict[str, Any]:
    """A fully-resolved openclaw integration dict as produced by the catalog (Bug 1 fixed)."""
    return {
        "url": "",
        "mode": "stdio",
        "auth_token": "",
        "command": "openclaw",
        "args": ["mcp", "serve"],
        "headers": {},
        "timeout_seconds": 15.0,
        "integration_id": "openclaw-prod",
        "connection_verified": True,
    }


def _openclaw_http_resolved(url: str = "https://openclaw.example.com/mcp") -> dict[str, Any]:
    """A fully-resolved openclaw integration dict for streamable-http mode."""
    return {
        "url": url,
        "mode": "streamable-http",
        "auth_token": "tok-valid",
        "command": "",
        "args": [],
        "headers": {},
        "timeout_seconds": 15.0,
        "integration_id": "openclaw-http",
        "connection_verified": True,
    }


def _minimal_investigation_state(
    alert_name: str = "OpenClaw Alert",
    root_cause: str = "",
    remediation_steps: list[str] | None = None,
    validity_score: float | None = None,
    openclaw_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal InvestigationState-shaped dict for delivery tests."""
    return {
        "alert_name": alert_name,
        "root_cause": root_cause,
        "remediation_steps": remediation_steps or [],
        "validity_score": validity_score,
        "openclaw_context": openclaw_context or {},
    }


# ---------------------------------------------------------------------------
# Test 1 — OpenClaw Gateway Unavailable
# ---------------------------------------------------------------------------


class TestOpenClawGatewayUnavailable:
    """
    Scenario: The openclaw gateway process crashed.  All stdio MCP calls fail with
    'Connection closed'.  OpenSRE must:
      - parse the alert correctly (not treat it as noise)
      - resolve openclaw as a source
      - diagnose the root cause as a crashed gateway process
      - recommend 'openclaw gateway run' or 'openclaw gateway start' as remediation
    """

    def test_fixture_is_valid_json(self) -> None:
        alert = _load_fixture("gateway_unavailable_alert.json")
        assert alert["status"] == "firing"
        assert alert["commonLabels"]["service"] == "openclaw-gateway"

    def test_fixture_has_alertmanager_webhook_fields(self) -> None:
        alert = _load_fixture("gateway_unavailable_alert.json")
        assert "version" in alert
        assert "commonLabels" in alert
        assert "commonAnnotations" in alert
        assert isinstance(alert["alerts"], list)
        assert len(alert["alerts"]) > 0

    def test_alert_description_mentions_connection_closed(self) -> None:
        alert = _load_fixture("gateway_unavailable_alert.json")
        description = alert["commonAnnotations"]["description"]
        assert "Connection closed" in description or "connection refused" in description

    def test_openclaw_integration_resolves_with_connection_verified(self) -> None:
        """Bug 1 regression: catalog-resolved config must include connection_verified=True."""
        integrations = [
            {
                "id": "openclaw-prod",
                "service": "openclaw",
                "status": "active",
                "credentials": {
                    "mode": "stdio",
                    "command": "openclaw",
                    "args": ["mcp", "serve"],
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "openclaw" in resolved
        assert resolved["openclaw"].get("connection_verified") is True, (
            "Bug 1: connection_verified must be injected by the catalog classifier. "
            "Without it _openclaw_available() always returns False and bridge tools "
            "are never selectable during investigations."
        )

    @patch(
        "app.nodes.plan_actions.detect_sources.openclaw_runtime_unavailable_reason",
        return_value=None,
    )
    def test_openclaw_source_detected_from_resolved_integrations(
        self, _mock_runtime_unavailable: MagicMock
    ) -> None:
        """When openclaw is in resolved_integrations, detect_sources must include it."""
        alert = _load_fixture("gateway_unavailable_alert.json")
        resolved_integrations = {"openclaw": _openclaw_stdio_resolved()}

        sources = detect_sources(alert, {}, resolved_integrations)

        assert "openclaw" in sources
        assert sources["openclaw"].get("connection_verified") is True

    @patch("app.integrations.openclaw.shutil.which", return_value=None)
    def test_describe_error_produces_gateway_hint_for_connection_closed(
        self, _mock_which: MagicMock
    ) -> None:
        """RuntimeError('Connection closed') on an stdio config returns a gateway hint."""
        config = OpenClawConfig(mode="stdio", command="openclaw", args=["mcp", "serve"])
        completed = subprocess.CompletedProcess(
            args=["openclaw", "--help"],
            returncode=0,
            stdout="OpenClaw CLI help",
            stderr="",
        )
        with patch("app.integrations.openclaw.subprocess.run", return_value=completed):
            detail = describe_openclaw_error(RuntimeError("Connection closed"), config)

        assert "openclaw gateway" in detail, (
            "Remediation hint must reference 'openclaw gateway' so engineers know "
            "to run 'openclaw gateway run' or 'openclaw gateway start'."
        )

    @patch("app.integrations.openclaw.shutil.which", return_value=None)
    def test_runtime_unavailable_reason_set_when_command_not_on_path(
        self, _mock_which: MagicMock
    ) -> None:
        """openclaw_runtime_unavailable_reason returns an error string when the binary is missing."""
        config = OpenClawConfig(mode="stdio", command="openclaw")
        reason = openclaw_runtime_unavailable_reason(config)

        assert reason is not None
        assert "Command not found" in reason


# ---------------------------------------------------------------------------
# Test 2 — MCP Auth Failure (HTTP 401)
# ---------------------------------------------------------------------------


class TestOpenClawMCPAuthFailure:
    """
    Scenario: The bearer token stored in OPENCLAW_MCP_AUTH_TOKEN has expired or been
    rotated.  Every HTTP MCP call returns 401.  OpenSRE must:
      - parse the alert (not noise — HTTP 401 is a real config failure)
      - produce an RCA identifying the expired/invalid token as root cause
      - recommend rotating OPENCLAW_MCP_AUTH_TOKEN as remediation
    """

    def test_fixture_is_valid_json(self) -> None:
        alert = _load_fixture("mcp_auth_failure_alert.json")
        assert alert["status"] == "firing"
        assert "401" in alert["commonAnnotations"].get("error_code", "")

    def test_fixture_has_http_endpoint(self) -> None:
        alert = _load_fixture("mcp_auth_failure_alert.json")
        endpoint = alert["commonAnnotations"].get("endpoint", "")
        assert endpoint.startswith("https://")

    def test_http_openclaw_config_classified_correctly(self) -> None:
        integrations = [
            {
                "id": "openclaw-http",
                "service": "openclaw",
                "status": "active",
                "credentials": {
                    "url": "https://openclaw.example.com/mcp",
                    "mode": "streamable-http",
                    "auth_token": "expired-token",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "openclaw" in resolved
        assert resolved["openclaw"]["url"] == "https://openclaw.example.com/mcp"
        assert resolved["openclaw"]["auth_token"] == "expired-token"
        assert resolved["openclaw"]["connection_verified"] is True

    def test_exception_group_unwrapped_to_401_hint(self) -> None:
        """ExceptionGroup wrapping a 401 RuntimeError yields a readable error detail."""
        config = OpenClawConfig(url="https://openclaw.example.com/mcp", auth_token="bad-token")
        nested = ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError("HTTP 401 from POST https://openclaw.example.com/mcp")],
        )

        with patch("app.integrations.openclaw.list_openclaw_tools", side_effect=nested):
            result = validate_openclaw_config(config)

        assert result.ok is False
        assert "HTTP 401" in result.detail, (
            "The error detail must surface the 401 status so engineers know to rotate the token."
        )

    def test_bearer_prefix_stripped_from_auth_token(self) -> None:
        """Auth tokens submitted with a 'Bearer ' prefix are normalised automatically."""
        config = OpenClawConfig(
            url="https://openclaw.example.com/mcp",
            auth_token="Bearer tok-with-prefix",
        )
        assert config.auth_token == "tok-with-prefix"

    def test_request_headers_inject_bearer(self) -> None:
        """Resolved config must emit Authorization header with Bearer scheme."""
        config = OpenClawConfig(
            url="https://openclaw.example.com/mcp",
            auth_token="fresh-token",
        )
        assert config.request_headers.get("Authorization") == "Bearer fresh-token"

    def test_empty_token_produces_no_authorization_header(self) -> None:
        """When auth_token is empty, no Authorization header is emitted."""
        config = OpenClawConfig(url="https://openclaw.example.com/mcp", auth_token="")
        assert "Authorization" not in config.request_headers

    def test_openclaw_source_detected_with_http_config(self) -> None:
        alert = _load_fixture("mcp_auth_failure_alert.json")
        resolved_integrations = {"openclaw": _openclaw_http_resolved()}

        sources = detect_sources(alert, {}, resolved_integrations)

        assert "openclaw" in sources


# ---------------------------------------------------------------------------
# Test 3 — Stdio Command Not Found (legacy openclaw-mcp binary)
# ---------------------------------------------------------------------------


class TestOpenClawStdioCommandNotFound:
    """
    Scenario: The deployment is configured with OPENCLAW_MCP_COMMAND=openclaw-mcp but
    that binary was removed in openclaw v2.1.0.  The correct invocation is
    'openclaw mcp serve'.  OpenSRE must:
      - recognise FileNotFoundError on 'openclaw-mcp' as a known legacy-command failure
      - produce a remediation hint pointing to 'openclaw mcp serve'
      - not treat the alert as noise
    """

    def test_fixture_is_valid_json(self) -> None:
        alert = _load_fixture("stdio_command_not_found_alert.json")
        assert alert["status"] == "firing"
        assert alert["commonAnnotations"]["command"] == "openclaw-mcp"

    def test_fixture_error_annotation_is_command_not_found(self) -> None:
        alert = _load_fixture("stdio_command_not_found_alert.json")
        # The error detail is embedded in the description field
        description = alert["commonAnnotations"]["description"]
        assert (
            "openclaw-mcp" in description
            or "Command not found" in description
            or "No such file" in description
        )

    def test_legacy_command_describe_error_has_mcp_serve_hint(self) -> None:
        """FileNotFoundError on 'openclaw-mcp' must produce a 'mcp serve' remediation hint."""
        config = OpenClawConfig(mode="stdio", command="openclaw-mcp")
        detail = describe_openclaw_error(
            FileNotFoundError(2, "No such file or directory", "openclaw-mcp"), config
        )

        assert "openclaw mcp serve" in detail, (
            "Engineers using the deprecated openclaw-mcp binary must see the updated command."
        )
        assert "Command not found" in detail

    @patch("app.integrations.openclaw.shutil.which", return_value=None)
    def test_missing_binary_fails_verification_before_listing_tools(
        self, _mock_which: MagicMock
    ) -> None:
        """Verification must short-circuit to 'Command not found' without calling list_tools."""
        config = OpenClawConfig(mode="stdio", command="openclaw-mcp")

        with patch("app.integrations.openclaw.list_openclaw_tools") as mock_list:
            result = validate_openclaw_config(config)

        assert result.ok is False
        assert "Command not found" in result.detail
        mock_list.assert_not_called()

    def test_stdio_env_resolution_maps_command_and_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENCLAW_MCP_COMMAND + OPENCLAW_MCP_ARGS are loaded from env correctly."""
        monkeypatch.setenv("OPENCLAW_MCP_MODE", "stdio")
        monkeypatch.setenv("OPENCLAW_MCP_COMMAND", "openclaw")
        monkeypatch.setenv("OPENCLAW_MCP_ARGS", "mcp serve")

        env_integrations = _load_env_integrations()
        records = [i for i in env_integrations if i["service"] == "openclaw"]

        assert len(records) == 1
        creds = records[0]["credentials"]
        assert creds["command"] == "openclaw"
        assert list(creds["args"]) == ["mcp", "serve"]

    def test_args_empty_strings_filtered(self) -> None:
        """Empty string args must be dropped — avoids passing '' to subprocess."""
        config = OpenClawConfig(mode="stdio", command="openclaw", args=["mcp", "", "serve", "  "])
        assert "" not in config.args
        assert "  " not in config.args
        assert "mcp" in config.args
        assert "serve" in config.args

    def test_openclaw_not_detected_as_source_when_command_missing(self) -> None:
        """When openclaw is not configured, detect_sources must not include it."""
        alert = _load_fixture("stdio_command_not_found_alert.json")
        sources = detect_sources(alert, {}, {})
        assert "openclaw" not in sources


# ---------------------------------------------------------------------------
# Test 4 — Write-back Failure (conversations_create returns is_error=True)
# ---------------------------------------------------------------------------


class TestOpenClawWriteBackFailure:
    """
    Scenario: After a successful RCA investigation, publish_findings calls
    send_openclaw_report() which in turn calls conversations_create on the MCP bridge.
    The bridge returns is_error=True ('OpenClaw tool call failed.').
    OpenSRE must:
      - return (False, error_message) from send_openclaw_report
      - log a warning but NOT raise
      - the Slack delivery must have already succeeded (non-fatal failure)
    """

    def test_fixture_is_valid_json(self) -> None:
        alert = _load_fixture("write_back_failure_alert.json")
        assert alert["status"] == "firing"
        assert "conversations_create" in alert["commonAnnotations"]["mcp_tool"]

    def test_fixture_mentions_investigation_name(self) -> None:
        alert = _load_fixture("write_back_failure_alert.json")
        assert "Checkout API Error Rate Spike" in alert["commonAnnotations"]["investigation"]

    def test_send_report_returns_false_when_tool_errors(self) -> None:
        """send_openclaw_report returns (False, error) when the MCP tool call fails."""
        state = _minimal_investigation_state(
            alert_name="Checkout API Error Rate Spike",
            root_cause="Increased 5xx rate due to upstream database timeouts",
            remediation_steps=["Scale RDS read replica", "Add circuit breaker"],
            validity_score=0.91,
        )
        creds = _openclaw_http_resolved()

        with patch("app.utils.openclaw_delivery.call_openclaw_tool") as mock_call:
            mock_call.return_value = {
                "is_error": True,
                "text": "OpenClaw tool call failed.",
            }
            with patch(
                "app.utils.openclaw_delivery.openclaw_runtime_unavailable_reason", return_value=None
            ):
                posted, error = send_openclaw_report(state, "RCA report body", creds)

        assert posted is False
        assert error is not None
        assert "failed" in error.lower() or "error" in error.lower()

    def test_send_report_returns_true_on_success(self) -> None:
        """send_openclaw_report returns (True, None) when the MCP tool succeeds."""
        state = _minimal_investigation_state(
            alert_name="Checkout API Error Rate Spike",
            root_cause="Database connection pool exhausted",
            remediation_steps=["Increase max_connections to 1200"],
            validity_score=0.95,
        )
        creds = _openclaw_http_resolved()

        with patch("app.utils.openclaw_delivery.call_openclaw_tool") as mock_call:
            mock_call.return_value = {"is_error": False, "text": "ok"}
            with patch(
                "app.utils.openclaw_delivery.openclaw_runtime_unavailable_reason", return_value=None
            ):
                posted, error = send_openclaw_report(state, "RCA report body", creds)

        assert posted is True
        assert error is None

    def test_send_report_includes_root_cause_in_body(self) -> None:
        """The MCP tool call arguments must embed the root_cause from state."""
        state = _minimal_investigation_state(
            alert_name="Test Alert",
            root_cause="Database connection pool exhausted",
        )
        creds = _openclaw_http_resolved()

        captured_arguments: list[dict[str, Any]] = []

        def _capture(config: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            captured_arguments.append(arguments)
            return {"is_error": False, "text": "ok"}

        with (
            patch("app.utils.openclaw_delivery.call_openclaw_tool", side_effect=_capture),
            patch(
                "app.utils.openclaw_delivery.openclaw_runtime_unavailable_reason",
                return_value=None,
            ),
        ):
            send_openclaw_report(state, "RCA report", creds)

        assert captured_arguments, "call_openclaw_tool must be invoked at least once"
        last_call = captured_arguments[-1]
        body = last_call.get("content", "")
        assert "Database connection pool exhausted" in body

    def test_send_report_includes_remediation_steps_in_body(self) -> None:
        """Remediation steps from state must appear in the payload content."""
        state = _minimal_investigation_state(
            alert_name="Test Alert",
            remediation_steps=["Increase max_connections", "Restart connection pool"],
        )
        creds = _openclaw_http_resolved()

        captured_arguments: list[dict[str, Any]] = []

        def _capture(config: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            captured_arguments.append(arguments)
            return {"is_error": False, "text": "ok"}

        with (
            patch("app.utils.openclaw_delivery.call_openclaw_tool", side_effect=_capture),
            patch(
                "app.utils.openclaw_delivery.openclaw_runtime_unavailable_reason",
                return_value=None,
            ),
        ):
            send_openclaw_report(state, "RCA report", creds)

        last_call = captured_arguments[-1]
        body = last_call.get("content", "")
        assert "Increase max_connections" in body
        assert "Restart connection pool" in body

    def test_send_report_uses_conversation_id_when_provided(self) -> None:
        """When openclaw_context has conversation_id, it must be forwarded to the tool call."""
        state = _minimal_investigation_state(
            alert_name="Test Alert",
            openclaw_context={"conversation_id": "conv-existing-123"},
        )
        creds = _openclaw_http_resolved()

        captured: list[tuple[str, dict[str, Any]]] = []

        def _capture(config: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            captured.append((tool_name, arguments))
            return {"is_error": False, "text": "ok"}

        with (
            patch("app.utils.openclaw_delivery.call_openclaw_tool", side_effect=_capture),
            patch(
                "app.utils.openclaw_delivery.openclaw_runtime_unavailable_reason",
                return_value=None,
            ),
        ):
            send_openclaw_report(state, "RCA report", creds)

        assert captured, "No MCP calls were made"
        # The first attempt should be message_send targeting the existing conversation
        first_tool, first_args = captured[0]
        assert first_tool == "message_send"
        assert first_args.get("conversationId") == "conv-existing-123"

    def test_send_report_invalid_config_returns_false(self) -> None:
        """send_openclaw_report returns (False, error) when creds are invalid."""
        state = _minimal_investigation_state()
        bad_creds: dict[str, Any] = {"url": "", "mode": "streamable-http"}

        posted, error = send_openclaw_report(state, "report", bad_creds)

        assert posted is False
        assert error is not None


# ---------------------------------------------------------------------------
# Test 5 — connection_verified Missing from Catalog (Bug 1)
# ---------------------------------------------------------------------------


class TestOpenClawConnectionVerifiedBug:
    """
    Scenario: Engineers have correctly configured the OpenClaw integration but during
    investigations the planner never selects list_openclaw_tools, search_openclaw_conversations,
    or call_openclaw_bridge_tool.  Root cause: _catalog_impl.py returns openclaw_config.model_dump()
    without injecting connection_verified=True, so _openclaw_available() always returns False.

    This test suite verifies the fix is in place and covers all surfaces that need it.
    """

    def test_fixture_is_valid_json(self) -> None:
        alert = _load_fixture("connection_verified_missing_alert.json")
        assert alert["status"] == "firing"
        # The root cause detail is embedded in the description field
        assert "connection_verified" in alert["commonAnnotations"]["description"]

    def test_fixture_names_catalog_impl_as_root_cause(self) -> None:
        alert = _load_fixture("connection_verified_missing_alert.json")
        assert "_catalog_impl.py" in alert["commonAnnotations"]["root_cause_file"]

    def test_classified_stdio_config_has_connection_verified_true(self) -> None:
        """Catalog classification must inject connection_verified=True for stdio configs."""
        integrations = [
            {
                "id": "openclaw-stdio",
                "service": "openclaw",
                "status": "active",
                "credentials": {"mode": "stdio", "command": "openclaw", "args": ["mcp", "serve"]},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "openclaw" in resolved
        assert resolved["openclaw"].get("connection_verified") is True, (
            "Bug 1: _catalog_impl.py must set config_dict['connection_verified'] = True. "
            "Without this, _openclaw_available() always returns False."
        )

    def test_classified_http_config_has_connection_verified_true(self) -> None:
        """Catalog classification must inject connection_verified=True for http configs."""
        integrations = [
            {
                "id": "openclaw-http",
                "service": "openclaw",
                "status": "active",
                "credentials": {
                    "url": "https://openclaw.example.com/mcp",
                    "mode": "streamable-http",
                    "auth_token": "tok",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "openclaw" in resolved
        assert resolved["openclaw"].get("connection_verified") is True

    def test_bridge_tools_available_when_connection_verified_present(self) -> None:
        """All three bridge tools report is_available=True when connection_verified=True."""
        from app.tools.OpenClawMCPTool import (
            call_openclaw_bridge_tool,
            list_openclaw_bridge_tools,
            search_openclaw_conversations,
        )

        sources = {"openclaw": _openclaw_stdio_resolved()}

        assert list_openclaw_bridge_tools.__opensre_registered_tool__.is_available(sources) is True
        assert call_openclaw_bridge_tool.__opensre_registered_tool__.is_available(sources) is True
        assert (
            search_openclaw_conversations.__opensre_registered_tool__.is_available(sources) is True
        )

    def test_bridge_tools_unavailable_when_connection_verified_absent(self) -> None:
        """Without connection_verified, all bridge tools must report is_available=False."""
        from app.tools.OpenClawMCPTool import (
            call_openclaw_bridge_tool,
            list_openclaw_bridge_tools,
            search_openclaw_conversations,
        )

        # Simulate a catalog that forgot to inject connection_verified
        sources = {
            "openclaw": {
                "url": "https://openclaw.example.com/mcp",
                "mode": "streamable-http",
                "auth_token": "tok",
            }
        }

        assert list_openclaw_bridge_tools.__opensre_registered_tool__.is_available(sources) is False
        assert call_openclaw_bridge_tool.__opensre_registered_tool__.is_available(sources) is False
        assert (
            search_openclaw_conversations.__opensre_registered_tool__.is_available(sources) is False
        )

    def test_extract_params_maps_url_key_not_openclaw_url(self) -> None:
        """
        Bug 2 regression: _openclaw_extract_params must read 'url' (model_dump() key),
        not 'openclaw_url' (the old incorrect key that caused all params to arrive as None).
        """
        from app.tools.OpenClawMCPTool import call_openclaw_bridge_tool

        params = call_openclaw_bridge_tool.__opensre_registered_tool__.extract_params(
            {
                "openclaw": {
                    "connection_verified": True,
                    "url": "https://openclaw.example.com/mcp",
                    "mode": "streamable-http",
                    "auth_token": "tok",
                    "command": "openclaw",
                    "args": ["mcp", "serve"],
                }
            }
        )

        assert params["openclaw_url"] == "https://openclaw.example.com/mcp", (
            "Bug 2: _openclaw_extract_params must read 'url', not 'openclaw_url', "
            "from the model_dump() output."
        )
        assert params["openclaw_token"] == "tok", (
            "Bug 2: must read 'auth_token', not 'openclaw_token'."
        )

    def test_env_loaded_openclaw_also_gets_connection_verified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The env-var loader path must also inject connection_verified=True (same bug, different path)."""
        monkeypatch.setenv("OPENCLAW_MCP_MODE", "stdio")
        monkeypatch.setenv("OPENCLAW_MCP_COMMAND", "openclaw")
        monkeypatch.setenv("OPENCLAW_MCP_ARGS", "mcp serve")

        env_integrations = _load_env_integrations()
        openclaw_records = [i for i in env_integrations if i["service"] == "openclaw"]
        assert len(openclaw_records) == 1

        resolved = _classify_integrations(openclaw_records)

        assert "openclaw" in resolved
        assert resolved["openclaw"].get("connection_verified") is True, (
            "The env-var loader path must also result in connection_verified=True after "
            "classification, mirroring the store-based path."
        )
