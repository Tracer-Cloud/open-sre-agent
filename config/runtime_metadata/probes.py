"""Runtime environment probes via pure Python (no subprocess).

Each probe replaces a shell command the agent would otherwise reach for:
timezone (``date``), hostname (``hostname``), interpreter version
(``python --version``), tool presence (``which``), kubeconfig
(``kubectl config view``), disk/memory (``df``/``free``/``top``), and cloud
identity (instance metadata endpoint).
"""

from __future__ import annotations

import os
import socket
import sys
import time as _time
from pathlib import Path
from typing import Any

# Tools the LLM commonly reflex-shells for. Presence-only surfaces so the agent
# can answer without invoking ``--version``, which the sandbox blocks.
_TOOLS_TO_PROBE = ("kubectl", "helm", "docker", "git", "python", "python3")

_LOCALTIME_LINK = Path("/etc/localtime")

_HOSTNAME_FILE = Path("/etc/hostname")


_local_tz_cache: tuple[str, str] | None = None


def local_tz_name() -> str:
    """Best-effort local timezone name — IANA (``Europe/Berlin``) when possible.

    Reads the ``/etc/localtime`` symlink target on macOS/Linux — the standard
    way the OS advertises which zone it's set to. Falls back to
    ``time.tzname`` short codes (``CET``, ``BST``) if the symlink can't be
    resolved (Windows, unusual OS config), and finally to ``UTC``.

    Cached while ``_LOCALTIME_LINK`` is unchanged (tests rebind the path).
    """
    global _local_tz_cache
    link_key = str(_LOCALTIME_LINK)
    cached = _local_tz_cache
    if cached is not None and cached[0] == link_key:
        return cached[1]
    value = "UTC"
    try:
        if _LOCALTIME_LINK.is_symlink():
            target = os.readlink(_LOCALTIME_LINK)
            marker = "zoneinfo/"
            idx = target.rfind(marker)
            if idx >= 0:
                iana = target[idx + len(marker) :]
                if iana:
                    value = iana
                    _local_tz_cache = (link_key, value)
                    return value
    except OSError:
        # Unreadable /etc/localtime: fall back to time.tzname/UTC below.
        pass
    value = _time.tzname[0] if _time.tzname else "UTC"
    _local_tz_cache = (link_key, value)
    return value


_python_version_cache: str | None = None


def python_version_string() -> str:
    """Interpreter version as ``major.minor.patch`` from :mod:`sys`."""
    global _python_version_cache
    if _python_version_cache is None:
        info = sys.version_info
        _python_version_cache = f"{info.major}.{info.minor}.{info.micro}"
    return _python_version_cache


# Cache key = full PATH string. Tool locations are session-static in practice;
# re-probe when PATH changes (tests / nested shells). Avoids re-walking slow
# WSL ``/mnt/c/...`` entries on every ``build_runtime_metadata`` call.
_installed_tools_cache: tuple[str, dict[str, str]] | None = None


def _probe_installed_tools(path: str) -> dict[str, str]:
    """Resolve all probed tools in a single left-to-right PATH walk.

    Matches :func:`shutil.which` semantics (first hit wins, executable file
    required, ``PATHEXT`` on Windows) but pays the directory walk once for the
    whole tool set instead of once per tool.
    """
    found: dict[str, str] = dict.fromkeys(_TOOLS_TO_PROBE, "")
    remaining = set(_TOOLS_TO_PROBE)
    if not remaining:
        return found

    mode = os.F_OK | os.X_OK
    path_exts: tuple[str, ...]
    if os.name == "nt":
        # Mirror shutil.which: empty extension first, then PATHEXT. When the
        # env var is missing/empty, use the same default as shutil
        # (``_WIN_DEFAULT_PATHEXT``) so ``name.exe`` still resolves.
        raw = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC"
        path_exts = ("",) + tuple(ext for ext in raw.split(os.pathsep) if ext)
    else:
        path_exts = ("",)

    for directory in path.split(os.pathsep):
        if not remaining:
            break
        if not directory:
            directory = os.curdir
        try:
            # Skip unreadable / non-dirs early (WSL mounts to missing drives).
            if not os.path.isdir(directory):
                continue
        except OSError:
            continue
        for tool in tuple(remaining):
            for ext in path_exts:
                # Windows: if the tool already has an extension, don't append.
                if ext and os.name == "nt" and os.path.splitext(tool)[1]:
                    candidate = os.path.join(directory, tool)
                else:
                    candidate = os.path.join(directory, tool + ext)
                try:
                    if os.path.isfile(candidate) and os.access(candidate, mode):
                        found[tool] = candidate
                        remaining.discard(tool)
                        break
                except OSError:
                    continue
    return found


