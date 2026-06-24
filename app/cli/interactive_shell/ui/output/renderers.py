from __future__ import annotations

from rich.text import Text

from app.cli.interactive_shell.ui.output.console_state import (
    _get_console,
    consume_footer_snapshot,
)
from app.cli.interactive_shell.ui.output.environment import (
    _is_silent_output,
    _safe_print,
    get_output_format,
)
from app.cli.interactive_shell.ui.output.labels import BADGE_STYLES
from app.cli.interactive_shell.ui.time_format import _elapsed_hms
from app.cli.interactive_shell.ui.theme import (
    BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)


def render_divider(width: int = 80) -> None:
    """Print a DIM-coloured dashed divider."""
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        _get_console().print(Text("┄" * width, style=DIM))
    else:
        _safe_print("─" * width)


def render_footer(
    phase: str,
    elapsed: float,
    model: str,
    mode: str,
    *,
    show_cancel: bool = True,
) -> None:
    """Print the persistent status footer line."""
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        t = Text()
        t.append(" ● ", style=f"bold {HIGHLIGHT}")
        t.append(f"{phase}  ", style=f"bold {SECONDARY}")
        t.append(f"{_elapsed_hms(elapsed)}  ", style=SECONDARY)
        if model:
            t.append(f"{model}  ", style=SECONDARY)
        t.append(f"{mode}  ", style=SECONDARY)
        if show_cancel:
            t.append("esc to cancel", style=DIM)
        _get_console().print(t)
    else:
        _safe_print(f"● {phase}  {elapsed:.1f}s  {model}  {mode}")


def render_completed_investigation_footer() -> None:
    """Print the captured phase footer once at the bottom of the report."""
    snapshot = consume_footer_snapshot()
    if snapshot is None or _is_silent_output():
        return
    phase, elapsed, model, mode = snapshot
    render_divider()
    render_footer(phase, elapsed, model, mode, show_cancel=False)


def render_event(
    event_type: str,
    message: str,
    *,
    insight: str | None = None,
    muted: bool = False,
    elapsed_s: float = 0.0,
    glyph: str = "✓",
    error: bool = False,
) -> None:
    """Print one typed event-log row."""
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        badge_label, badge_color = BADGE_STYLES.get(event_type, ("DIAG  ", WARNING))
        t = Text()
        t.append(f"{_elapsed_hms(elapsed_s)}  ", style=SECONDARY)
        if muted:
            t.append(f"{glyph}  ", style=SECONDARY)
            msg_style = SECONDARY
        elif error:
            t.append("✗  ", style=f"bold {ERROR}")
            msg_style = TEXT
        else:
            t.append(f"{glyph}  ", style=f"bold {HIGHLIGHT}")
            msg_style = TEXT
        t.append(badge_label, style=f"bold {badge_color}")
        t.append("  ·  ", style=DIM)
        t.append(message, style=msg_style)
        if insight:
            t.append(f"  ↳ {insight}", style=BRAND)
        _get_console().print(t)
    else:
        mark = "✗" if error else ("·" if muted else "✓")
        line = f"  {mark}  [{event_type}]  {message}"
        if insight:
            line += f"  ↳ {insight}"
        _safe_print(line)


def render_investigation_header(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    alert_id: str | None = None,
) -> None:
    sev_color = ERROR if severity.lower() == "critical" else WARNING
    fields = [
        ("Alert     ", alert_name, f"bold {TEXT}"),
        ("Pipeline  ", pipeline_name, BRAND),
        ("Severity  ", severity, f"bold {sev_color}"),
    ]
    if alert_id:
        fields.append(("Alert ID  ", alert_id, SECONDARY))

    if get_output_format() == "rich":
        console = _get_console()
        console.print()
        for label, value, style in fields:
            console.print(
                Text.assemble(
                    ("  ┃  ", f"bold {BRAND}"),
                    (label, SECONDARY),
                    (value, style),
                )
            )
        console.print()
    else:
        print()
        for label, value, _ in fields:
            print(f"{label}{value}")
        print()
