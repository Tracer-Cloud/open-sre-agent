"""Harbor custom agent that installs and runs native OpenSRE inside ORCA."""

from __future__ import annotations

import json
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, override

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from tests.benchmarks.orcabench.config import BenchmarkSettings, RunnerSettings
from tests.benchmarks.orcabench.host.bundle import validate_bundle
from tests.benchmarks.orcabench.host.pricing import calculate_orca_cost


class OpenSRENativeAgent(BaseInstalledAgent):
    """Install a pinned OpenSRE bundle and invoke its native ORCA runner."""

    _INTEGRATION_VERSION = "1"
    _REMOTE_ROOT = PurePosixPath("/installed-agent/opensre-orca")
    _REMOTE_BUNDLE = _REMOTE_ROOT / "bundle"
    _REMOTE_VENV = _REMOTE_ROOT / "venv"
    _REMOTE_CONFIG = _REMOTE_ROOT / "runner-config.json"
    _REMOTE_INSTRUCTION = _REMOTE_ROOT / "instruction.txt"
    _REMOTE_ARTIFACTS = PurePosixPath("/logs/agent/opensre-orca")

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        benchmark_config_path: str | Path,
        bundle_path: str | Path,
        **kwargs: Any,
    ) -> None:
        self._benchmark_config_path = Path(benchmark_config_path).expanduser().resolve()
        self._bundle_path = Path(bundle_path).expanduser().resolve()
        self._benchmark_settings = BenchmarkSettings.from_yaml(self._benchmark_config_path)
        self._build_manifest = validate_bundle(self._bundle_path)

        configured_model = self._benchmark_settings.model.harbor_model
        effective_model = model_name or configured_model
        if effective_model != configured_model:
            raise ValueError(
                f"Harbor model {effective_model!r} does not match benchmark config "
                f"{configured_model!r}"
            )
        super().__init__(
            logs_dir=logs_dir,
            model_name=effective_model,
            version=self._INTEGRATION_VERSION,
            **kwargs,
        )

    @staticmethod
    @override
    def name() -> str:
        return "opensre-native"

    def _runner_settings(self) -> RunnerSettings:
        return RunnerSettings(
            benchmark=self._benchmark_settings,
            build=self._build_manifest,
            integration_version=self._INTEGRATION_VERSION,
        )

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        local_config = self.logs_dir / "setup" / "runner-config.json"
        local_config.write_text(self._runner_settings().to_json(), encoding="utf-8")

        await self.exec_as_root(
            environment,
            command=(
                f"rm -rf {shlex.quote(self._REMOTE_ROOT.as_posix())} && "
                f"install -d -m 0755 {shlex.quote(self._REMOTE_ROOT.as_posix())} && "
                f"install -d -m 0777 {shlex.quote(self._REMOTE_ARTIFACTS.as_posix())}"
            ),
        )
        await environment.upload_dir(self._bundle_path, self._REMOTE_BUNDLE.as_posix())
        await environment.upload_file(local_config, self._REMOTE_CONFIG.as_posix())

        remote_wheelhouse = self._REMOTE_BUNDLE / "wheelhouse"
        remote_wheel = self._REMOTE_BUNDLE / self._build_manifest.opensre_wheel
        await self.exec_as_root(
            environment,
            command=(
                "python3 -m venv "
                f"{shlex.quote(self._REMOTE_VENV.as_posix())} && "
                f"{shlex.quote((self._REMOTE_VENV / 'bin/pip').as_posix())} install "
                "--disable-pip-version-check --no-index "
                f"--find-links {shlex.quote(remote_wheelhouse.as_posix())} "
                f"{shlex.quote(remote_wheel.as_posix())}"
            ),
        )

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        instruction_path = self.logs_dir / "instruction.txt"
        instruction_path.write_bytes(instruction.encode("utf-8"))
        await environment.upload_file(instruction_path, self._REMOTE_INSTRUCTION.as_posix())

        python = self._REMOTE_VENV / "bin/python"
        await self.exec_as_agent(
            environment,
            command=(
                f"{shlex.quote(python.as_posix())} "
                "-m tests.benchmarks.orcabench.execution.runner "
                f"--config {shlex.quote(self._REMOTE_CONFIG.as_posix())} "
                f"--instruction {shlex.quote(self._REMOTE_INSTRUCTION.as_posix())}"
            ),
        )
        self._populate_context(context)

    def _populate_context(self, context: AgentContext) -> None:
        """Copy usage totals from the mounted artifact summary into Harbor metadata."""
        summary_path = self.logs_dir / "opensre-orca" / "summary.json"
        if not summary_path.is_file():
            return
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        input_tokens = int(summary.get("input_tokens", 0))
        output_tokens = int(summary.get("output_tokens", 0))
        context.n_input_tokens = input_tokens
        context.n_output_tokens = output_tokens

        context.cost_usd = calculate_orca_cost(
            self._benchmark_settings.model.harbor_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        context.metadata = {
            "mode": "native",
            "llm_calls": int(summary.get("llm_calls", 0)),
            "report_sha256": summary.get("report_sha256"),
            "opensre_commit": self._build_manifest.opensre_commit,
            "cost_basis": "ORCA pricing; usage hook does not expose cache tokens",
        }
