"""Optional OpenSRE LLM evaluation hook for delivered reports."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_optional_opensre_evaluation(state: dict[str, Any]) -> dict[str, Any]:
    """Run the OpenSRE LLM judge when requested and return state updates."""
    if not state.get("opensre_evaluate"):
        return {}

    rubric_value = state.get("opensre_eval_rubric")
    if not (isinstance(rubric_value, str) and rubric_value.strip()):
        return {
            "opensre_llm_eval": {
                "skipped": True,
                "reason": "opensre_eval_rubric missing or invalid; expected non-empty string",
            }
        }

    from app.integrations.opensre.llm_eval_judge import run_opensre_llm_judge

    try:
        judge_result = run_opensre_llm_judge(
            state=state,
            rubric=rubric_value,
        )
    except Exception as exc:
        logger.exception("LLM judge failed: %s", exc)
        return {
            "opensre_llm_eval": {
                "skipped": True,
                "reason": f"Judge run failed: {exc}",
            }
        }

    return {"opensre_llm_eval": judge_result}
