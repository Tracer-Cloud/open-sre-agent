from __future__ import annotations

from pathlib import Path

from app.runbooks.retrieval import retrieve_matching_runbook
from app.runbooks.store import Runbook


def _rb(
    slug: str,
    triggers: tuple[str, ...] = (),
    service: str | None = None,
) -> Runbook:
    return Runbook(
        slug=slug,
        title=slug,
        service=service,
        category=None,
        triggers=triggers,
        body="",
        path=Path(f"/tmp/{slug}.md"),
    )


def test_returns_none_when_no_matches() -> None:
    runbooks = [_rb("a", triggers=("oom",), service="foo")]

    result = retrieve_matching_runbook(
        runbooks=runbooks,
        keywords=["cpu"],
        service="bar",
        pipeline_name="baz",
    )

    assert result is None


def test_service_match_alone_wins() -> None:
    runbooks = [_rb("payments-oom", triggers=("oom",), service="payments-api")]

    result = retrieve_matching_runbook(
        runbooks=runbooks,
        keywords=[],
        service="payments-api",
        pipeline_name=None,
    )

    assert result is not None
    assert result.slug == "payments-oom"


def test_pipeline_name_also_counts_as_service_match() -> None:
    runbooks = [_rb("p", triggers=("oom",), service="pipeline-x")]

    result = retrieve_matching_runbook(
        runbooks=runbooks,
        keywords=[],
        service=None,
        pipeline_name="pipeline-x",
    )

    assert result is not None
    assert result.slug == "p"


def test_service_match_outranks_keyword_only_match() -> None:
    keyword_only = _rb("keyword-only", triggers=("oom", "memory"))
    service_match = _rb("service-match", triggers=("oom",), service="payments-api")

    result = retrieve_matching_runbook(
        runbooks=[keyword_only, service_match],
        keywords=["oom"],
        service="payments-api",
        pipeline_name=None,
    )

    assert result is not None
    assert result.slug == "service-match"


def test_ties_broken_by_slug() -> None:
    a = _rb("a-runbook", triggers=("oom",))
    b = _rb("b-runbook", triggers=("oom",))

    result = retrieve_matching_runbook(
        runbooks=[b, a],
        keywords=["oom"],
        service=None,
        pipeline_name=None,
    )

    assert result is not None
    assert result.slug == "a-runbook"


def test_multi_word_trigger_matches_when_all_tokens_present() -> None:
    runbooks = [_rb("x", triggers=("exit code 137",))]

    result = retrieve_matching_runbook(
        runbooks=runbooks,
        keywords=["exit", "code", "137"],
        service=None,
        pipeline_name=None,
    )

    assert result is not None
    assert result.slug == "x"


def test_multi_word_trigger_no_match_when_partial_tokens() -> None:
    runbooks = [_rb("x", triggers=("exit code 137",))]

    result = retrieve_matching_runbook(
        runbooks=runbooks,
        keywords=["exit", "code"],
        service=None,
        pipeline_name=None,
    )

    assert result is None


def test_keyword_case_insensitive() -> None:
    runbooks = [_rb("x", triggers=("oom",))]

    result = retrieve_matching_runbook(
        runbooks=runbooks,
        keywords=["OOM"],
        service=None,
        pipeline_name=None,
    )

    assert result is not None
    assert result.slug == "x"
