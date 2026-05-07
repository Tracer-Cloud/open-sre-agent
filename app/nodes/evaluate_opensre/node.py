"""LLM judge node — runs after diagnosis when ``opensre_evaluate`` is set."""

from __future__ import annotations

import logging
import time
from typing import Any, cast

from langsmith import traceable

from app.analytics.cli import (
    capture_eval_process_completed,
    capture_eval_process_failed,
    capture_eval_process_parse_failed,
    capture_eval_process_skipped,
    capture_eval_process_started,
)
from app.output import debug_print, get_tracker
from app.state import InvestigationState

logger = logging.getLogger(__name__)


@traceable(name="node_opensre_llm_eval")
def node_opensre_llm_eval(state: InvestigationState) -> dict[str, Any]:
    """Score the investigation against ``opensre_eval_rubric`` (dataset scoring_points)."""
    tracker = get_tracker()
    tracker.start("opensre_llm_eval", "Evaluating vs OpenRCA rubric")
    started_at = time.perf_counter()

    rubric = (state.get("opensre_eval_rubric") or "").strip()
    capture_eval_process_started(rubric=rubric, mode="opensre_llm_judge")
    if not rubric:
        debug_print("OpenSRE LLM eval skipped: no scoring_points on alert")
        capture_eval_process_skipped(reason="missing_rubric", mode="opensre_llm_judge")
        tracker.complete("opensre_llm_eval", fields_updated=[])
        return {
            "opensre_llm_eval": {
                "skipped": True,
                "reason": "no scoring_points in alert payload",
            }
        }

    try:
        from app.integrations.opensre.llm_eval_judge import run_opensre_llm_judge

        result = run_opensre_llm_judge(state=cast(dict[str, Any], state), rubric=rubric)
        if not isinstance(result, dict):
            raise TypeError("judge result must be a JSON object")
        score_0_100 = int(result.get("score_0_100", 0))
        overall_pass = bool(result.get("overall_pass", False))
        rubric_items = result.get("rubric_items")
        rubric_item_count = len(rubric_items) if isinstance(rubric_items, list) else 0
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        capture_eval_process_completed(
            duration_ms=duration_ms,
            overall_pass=overall_pass,
            score_0_100=max(0, min(100, score_0_100)),
            rubric_item_count=rubric_item_count,
            mode="opensre_llm_judge",
        )
    except Exception as exc:
        logger.exception("OpenSRE LLM eval failed")
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        if isinstance(exc, ValueError):
            capture_eval_process_parse_failed(
                failure_type=type(exc).__name__,
                mode="opensre_llm_judge",
            )
            failure_stage = "parse_response"
        else:
            failure_stage = "invoke_judge"
        capture_eval_process_failed(
            duration_ms=duration_ms,
            failure_stage=failure_stage,
            failure_type=type(exc).__name__,
            mode="opensre_llm_judge",
        )
        tracker.complete("opensre_llm_eval", fields_updated=[])
        return {"opensre_llm_eval": {"error": str(exc)}}

    tracker.complete("opensre_llm_eval", fields_updated=["opensre_llm_eval"])
    return {"opensre_llm_eval": result}
