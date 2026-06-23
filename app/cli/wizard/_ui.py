"""UI primitives and rendering helpers for the wizard onboarding flow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import questionary
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from app.cli.interactive_shell.ui.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    GLYPH_WARNING,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from app.cli.wizard.config import PROVIDER_BY_VALUE, ProviderOption
from app.cli.wizard.integration_health import IntegrationHealthResult
from app.cli.wizard.probes import ProbeResult
from app.cli.wizard.prompts import select as select_prompt
from app.cli.wizard.store import get_store_path, load_local_config
from app.integrations.store import get_integration
from app.llm_credentials import get_keyring_setup_instructions, has_llm_api_key, save_llm_api_key
from app.version import get_version

_console = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)

_STYLE = questionary.Style(
    [
        ("qmark", f"fg:{HIGHLIGHT} bold"),
        ("question", f"fg:{TEXT} bold"),
        ("answer", f"fg:{BRAND} bold"),
        ("pointer", f"fg:{HIGHLIGHT} bold"),
        ("highlighted", f"fg:{TEXT} bg:{HIGHLIGHT} bold"),
        ("selected", f"fg:{TEXT} bg:default bold"),
        ("separator", f"fg:{DIM}"),
        ("text", f"fg:{TEXT} bg:default"),
        ("disabled", f"fg:{SECONDARY} bg:default italic"),
        ("instruction", f"fg:{SECONDARY} italic"),
    ]
)


@dataclass(frozen=True)
class Choice:
    """A selectable wizard choice."""

    value: str
    label: str
    group: str | None = None
    hint: str | None = None


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string_value(value: object, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _joined_values(value: object, *, separator: str, fallback: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return separator.join(value)
    return fallback


def _local_defaults() -> dict[str, str | bool | None]:
    stored = load_local_config(get_store_path())
    wizard = _as_mapping(stored.get("wizard"))
    targets = _as_mapping(stored.get("targets"))
    local = _as_mapping(targets.get("local"))
    raw_provider = local.get("provider")
    provider = PROVIDER_BY_VALUE.get(_string_value(raw_provider)) if raw_provider else None
    api_key_env = _string_value(local.get("api_key_env"), provider.api_key_env if provider else "")
    is_cli = bool(provider and provider.credential_kind == "cli")
    return {
        "wizard_mode": _string_value(wizard.get("mode"), "quickstart"),
        "provider": _string_value(raw_provider) if raw_provider else None,
        "model": _string_value(local.get("model")),
        "api_key_env": api_key_env,
        "has_api_key": True if is_cli else bool(api_key_env and has_llm_api_key(api_key_env)),
        "legacy_api_key": _string_value(local.get("api_key")),
    }


def _integration_defaults(service: str) -> tuple[Mapping[str, object], Mapping[str, object]]:
    entry = _as_mapping(get_integration(service))
    return entry, _as_mapping(entry.get("credentials"))


def _step(title: str) -> None:
    _console.print()
    t = Text()
    t.append("  ")
    t.append(title, style=f"bold {HIGHLIGHT}")
    _console.print(t)
    _console.print(Rule(style=DIM))


def _step_header(n: int, total: int, title: str) -> None:
    """Print a numbered wizard stage header.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
      ●●○○  LLM Provider  2/4                   [BRAND dots] [TEXT title] [SECONDARY counter]
      ─────────────────────────────────────────  [DIM rule]
    """
    dots = "●" * n + "○" * (total - n)
    _console.print()
    _console.print(Rule(style=DIM))
    header = Text()
    header.append("  ")
    header.append(dots, style=f"bold {BRAND}")
    header.append("  ", style=DIM)
    header.append(title, style=f"bold {TEXT}")
    header.append(f"  {n}/{total}", style=SECONDARY)
    _console.print(header)
    _console.print(Rule(style=DIM))


def _choice_title(choice: Choice) -> str:
    return choice.label


def _choice_description(choice: Choice) -> str | None:
    if choice.hint:
        return choice.hint
    return choice.group


def _questionary_choice(choice: Choice) -> questionary.Choice:
    return questionary.Choice(
        title=_choice_title(choice),
        value=choice.value,
        description=_choice_description(choice),
    )


_CUSTOM_MODEL_SENTINEL = "__custom__"


def _choose_model(provider: ProviderOption, *, default: str | None) -> str:
    """Prompt the user to pick a model from ``provider.models``.

    Choices come from the curated config in ``app/cli/wizard/config.py``.
    A saved model that isn't in the curated list is preserved as ``current``
    so re-running the wizard never silently drops a user's prior pick, and an
    "Enter custom model ID" escape hatch is always available.
    """
    resolved_default = (default or "").strip()
    models = provider.models
    if not models:
        return resolved_default or provider.default_model

    _step("Model")

    curated_values = {option.value for option in models}
    curated_choices: list[Choice] = [
        Choice(value=option.value, label=option.label) for option in models
    ]

    extra_choices: list[Choice] = []
    if resolved_default and resolved_default not in curated_values:
        extra_choices.append(Choice(value=resolved_default, label=resolved_default, hint="current"))

    custom_choice = Choice(
        value=_CUSTOM_MODEL_SENTINEL,
        label="Enter custom model ID",
        hint="type any model identifier",
    )

    choices = curated_choices + extra_choices + [custom_choice]
    default_value = resolved_default or provider.default_model
    if default_value and not any(c.value == default_value for c in choices):
        default_value = curated_choices[0].value if curated_choices else _CUSTOM_MODEL_SENTINEL

    selection = _choose(
        f"Choose {provider.label} model",
        choices,
        default=default_value or None,
    )

    if selection != _CUSTOM_MODEL_SENTINEL:
        return selection

    return _prompt_value(
        f"Custom {provider.label} model ID ({provider.model_env})",
        default=resolved_default,
        allow_empty=False,
    )


def _choose(prompt: str, choices: list[Choice], *, default: str | None = None) -> str:
    q_choices = [_questionary_choice(choice) for choice in choices]

    result = select_prompt(
        prompt,
        choices=q_choices,
        default=default,
        style=_STYLE,
        instruction="(Use arrows to move, Enter to choose)",
    ).ask()

    if result is None:
        raise KeyboardInterrupt
    return str(result)


def _confirm(prompt: str, *, default: bool = True) -> bool:
    result = questionary.confirm(prompt, default=default, style=_STYLE).ask()
    if result is None:
        raise KeyboardInterrupt
    return bool(result)


def _prompt_value(
    label: str,
    *,
    default: str = "",
    secret: bool = False,
    allow_empty: bool = False,
) -> str:
    while True:
        instruction = "(Enter to keep current)" if default else None
        if secret:
            result = questionary.password(
                label,
                default=default,
                style=_STYLE,
                instruction=instruction,
            ).ask()
        else:
            result = questionary.text(
                label,
                default=default,
                style=_STYLE,
                instruction=instruction,
            ).ask()

        if result is None:
            raise KeyboardInterrupt

        value = str(result).strip()
        if value:
            return value
        if default:
            return default
        if allow_empty:
            return ""
        _console.print(f"[{ERROR}]  {GLYPH_ERROR}  Required.[/]")


def _persist_llm_api_key(env_var: str, value: str) -> bool:
    try:
        save_llm_api_key(env_var, value)
    except RuntimeError as exc:
        _console.print(f"[{ERROR}]  {GLYPH_ERROR}  {exc}[/]")
        _console.print(
            f"[{WARNING}]  {GLYPH_WARNING}  OpenSRE could not save your API key to the local system keychain.[/]"
        )
        for line in get_keyring_setup_instructions(env_var):
            _console.print(f"[{SECONDARY}]    {line}[/]")
        return False
    return True


def _parse_csv_values(raw_value: str) -> list[str]:
    return [part.strip() for part in raw_value.split(",") if part.strip()]


def _display_probe(result: ProbeResult) -> None:
    status = f"[{HIGHLIGHT}]reachable[/]" if result.reachable else f"[{ERROR}]unreachable[/]"
    _console.print(f"{result.target}: {status} [{SECONDARY}]({result.detail})[/]")


def _select_target_for_advanced(local_probe: ProbeResult, remote_probe: ProbeResult) -> str | None:
    _console.print(f"\n[{SECONDARY}]reachability[/]")
    _display_probe(local_probe)
    _display_probe(remote_probe)

    target = _choose(
        "Choose a configuration target:",
        [
            Choice(value="local", label="Local machine"),
            Choice(value="remote", label="Remote target (future support)"),
        ],
        default="local",
    )
    if target == "local":
        return "local"

    _console.print(f"\n[{WARNING}]Remote setup is not available yet.[/]")
    if _confirm("Use local setup instead?", default=True):
        return "local"
    _console.print(f"[{WARNING}]Setup cancelled.[/]")
    return None


def _render_header() -> None:
    """Print the onboarding splash using the design-system palette.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
        ___                    ____  ____  _____ [HIGHLIGHT art]
       / _ \\ ...
      opensre  ·  v<version>                     [SECONDARY name] [DIM ·] [BRAND version]
      open-source SRE agent for automated …      [DIM description]
      ─────────────────────────────────────────  [DIM rule]
      Setup — Configure your local AI stack …    [SECONDARY subtitle]
    """
    from app.cli.interactive_shell.ui.banner_art import _render_art

    art = _render_art()
    version = get_version()

    _console.print()
    _console.print(Rule(style=DIM))
    _console.print()

    for line in art.splitlines():
        t = Text()
        t.append("  ")
        t.append(line, style=f"bold {HIGHLIGHT}")
        _console.print(t)

    _console.print()

    subtitle = Text()
    subtitle.append("  ")
    subtitle.append("opensre", style=SECONDARY)
    subtitle.append("  ·  ", style=DIM)
    subtitle.append(f"v{version}", style=BRAND)
    _console.print(subtitle)

    desc = Text()
    desc.append(
        "  open-source SRE agent for automated incident investigation and root cause analysis",
        style=DIM,
    )
    _console.print(desc)
    _console.print()
    _console.print(Rule(style=DIM))
    _console.print()

    setup_line = Text()
    setup_line.append("  Setup", style=f"bold {TEXT}")
    setup_line.append(
        "  —  Configure your local AI stack and optional integrations.", style=SECONDARY
    )
    _console.print(setup_line)
    _console.print()


def _render_saved_summary(
    *,
    provider_label: str,
    model: str,
    saved_path: str,
    env_path: str,
    configured_integrations: list[str],
    credential_line: str = "system keychain",
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
        credentials system keychain              [SECONDARY key] [TEXT value]
        store       ~/.opensre/store.json        [SECONDARY key] [BRAND path]
    """
    from app.integrations.store import STORE_PATH

    integrations_str = "  ·  ".join(configured_integrations) if configured_integrations else "none"

    _console.print()
    _console.print(Rule(style=DIM))

    done = Text()
    done.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {HIGHLIGHT}")
    done.append("Done.", style=f"bold {TEXT}")
    _console.print(done)

    _console.print(Rule(style=DIM))
    _console.print()

    key_col = 14

    def _kv(key: str, value: str, value_style: str = TEXT) -> None:
        row = Text()
        row.append(f"    {key:<{key_col}}", style=SECONDARY)
        row.append(value, style=value_style)
        _console.print(row)

    _kv("provider", provider_label)
    _kv("model", model)
    _kv("services", integrations_str)
    _kv("config", saved_path, BRAND)
    _kv("env", env_path, BRAND)
    _kv("credentials", credential_line)
    _kv("store", str(STORE_PATH), BRAND)
    _console.print()


