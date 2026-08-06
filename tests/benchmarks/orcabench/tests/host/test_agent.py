from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("harbor", reason="Harbor is supplied by the ORCA-Bench environment")

from harbor.agents.installed.base import BaseInstalledAgent

from tests.benchmarks.orcabench.host.agent import OpenSRENativeAgent
from tests.benchmarks.orcabench.tests._support import create_bundle


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs/native_one_task.yml"


def test_custom_agent_is_real_harbor_installed_agent(tmp_path: Path) -> None:
    agent = OpenSRENativeAgent(
        logs_dir=tmp_path / "logs",
        model_name="gradient_ai/openai-gpt-5.5",
        benchmark_config_path=_config_path(),
        bundle_path=create_bundle(tmp_path),
    )

    assert isinstance(agent, BaseInstalledAgent)
    assert agent.name() == "opensre-native"
    assert agent.version() == "1"


def test_custom_agent_rejects_model_drift(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not match benchmark config"):
        OpenSRENativeAgent(
            logs_dir=tmp_path / "logs",
            model_name="gradient_ai/openai-gpt-5.6",
            benchmark_config_path=_config_path(),
            bundle_path=create_bundle(tmp_path),
        )
