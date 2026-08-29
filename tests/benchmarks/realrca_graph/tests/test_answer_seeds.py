from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.answer_seeds import (
    load_answer_trace_seed_map,
    trace_ids_from_answer,
)
from tests.benchmarks.realrca_graph.models import CandidateAnswer


def test_trace_ids_from_answer_preserves_field_then_text_order() -> None:
    answer = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "Trace 0b13be6117833251722073237d0c28 shows timeout; "
            "Trace 0b51929a17833249398401293d0c51 reproduces it."
        ),
        "0b13be6117833251722073237d0c28",
    )

    assert trace_ids_from_answer(answer) == [
        "0b13be6117833251722073237d0c28",
        "0b51929a17833249398401293d0c51",
    ]


def test_trace_ids_from_answer_ignores_uuid_case_ids_and_invalid_placeholders() -> None:
    answer = CandidateAnswer(
        "candidate",
        "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d89",
        "case 01a0330f-2efc-7f72-bbd5-a3c1d5dc1d89 and trace dma-generated",
        "fallback",
    )

    assert trace_ids_from_answer(answer) == []


def test_load_answer_trace_seed_map_merges_multiple_visible_result_files(tmp_path) -> None:
    first = tmp_path / "first.json"
    first.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": "case-1",
                        "diagnosis_output": "Trace 0b51929a17833249398401293d0c51 reproduces it.",
                        "trace_id": "0b13be6117833251722073237d0c28",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    second = tmp_path / "second.json"
    second.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": "case-1",
                        "diagnosis_output": "Trace 210841eb17826923209095753da24b is unrelated.",
                        "trace_id": "0b13be6117833251722073237d0c28",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert load_answer_trace_seed_map([first, second]) == {
        "case-1": [
            "0b13be6117833251722073237d0c28",
            "0b51929a17833249398401293d0c51",
            "210841eb17826923209095753da24b",
        ]
    }
