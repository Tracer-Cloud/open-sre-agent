"""A Yandex Monitoring alert has to reach the tools that can explain it.

Yandex Monitoring has no plain webhook: alerts leave it by email, SMS, push,
Telegram or a Cloud Function, so the payload OpenSRE receives is whatever the
operator's function forwards. There is no published schema to match, which is
why detection keys off markers only a Yandex Cloud alert carries rather than an
exact shape - and why a payload that merely mentions a folder must not be
mistaken for a firing alert.
"""

from __future__ import annotations

import pytest

from core.domain.alerts.alert_source import (
    primary_sources_for_alert,
    resolve_alert_source,
    routing_for_alert_source,
)
from integrations.yandex_cloud.alert_source_detect import detect_yandex_cloud_alert_source

FOLDER = "b1gapqc3kb2vii7cs9i3"


@pytest.fixture(autouse=True)
def _adapters() -> None:
    """Rebuild the adapters the way startup does, so detectors are registered."""
    import integrations.harness_adapters as harness_adapters

    harness_adapters.register_harness_adapters()


class TestDetection:
    def test_a_cloud_function_forwarding_an_alert(self) -> None:
        raw = {
            "alert_id": "aoe1abc",
            "alert_name": "CPU saturated",
            "status": "ALARM",
            "folder_id": FOLDER,
        }

        assert detect_yandex_cloud_alert_source(raw) == "yandex_monitoring"

    def test_the_rest_api_spelling(self) -> None:
        """The API answers in camelCase; hand-written functions use snake_case."""
        raw = {"alertId": "aoe1abc", "evaluationStatus": "ALARM", "folderId": FOLDER}

        assert detect_yandex_cloud_alert_source(raw) == "yandex_monitoring"

    def test_a_yandex_host_is_enough_on_its_own(self) -> None:
        """Nothing else produces an mdb.yandexcloud.net address."""
        raw = {"labels": {"host": "rc1b-73a5kp3f3j6qmofo.mdb.yandexcloud.net"}}

        assert detect_yandex_cloud_alert_source(raw) == "yandex_monitoring"


class TestItDoesNotOverreach:
    def test_a_folder_id_alone_is_not_an_alert(self) -> None:
        """A forwarded tool result mentions a folder and is not a firing alert."""
        assert detect_yandex_cloud_alert_source({"folder_id": FOLDER}) is None

    def test_another_vendors_alert_is_left_alone(self) -> None:
        raw = {
            "commonLabels": {"alertname": "KubePodCrashLooping", "pod": "checkout"},
            "externalURL": "https://grafana.example.com",
        }

        assert detect_yandex_cloud_alert_source(raw) is None

    def test_an_empty_payload_matches_nothing(self) -> None:
        assert detect_yandex_cloud_alert_source({}) is None


class TestRoutingReachesTheTools:
    def test_a_detected_alert_routes_to_the_integration(self) -> None:
        state = {"raw_alert": {"alert_id": "a", "status": "ALARM", "folder_id": FOLDER}}

        assert resolve_alert_source(state) == "yandex_monitoring"
        assert primary_sources_for_alert(state) == ("yandex_cloud",)

    def test_one_source_covers_the_whole_cloud(self) -> None:
        """Metrics, logs, compute, balancers and databases all report as one source."""
        entry = routing_for_alert_source("yandex_monitoring")

        assert entry is not None
        assert entry.relevance_tool_sources == ("yandex_cloud",)
        assert entry.seed_tool_sources == ("yandex_cloud",)

    def test_another_vendors_detector_still_wins_its_own_alerts(self) -> None:
        """Registration order must not let one vendor swallow another's payloads."""
        state = {"raw_alert": {"commonLabels": {"grafana_folder": "prod"}}}

        assert resolve_alert_source(state) == "grafana"


class TestTheRegistrationSurvivesAStartupRebuild:
    def test_a_rebuild_does_not_drop_the_detector(self) -> None:
        """Startup clears the detector list before re-registering; ours must return."""
        import integrations.harness_adapters as harness_adapters

        harness_adapters.register_harness_adapters()
        harness_adapters.register_harness_adapters()

        state = {"raw_alert": {"alert_id": "a", "status": "ALARM", "folder_id": FOLDER}}
        assert resolve_alert_source(state) == "yandex_monitoring"