def _render_integration_result(
    service_label: str,
    result: IntegrationHealthResult,
    *,
    github_display_level: str | None = None,
) -> None:
    if result.github_mcp is not None:
        from app.integrations.github_mcp import (
            GitHubMcpDisplayDetailLevel,
            print_github_mcp_validation_report,
        )

        print_github_mcp_validation_report(
            result.github_mcp,
            console=_console,
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
    _console.print(status_line)

    for raw_line in detail.splitlines():
        line = raw_line.strip()
        if line:
            detail_text = Text()
            detail_text.append(f"     {line}", style=SECONDARY)
            _console.print(detail_text)


def _render_next_steps() -> None:
    """Print the 'What's next' section after successful onboarding.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
      What's next                                [SECONDARY label]
      ─────────────────────────────────────────  [DIM rule]
        opensre                                  [BRAND runnable command]
          Start the interactive agent
        opensre investigate -i alert.json        [BRAND runnable command]
          Run root-cause analysis on an alert
        opensre doctor                           [BRAND runnable command]
          Verify your environment setup
    """
    _console.print(Rule(style=DIM))

    section = Text()
    section.append("  What's next", style=SECONDARY)
    _console.print(section)

    _console.print(Rule(style=DIM))
    _console.print()

    _NEXT: tuple[tuple[str, str], ...] = (
        ("opensre", "Start the interactive agent"),
        (
            "opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json",
            "Run root-cause analysis on a sample alert",
        ),
        ("opensre doctor", "Verify your full environment setup"),
        ("opensre onboard", "Re-run this setup at any time"),
    )

    for cmd, description in _NEXT:
        cmd_line = Text()
        cmd_line.append(f"  {cmd}", style=f"bold {BRAND}")
        _console.print(cmd_line)
        desc_line = Text()
        desc_line.append(f"    {description}", style=SECONDARY)
        _console.print(desc_line)

    _console.print()
