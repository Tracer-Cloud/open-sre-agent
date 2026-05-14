"""Deterministic top-1 runbook retrieval.

Pure scoring — no disk I/O, no LLM calls. The caller is responsible for
loading the candidate runbooks (see ``app.runbooks.store.load_all``) and for
producing keyword/service inputs from the current alert state.
"""

from __future__ import annotations

from app.runbooks.store import Runbook


def _score(
    runbook: Runbook,
    keyword_set: frozenset[str],
    service: str | None,
    pipeline_name: str | None,
) -> int:
    """Score a single runbook against the current alert.

    +2 when ``runbook.service`` matches the alert service or pipeline name.
    +1 for each shared trigger keyword.
    """
    service_score = 0
    if runbook.service:
        rb_service = runbook.service.lower()
        if (service and rb_service == service.lower()) or (
            pipeline_name and rb_service == pipeline_name.lower()
        ):
            service_score = 2

    keyword_overlap = len(keyword_set & set(runbook.triggers))
    return service_score + keyword_overlap


def retrieve_matching_runbook(
    runbooks: list[Runbook],
    keywords: list[str],
    service: str | None,
    pipeline_name: str | None,
) -> Runbook | None:
    """Return the top-1 runbook by score, or ``None`` when nothing matches.

    Ties broken by slug (sorted ascending) for deterministic output.
    """
    if not runbooks:
        return None

    keyword_set = frozenset(k.lower() for k in keywords if k)
    best: tuple[int, str] | None = None
    winner: Runbook | None = None

    for runbook in runbooks:
        score = _score(runbook, keyword_set, service, pipeline_name)
        if score <= 0:
            continue
        candidate = (score, runbook.slug)
        # Higher score wins; on tie, lexicographically smaller slug wins.
        if (
            best is None
            or candidate[0] > best[0]
            or (candidate[0] == best[0] and candidate[1] < best[1])
        ):
            best = candidate
            winner = runbook

    return winner
