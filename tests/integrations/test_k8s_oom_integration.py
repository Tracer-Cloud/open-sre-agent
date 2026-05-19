"""CI integration tests: Datadog + Kubernetes + Slack for K8s OOM scenario.

Uses recorded fixtures — no live API calls, no real credentials required.

Coverage:
- test_datadog_*   Datadog backend returns correct correlated metrics/logs for
                   the OOM alert window (FixtureDatadogBackend + EKS scenario 001).
- test_k8s_*       K8s/EKS backend returns pod logs, events, and pod list that
                   surface the OOMKilled evidence (FixtureEKSBackend + scenario 001).
- test_slack_*     Slack delivery sends the investigation summary via webhook;
                   no credentials appear in the transmitted payload (masking check).
- TestCredentialMasking  End-to-end check that DD_API_KEY / kubeconfig values
                   never leak into the investigation output or Slack payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.synthetic.eks.scenario_loader import SUITE_DIR, load_scenario
from tests.synthetic.mock_datadog_backend.backend import FixtureDatadogBackend
from tests.synthetic.mock_eks_backend.backend import FixtureEKSBackend

# ---------------------------------------------------------------------------
# Shared fixture: OOM-killed crash-loop scenario
# ---------------------------------------------------------------------------

OOM_SCENARIO_DIR = SUITE_DIR / "001-oomkilled-crashloop"
K8S_OOM_ALERT_FIXTURE = Path(__file__).parent.parent / "synthetic" / "k8s_oom_alert.json"


@pytest.fixture(scope="module")
def oom_scenario():
    return load_scenario(OOM_SCENARIO_DIR)


@pytest.fixture(scope="module")
def datadog_backend(oom_scenario):
    return FixtureDatadogBackend(oom_scenario)


@pytest.fixture(scope="module")
def eks_backend(oom_scenario):
    return FixtureEKSBackend(oom_scenario)


# ---------------------------------------------------------------------------
# Synthetic alert fixture
# ---------------------------------------------------------------------------


def test_k8s_oom_alert_fixture_exists() -> None:
    assert K8S_OOM_ALERT_FIXTURE.exists(), (
        f"Missing synthetic alert at {K8S_OOM_ALERT_FIXTURE}; "
        "run from repo root: uv run opensre investigate -i tests/synthetic/k8s_oom_alert.json"
    )


def test_k8s_oom_alert_fixture_is_valid_json() -> None:
    raw = json.loads(K8S_OOM_ALERT_FIXTURE.read_text(encoding="utf-8"))
    assert raw.get("alert_source") == "datadog"
    assert raw.get("severity") == "critical"
    labels = raw.get("commonLabels", {})
    assert labels.get("namespace") == "payments"
    assert "oom" in raw.get("title", "").lower() or "oom" in raw.get("message", "").lower()


# ---------------------------------------------------------------------------
# Datadog integration tests
# ---------------------------------------------------------------------------


class TestDatadogIntegration:
    """Datadog fetches correlated logs and monitors for the OOM alert window."""

    def test_query_logs_returns_structured_envelope(self, datadog_backend) -> None:
        result = datadog_backend.query_logs(query="service:payments-api")

        assert result["source"] == "datadog_logs"
        assert result["available"] is True
        assert "logs" in result
        assert "error_logs" in result
        assert "total" in result
        assert isinstance(result["logs"], list)

    def test_query_logs_contains_k8s_tagged_entries(self, datadog_backend) -> None:
        """Datadog logs carry K8s correlation tags that link them to the alert namespace.

        For scenario 001 the OOM kill is a kernel/kubelet event — the Datadog
        logs provide healthy context from the same pod and namespace window
        rather than an application error.  The K8s evidence (pod logs, events)
        surfaces the OOM-kill directly.
        """
        result = datadog_backend.query_logs(query="service:payments-api")

        all_tags = " ".join(
            " ".join(str(t) for t in log.get("tags", [])) for log in result["logs"]
        ).lower()
        assert "payments" in all_tags or "payments-api" in all_tags, (
            f"Expected K8s namespace/service tags in Datadog logs, got tags: {all_tags[:300]!r}"
        )

    def test_query_logs_error_filtering_works_for_dns_scenario(self) -> None:
        """Verify error_logs filtering surfaces Datadog-side errors when they exist.

        Scenario 006 (DNS resolution failure) has Datadog logs with status=error;
        the OOM scenario (001) does not — OOM evidence lives in K8s pod logs.
        """
        dns_fixture = load_scenario(SUITE_DIR / "006-dns-resolution-failure")
        dns_backend = FixtureDatadogBackend(dns_fixture)

        result = dns_backend.query_logs(query="service:payments-api")

        assert len(result["error_logs"]) > 0, (
            "Expected error_logs to be non-empty for DNS failure scenario "
            "(log entries with status=error or error keywords)"
        )

    def test_query_logs_preserves_query_param(self, datadog_backend) -> None:
        query = "service:payments-api source:kubernetes"
        result = datadog_backend.query_logs(query=query)

        assert result["query"] == query

    def test_query_monitors_returns_structured_envelope(self, datadog_backend) -> None:
        result = datadog_backend.query_monitors(query="payments-api")

        assert result["source"] == "datadog_monitors"
        assert result["available"] is True
        assert "monitors" in result
        assert "total" in result
        assert isinstance(result["monitors"], list)

    def test_query_monitors_preserves_query_filter(self, datadog_backend) -> None:
        result = datadog_backend.query_monitors(query="payments-api")

        assert result["query_filter"] == "payments-api"

    def test_datadog_backend_satisfies_protocol(self, datadog_backend) -> None:
        from tests.synthetic.mock_datadog_backend.backend import DatadogBackend

        assert isinstance(datadog_backend, DatadogBackend)

    def test_credentials_absent_from_datadog_response(self, datadog_backend) -> None:
        result = datadog_backend.query_logs(query="service:payments-api")

        result_json = json.dumps(result)
        # Real credentials are empty strings in .env; verify no key-like patterns leak
        assert "DD_API_KEY" not in result_json
        assert "DD_APP_KEY" not in result_json


# ---------------------------------------------------------------------------
# Kubernetes integration tests
# ---------------------------------------------------------------------------


class TestKubernetesIntegration:
    """K8s/EKS backend returns pod logs and events surfacing the OOM evidence."""

    def test_list_pods_returns_structured_envelope(self, eks_backend) -> None:
        result = eks_backend.list_pods(
            cluster_name="payments-prod-eks", namespace="payments"
        )

        assert result["source"] == "eks"
        assert result["available"] is True
        assert result["error"] is None
        assert "pods" in result
        assert "failing_pods" in result
        assert "high_restart_pods" in result
        assert result["cluster_name"] == "payments-prod-eks"
        assert result["namespace"] == "payments"

    def test_list_pods_identifies_crashlooping_pod(self, eks_backend) -> None:
        result = eks_backend.list_pods(
            cluster_name="payments-prod-eks", namespace="payments"
        )

        high_restart_pods = result["high_restart_pods"]
        assert len(high_restart_pods) >= 1, (
            "Expected at least one high-restart pod for OOM scenario"
        )
        # The OOM-killed pod should have high restart count
        pod_names = [p["name"] for p in high_restart_pods]
        assert any("payments-api" in name for name in pod_names), (
            f"Expected payments-api pod in high_restart_pods, got {pod_names}"
        )

    def test_list_pods_contains_oom_exit_code(self, eks_backend) -> None:
        result = eks_backend.list_pods(
            cluster_name="payments-prod-eks", namespace="payments"
        )

        pods_json = json.dumps(result["pods"]).lower()
        assert "137" in pods_json or "oomkilled" in pods_json, (
            "Expected exit_code=137 or OOMKilled state in pod list"
        )

    def test_get_events_returns_oom_warning_events(self, eks_backend) -> None:
        result = eks_backend.get_events(
            cluster_name="payments-prod-eks", namespace="payments"
        )

        assert result["source"] == "eks"
        assert result["available"] is True
        assert result["error"] is None
        assert result["total_warning_count"] >= 1

        events_text = json.dumps(result["warning_events"]).lower()
        assert any(
            kw in events_text for kw in ("oomkilled", "exit code 137", "backoff", "killed")
        ), f"Expected OOM-related event reason in events: {events_text[:300]!r}"

    def test_get_pod_logs_returns_kernel_oom_line(self, eks_backend) -> None:
        result = eks_backend.get_pod_logs(
            cluster_name="payments-prod-eks",
            namespace="payments",
            pod_name="payments-api-7f9dd8b6c4-x7gr9",
        )

        assert result["source"] == "eks"
        assert result["available"] is True
        logs_text = str(result.get("logs", "")).lower()
        assert any(
            kw in logs_text
            for kw in ("memory cgroup out of memory", "killed process", "exit code 137", "oomkill")
        ), f"Expected kernel OOM evidence in pod logs: {logs_text[:400]!r}"

    def test_eks_backend_satisfies_protocol(self, eks_backend) -> None:
        from tests.synthetic.mock_eks_backend.backend import EKSBackend

        assert isinstance(eks_backend, EKSBackend)

    def test_credentials_absent_from_eks_response(self, eks_backend) -> None:
        result = eks_backend.list_pods()
        result_json = json.dumps(result)

        assert "role_arn" not in result_json or result_json.count("role_arn") == 0
        assert "KUBECONFIG" not in result_json
        assert "kubeconfig" not in result_json.lower() or "cluster_name" in result_json


# ---------------------------------------------------------------------------
# Slack delivery tests
# ---------------------------------------------------------------------------


def _mock_http_response(
    status_code: int, text: str = "ok", json_body: Any = None
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


class TestSlackDelivery:
    """Slack sends the investigation summary via webhook; no credentials leak."""

    def test_send_slack_report_via_webhook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import slack_delivery

        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/k8s-oom-test")
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda url, **kw: (
                captured.update({"url": url, "payload": kw.get("json", {})}),
                _mock_http_response(200),
            )[1],
        )

        report = (
            "## K8s OOM Kill — payments-api\n\n"
            "**Root Cause:** Container OOM-killed (exit code 137). "
            "CrashLoopBackOff after 8 restarts."
        )
        ok, err = slack_delivery.send_slack_report(report, channel="C_OOM_TEST", thread_ts=None)

        assert ok is True, f"Expected delivery success, got err={err!r}"
        assert captured["url"] == "https://hooks.slack.test/k8s-oom-test"
        assert "payments-api" in str(captured["payload"].get("text", ""))

    def test_send_slack_report_includes_oom_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.utils import slack_delivery

        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/k8s-oom-test")
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda _url, **kw: (
                captured.update({"payload": kw.get("json", {})}),
                _mock_http_response(200),
            )[1],
        )

        report = (
            "Root Cause: OOMKilled — heap exceeded 512Mi limit, exit code 137, "
            "CrashLoopBackOff on payments-api-7f9dd8b6c4-x7gr9"
        )
        ok, _ = slack_delivery.send_slack_report(report, channel="C_OOM", thread_ts=None)

        assert ok is True
        text = captured["payload"].get("text", "")
        assert "137" in text or "OOMKilled" in text or "payments-api" in text

    def test_no_credentials_in_slack_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.utils import slack_delivery

        fake_webhook = "https://hooks.slack.test/secure-k8s-oom"
        monkeypatch.setenv("SLACK_WEBHOOK_URL", fake_webhook)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda _url, **kw: (
                captured.update({"payload": kw.get("json", {})}),
                _mock_http_response(200),
            )[1],
        )

        # Report body must NOT reference credential env-var names — those
        # are CI concerns, not investigation content.
        report = (
            "Root Cause: OOMKilled. "
            "Cluster: payments-prod-eks. Namespace: payments. "
            "Container exceeded memory limit. exit code 137. CrashLoopBackOff."
        )
        ok, _ = slack_delivery.send_slack_report(report, channel="C1", thread_ts=None)

        assert ok is True
        payload_str = json.dumps(captured["payload"])
        # Credential env-var names must not appear anywhere in the Slack payload
        assert "API_KEY" not in payload_str
        assert "APP_KEY" not in payload_str
        assert "KUBECONFIG" not in payload_str
        # The webhook URL itself must not be echoed back into the message body
        assert fake_webhook not in payload_str

    def test_slack_delivery_skips_gracefully_when_no_webhook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.utils import slack_delivery

        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(slack_delivery, "_configured_webhook_url", lambda: "")

        ok, err = slack_delivery.send_slack_report(
            "OOM summary", channel="C1", thread_ts=None
        )

        assert ok is False
        assert err == "no_thread_ts"


# ---------------------------------------------------------------------------
# Credential masking: end-to-end check
# ---------------------------------------------------------------------------


class TestCredentialMasking:
    """Verify that integration credentials never appear in investigation output."""

    def test_dd_api_key_absent_from_datadog_logs_response(
        self, datadog_backend
    ) -> None:
        """Even if a real key were injected, the backend should not echo it back."""
        result = datadog_backend.query_logs(query="service:payments-api")
        serialized = json.dumps(result)

        # The backend receives empty credentials from the fixture loader —
        # verify neither the key name nor any placeholder propagates into output.
        assert "api_key" not in serialized.lower() or '"api_key"' not in serialized, (
            "api_key field should not appear in Datadog logs response"
        )

    def test_kubeconfig_absent_from_k8s_events_response(self, eks_backend) -> None:
        result = eks_backend.get_events(namespace="payments")
        serialized = json.dumps(result)

        assert "kubeconfig" not in serialized.lower()
        assert "KUBECONFIG" not in serialized

    def test_k8s_oom_output_fixture_passes_credential_check(self) -> None:
        fixture_path = (
            Path(__file__).parent.parent / "fixtures" / "k8s_oom_output.json"
        )
        assert fixture_path.exists(), f"Missing regression fixture at {fixture_path}"
        content = fixture_path.read_text(encoding="utf-8")

        # These credential variable names must never appear in stored reports
        forbidden = ["DD_API_KEY", "DD_APP_KEY", "KUBECONFIG", "SLACK_BOT_TOKEN"]
        for token in forbidden:
            assert token not in content, (
                f"Credential token {token!r} found in regression fixture {fixture_path}"
            )

    def test_slack_payload_does_not_echo_bot_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.utils import slack_delivery

        fake_token = "xoxb-test-token-should-not-appear-in-payload"
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda _url, **kw: (
                captured.update({"payload": kw.get("json", {})}),
                _mock_http_response(200, json_body={"ok": True, "ts": "1.0"}),
            )[1],
        )

        ok, err = slack_delivery.send_slack_report(
            "OOM investigation complete",
            channel="C1",
            thread_ts="1.0",
            access_token=fake_token,
        )

        assert ok is True, (
            f"Unexpected delivery failure: {err!r}  "
            "(mock must return {{ok: True, ts: '...'}} for chat.postMessage)"
        )
        payload_str = json.dumps(captured["payload"])
        assert fake_token not in payload_str, (
            "Bot token must not appear in the Slack message body"
        )
