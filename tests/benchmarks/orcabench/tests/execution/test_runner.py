from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.benchmarks.orcabench.execution.runner import (
    _write_smoke_source_probe,
    _write_smoke_telemetry_probe,
)


class _Backend:
    def __init__(self) -> None:
        self.calls = 0

    def probe(self) -> dict[str, int]:
        self.calls += 1
        return {"trace_count": 1}


class _Writer:
    def __init__(self) -> None:
        self.writes: list[tuple[str, Any]] = []

    def write_json(self, path: str, payload: Any) -> None:
        self.writes.append((path, payload))


def test_smoke_profile_writes_telemetry_probe() -> None:
    backend = _Backend()
    writer = _Writer()

    _write_smoke_telemetry_probe(
        "smoke",
        {"grafana": {"_backend": backend}},
        writer,  # type: ignore[arg-type]
    )

    assert backend.calls == 1
    assert writer.writes == [("telemetry-probe.json", {"trace_count": 1})]


def test_benchmark_profile_does_not_probe_telemetry() -> None:
    backend = _Backend()
    writer = _Writer()

    _write_smoke_telemetry_probe(
        "benchmark",
        {"grafana": {"_backend": backend}},
        writer,  # type: ignore[arg-type]
    )

    assert backend.calls == 0
    assert writer.writes == []


def test_smoke_profile_probes_source_without_persisting_contents(tmp_path: Path) -> None:
    root = tmp_path / "opentelemetry-demo"
    root.mkdir()
    (root / "docker-compose.yml").write_text(
        "services:\n  checkout:\n    image: checkout\n",
        encoding="utf-8",
    )
    writer = _Writer()

    _write_smoke_source_probe(
        "smoke",
        {"local_source": {"root_path": str(root), "connection_verified": True}},
        writer,  # type: ignore[arg-type]
    )

    assert writer.writes == [
        (
            "source-probe.json",
            {
                "list_available": True,
                "entry_count": 1,
                "search_available": True,
                "match_count": 1,
                "read_available": True,
                "read_nonempty": True,
            },
        )
    ]
    assert "services:" not in str(writer.writes)


def test_benchmark_profile_does_not_probe_source(tmp_path: Path) -> None:
    root = tmp_path / "opentelemetry-demo"
    root.mkdir()
    writer = _Writer()

    _write_smoke_source_probe(
        "benchmark",
        {"local_source": {"root_path": str(root), "connection_verified": True}},
        writer,  # type: ignore[arg-type]
    )

    assert writer.writes == []
