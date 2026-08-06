"""Native runtime environment configuration and bounded readiness waiting."""

from __future__ import annotations

import os
import time
from pathlib import Path

from tests.benchmarks.orcabench.config import ModelSettings, RunnerSettings


def wait_for_path(path: Path, timeout_seconds: int) -> None:
    """Wait for one required environment marker using a monotonic deadline."""
    deadline = time.monotonic() + timeout_seconds
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"ORCA environment did not become ready: {path}")
        time.sleep(0.25)


def native_environment_values(model: ModelSettings) -> dict[str, str]:
    """Build OpenSRE's provider-specific, secret-free runtime environment."""
    model_prefix = model.route.environment_prefix
    return {
        "LLM_PROVIDER": model.provider,
        "OPENSRE_LLM_TRANSPORT": model.transport,
        f"{model_prefix}_REASONING_MODEL": model.opensre_model,
        f"{model_prefix}_CLASSIFICATION_MODEL": model.opensre_model,
        f"{model_prefix}_TOOLCALL_MODEL": model.opensre_model,
        "OPENSRE_REASONING_EFFORT": model.reasoning_effort,
        "OPENSRE_MEMORY_DISABLED": "1",
        "OPENSRE_MEMORY_AUTOEXTRACT_DISABLED": "1",
        "OPENSRE_NO_TELEMETRY": "1",
    }


def configure_native_environment(settings: RunnerSettings) -> None:
    """Apply the explicit native OpenSRE route before importing LLM clients."""
    model = settings.benchmark.model
    os.environ.update(native_environment_values(model))

    for name in model.required_environment_names:
        if not os.environ.get(name, "").strip():
            raise RuntimeError(f"{name} is required for the native ORCA run")
