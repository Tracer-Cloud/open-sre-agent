"""Terminal rendering for RCA reports — Claude-style output."""

import io
import re
import shutil
import sys

from rich.console import Console
from rich.text import Text

from app.observability import get_output_format
from app.ui_theme import BRAND, DIM, HIGHLIGHT, WARNING

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+")
# Matches Slack-style links: <url|label> or <url>
_SLACK_LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")


def _rich_line_with_links(text: str) -> Text:
    """Convert a plain/Slack-mrkdwn string into a Rich Text with blue hyperlinks."""
    result = Text()
    cursor = 0

    for m in _SLACK_LINK_RE.finditer(text):
        # Text before the match
        if m.start() > cursor:
            result.append(text[cursor : m.start()])
        url = m.group(1)
        label = m.group(2) or url
        result.append(label, style=f"link {url} bold {BRAND} underline")
        cursor = m.end()

    remaining = text[cursor:]
    # Linkify any bare https?:// URLs left in remaining text
    sub_cursor = 0
    for m in _URL_RE.finditer(remaining):
        if m.start() > sub_cursor:
            result.append(remaining[sub_cursor : m.start()])
        url = m.group(0).rstrip(".,;)")
        result.append(url, style=f"link {url} bold {BRAND} underline")
        sub_cursor = m.end()
    if sub_cursor < len(remaining):
        result.append(remaining[sub_cursor:])

    return result


def _strip_slack_links(text: str) -> str:
    """Convert Slack <url|label> to plain 'label (url)' for plain text mode."""

    def _repl(m: re.Match[str]) -> str:
        url = str(m.group(1))
        label = m.group(2)
        return f"{label} ({url})" if label else url

    return _SLACK_LINK_RE.sub(_repl, text)


def _strip_mrkdwn(text: str) -> str:
    """Remove Slack mrkdwn bold markers (*text*) for plain output."""
    return re.sub(r"\*([^*\n]+)\*", r"\1", text)


# ─────────────────────────────────────────────────────────────────────────────
# Section renderers
# ─────────────────────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*?([^*]+)\*\*?")


def _render_rich_section_heading(console: Console, title: str) -> None:
    from rich.rule import Rule

    console.print()
    console.print(Rule(f"[bold {HIGHLIGHT}] {title} [/]", style=DIM, align="left"))
    console.print()


def _render_rich_bullet(console: Console, line: str, *, indent: int = 4) -> None:
    """Render a bullet line with links resolved."""
    body = line.lstrip("•● -").strip()
    t = Text(" " * indent + "· ")
    t.append_text(_rich_line_with_links(body))
    console.print(t)


def _render_rich_numbered(console: Console, line: str) -> None:
    """Render a numbered trace step."""
    m = re.match(r"^(\d+)\.\s+(.+)$", line)
    if not m:
        _render_rich_bullet(console, line)
        return
    num, body = m.group(1), m.group(2)
    t = Text(f"    {num}. ")
    t.style = DIM
    t.append_text(_rich_line_with_links(body))
    console.print(t)


def _render_rich_evidence_item(console: Console, line: str) -> None:
    """Render a cited evidence item (lines starting with '- ')."""
    body = line.lstrip("- ").strip()
    t = Text("    — ")
    t.append_text(_rich_line_with_links(body))
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
# Main render entry points
# ─────────────────────────────────────────────────────────────────────────────


def _report_width() -> int:
    """Best-effort terminal width for the buffered report render."""
    return max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)


