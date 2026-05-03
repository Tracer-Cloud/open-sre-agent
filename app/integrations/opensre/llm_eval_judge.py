"""LLM judge: compare investigation conclusions to OpenRCA ``scoring_points`` rubric."""

from __future__ import annotations

import json
import re
from typing import Any, cast


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _evidence_excerpt(evidence: dict[str, Any], max_total: int) -> str:
    if not evidence:
        return "(no evidence dict)"
    parts: list[str] = []
    budget = max_total
    for key in sorted(evidence.keys()):
        raw = evidence[key]
        if raw is None:
            continue
        try:
            blob = json.dumps(raw, indent=2, default=str)
        except TypeError:
            blob = str(raw)
        chunk = f"### {key}\n{_truncate(blob, min(8000, budget))}"
        parts.append(chunk)
        budget -= len(chunk)
        if budget <= 0:
            break
    return "\n\n".join(parts) if parts else "(empty evidence)"


def _claims_lines(claims: list[Any], key: str = "claim") -> str:
    lines: list[str] = []
    for item in claims:
        if isinstance(item, dict):
            lines.append(str(item.get(key, item)))
        else:
            lines.append(str(item))
    return "\n".join(f"- {line}" for line in lines) if lines else "(none)"


def extract_judge_json_from_response(text: str) -> dict[str, Any]:
    text = text.strip()

    fences = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.DOTALL)
    if fences:
        for fence_candidate in reversed(fences):
            fence_candidate = fence_candidate.strip()
            try:
                parsed_fence = json.loads(fence_candidate)
            except json.JSONDecodeError:
                continue

            if isinstance(parsed_fence, dict):
                return cast(dict[str, Any], parsed_fence)

            if isinstance(parsed_fence, list):
                continue

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = None

    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if isinstance(raw, list):
        msg = "Judge response JSON must be an object"
        raise ValueError(msg)

    obj_start = text.find("{")
    obj_end = text.rfind("}")
    arr_start = text.find("[")
    arr_end = text.rfind("]")

    has_obj = obj_start != -1 and obj_end != -1 and obj_end > obj_start
    has_arr = arr_start != -1 and arr_end != -1 and arr_end > arr_start

    # If an array span exists and fully contains the object span,
    # the top-level value is an array — reject it.
    if has_arr and has_obj and arr_start < obj_start and arr_end > obj_end:
        try:
            arr_candidate = json.loads(text[arr_start : arr_end + 1])
        except json.JSONDecodeError:
            arr_candidate = None

        if isinstance(arr_candidate, list):
            msg = "Judge response JSON must be an object"
            raise ValueError(msg)

        if arr_candidate is None:
            for i, ch in enumerate(text):
                if ch == "[" and i < obj_start:
                    inner_arr_end = text.rfind("]", obj_end)
                    if inner_arr_end == -1:
                        continue
                    try:
                        inner = json.loads(text[i : inner_arr_end + 1])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(inner, list):
                        msg = "Judge response JSON must be an object"
                        raise ValueError(msg)

    if not has_obj:
        msg = "Judge response did not contain a JSON object"
        raise ValueError(msg)

    raw = json.loads(text[obj_start : obj_end + 1])
    if not isinstance(raw, dict):
        msg = "Judge response JSON must be an object"
        raise ValueError(msg)

    return cast(dict[str, Any], raw)


def build_opensre_judge_prompt(*, rubric: str, state: dict[str, Any]) -> str:
    root_cause = str(state.get("root_cause") or "")
    category = str(state.get("root_cause_category") or "")
    problem = str(state.get("problem_md") or "")
    val_claims = state.get("validated_claims") or []
    non_val = state.get("non_validated_claims") or []
    if not isinstance(val_claims, list):
        val_claims = []
    if not isinstance(non_val, list):
        non_val = []
    _raw_evidence = state.get("evidence")
    evidence: dict[str, Any] = _raw_evidence if isinstance(_raw_evidence, dict) else {}

    return f"""You are an expert evaluator for incident root-cause reports.

Your job: compare the AGENT CONCLUSIONS to the official RUBRIC (OpenRCA scoring_points).
The rubric is ground truth for grading — the agent did NOT see it during the run.

## RUBRIC (ground truth)
{rubric}

## AGENT CONCLUSIONS
ROOT_CAUSE_CATEGORY: {category}

ROOT_CAUSE:
{root_cause}

PROBLEM_SUMMARY (markdown excerpt):
{_truncate(problem, 6000)}

VALIDATED_CLAIMS:
{_claims_lines(val_claims)}

NON_VALIDATED_CLAIMS:
{_claims_lines(non_val)}

EVIDENCE_DIGEST (may be truncated):
{_evidence_excerpt(evidence, 24000)}

Respond with ONE JSON object only (no markdown), exactly this shape:
{{
  "overall_pass": <boolean>,
  "score_0_100": <integer 0-100>,
  "rubric_items": [
    {{
      "id": <string, short id you choose per rubric bullet or criterion>,
      "satisfied": <boolean>,
      "explanation": <string, one or two sentences>
    }}
  ],
  "summary": <string, 2-4 sentences on how well the investigation matches the rubric>
}}
"""


def run_opensre_llm_judge(*, state: dict[str, Any], rubric: str) -> dict[str, Any]:
    from app.config import LLMSettings
    from app.services import get_llm_for_reasoning

    LLMSettings.from_env()
    prompt = build_opensre_judge_prompt(rubric=rubric, state=state)
    llm = get_llm_for_reasoning()
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    if not isinstance(content, str):
        content = str(content)
    return extract_judge_json_from_response(content)
