from __future__ import annotations

from tests.benchmarks._framework.adapters import BenchmarkAdapter
from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter
from tests.benchmarks.openrca_scenarios.adapter import OpenRCAScenariosAdapter


def available_adapters() -> dict[str, type[BenchmarkAdapter]]:
    return {
        "cloudopsbench": CloudOpsBenchAdapter,
        "openrca_scenarios": OpenRCAScenariosAdapter,
    }


def create_adapter(name: str) -> BenchmarkAdapter:
    adapters = available_adapters()
    try:
        return adapters[name]()
    except KeyError as exc:
        names = ", ".join(sorted(adapters))
        raise ValueError(f"Unknown benchmark adapter '{name}'. Available: {names}") from exc
