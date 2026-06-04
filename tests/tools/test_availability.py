"""Tests for tool availability helpers."""

from __future__ import annotations

from app.tools.utils.availability import (
    cloudwatch_is_available,
    datadog_available_or_backend,
    eks_available_or_backend,
)


class TestEksAvailableOrBackend:
    def test_eks_missing(self) -> None:
        sources: dict[str, dict] = {}
        assert eks_available_or_backend(sources) is False

    def test_eks_empty(self) -> None:
        sources = {"eks": {}}
        assert eks_available_or_backend(sources) is False

    def test_eks_verified(self) -> None:
        sources = {"eks": {"connection_verified": True}}
        assert eks_available_or_backend(sources) is True

    def test_eks_backend(self) -> None:
        sources = {"eks": {"_backend": object()}}
        assert eks_available_or_backend(sources) is True

    def test_eks_not_available(self) -> None:
        sources = {"eks": {"connection_verified": False}}
        assert eks_available_or_backend(sources) is False

    def test_eks_backend_none(self) -> None:
        sources = {"eks": {"_backend": None}}
        assert eks_available_or_backend(sources) is False

    def test_eks_backend_overrides_failed_verification(self) -> None:
        sources = {"eks": {"connection_verified": False, "_backend": object()}}
        assert eks_available_or_backend(sources) is True

    def test_production_backend_without_cloudops_marker_keeps_eks_available(self) -> None:
        """Production backends (synthetic test backends, real client wrappers)
        do NOT have ``is_cloudopsbench_backend = True``. The CloudOpsBench
        guard added for the bench MUST be a no-op for them — getattr falls
        back to False, the original verified-or-backend logic still runs.
        Regression-pin for the bench Bug-2 fix in availability.py."""

        class _ProductionLikeBackend:
            # Has a marker attribute but it's False — same as no marker
            # at all from getattr's perspective.
            is_cloudopsbench_backend = False

        sources = {"eks": {"connection_verified": False, "_backend": _ProductionLikeBackend()}}
        assert eks_available_or_backend(sources) is True

    def test_cloudops_backend_is_hidden_when_marker_true(self) -> None:
        """Inverse of the above: when the backend IS the CloudOpsBench
        replay backend, the EKS surface MUST be hidden (the bench's
        CloudOpsBenchK8sTools serve the EKS surface against the case
        snapshot instead — exposing the real EKS tools would have the
        agent try sts:AssumeRole on placeholder ARNs)."""

        class _CloudOpsBenchBackend:
            is_cloudopsbench_backend = True

        sources = {"eks": {"connection_verified": True, "_backend": _CloudOpsBenchBackend()}}
        assert eks_available_or_backend(sources) is False


class TestDatadogAvailableOrBackend:
    def test_datadog_missing(self) -> None:
        sources: dict[str, dict] = {}
        assert datadog_available_or_backend(sources) is False

    def test_datadog_empty(self) -> None:
        sources = {"datadog": {}}
        assert datadog_available_or_backend(sources) is False

    def test_datadog_verified(self) -> None:
        sources = {"datadog": {"connection_verified": True}}
        assert datadog_available_or_backend(sources) is True

    def test_datadog_backend(self) -> None:
        sources = {"datadog": {"_backend": object()}}
        assert datadog_available_or_backend(sources) is True

    def test_datadog_not_available(self) -> None:
        sources = {"datadog": {"connection_verified": False}}
        assert datadog_available_or_backend(sources) is False

    def test_datadog_backend_none(self) -> None:
        sources = {"datadog": {"_backend": None}}
        assert datadog_available_or_backend(sources) is False

    def test_datadog_backend_overrides_failed_verification(self) -> None:
        sources = {"datadog": {"connection_verified": False, "_backend": object()}}
        assert datadog_available_or_backend(sources) is True


class TestCloudwatchIsAvailable:
    def test_cloudwatch_missing(self) -> None:
        sources: dict[str, dict] = {}
        assert cloudwatch_is_available(sources) is False

    def test_cloudwatch_present_empty(self) -> None:
        sources = {"cloudwatch": {}}
        assert cloudwatch_is_available(sources) is False

    def test_cloudwatch_with_data(self) -> None:
        sources = {"cloudwatch": {"log_group": "test"}}
        assert cloudwatch_is_available(sources) is True
