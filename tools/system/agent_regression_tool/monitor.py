"""Local process monitoring to check active agent test suite runs."""

from __future__ import annotations

from typing import Any

from tools.system.fleet_monitoring.probe import process_iter


def get_running_test_suites() -> dict[str, list[dict[str, Any]]]:
    """Inspect active system processes for running test suites."""
    status: dict[str, list[dict[str, Any]]] = {
        "synthetic": [],
        "prompting": [],
        "live-turn": [],
    }

    for proc in process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline")
            if not cmdline:
                continue

            cmd_str = " ".join(cmdline)
            name = str(proc.info.get("name") or "").lower()

            is_python_or_pytest = any(
                x in name or x in cmdline[0].lower() for x in ("python", "pytest", "pytest-3", "uv")
            )
            if not is_python_or_pytest:
                continue

            if "agent_regression_tool" in cmd_str:
                continue

            if (
                ("synthetic" in cmd_str)
                or ("run_suite" in cmd_str)
                or ("tests/synthetic" in cmd_str)
            ):
                status["synthetic"].append(
                    {
                        "pid": proc.info["pid"],
                        "cmdline": cmdline,
                    }
                )

            elif ("test_prompt_characterization" in cmd_str) or (
                "tests/core/agent/prompts" in cmd_str
            ):
                status["prompting"].append(
                    {
                        "pid": proc.info["pid"],
                        "cmdline": cmdline,
                    }
                )

            elif (
                ("run_live_turn_shards" in cmd_str)
                or ("test_turn_scenarios" in cmd_str)
                or ("live_llm" in cmd_str)
                or ("test-turn-live" in cmd_str)
            ):
                status["live-turn"].append(
                    {
                        "pid": proc.info["pid"],
                        "cmdline": cmdline,
                    }
                )

        except Exception:
            continue

    return status
