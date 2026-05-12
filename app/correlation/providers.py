from __future__ import annotations

from app.correlation.upstream import UpstreamEvidenceBundle


class NoopUpstreamEvidenceProvider:
    def collect_upstream_evidence(
        self,
        *,
        alert_id: str,
        service_name: str,
        window_start: str,
        window_end: str,
    ) -> UpstreamEvidenceBundle:
        _ = (alert_id, service_name, window_start, window_end)
        return UpstreamEvidenceBundle()
