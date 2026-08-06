"""Native runtime environment configuration and bounded readiness waiting."""

from __future__ import annotations

import os
import time
from pathlib import Path

from tests.benchmarks.orcabench.config import RunnerSettings


def wait_for_path(path: Path, timeout_seconds: int) -> None:
    """Wait for one required environment marker using a monotonic deadline."""
    deadline = time.monotonic() + timeout_seconds
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"ORCA environment did not become ready: {path}")
        time.sleep(0.25)


def configure_native_environment(settings: RunnerSettings) -> None:
    """Apply the explicit native OpenSRE route before importing LLM clients."""
    model = settings.benchmark.model
    values = {
        "LLM_PROVIDER": model.provider,
        "OPENSRE_LLM_TRANSPORT": model.transport,
        "OPENAI_REASONING_MODEL": model.opensre_model,
        "OPENAI_CLASSIFICATION_MODEL": model.opensre_model,
        "OPENAI_TOOLCALL_MODEL": model.opensre_model,
        "OPENSRE_REASONING_EFFORT": model.reasoning_effort,
        "OPENSRE_MEMORY_DISABLED": "1",
        "OPENSRE_MEMORY_AUTOEXTRACT_DISABLED": "1",
        "OPENSRE_NO_TELEMETRY": "1",
    }
    os.environ.update(values)

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY is required for the native ORCA run")
    if not os.environ.get("OPENAI_BASE_URL", "").strip():
        raise RuntimeError(
            "OPENAI_BASE_URL is required because the pinned ORCA model uses "
            "Gradient AI's OpenAI-compatible endpoint"
        )
