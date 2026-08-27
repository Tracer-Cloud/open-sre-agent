"""Rendered summary screens for the wizard onboarding flow.

One job: print the wizard's non-interactive output sections — the opening
splash header, the post-onboarding saved-configuration summary, the per-step
integration result card, and the closing next-steps list. These are pure
renders against the shared ``console`` (from
:mod:`surfaces.cli.wizard.components`); they hold no prompt or state logic.
"""

from __future__ import annotations

from typing import cast

from rich.rule import Rule
from rich.text import Text

from config.version import get_opensre_version
from infrastructure.terminal.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
)
from surfaces.cli.wizard.components import console
from surfaces.cli.wizard.integration_health import IntegrationHealthResult


def render_header() -> None:
    """Print the onboarding splash using the design-system palette.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
        ___                    ____  ____  _____ [HIGHLIGHT art]
       / _ \\ ...
      opensre  ·  v<version>                     [SECONDARY name] [DIM ·] [BRAND version]
      open-source SRE agent for automated …      [SECONDARY description]
      ─────────────────────────────────────────  [DIM rule]
      Setup — Configure your local AI stack …    [SECONDARY subtitle]
    """
    from surfaces.shared.terminal.components.banner_art import render_art

    art = render_art()
    version = get_opensre_version()

    console.print()
    console.print(Rule(style=DIM))
    console.print()

    for line in art.splitlines():
        t = Text()
        t.append("  ")
        t.append(line, style=f"bold {HIGHLIGHT}")
        console.print(t)

    console.print()

    subtitle = Text()
    subtitle.append("  ")
    subtitle.append("opensre", style=SECONDARY)
    subtitle.append("  ·  ", style=DIM)
    subtitle.append(f"v{version}", style=BRAND)
    console.print(subtitle)

    desc = Text()
    desc.append(
        "  open-source SRE agent for automated incident investigation and root cause analysis",
        style=SECONDARY,
    )
    console.print(desc)
    console.print()
    console.print(Rule(style=DIM))
    console.print()

    setup_line = Text()
    setup_line.append("  Setup", style=f"bold {TEXT}")
    setup_line.append(
        "  —  Configure your local AI stack and optional integrations.", style=SECONDARY
    )
    console.print(setup_line)
    console.print()


def render_saved_summary(
    *,
    provider_label: str,
    model: str,
    saved_path: str,
    env_path: str,
    configured_integrations: list[str],
    credential_line: str = "local credentials file (~/.opensre/credentials.json)",
) -> None:
    """Print the post-onboarding success screen.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
      ✓  Done.                                   [HIGHLIGHT ✓ + text]
      ─────────────────────────────────────────  [DIM rule]
                                                  [blank]
        provider    Anthropic                    [SECONDARY key] [TEXT value]
        model       claude-opus-4-5              [SECONDARY key] [TEXT value]
        services    grafana · datadog            [SECONDARY key] [TEXT value]
        config      ~/.opensre/opensre.json      [SECONDARY key] [BRAND path]
        env         .env                         [SECONDARY key] [BRAND path]
        credentials ~/.opensre/credentials.json  [SECONDARY key] [TEXT value]
        store       ~/.opensre/store.json        [SECONDARY key] [BRAND path]
    """
    from integrations.store import STORE_PATH

    integrations_str = "  ·  ".join(configured_integrations) if configured_integrations else "none"

    console.print()
    console.print(Rule(style=DIM))

    done = Text()
    done.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {HIGHLIGHT}")
    done.append("Done.", style=f"bold {TEXT}")
    console.print(done)

    console.print(Rule(style=DIM))
    console.print()

    key_col = 14

    def _kv(key: str, value: str, value_style: str = TEXT) -> None:
        row = Text()
        row.append(f"    {key:<{key_col}}", style=SECONDARY)
        row.append(value, style=value_style)
        console.print(row)

    _kv("provider", provider_label)
    _kv("model", model)
    _kv("services", integrations_str)
    _kv("config", saved_path, BRAND)
    _kv("env", env_path, BRAND)
    _kv("credentials", credential_line)
    _kv("store", str(STORE_PATH), BRAND)
    console.print()


def render_integration_result(
    service_label: str,
    result: IntegrationHealthResult,
    *,
    github_display_level: str | None = None,
) -> None:
    if result.github_mcp is not None:
        from integrations.github import (
            GitHubMcpDisplayDetailLevel,
            print_github_mcp_validation_report,
        )

        print_github_mcp_validation_report(
            result.github_mcp,
            console=console,
            detail_level=cast(
                GitHubMcpDisplayDetailLevel,
                github_display_level or "standard",
            ),
        )
        return
    ok = bool(result.ok)
    detail = str(result.detail)
    glyph = GLYPH_SUCCESS if ok else GLYPH_ERROR
    glyph_style = f"bold {HIGHLIGHT}" if ok else f"bold {ERROR}"
    prefix = "Connected" if ok else "Failed"

    status_line = Text()
    status_line.append(f"  {glyph}  ", style=glyph_style)
    status_line.append(f"{service_label}", style=f"bold {TEXT}")
    status_line.append("  ·  ", style=DIM)
    status_line.append(prefix, style=TEXT)
    console.print(status_line)

    for raw_line in detail.splitlines():
        line = raw_line.strip()
        if line:
            detail_text = Text()
            detail_text.append(f"     {line}", style=SECONDARY)
            console.print(detail_text)


def render_next_steps() -> None:
    """Print suggested commands after onboarding."""
    console.print(Rule(style=DIM))

    section = Text()
    section.append("  What's next", style=SECONDARY)
    console.print(section)

    console.print(Rule(style=DIM))
    console.print()

    next_steps: tuple[tuple[str, str], ...] = (
        ("opensre", "Start the interactive agent and run /loops"),
        (
            "opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json",
            "Run root-cause analysis on a sample alert",
        ),
        ("opensre doctor", "Verify your full environment setup"),
        ("opensre onboard", "Re-run this setup at any time"),
    )

    for cmd, description in next_steps:
        cmd_line = Text()
        cmd_line.append(f"  {cmd}", style=f"bold {BRAND}")
        console.print(cmd_line)
        desc_line = Text()
        desc_line.append(f"    {description}", style=SECONDARY)
        console.print(desc_line)

    console.print()
