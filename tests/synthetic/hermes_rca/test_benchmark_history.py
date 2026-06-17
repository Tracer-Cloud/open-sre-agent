from __future__ import annotations

import json

from tests.synthetic.hermes_rca import run_suite


def test_write_history_snapshot_creates_valid_payload(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(run_suite, "HISTORY_DIR", tmp_path)

    results = [
        {
            "scenario_id": "050-surface-sprawl-unknown-adapter",
            "status": "pass",
            "mode": "offline",
        }
    ]

    path = run_suite._write_history_snapshot(results)

    assert path.exists()
    assert path.parent == tmp_path

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["suite"] == "hermes_rca"
    assert payload["total"] == 1
    assert payload["passed"] == 1
    assert payload["failed"] == 0
    assert payload["pass_rate"] == 1.0
    assert payload["results"][0]["scenario_id"] == "050-surface-sprawl-unknown-adapter"
