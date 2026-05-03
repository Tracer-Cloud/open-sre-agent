from __future__ import annotations

import pytest

from app.integrations.opensre.llm_eval_judge import extract_judge_json_from_response


def test_extract_judge_json_from_clean_json() -> None:
    out = extract_judge_json_from_response(
        '{"overall_pass": true, "score_0_100": 95, "rubric_items": [], "summary": "ok"}'
    )

    assert out["overall_pass"] is True
    assert out["score_0_100"] == 95


def test_extract_judge_json_from_markdown_fence() -> None:
    out = extract_judge_json_from_response(
        """```json
{"overall_pass": false, "score_0_100": 40, "rubric_items": [], "summary": "missed rubric"}
```"""
    )

    assert out["overall_pass"] is False
    assert out["score_0_100"] == 40


def test_extract_judge_json_with_extra_text() -> None:
    out = extract_judge_json_from_response(
        """Here is the evaluation result:

{"overall_pass": true, "score_0_100": 88, "rubric_items": [{"id": "rca", "satisfied": true, "explanation": "matches"}], "summary": "good"}

Thanks."""
    )

    assert out["overall_pass"] is True
    assert out["rubric_items"][0]["id"] == "rca"


def test_extract_judge_json_raises_when_no_json_object() -> None:
    with pytest.raises(ValueError, match="did not contain a JSON object"):
        extract_judge_json_from_response("The investigation looks good overall.")


def test_extract_judge_json_raises_for_json_array() -> None:
    with pytest.raises(ValueError, match="JSON must be an object"):
        extract_judge_json_from_response('[{"overall_pass": true}]')


def test_extract_judge_json_raises_for_invalid_json() -> None:
    with pytest.raises(ValueError):
        extract_judge_json_from_response('{"overall_pass": true, "score_0_100": 90,')


def test_extract_judge_json_raises_for_fenced_json_array() -> None:
    with pytest.raises(ValueError, match="JSON must be an object"):
        extract_judge_json_from_response(
            """```json
[{"overall_pass": true}]
```"""
        )


def test_extract_judge_json_raises_for_whitespace_json_array() -> None:
    with pytest.raises(ValueError, match="JSON must be an object"):
        extract_judge_json_from_response('   \n  [{"overall_pass": true}]')


def test_extract_judge_json_picks_last_valid_fence_with_multiple_fences() -> None:
    out = extract_judge_json_from_response(
        "Example shape:\n"
        "```json\n"
        '{"old": 1}\n'
        "```\n\n"
        "Actual answer:\n"
        "```json\n"
        '{"overall_pass": true, "score_0_100": 90, "rubric_items": [], "summary": "ok"}\n'
        "```"
    )

    assert out["overall_pass"] is True
    assert out["score_0_100"] == 90


def test_extract_judge_json_raises_for_prose_wrapped_array() -> None:
    with pytest.raises(ValueError, match="JSON must be an object"):
        extract_judge_json_from_response('Here is the result: [{"overall_pass": true}] Thanks')


def test_extract_judge_json_raises_for_info_prefixed_array() -> None:
    with pytest.raises(ValueError, match="JSON must be an object"):
        extract_judge_json_from_response('[INFO] result: [{"overall_pass": true}]')


def test_extract_judge_json_allows_log_prefix_with_nested_array() -> None:
    out = extract_judge_json_from_response(
        '[INFO] result: {"overall_pass": true, "score_0_100": 90, "rubric_items": [], "summary": "ok"}'
    )

    assert out["overall_pass"] is True
    assert out["score_0_100"] == 90
    assert out["rubric_items"] == []


def test_extract_judge_json_skips_array_fence_and_returns_earlier_dict() -> None:
    out = extract_judge_json_from_response(
        "Result:\n"
        "```json\n"
        '{"overall_pass": true, "score_0_100": 90, "rubric_items": [], "summary": "ok"}\n'
        "```\n\n"
        "Examples:\n"
        "```json\n"
        '["item1", "item2"]\n'
        "```"
    )
    assert out["overall_pass"] is True
    assert out["score_0_100"] == 90


def test_extract_judge_json_allows_bracketed_prose_around_object() -> None:
    out = extract_judge_json_from_response(
        '[INFO] here is the result: {"overall_pass": true, "score_0_100": 90, "rubric_items": [], "summary": "ok"} [end]'
    )
    assert out["overall_pass"] is True
    assert out["score_0_100"] == 90


def test_extract_judge_json_prefers_inline_dict_over_array_fence() -> None:
    out = extract_judge_json_from_response(
        "For reference:\n"
        "```json\n"
        '["item1", "item2"]\n'
        "```\n\n"
        "Actual result:\n"
        '{"overall_pass": true, "score_0_100": 90, "rubric_items": [], "summary": "ok"}'
    )

    assert out["overall_pass"] is True
    assert out["score_0_100"] == 90
