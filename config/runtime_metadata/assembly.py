"""Assemble runtime facts from the probes, build marker, and contract.

The orchestration tier: :func:`build_runtime_metadata` composes the
session-static facts, :func:`capture_runtime_facts` adds the live slots, and
:func:`merge_runtime_into_inputs` injects them into sandbox ``inputs``. Leaf
concerns live in :mod:`.probes`, :mod:`.build_info`, and :mod:`.contract`.
"""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
import time as _time
from typing import Any

from config.config import get_environment
from config.runtime_metadata.build_info import detect_build_info
from config.runtime_metadata.contract import RUNTIME_INPUTS_KEY
from config.runtime_metadata.probes import (
    cloud_facts,
    disk_memory_facts,
    installed_tools,
    kubeconfig_path,
    local_tz_name,
    pod_hostname,
    python_version_string,
)
from config.version import get_opensre_version

# Monotonic snapshot at import — anchor for uptime deltas that resist wall-clock skew.
_PROCESS_START_MONOTONIC = _time.monotonic()


_scratchpad_dir_cache: str | None = None
_runtime_env_cache: tuple[str, str] | None = None
_full_metadata_cache: tuple[tuple[Any, ...], dict[str, Any]] | None = None


def _copy_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Shallow-copy ``metadata`` and deep-copy nested mappings (``tools``).

    The full-metadata cache must not share nested dicts with callers: a
    mutation of ``meta[\"tools\"][name]`` would otherwise poison later reads.
    """
    out = dict(metadata)
    tools = out.get("tools")
    if isinstance(tools, dict):
        out["tools"] = dict(tools)
    return out


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
    global _scratchpad_dir_cache, _runtime_env_cache, _full_metadata_cache
    env_override = (os.environ.get("OPENSRE_ENV") or "").strip()
    path = os.environ.get("PATH") or os.defpath
    kube = os.environ.get("KUBECONFIG") or ""
    cloud_key = (
        os.environ.get("CLOUD_PROVIDER") or "",
        os.environ.get("CLOUD_REGION") or "",
        os.environ.get("AWS_REGION") or "",
        os.environ.get("AWS_DEFAULT_REGION") or "",
    )
    full_key = (env_override, path, kube, cloud_key, os.getpid(), os.getppid())
    cached = _full_metadata_cache
    if cached is not None and cached[0] == full_key:
        return _copy_metadata(cached[1])

    env_cached = _runtime_env_cache
    if env_cached is not None and env_cached[0] == env_override:
        runtime_env = env_cached[1]
    else:
        runtime_env = env_override or get_environment().value
        _runtime_env_cache = (env_override, runtime_env)
    if _scratchpad_dir_cache is None:
        _scratchpad_dir_cache = tempfile.gettempdir()
    result = {
        "opensre_version": get_opensre_version(),
        "opensre_build": detect_build_info(),
        "runtime_env": runtime_env,
        "tz_name": local_tz_name(),
        "python_version": python_version_string(),
        "pid": full_key[4],
        "ppid": full_key[5],
        "tools": installed_tools(),
        "kubeconfig": kubeconfig_path(),
        "hostname": pod_hostname(),
        "scratchpad_dir": _scratchpad_dir_cache,
        **cloud_facts(),
    }
    # Store an isolated copy so callers cannot mutate the cache via nested
    # mappings (notably ``tools``).
    _full_metadata_cache = (full_key, _copy_metadata(result))
    return _copy_metadata(result)


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
    "build_runtime_metadata",
    "capture_runtime_facts",
    "merge_runtime_into_inputs",
]