def _emit(rendered: str) -> None:
    """Write a pre-rendered report in a single TTY-safe stdout call.

    The report is built into one string and emitted with a single
    ``sys.stdout.write``. When stdout is a TTY, line endings are normalised to
    ``\\r\\n`` so that under prompt_toolkit's ``patch_stdout(raw=True)`` (the
    interactive REPL) every line starts at column zero. Rendering line-by-line
    through a fresh Rich Console there interleaves with the proxy and the report
    body is dropped from scrollback; one normalised write avoids that.

    The ``isatty()`` branch is intentional, not dead code. Under ``patch_stdout``
    ``sys.stdout`` is prompt_toolkit's ``StdoutProxy``, whose ``isatty()``
    delegates to the real underlying stdout — and the REPL only enables
    ``patch_stdout`` when that is a TTY — so this returns ``True`` in the REPL.
    In ``raw=True`` mode the proxy writes text verbatim (``write_raw``) without
    converting bare ``\\n`` to ``\\r\\n``, so we must normalise here ourselves
    (mirrors ``rendering._normalize_repl_line_endings``). For non-TTY stdout
    (piped/captured/tests) the text is written as-is.
    """
    if sys.stdout.isatty():
        rendered = rendered.replace("\r\n", "\n").replace("\n", "\r\n")
    sys.stdout.write(rendered)
    sys.stdout.flush()


def render_report(slack_message: str) -> None:
    """Render the final RCA report to terminal."""
    from app.observability import (
        get_progress_tracker,
        render_completed_investigation_footer,
    )

    get_progress_tracker().stop()
    fmt = get_output_format()

    if not slack_message:
        if fmt == "rich":
            buf = io.StringIO()
            Console(file=buf, highlight=False, force_terminal=True, width=_report_width()).print(
                Text.assemble(("  ● ", f"bold {WARNING}"), ("No report generated.", DIM))
            )
            _emit(buf.getvalue())
        else:
            _emit("No report generated.\n")
        return

    if fmt == "rich":
        _emit(_render_rich_report(slack_message))
    else:
        _emit(_render_plain_report(slack_message))

    # Print the investigation phase footer at the absolute bottom of the
    # RCA report (without "esc to cancel" — the investigation is complete).
    render_completed_investigation_footer()


def _render_rich_report(slack_message: str) -> str:
    buf = io.StringIO()
    console = Console(
        file=buf,
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        width=_report_width(),
    )
    console.print()

    lines = slack_message.splitlines()
    in_evidence = False

    for line in lines:
        stripped = line.strip()

        # Section headings  (## Findings / ## Investigation Trace)
        m = _HEADING_RE.match(stripped)
        if m:
            _render_rich_section_heading(console, m.group(1))
            in_evidence = False
            continue

        # *Cited Evidence:* label
        if stripped in ("*Cited Evidence:*", "Cited Evidence:"):
            _render_rich_section_heading(console, "Cited Evidence")
            in_evidence = True
            continue

        # Evidence items  (lines starting with "- ")
        if stripped.startswith("- ") and in_evidence:
            _render_rich_evidence_item(console, stripped)
            continue

        # Bullet points  (• or - at start)
        if stripped.startswith(("• ", "● ", "- ")) and not in_evidence:
            _render_rich_bullet(console, stripped)
            continue

        # Numbered trace steps  "1. …"
        if re.match(r"^\d+\.", stripped):
            _render_rich_numbered(console, stripped)
            continue

        # Code spans  "`…`"
        if stripped.startswith("`") and stripped.endswith("`"):
            t = Text(f"    {stripped}", style=BRAND)
            console.print(t)
            continue

        # Skip Timing line — already visible in spinner timings per step
        if stripped.startswith("Timing:"):
            continue

        # Alert ID meta
        if stripped.startswith(("*Alert ID:*", "Alert ID:")):
            clean = _BOLD_RE.sub(r"\1", stripped)
            console.print(Text(f"    {clean}", style=DIM))
            continue

        # Blank lines — pass through (skip double blanks)
        if not stripped:
            continue

        # Default: render with link highlighting
        t = Text("  ")
        t.append_text(_rich_line_with_links(stripped))
        console.print(t)

    console.print()
    return buf.getvalue()


def _render_plain_report(slack_message: str) -> str:
    clean = _strip_slack_links(_strip_mrkdwn(slack_message))
    return f"\n{clean}\n"
