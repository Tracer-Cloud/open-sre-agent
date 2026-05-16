from __future__ import annotations

import pytest

from tests.benchmarks._framework.config import benchmark_config_from_dict


def test_benchmark_config_defaults_parallel_workers() -> None:
    config = benchmark_config_from_dict({"benchmark": "cloudopsbench"})

    assert config.benchmark == "cloudopsbench"
    assert config.modes == ("opensre+llm",)
    assert config.workers >= 1
    assert "json" in config.report_formats


def test_benchmark_config_rejects_unknown_modes() -> None:
    with pytest.raises(ValueError, match="unsupported modes"):
        benchmark_config_from_dict({"benchmark": "cloudopsbench", "modes": ["unknown"]})
