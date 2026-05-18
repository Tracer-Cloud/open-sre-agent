"""Terminal rendering for RCA reports — Claude-style output."""

import math
import re

from rich.console import Console
from rich.text import Text

from app.cli.interactive_shell.ui.theme import BRAND, DIM, HIGHLIGHT, WARNING
from app.cli.support.output import get_output_format

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


_CONFIDENCE_LINE_RE = re.compile(r"^\*?Confidence:\*?\s+\w+", re.IGNORECASE)
_CONFIDENCE_BLOCK_LABELS = frozenset({"*Alternative hypotheses:*", "*Missing evidence:*"})


def _filter_confidence_sections(lines: list[str]) -> list[str]:
    """Drop lines rendered separately by _render_rich_confidence_block.

    Removes the inline Confidence: line and the Alternative hypotheses /
    Missing evidence sections (header + bullets) so they are not printed twice.
    Exits a skip-section on any ## heading or any other *Label:* section header.
    """
    result: list[str] = []
    in_skip = False
    for line in lines:
        stripped = line.strip()
        if in_skip:
            is_heading = bool(_HEADING_RE.match(stripped))
            is_other_label = (
                stripped.startswith("*")
                and stripped.endswith(":*")
                and stripped not in _CONFIDENCE_BLOCK_LABELS
            )
            if is_heading or is_other_label:
                in_skip = False  # fall through
            else:
                continue
        if stripped in _CONFIDENCE_BLOCK_LABELS:
            in_skip = True
            continue
        if _CONFIDENCE_LINE_RE.match(stripped):
            continue
        result.append(line)
    return result


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


def render_report(
    slack_message: str,
    root_cause_category: str | None = None,
    confidence_band: str = "",
    validity_score: float | None = None,
    ranked_hypotheses: list[str] | None = None,
    missing_evidence: list[str] | None = None,
) -> None:
    """Render the final RCA report to terminal."""
    from app.cli.support.output import render_completed_investigation_footer, stop_display

    stop_display()
    fmt = get_output_format()

    if not slack_message:
        if fmt == "rich":
            Console(highlight=False, force_terminal=True).print(
                Text.assemble(("  ● ", f"bold {WARNING}"), ("No report generated.", DIM))
            )
        else:
            print("No report generated.")
        return

    if fmt == "rich":
        _render_rich_report(
            slack_message,
            root_cause_category=root_cause_category,
            confidence_band=confidence_band,
            validity_score=validity_score,
            ranked_hypotheses=ranked_hypotheses or [],
            missing_evidence=missing_evidence or [],
        )
    else:
        _render_plain_report(
            slack_message,
            root_cause_category=root_cause_category,
            confidence_band=confidence_band,
            validity_score=validity_score,
            ranked_hypotheses=ranked_hypotheses or [],
            missing_evidence=missing_evidence or [],
        )

    # Print the investigation phase footer at the absolute bottom of the
    # RCA report (without "esc to cancel" — the investigation is complete).
    render_completed_investigation_footer()


def _render_rich_report(
    slack_message: str,
    root_cause_category: str | None = None,
    confidence_band: str = "",
    validity_score: float | None = None,
    ranked_hypotheses: list[str] | None = None,
    missing_evidence: list[str] | None = None,
) -> None:
    _ = root_cause_category
    console = Console(highlight=False, force_terminal=True, color_system="truecolor")
    console.print()

    lines = _filter_confidence_sections(slack_message.splitlines())
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

    _render_rich_confidence_block(
        console,
        confidence_band=confidence_band,
        validity_score=validity_score,
        ranked_hypotheses=ranked_hypotheses or [],
        missing_evidence=missing_evidence or [],
    )
    console.print()


def _render_rich_confidence_block(
    console: Console,
    confidence_band: str,
    validity_score: float | None,
    ranked_hypotheses: list[str],
    missing_evidence: list[str],
) -> None:
    if not confidence_band and validity_score is None:
        return

    console.print()
    band_upper = confidence_band.upper() if confidence_band else ""
    band_style = {"HIGH": "bold green", "MEDIUM": "bold yellow", "LOW": "bold red"}.get(
        band_upper, f"bold {TEXT}"
    )
    score_str = (
        f" ({int(validity_score * 100)}%)"
        if validity_score is not None and not math.isnan(validity_score)
        else ""
    )

    t = Text("  Confidence: ")
    t.append(f"{band_upper}{score_str}" if band_upper else score_str.strip(), style=band_style)
    console.print(t)

    if ranked_hypotheses:
        _render_rich_section_heading(console, "Alternative hypotheses")
        for h in ranked_hypotheses:
            _render_rich_bullet(console, h)

    if missing_evidence:
        _render_rich_section_heading(console, "Missing evidence")
        for item in missing_evidence:
            _render_rich_bullet(console, item)


def _render_plain_report(
    slack_message: str,
    root_cause_category: str | None = None,
    confidence_band: str = "",
    validity_score: float | None = None,
    ranked_hypotheses: list[str] | None = None,
    missing_evidence: list[str] | None = None,
) -> None:
    _ = root_cause_category
    print()
    filtered = "\n".join(_filter_confidence_sections(slack_message.splitlines()))
    clean = _strip_slack_links(_strip_mrkdwn(filtered))
    print(clean)

    if confidence_band or validity_score is not None:
        band_str = confidence_band.upper() if confidence_band else ""
        score_str = (
            f" ({int(validity_score * 100)}%)"
            if validity_score is not None and not math.isnan(validity_score)
            else ""
        )
        print(f"\nConfidence: {band_str}{score_str}".strip())

    if ranked_hypotheses:
        print("\nAlternative hypotheses:")
        for h in ranked_hypotheses:
            print(f"  - {h}")

    if missing_evidence:
        print("\nMissing evidence:")
        for item in missing_evidence:
            print(f"  - {item}")
