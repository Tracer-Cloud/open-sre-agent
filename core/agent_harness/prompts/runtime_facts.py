"""Runtime-facts section of the assistant environment block.

Renders the ``capture_runtime_facts()`` dict into verbatim-quotable prompt
strings plus anti-hallucination guidance. Phrased for quote-verbatim recall:
earlier prompt wording ("including the build marker if present") caused the
LLM to treat "build marker" as a slot name and hallucinate a value like ``0``
when the marker was empty.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from config.runtime_metadata import BLOCKED_INTROSPECTION_COMMANDS

_BLOCKED_COMMANDS = ", ".join(f"`{command}`" for command in BLOCKED_INTROSPECTION_COMMANDS)

_GUIDANCE = (
    ". When the user asks which OpenSRE version is running, reply with the "
    "full version string above verbatim — including any parenthetical suffix. "
    "When the user asks for the current date, time, day of the week, or "
    "timezone, answer from the strings above — do NOT guess a date/time from "
    "your training data. When the user asks for the Python version, process "
    "id, parent process id, uptime, host/pod name, disk or memory usage, "
    "cloud provider or region, kubeconfig path, or which tools are installed, "
    "answer from the strings above — never run "
    f"{_BLOCKED_COMMANDS}, and never probe cloud instance metadata over the "
    "network. To list files in the scratchpad or another directory, "
    "use the Python execution sandbox with `pathlib.Path(...).iterdir()` — "
    "never `ls` or subprocess. Do NOT "
    "invent field names, values, or numbers not present above. Do NOT shell "
    "out or use subprocess — the Python execution sandbox blocks process "
    "spawning; use `inputs['opensre_runtime']` inside the sandbox instead."
)

FactProducer = Callable[[Mapping[str, Any]], str | None]


def _clean_str(runtime: Mapping[str, Any], key: str) -> str:
    return str(runtime.get(key) or "").strip()


def _clean_int(runtime: Mapping[str, Any], key: str) -> int | None:
    value = runtime.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clean_number(runtime: Mapping[str, Any], key: str) -> float | None:
    value = runtime.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _str_fact(key: str, template: str) -> FactProducer:
    """Produce ``template.format(value)`` when ``key`` is a non-empty string."""

    def produce(runtime: Mapping[str, Any]) -> str | None:
        value = _clean_str(runtime, key)
        return template.format(value) if value else None

    return produce


def _version_line(runtime: Mapping[str, Any]) -> str | None:
    version = _clean_str(runtime, "opensre_version")
    if not version:
        return None
    build = _clean_str(runtime, "opensre_build")
    display = f"{version} ({build})" if build else version
    return f"OpenSRE version is {display}"


def _uptime_line(runtime: Mapping[str, Any]) -> str | None:
    uptime = _clean_number(runtime, "uptime_seconds")
    if uptime is None or uptime < 0:
        return None
    return f"process uptime is {uptime} seconds"


def _pid_line(runtime: Mapping[str, Any]) -> str | None:
    pid = _clean_int(runtime, "pid")
    if not pid:
        return None
    ppid = _clean_int(runtime, "ppid")
    parent = f", parent {ppid}" if ppid else ""
    return f"process id is {pid}{parent}"


def _pair_line(
    left_key: str,
    right_key: str,
    template: str,
) -> FactProducer:
    """Produce a line only when both numeric halves are present."""

    def produce(runtime: Mapping[str, Any]) -> str | None:
        left = _clean_number(runtime, left_key)
        right = _clean_number(runtime, right_key)
        if left is None or right is None:
            return None
        return template.format(left, right)

    return produce


def _tools_line(runtime: Mapping[str, Any]) -> str | None:
    tools = runtime.get("tools")
    if not isinstance(tools, dict):
        return None
    present = sorted(name for name, path in tools.items() if path)
    if not present:
        return None
    return f"installed tools on PATH are {', '.join(present)}"


# Order is part of the prompt contract — keep stable for snapshot/tests.
_FACT_PRODUCERS: tuple[FactProducer, ...] = (
    _version_line,
    _str_fact("runtime_env", "runtime environment is {}"),
    _str_fact("hostname", "host name is {}"),
    _str_fact("now_iso", "current time is {}"),
    _str_fact("tz_name", "local timezone is {}"),
    _uptime_line,
    _str_fact("python_version", "Python interpreter version is {}"),
    _pid_line,
    _pair_line("disk_used_percent", "disk_free_gb", "root disk is {}% used with {} GB free"),
    _pair_line(
        "memory_used_percent",
        "memory_available_gb",
        "memory is {}% used with {} GB available",
    ),
    _str_fact("cloud_provider", "cloud provider is {}"),
    _str_fact("cloud_region", "cloud region is {}"),
    _tools_line,
    _str_fact("kubeconfig", "kubeconfig path is {}"),
    _str_fact("scratchpad_dir", "scratchpad directory is {}"),
)


def _fact_lines(runtime: Mapping[str, Any]) -> list[str]:
    """One quotable string per available fact; empty slots are omitted."""
    return [line for produce in _FACT_PRODUCERS if (line := produce(runtime))]


def render_runtime_facts(runtime: Mapping[str, Any]) -> str:
    """Runtime section of the environment block, or ``""`` when nothing to say."""
    lines = _fact_lines(runtime)
    if not lines:
        return ""
    return (
        "Runtime facts (quote the strings below EXACTLY when asked; do not "
        "paraphrase them into other field names): " + "; ".join(lines) + _GUIDANCE
    )


__all__ = ["render_runtime_facts"]