def installed_tools() -> dict[str, str]:
    """Map each probed tool name to its ``PATH`` location (empty if absent).

    Walks ``PATH`` once for the whole tool set (not once per tool) and caches
    the result for the current ``PATH`` value. Version strings would require
    invoking the binary and are intentionally omitted; presence is what the
    agent needs to stop reflex-shelling for ``--version``.
    """
    global _installed_tools_cache
    path = os.environ.get("PATH") or os.defpath
    cached = _installed_tools_cache
    if cached is not None and cached[0] == path:
        # Return a shallow copy so callers cannot corrupt the cache.
        return dict(cached[1])
    result = _probe_installed_tools(path)
    _installed_tools_cache = (path, result)
    return dict(result)


_hostname_cache: tuple[str, str] | None = None


def pod_hostname() -> str:
    """Hostname via file read.

    ``/etc/hostname`` holds the pod name inside Kubernetes containers, which is
    the value SRE questions ("which pod am I in?") actually want. Falls back to
    :func:`socket.gethostname` on hosts without the file (macOS, some distros).

    Cached while ``_HOSTNAME_FILE`` is unchanged (tests rebind the path).
    """
    global _hostname_cache
    file_key = str(_HOSTNAME_FILE)
    cached = _hostname_cache
    if cached is not None and cached[0] == file_key:
        return cached[1]
    value = ""
    try:
        if _HOSTNAME_FILE.is_file():
            name = _HOSTNAME_FILE.read_text(encoding="utf-8").strip()
            if name:
                value = name
                _hostname_cache = (file_key, value)
                return value
    except OSError:
        # Unreadable /etc/hostname: fall back to the socket API below.
        pass
    try:
        value = socket.gethostname()
    except OSError:
        value = ""
    _hostname_cache = (file_key, value)
    return value


def disk_memory_facts() -> dict[str, Any]:
    """Live disk and memory readings via psutil.

    Degrades to an empty dict if psutil misbehaves on an exotic platform —
    the facts are then simply absent rather than crashing prompt assembly.
    """
    try:
        import psutil

        disk = psutil.disk_usage("/")
        memory = psutil.virtual_memory()
    except Exception:
        return {}
    gib = 1024**3
    return {
        "disk_used_percent": round(disk.percent, 1),
        "disk_free_gb": round(disk.free / gib, 1),
        "memory_used_percent": round(memory.percent, 1),
        "memory_available_gb": round(memory.available / gib, 1),
    }


def cloud_facts() -> dict[str, str]:
    """Cloud provider/region from deploy-time env vars — no metadata endpoint.

    ``CLOUD_PROVIDER`` / ``CLOUD_REGION`` are the canonical injection points
    (set at deploy time). Region falls back to ``AWS_REGION`` /
    ``AWS_DEFAULT_REGION`` — the same pair the LLM transports already read —
    and when the region came from an AWS var the provider defaults to ``aws``.
    Never calls the instance metadata service (IMDS); the sandbox blocks
    network anyway.
    """
    provider = (os.environ.get("CLOUD_PROVIDER") or "").strip()
    region = (os.environ.get("CLOUD_REGION") or "").strip()
    aws_region = (
        os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    ).strip()
    if not region and aws_region:
        region = aws_region
        if not provider:
            provider = "aws"
    return {"cloud_provider": provider, "cloud_region": region}


_kubeconfig_cache: tuple[str, str, str] | None = None


def kubeconfig_path() -> str:
    """Effective ``kubeconfig`` path from env, or the default under ``~/.kube``.

    Kept as a session-static fact so the agent can answer "which cluster
    config is loaded" without shelling to ``kubectl config view``.

    Cached while ``KUBECONFIG`` and the home directory are unchanged.
    """
    global _kubeconfig_cache
    override = (os.environ.get("KUBECONFIG") or "").strip()
    home = str(Path.home())
    cached = _kubeconfig_cache
    if cached is not None and cached[0] == override and cached[1] == home:
        return cached[2]
    if override:
        # ``KUBECONFIG`` may be a ``:``-separated list; the first entry wins.
        first = override.split(os.pathsep, 1)[0]
        value = first if first else ""
    else:
        default = Path(home) / ".kube" / "config"
        value = str(default) if default.is_file() else ""
    _kubeconfig_cache = (override, home, value)
    return value


__all__ = [
    "cloud_facts",
    "disk_memory_facts",
    "installed_tools",
    "kubeconfig_path",
    "local_tz_name",
    "pod_hostname",
    "python_version_string",
]
