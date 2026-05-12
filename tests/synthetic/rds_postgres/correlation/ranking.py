from __future__ import annotations

from tests.synthetic.rds_postgres.correlation.models import UpstreamCandidate


def rank_upstream_candidates(
    candidates: list[UpstreamCandidate],
    *,
    limit: int | None = None,
) -> list[UpstreamCandidate]:
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.confidence,
            candidate.tier,
            candidate.name,
        ),
    )
    return ranked[:limit] if limit is not None else ranked
