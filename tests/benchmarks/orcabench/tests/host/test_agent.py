from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("harbor", reason="Harbor is supplied by the ORCA-Bench environment")

from harbor.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
from harbor.models.agent.context import AgentContext

from tests.benchmarks.orcabench.artifacts import RunSummary
from tests.benchmarks.orcabench.host.agent import OpenSRENativeAgent, OpenSRERunnerError
from tests.benchmarks.orcabench.host.validation import _harbor_check
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


def test_post_run_summary_populates_harbor_context(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    agent = OpenSRENativeAgent(
        logs_dir=logs_dir,
        model_name="gradient_ai/openai-gpt-5.5",
        benchmark_config_path=_config_path(),
        bundle_path=create_bundle(tmp_path),
    )
    summary_dir = logs_dir / "opensre-orca"
    summary_dir.mkdir(parents=True)
    summary = RunSummary(
        llm_calls=8,
        input_tokens=1200,
        output_tokens=300,
        report_sha256="a" * 64,
    )
    (summary_dir / "summary.json").write_text(summary.model_dump_json(), encoding="utf-8")
    context = AgentContext()

    agent.populate_context_post_run(context)

    assert context.n_input_tokens == 1200
    assert context.n_output_tokens == 300
    assert context.metadata is not None
    assert context.metadata["llm_calls"] == 8
    assert context.metadata["report_sha256"] == "a" * 64


def test_missing_post_run_summary_is_nonfatal(tmp_path: Path) -> None:
    agent = OpenSRENativeAgent(
        logs_dir=tmp_path / "logs",
        model_name="gradient_ai/openai-gpt-5.5",
        benchmark_config_path=_config_path(),
        bundle_path=create_bundle(tmp_path),
    )
    context = AgentContext()

    agent.populate_context_post_run(context)

    assert context.n_input_tokens is None
    assert context.metadata is not None
    assert context.metadata["mode"] == "native"


def test_invalid_post_run_summary_is_nonfatal(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    agent = OpenSRENativeAgent(
        logs_dir=logs_dir,
        model_name="gradient_ai/openai-gpt-5.5",
        benchmark_config_path=_config_path(),
        bundle_path=create_bundle(tmp_path),
    )
    summary_dir = logs_dir / "opensre-orca"
    summary_dir.mkdir(parents=True)
    (summary_dir / "summary.json").write_text(
        '{"input_tokens":"not-a-count"}', encoding="utf-8"
    )
    context = AgentContext()

    agent.populate_context_post_run(context)

    assert context.n_input_tokens is None
    assert context.metadata is not None
    assert "llm_calls" not in context.metadata


def test_runner_nonzero_escapes_harbor_installed_agent_verification_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    agent = OpenSRENativeAgent(
        logs_dir=logs_dir,
        model_name="gradient_ai/openai-gpt-5.5",
        benchmark_config_path=_config_path(),
        bundle_path=create_bundle(tmp_path),
    )
    exec_as_agent = AsyncMock(
        side_effect=NonZeroAgentExitCodeError("native runner exited 1")
    )
    monkeypatch.setattr(agent, "exec_as_agent", exec_as_agent)
    environment = SimpleNamespace(upload_file=AsyncMock())

    with pytest.raises(OpenSRERunnerError, match="benchmark-scorable"):
        asyncio.run(
            agent.run(
                "investigate",
                environment,  # type: ignore[arg-type]
                AgentContext(),
            )
        )

    environment.upload_file.assert_awaited_once()


def test_harbor_validation_recognizes_orca_checksum_patch() -> None:
    result = _harbor_check()

    assert result.ok, result.detail
