"""Harbor custom agent that installs and runs native OpenSRE inside ORCA."""

from __future__ import annotations

import json
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, override

from harbor.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import ValidationError

from tests.benchmarks.orcabench.artifacts import RunSummary
from tests.benchmarks.orcabench.config import BenchmarkSettings, RunnerSettings
from tests.benchmarks.orcabench.host.bundle import validate_bundle
from tests.benchmarks.orcabench.host.pricing import calculate_orca_cost


class OpenSRERunnerError(RuntimeError):
    """The native runner failed before producing a benchmark-scorable result."""


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
        model_provider: str | None = None,
        tool_capability_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._benchmark_config_path = Path(benchmark_config_path).expanduser().resolve()
        self._bundle_path = Path(bundle_path).expanduser().resolve()
        configured_settings = BenchmarkSettings.from_yaml(
            self._benchmark_config_path
        ).with_tool_capability_mode_override(tool_capability_mode)
        self._build_manifest = validate_bundle(self._bundle_path)

        configured_model = configured_settings.model.harbor_model
        effective_model = model_name or configured_model
        if model_provider is not None:
            self._benchmark_settings = configured_settings.with_harbor_model_override(
                model_provider,
                effective_model,
            )
        elif effective_model != configured_model:
            raise ValueError(
                f"Harbor model {effective_model!r} does not match benchmark config "
                f"{configured_model!r}"
            )
        else:
            self._benchmark_settings = configured_settings
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
        try:
            await self.exec_as_agent(
                environment,
                command=(
                    f"{shlex.quote(python.as_posix())} "
                    "-m tests.benchmarks.orcabench.execution.runner "
                    f"--config {shlex.quote(self._REMOTE_CONFIG.as_posix())} "
                    f"--instruction {shlex.quote(self._REMOTE_INSTRUCTION.as_posix())}"
                ),
            )
        except NonZeroAgentExitCodeError as exc:
            # Harbor's single-step runner verifies ordinary installed-agent nonzero
            # exits. Terminus provider errors escape its in-process agent instead,
            # skipping verification. Translate here so both agents produce the same
            # unscored trial semantics while Harbor still recovers our artifacts.
            raise OpenSRERunnerError(
                "Native OpenSRE failed before producing a benchmark-scorable result"
            ) from exc

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """Backfill Harbor metadata after it makes mounted logs host-readable."""
        metadata: dict[str, Any] = {
            "tool_capability_mode": self._benchmark_settings.tool_capability_mode,
            "profile": self._benchmark_settings.profile,
            "opensre_commit": self._build_manifest.opensre_commit,
        }
        context.metadata = metadata
        summary_path = self.logs_dir / "opensre-orca" / "summary.json"
        try:
            summary = RunSummary.model_validate_json(
                summary_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            self.logger.warning(
                "Could not read OpenSRE run summary %s: %s", summary_path, exc
            )
            return

        context.n_input_tokens = summary.input_tokens
        context.n_output_tokens = summary.output_tokens
        context.n_cache_tokens = summary.cache_read_tokens
        metadata.update(
            {
                "llm_calls": summary.llm_calls,
                "report_sha256": summary.report_sha256,
                "cache_creation_tokens": summary.cache_creation_tokens,
            }
        )
        try:
            usage_path = self.logs_dir / "opensre-orca" / "usage.jsonl"
            usage_events = [
                json.loads(line)
                for line in usage_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            context.cost_usd = calculate_orca_cost(
                self._benchmark_settings.model.harbor_model,
                usage_events=usage_events,
            )
        except Exception as exc:  # noqa: BLE001 - metadata must not fail a trial
            self.logger.warning("Could not calculate ORCA model cost: %s", exc)
            context.cost_usd = None

        metadata["cost_basis"] = (
            "ORCA per-call pricing with provider-reported cache usage"
            if context.cost_usd is not None
            else "unavailable for configured model"
        )
