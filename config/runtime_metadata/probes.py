"""Runtime environment probes via pure Python (no subprocess).

Each probe replaces a shell command the agent would otherwise reach for:
timezone (``date``), hostname (``hostname``), interpreter version
(``python --version``), tool presence (``which``), kubeconfig
(``kubectl config view``), disk/memory (``df``/``free``/``top``), and cloud
identity (instance metadata endpoint).
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import sys
import time as _time
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from config.constants.runtime_metadata import (
    OPENSRE_ALLOW_NETWORK_ENV,
    WORKSPACE_REPO_ENV_KEYS,
)

# Tools the LLM commonly reflex-shells for. Presence-only surfaces so the agent
# can answer without invoking ``--version``, which the sandbox blocks.
_TOOLS_TO_PROBE = (
    "kubectl",
    "helm",
    "docker",
    "git",
    "python",
    "python3",
    "curl",
    "bash",
    "sh",
    "buzz",
)

_LOCALTIME_LINK = Path("/etc/localtime")

_HOSTNAME_FILE = Path("/etc/hostname")


def local_tz_name() -> str:
    """Best-effort local timezone name — IANA (``Europe/Berlin``) when possible.

    Reads the ``/etc/localtime`` symlink target on macOS/Linux — the standard
    way the OS advertises which zone it's set to. Falls back to
    ``time.tzname`` short codes (``CET``, ``BST``) if the symlink can't be
    resolved (Windows, unusual OS config), and finally to ``UTC``.
    """
    try:
        if _LOCALTIME_LINK.is_symlink():
            target = os.readlink(_LOCALTIME_LINK)
            marker = "zoneinfo/"
            idx = target.rfind(marker)
            if idx >= 0:
                iana = target[idx + len(marker) :]
                if iana:
                    return iana
    except OSError:
        # Unreadable /etc/localtime: fall back to time.tzname/UTC below.
        pass
    return _time.tzname[0] if _time.tzname else "UTC"


def python_version_string() -> str:
    """Interpreter version as ``major.minor.patch`` from :mod:`sys`."""
    info = sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def installed_tools() -> dict[str, str]:
    """Map each probed tool name to its ``PATH`` location (empty if absent).

    :func:`shutil.which` walks ``PATH`` in pure Python. Version strings would
    require invoking the binary and are intentionally omitted; presence is what
    the agent needs to stop reflex-shelling for ``--version``.
    """
    return {tool: shutil.which(tool) or "" for tool in _TOOLS_TO_PROBE}


def pod_hostname() -> str:
    """Hostname via file read.

    ``/etc/hostname`` holds the pod name inside Kubernetes containers, which is
    the value SRE questions ("which pod am I in?") actually want. Falls back to
    :func:`socket.gethostname` on hosts without the file (macOS, some distros).
    """
    try:
        if _HOSTNAME_FILE.is_file():
            name = _HOSTNAME_FILE.read_text(encoding="utf-8").strip()
            if name:
                return name
    except OSError:
        # Unreadable /etc/hostname: fall back to the socket API below.
        pass
    try:
        return socket.gethostname()
    except OSError:
        return ""


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


def kubeconfig_path() -> str:
    """Effective ``kubeconfig`` path from env, or the default under ``~/.kube``.

    Kept as a session-static fact so the agent can answer "which cluster
    config is loaded" without shelling to ``kubectl config view``.
    """
    override = (os.environ.get("KUBECONFIG") or "").strip()
    if override:
        # ``KUBECONFIG`` may be a ``:``-separated list; the first entry wins.
        first = override.split(os.pathsep, 1)[0]
        if first:
            return first
    default = Path.home() / ".kube" / "config"
    return str(default) if default.is_file() else ""


#: Hosts whose remotes shorten to a bare ``owner/repo`` identity.
_GITHUB_HOSTS: Final[frozenset[str]] = frozenset({"github.com", "www.github.com"})

#: scp-style remote: ``[user@]host:path`` (no scheme, so urlsplit cannot parse it).
_SCP_REMOTE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[^@/]+@)?(?P<host>[A-Za-z0-9._-]+):(?P<path>.+)$"
)


def _remote_host_and_path(text: str) -> tuple[str, str]:
    """Split a git remote into (host, path), or ``("", "")`` when unrecognised."""
    if "://" in text:
        parsed = urlsplit(text)
        return (parsed.hostname or "").lower(), parsed.path
    match = _SCP_REMOTE_RE.match(text)
    if match is None:
        return "", ""
    return match.group("host").lower(), match.group("path")


def _normalize_repo_identity(raw: str) -> str:
    """Shorten a GitHub remote to ``owner/repo``; leave anything else as-is.

    The host is compared exactly. A substring test would accept
    ``https://evil.test/github.com/attacker/repo`` and report ``attacker/repo``
    as this project's repository, and that fact reaches prompts and reports.
    """
    text = raw.strip()
    if not text:
        return ""
    if text.endswith(".git"):
        text = text[:-4]
    host, path = _remote_host_and_path(text)
    if host in _GITHUB_HOSTS and path.strip("/"):
        return path.strip("/")
    return text


def read_git_origin_identity(config_text: str) -> str:
    """Parse a ``.git/config`` body for ``remote.origin.url`` → ``owner/repo``."""
    in_origin = False
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_origin = stripped.lower() == '[remote "origin"]'
            continue
        if in_origin and stripped.lower().startswith("url"):
            _, _, value = stripped.partition("=")
            return _normalize_repo_identity(value)
    return ""


def _git_origin_from_config(repo_root: Path) -> str:
    config = repo_root / ".git" / "config"
    if not config.is_file():
        # Worktrees point .git at a file; skip deep resolution for the fact.
        return ""
    try:
        return read_git_origin_identity(config.read_text(encoding="utf-8"))
    except OSError:
        return ""


def workspace_identity_facts() -> dict[str, str]:
    """Which git/GitHub repo is “ours” for this OpenSRE process.

    Prefer an explicit env override, then the checkout’s ``origin`` remote.
    Empty string means unknown — prompts must state that rather than invent
    Tracer-Cloud/opensre or the user’s employer repo.
    """
    for key in WORKSPACE_REPO_ENV_KEYS:
        value = _normalize_repo_identity(os.environ.get(key, ""))
        if value:
            return {"workspace_repo": value}
    # Walk up from cwd for a .git directory (dev checkouts).
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            origin = _git_origin_from_config(candidate)
            if origin:
                return {"workspace_repo": origin}
            break
    return {"workspace_repo": ""}


def capability_warning_facts(tools: dict[str, str] | None = None) -> dict[str, Any]:
    """Honest capability gaps the agent should surface at boot, not mid-turn.

    ``network_egress`` is false unless ``OPENSRE_ALLOW_NETWORK=1`` — matching the
    sandbox default (blocked egress). Shell presence is derived from ``bash``/``sh``.
    """
    resolved = tools if tools is not None else installed_tools()
    missing = sorted(name for name, path in resolved.items() if not path)
    allow_network = (os.environ.get(OPENSRE_ALLOW_NETWORK_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    shell_available = bool(resolved.get("bash") or resolved.get("sh"))
    warnings: list[str] = []
    if "curl" in missing:
        warnings.append("curl is not on PATH")
    if not shell_available:
        warnings.append("no interactive shell (bash/sh) on PATH")
    if not allow_network:
        warnings.append("network egress is blocked for sandboxed code by default")
    return {
        "network_egress": allow_network,
        "shell_available": shell_available,
        "capability_warnings": warnings,
    }


__all__ = [
    "capability_warning_facts",
    "cloud_facts",
    "disk_memory_facts",
    "installed_tools",
    "kubeconfig_path",
    "local_tz_name",
    "pod_hostname",
    "python_version_string",
    "read_git_origin_identity",
    "workspace_identity_facts",
]
