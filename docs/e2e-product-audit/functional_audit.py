#!/usr/bin/env python3
"""Functional E2E audit: verify CLI and REPL commands behave as documented.

Run from repo root::

    OPENSRE_NO_TELEMETRY=1 uv run python docs/e2e-product-audit/functional_audit.py

Writes ``functional_audit.json`` next to this script. See REPORT.md for findings.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from rich.console import Console

from app.cli.interactive_shell.command_registry import dispatch_slash
from app.cli.interactive_shell.runtime.session import ReplSession

AUDIT_DIR = Path(__file__).resolve().parent
ROOT = AUDIT_DIR.parents[1]
FIXTURE = ROOT / "tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json"
JSON_OUT = AUDIT_DIR / "functional_audit.json"


@dataclass
class Check:
    surface: str
    command: str
    expected: str
    passed: bool
    actual: str
    notes: str = ""


def cli_run(args: list[str], *, timeout: int = 90) -> tuple[int, str]:
    proc = subprocess.run(
        ["uv", "run", "opensre", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "OPENSRE_NO_TELEMETRY": "1"},
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def repl_run(line: str, *, trust: bool = True) -> tuple[bool, str, ReplSession]:
    session = ReplSession()
    if trust:
        session.trust_mode = True
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    try:
        cont = dispatch_slash(line, session, console, is_tty=False)
    except Exception as exc:  # noqa: BLE001
        return False, f"EXCEPTION: {exc}", session
    return cont, buf.getvalue(), session


def record(checks: list[Check], **kwargs: object) -> None:
    checks.append(Check(**{k: v for k, v in kwargs.items() if k in Check.__annotations__}))  # type: ignore[arg-type]


def main() -> int:
    checks: list[Check] = []

    cli_specs: list[tuple[list[str], str, Callable[[int, str], tuple[bool, str]]]] = [
        (
            ["version"],
            "prints version and python",
            lambda c, o: (c == 0 and "opensre" in o.lower() and "python" in o.lower(), o[:120]),
        ),
        (
            ["doctor"],
            "diagnostic table with python + llm",
            lambda c, o: (
                c == 0 and "python" in o.lower() and "llm" in o.lower(),
                o[:120],
            ),
        ),
        (
            ["health"],
            "integration health table",
            lambda c, o: (c == 0 and "integration" in o.lower(), o[:120]),
        ),
        (
            ["config", "show"],
            "shows config path or yaml",
            lambda c, o: (c == 0 and "config" in o.lower(), o[:120]),
        ),
        (
            ["integrations", "list"],
            "lists integrations or empty message",
            lambda c, o: (
                c == 0 and ("integration" in o.lower() or "no integrations" in o.lower()),
                o[:120],
            ),
        ),
        (
            ["agents", "list"],
            "agent table headers",
            lambda c, o: (c == 0 and "agent" in o.lower(), o[:120]),
        ),
        (
            ["agents", "scan"],
            "scan completes",
            lambda c, o: (c == 0, o[:120]),
        ),
        (
            ["tests", "list"],
            "lists synthetic tests",
            lambda c, o: (c == 0 and "synthetic:" in o, o[:120]),
        ),
        (
            ["guardrails", "rules"],
            "rules or not-configured message",
            lambda c, o: (
                c == 0 and ("guardrail" in o.lower() or "no guardrails" in o.lower()),
                o[:120],
            ),
        ),
        (
            ["messaging", "status", "--platform", "telegram"],
            "messaging status output",
            lambda c, o: (c == 0 and "telegram" in o.lower(), o[:120]),
        ),
        (
            ["remote", "health"],
            "fails without URL (expected)",
            lambda c, o: (c != 0 or "url" in o.lower() or "remote" in o.lower(), o[:120]),
        ),
        (
            ["watchdog", "--help"],
            "watchdog usage",
            lambda c, o: (c == 0 and "watchdog" in o.lower(), o[:120]),
        ),
    ]

    print("CLI checks...")
    for args, expected, validator in cli_specs:
        label = " ".join(args)
        try:
            code, out = cli_run(args)
            passed, actual = validator(code, out)
            notes = "" if passed else f"exit={code}"
            record(
                checks,
                surface="cli",
                command=label,
                expected=expected,
                passed=passed,
                actual=actual,
                notes=notes,
            )
            print(f"{'PASS' if passed else 'FAIL'}  cli  {label}")
        except subprocess.TimeoutExpired:
            record(
                checks,
                surface="cli",
                command=label,
                expected=expected,
                passed=False,
                actual="timeout",
                notes="timeout",
            )
            print(f"FAIL  cli  {label}  timeout")

    repl_specs: list[tuple[str, str, Callable[[str, ReplSession], tuple[bool, str]]]] = [
        (
            "/help",
            "shows command index",
            lambda o, _s: ("/investigate" in o or "Commands" in o or "help" in o.lower(), o[:150]),
        ),
        (
            "/status",
            "session status table",
            lambda o, _s: ("trust mode" in o.lower() or "interactions" in o.lower(), o[:150]),
        ),
        (
            "/version",
            "version info",
            lambda o, _s: ("opensre" in o.lower() or "python" in o.lower(), o[:150]),
        ),
        (
            "/doctor",
            "doctor output",
            lambda o, _s: ("python" in o.lower(), o[:150]),
        ),
        (
            "/health",
            "health summary",
            lambda o, _s: ("integration" in o.lower() or "health" in o.lower(), o[:150]),
        ),
        (
            "/model",
            "shows LLM provider/model",
            lambda o, _s: ("provider" in o.lower() or "model" in o.lower() or "codex" in o.lower(), o[:150]),
        ),
        (
            "/integrations",
            "integrations list or setup hint",
            lambda o, _s: ("integration" in o.lower(), o[:150]),
        ),
        (
            "/agents",
            "agents table",
            lambda o, _s: ("agent" in o.lower(), o[:150]),
        ),
        (
            "/tests list",
            "test inventory",
            lambda o, _s: ("synthetic:" in o, o[:150]),
        ),
        (
            "/template",
            "alert JSON template",
            lambda o, _s: ("alert" in o.lower() and ("{" in o or "json" in o.lower()), o[:150]),
        ),
        (
            "/trust on",
            "enables trust mode",
            lambda o, s: (s.trust_mode is True, o[:150]),
        ),
        (
            "/reset",
            "clears session",
            lambda o, s: ("cleared" in o.lower() and len(s.history) == 0, o[:150]),
        ),
        (
            "/tasks",
            "task list (may be empty)",
            lambda o, _s: ("task" in o.lower() or "no " in o.lower(), o[:150]),
        ),
        (
            "/watches",
            "watchdog task list",
            lambda o, _s: (True, o[:150]),
        ),
        (
            "/onboard",
            "handoff message (cannot run in REPL)",
            lambda o, _s: (
                "wizard" in o.lower() or "opensre onboard" in o.lower() or "interactive" in o.lower(),
                o[:150],
            ),
        ),
        (
            "/privacy",
            "privacy / history info",
            lambda o, _s: ("history" in o.lower() or "privacy" in o.lower() or "telemetry" in o.lower(), o[:150]),
        ),
        (
            "/guardrails",
            "guardrails info or init hint",
            lambda o, _s: ("guardrail" in o.lower(), o[:150]),
        ),
        (
            "/list",
            "browse integrations/models/tools",
            lambda o, _s: (len(o.strip()) > 20, o[:150]),
        ),
    ]

    print("\nREPL slash checks...")
    for line, expected, validator in repl_specs:
        try:
            _cont, out, session = repl_run(line)
            passed, actual = validator(out, session)
            fail_markers = ["unknown command", "[error]error:", "exception:"]
            if any(m in out.lower() for m in fail_markers):
                passed = False
            record(
                checks,
                surface="repl",
                command=line,
                expected=expected,
                passed=passed,
                actual=actual,
            )
            print(f"{'PASS' if passed else 'FAIL'}  repl  {line}")
        except Exception as exc:  # noqa: BLE001
            record(
                checks,
                surface="repl",
                command=line,
                expected=expected,
                passed=False,
                actual=str(exc),
            )
            print(f"FAIL  repl  {line}  {exc}")

    if FIXTURE.is_file():
        print("\nREPL /investigate (smoke)...")
        try:
            _cont, out, session = repl_run(f"/investigate {FIXTURE}")
            passed = not re.search(r"\[error\]", out, re.I) and (
                "investigation" in out.lower()
                or "running" in out.lower()
                or session.last_state is not None
            )
            record(
                checks,
                surface="repl",
                command="/investigate <fixture>",
                expected="starts or completes file-based investigation",
                passed=passed,
                actual=out[-200:] if out else "(no output yet - may be background)",
                notes="requires LLM_PROVIDER in .env via opensre entrypoint",
            )
            print(f"{'PASS' if passed else 'FAIL'}  repl  /investigate")
        except Exception as exc:  # noqa: BLE001
            record(
                checks,
                surface="repl",
                command="/investigate <fixture>",
                expected="investigation",
                passed=False,
                actual=str(exc),
            )

    _c, help_out, _ = repl_run("/help")
    markup_broken = "[/" in help_out and "[/]" not in help_out
    record(
        checks,
        surface="repl-ui",
        command="/help rendering",
        expected="no broken Rich markup in help",
        passed=not markup_broken and len(help_out) > 100,
        actual=f"len={len(help_out)}",
        notes="manual: verify tables align in real TTY",
    )

    passed_n = sum(1 for c in checks if c.passed)
    failed = [c for c in checks if not c.passed]
    report = {
        "summary": {"total": len(checks), "passed": passed_n, "failed": len(failed)},
        "failed": [asdict(c) for c in failed],
        "all": [asdict(c) for c in checks],
    }
    JSON_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {JSON_OUT}")
    print(report["summary"])
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
