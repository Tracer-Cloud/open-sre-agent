"""Full environment diagnostic command, inspired by ``fly doctor``.

Rendered output (colour roles):
──────────────────────────────────────────── [BORDER rule]
  OpenSRE Doctor                             [TEXT bold section header]
──────────────────────────────────────────── [BORDER rule]

  ✓  python          Python 3.13.2           [PRIMARY ✓] [TEXT_DIM check] [TEXT detail]
  ✓  env_file        .env (12 keys)
  ⚠  integrations    no integrations         [WARNING ⚠]
  ✗  llm_provider    ANTHROPIC_API_KEY unset [ERROR ✗]
  ✓  version         2026.4.7 (up to date)
  ✓  network         github.com reachable

──────────────────────────────────────────── [BORDER rule]
  1 error · 1 warning — run opensre doctor   [ERROR/WARNING counts · TEXT_DIM hint]
  All checks passed.                         [PRIMARY — when clean]
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from app.cli.interactive_shell.theme import (
    BORDER,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    GLYPH_WARNING,
    PRIMARY,
    TEXT,
    TEXT_DIM,
    WARNING,
)
from app.cli.support.context import is_json_output
from app.cli.support.exit_codes import ERROR as EXIT_ERROR
from app.cli.support.exit_codes import SUCCESS
from app.version import get_version


def _check(name: str, fn: Any) -> dict[str, str]:
    """Run a single diagnostic check and return a result dict."""
    try:
        ok, detail = fn()
        return {"check": name, "status": "ok" if ok else "warn", "detail": detail}
    except Exception as exc:
        return {"check": name, "status": "error", "detail": str(exc)}


def _check_python_version() -> tuple[bool, str]:
    version = platform.python_version()
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 11):
        return False, f"Python {version} — opensre requires >= 3.11"
    return True, f"Python {version}"


def _check_env_file() -> tuple[bool, str]:
    env_path = os.getenv("OPENSRE_PROJECT_ENV_PATH", ".env")
    path = Path(env_path)
    if not path.exists():
        return False, f"{env_path} not found"
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return True, f"{env_path} ({len(lines)} keys)"


def _check_llm_provider() -> tuple[bool, str]:
    provider = os.getenv("LLM_PROVIDER", "").lower() or "not set"
    key_vars = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "requesty": "REQUESTY_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "bedrock": "AWS_DEFAULT_REGION",
        "ollama": "OLLAMA_HOST",
    }
    if provider == "not set":
        return False, "LLM_PROVIDER env var is not set"

    from app.integrations.llm_cli.registry import get_cli_provider_registration

    cli_reg = get_cli_provider_registration(provider)
    if cli_reg is not None:
        probe = cli_reg.adapter_factory().detect()
        if not probe.installed or not probe.bin_path:
            return False, f"provider={provider}, CLI not installed ({probe.detail})"
        if probe.logged_in is False:
            return False, f"provider={provider}, CLI not authenticated ({probe.detail})"
        if probe.logged_in is None:
            return False, f"provider={provider}, CLI auth status unclear ({probe.detail})"
        return True, f"provider={provider}, CLI ready ({probe.detail})"

    expected_key = key_vars.get(provider)
    if expected_key and not os.getenv(expected_key):
        return False, f"provider={provider}, but {expected_key} is not set"
    return True, f"provider={provider}"


def _check_integrations() -> tuple[bool, str]:
    from app.integrations.store import STORE_PATH, list_integrations

    path = Path(str(STORE_PATH))
    if not path.exists():
        return False, f"{STORE_PATH} not found — run 'opensre integrations setup'"
    items = list_integrations()
    if not items:
        return False, "no integrations configured"
    names = [i["service"] for i in items]
    return True, f"{len(items)} configured: {', '.join(names)}"


def _check_version_freshness() -> tuple[bool, str]:
    current = get_version()
    from app.cli.support.update import (
        _fetch_latest_version,
        _is_update_available,
        development_install_doctor_version_detail,
    )

    dev_detail = development_install_doctor_version_detail(current)
    if dev_detail is not None:
        return True, dev_detail

    try:
        latest = _fetch_latest_version()
        if _is_update_available(current, latest):
            return False, f"current={current}, latest={latest} — run 'opensre update'"
        return True, f"{current} (up to date)"
    except Exception as exc:
        return True, f"{current} (could not check: {exc})"


_CHECKS = [
    ("python", _check_python_version),
    ("env_file", _check_env_file),
    ("llm_provider", _check_llm_provider),
    ("integrations", _check_integrations),
    ("version", _check_version_freshness),
]


def _render_doctor_results(console: Console, results: list[dict[str, str]]) -> None:
    """Print formatted diagnostic results with design-system colour roles.

    Section header: TEXT bold + BORDER rule (col 45).
    ✓ checks:  PRIMARY glyph, TEXT_DIM check name, TEXT detail.
    ⚠ warnings: WARNING glyph, TEXT_DIM check name, TEXT detail.
    ✗ errors:  ERROR glyph, TEXT_DIM check name, TEXT detail.
    Summary:   ERROR count + WARNING count in their respective roles, TEXT_DIM hint.
    """
    console.print()
    console.print(Rule(style=BORDER))

    # Section header — TEXT weight, BORDER rule to col 45.
    header = Text()
    header.append("  OpenSRE Doctor", style=f"bold {TEXT}")
    console.print(header)

    console.print(Rule(style=BORDER))
    console.print()

    check_col = 18  # fixed column width for check name alignment

    for r in results:
        status = r["status"]

        if status == "ok":
            glyph = GLYPH_SUCCESS
            glyph_style = f"bold {PRIMARY}"
            detail_style = TEXT
        elif status == "warn":
            glyph = GLYPH_WARNING
            glyph_style = f"bold {WARNING}"
            detail_style = TEXT
        else:
            glyph = GLYPH_ERROR
            glyph_style = f"bold {ERROR}"
            detail_style = TEXT

        row = Text()
        row.append(f"  {glyph}  ", style=glyph_style)
        row.append(f"{r['check']:<{check_col}}", style=TEXT_DIM)
        row.append(r["detail"], style=detail_style)
        console.print(row)

    console.print()
    console.print(Rule(style=BORDER))

    error_count = sum(1 for r in results if r["status"] == "error")
    warn_count = sum(1 for r in results if r["status"] == "warn")

    if error_count == 0 and warn_count == 0:
        summary = Text()
        summary.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {PRIMARY}")
        summary.append("All checks passed.", style=TEXT)
        console.print(summary)
    else:
        summary = Text()
        summary.append("  ")
        if error_count:
            summary.append(
                f"{error_count} error{'s' if error_count > 1 else ''}", style=f"bold {ERROR}"
            )
        if error_count and warn_count:
            summary.append("  ·  ", style=TEXT_DIM)
        if warn_count:
            summary.append(
                f"{warn_count} warning{'s' if warn_count > 1 else ''}", style=f"bold {WARNING}"
            )
        summary.append("   —   fix and rerun ", style=TEXT_DIM)
        summary.append("opensre doctor", style=f"bold {TEXT}")
        console.print(summary)

    console.print()


@click.command(name="doctor")
def doctor_command() -> None:
    """Run a full environment diagnostic to surface setup issues."""
    results: list[dict[str, str]] = []
    for name, fn in _CHECKS:
        results.append(_check(name, fn))

    if is_json_output():
        click.echo(json.dumps(results, indent=2))
    else:
        console = Console(highlight=False, force_terminal=True, color_system="truecolor")
        _render_doctor_results(console, results)

    has_errors = any(r["status"] == "error" for r in results)
    raise SystemExit(EXIT_ERROR if has_errors else SUCCESS)
