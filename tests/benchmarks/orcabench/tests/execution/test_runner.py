from __future__ import annotations

from typing import Any

from tests.benchmarks.orcabench.execution.runner import _write_smoke_telemetry_probe


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
