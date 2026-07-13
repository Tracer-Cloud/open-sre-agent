"""Safe read-only runtime metadata for sessions and sandboxed agent tools.

Populated at session init so agents can answer introspection questions
(e.g. OpenSRE version) without shelling out. Subprocess remains blocked in
the Python execution sandbox; this is the preferred alternative.

Split by lifetime: session-static facts come from :func:`build_runtime_metadata`
(cached on ``SessionCore.runtime_metadata``); live facts (time, uptime, disk,
memory) come from :func:`capture_runtime_facts` at each prompt render or
sandbox call. Git build-marker reading lives in :mod:`.build_info`; host and
process probes live in :mod:`.host_facts`.
"""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
import time as _time
from typing import Any

from config.config import get_environment
from config.runtime_metadata.build_info import detect_build_info
from config.runtime_metadata.host_facts import (
    cloud_facts,
    disk_memory_facts,
    installed_tools,
    kubeconfig_path,
    local_tz_name,
    pod_hostname,
    python_version_string,
)
from config.version import get_opensre_version

# Reserved key merged into ``execute_python_code`` inputs (never overwrite user keys).
RUNTIME_INPUTS_KEY = "opensre_runtime"

# The fact contract: every consumer (prompt renderer, sandbox tool schema,
# contract tests, smoke validators) derives its key lists from these tuples,
# so adding a fact is one probe + one tuple entry + one prompt producer.
STATIC_FACT_KEYS: tuple[str, ...] = (
    "opensre_version",
    "opensre_build",
    "runtime_env",
    "tz_name",
    "python_version",
    "pid",
    "ppid",
    "tools",
    "kubeconfig",
    "hostname",
    "scratchpad_dir",
    "cloud_provider",
    "cloud_region",
)

# Captured fresh per prompt render / sandbox call; never cached at bootstrap.
# Disk/memory keys are absent when psutil fails (degraded, not fatal).
LIVE_FACT_KEYS: tuple[str, ...] = (
    "now_iso",
    "uptime_seconds",
    "disk_used_percent",
    "disk_free_gb",
    "memory_used_percent",
    "memory_available_gb",
)

# Shell commands the agent must never reach for — every fact above (or the
# sandbox socket/pathlib guidance) replaces one of these. Rendered into both
# the assistant prompt guidance and the python-execution tool description.
# Do not add cloud-instance-metadata link-local addresses here or into prompts:
# naming them teaches the model the reflex we are removing.
BLOCKED_INTROSPECTION_COMMANDS: tuple[str, ...] = (
    "opensre --version",
    "python --version",
    "kubectl version",
    "which",
    "ps",
    "date",
    "uptime",
    "hostname",
    "ls",
    "df",
    "free",
    "top",
    "curl",
    "ping",
    "nslookup",
)

# Monotonic snapshot at import — anchor for uptime deltas that resist wall-clock skew.
_PROCESS_START_MONOTONIC = _time.monotonic()


def build_runtime_metadata() -> dict[str, Any]:
    """Session-lifetime read-only runtime facts.

    Keys are stable for prompts and sandbox ``inputs`` and safe to cache at
    session bootstrap (nothing here changes turn to turn):

    - ``opensre_version`` — package version via ``importlib.metadata``.
    - ``opensre_build`` — ``""`` in released wheels; ``dev, v0.1.YYYY.M.D @ SHA``
      in a git checkout so the LLM can quote the exact build in local dev.
    - ``runtime_env`` — ``OPENSRE_ENV`` env var, else the app environment name.
    - ``tz_name`` — local timezone name (rarely changes mid-session).
    - ``python_version`` — interpreter version from :data:`sys.version_info`.
    - ``pid`` / ``ppid`` — this process and its parent from :mod:`os`.
    - ``tools`` — probed tool paths (``kubectl``, ``helm``, ``docker``, ``git``, …).
    - ``kubeconfig`` — effective kubeconfig path (``KUBECONFIG`` or ``~/.kube/config``).
    - ``hostname`` — from ``/etc/hostname`` (the pod name in Kubernetes) or
      :func:`socket.gethostname`, never the ``hostname`` binary.
    - ``scratchpad_dir`` — the temp directory scripts may write to.
    - ``cloud_provider`` / ``cloud_region`` — deploy-time env vars
      (``CLOUD_PROVIDER`` / ``CLOUD_REGION``, AWS var fallback), never the
      instance metadata service (IMDS).

    The exact key set is :data:`STATIC_FACT_KEYS` (contract-tested). Live
    values that must NOT be cached (current time, uptime, disk, memory) come
    from :func:`capture_runtime_facts` at each render/sandbox call.
    """
    env_override = (os.environ.get("OPENSRE_ENV") or "").strip()
    return {
        "opensre_version": get_opensre_version(),
        "opensre_build": detect_build_info(),
        "runtime_env": env_override or get_environment().value,
        "tz_name": local_tz_name(),
        "python_version": python_version_string(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "tools": installed_tools(),
        "kubeconfig": kubeconfig_path(),
        "hostname": pod_hostname(),
        "scratchpad_dir": tempfile.gettempdir(),
        **cloud_facts(),
    }


def capture_runtime_facts(*, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Session metadata plus fresh live facts (time, uptime, disk, memory).

    Read once per prompt render or sandbox invocation so time doesn't lie. Pass
    ``metadata`` (typically ``session.runtime_metadata``) to avoid re-running
    the git+importlib probe every call.
    """
    facts = dict(metadata or build_runtime_metadata())
    facts["now_iso"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    facts["uptime_seconds"] = round(_time.monotonic() - _PROCESS_START_MONOTONIC, 3)
    facts.update(disk_memory_facts())
    return facts


def merge_runtime_into_inputs(
    inputs: dict[str, Any] | None,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy ``inputs`` and inject runtime facts under :data:`RUNTIME_INPUTS_KEY`.

    Never overwrites an existing ``opensre_runtime`` key supplied by the caller.
    Facts are captured live via :func:`capture_runtime_facts` so ``now_iso`` is
    fresh for each sandbox invocation.
    """
    merged: dict[str, Any] = dict(inputs or {})
    if RUNTIME_INPUTS_KEY not in merged:
        merged[RUNTIME_INPUTS_KEY] = capture_runtime_facts(metadata=metadata)
    return merged


__all__ = [
    "BLOCKED_INTROSPECTION_COMMANDS",
    "LIVE_FACT_KEYS",
    "RUNTIME_INPUTS_KEY",
    "STATIC_FACT_KEYS",
    "build_runtime_metadata",
    "capture_runtime_facts",
    "merge_runtime_into_inputs",
]
