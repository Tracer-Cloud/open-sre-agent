from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.benchmarks.orcabench.artifacts.redaction import REDACTED, Redactor
from tests.benchmarks.orcabench.artifacts.writer import ArtifactWriter


def test_artifact_writer_redacts_nested_keys_and_known_values(tmp_path: Path) -> None:
    writer = ArtifactWriter(tmp_path, Redactor(("known-secret-value",)))
    destination = writer.write_json(
        "state.json",
        {
            "resolved_integrations": {
                "grafana": {
                    "api_key": "token-value",
                    "access_token": "token-value",
                    "password": "admin",
                    "endpoint": "http://grafana",
                }
            },
            "input_tokens": 123,
            "output_tokens": 45,
            "error": "request contained known-secret-value",
        },
    )

    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert saved["resolved_integrations"]["grafana"]["api_key"] == REDACTED
    assert saved["resolved_integrations"]["grafana"]["access_token"] == REDACTED
    assert saved["resolved_integrations"]["grafana"]["password"] == REDACTED
    assert saved["resolved_integrations"]["grafana"]["endpoint"] == "http://grafana"
    assert saved["error"] == f"request contained {REDACTED}"
    assert saved["input_tokens"] == 123
    assert saved["output_tokens"] == 45


def test_artifact_writer_rejects_paths_outside_its_root(tmp_path: Path) -> None:
    writer = ArtifactWriter(tmp_path, Redactor())

    with pytest.raises(ValueError, match="single filename"):
        writer.write_text("../outside.txt", "unsafe")
