from app.correlation.providers import NoopUpstreamEvidenceProvider
from app.correlation.upstream import UpstreamEvidenceBundle


def test_noop_upstream_evidence_provider_returns_empty_bundle() -> None:
    provider = NoopUpstreamEvidenceProvider()

    bundle = provider.collect_upstream_evidence(
        alert_id="synthetic-alert",
        service_name="orders",
        window_start="2026-04-15T14:00:00Z",
        window_end="2026-04-15T14:15:00Z",
    )

    assert isinstance(bundle, UpstreamEvidenceBundle)
    assert bundle.rds_metrics == ()
    assert bundle.upstream_metrics == ()
    assert bundle.web_request_logs == ()
    assert bundle.app_logs == ()
    assert bundle.topology_hints == ()
    assert bundle.operator_hints == ()
