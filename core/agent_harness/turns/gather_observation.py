"""Gather observation blocks: structured outcome + plain Tool/Result parsing.

Gather renders executed tools as ``\\n\\n``-joined paragraphs, **newest
first** (so head truncation keeps late count/SQL results)::

    Tool: <name>
    Arguments: …
    Result: …

Callers that only have the rendered string should split with
:func:`iter_tool_result_blocks` (same shape as
``pending_offer._compact_evidence_lines``) — not regex. Prefer
:class:`GatheredEvidence.tool_results` when the gather loop still has the
raw ``(tool_name, payload)`` pairs (those stay chronological).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GatheredEvidence:
    """One gather pass: prompt text plus structured tool payloads.

    ``observation`` is what the answer path injects into the prompt.
    ``tool_results`` are ``(tool_name, raw_output)`` pairs from the gather
    loop — used for typed checks (e.g. ``tool_unavailable``) without
    scraping the rendered string.
    """

    observation: str
    tool_results: tuple[tuple[str, Any], ...] = ()
    #: The gather loop stopped at its iteration cap instead of concluding, so
    #: these results are whatever it had reached, not a finished answer.
    truncated: bool = False


def iter_tool_result_blocks(observation: str) -> Iterator[tuple[str, str]]:
    """Yield ``(tool_line, result_text)`` for each Tool/Result paragraph.

    Matches the gather formatter in ``evidence_driver._format_observation``:
    blocks separated by a blank line, each starting with ``Tool: ``, result
    after ``\\nResult: `` (``Arguments:`` may sit between).
    """
    text = (observation or "").strip()
    if not text:
        return
    for block in text.split("\n\n"):
        if not block.startswith("Tool: "):
            continue
        name = block.split("\n", 1)[0][len("Tool: ") :].strip()
        _, _, result = block.partition("\nResult: ")
        yield name, result


def coerce_gathered_evidence(
    raw: str | GatheredEvidence | None,
) -> GatheredEvidence | None:
    """Normalize gather return values (legacy ``str`` or :class:`GatheredEvidence`)."""
    if raw is None:
        return None
    if isinstance(raw, GatheredEvidence):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        return GatheredEvidence(observation=text) if text else None
    return None


def tool_results_from_executed(
    executed: Sequence[tuple[Any, Any]],
) -> tuple[tuple[str, Any], ...]:
    """Project gather ``(tool_call, output)`` pairs to ``(name, output)``."""
    out: list[tuple[str, Any]] = []
    for tc, output in executed:
        name = str(getattr(tc, "name", "") or "").strip()
        if not name:
            continue
        out.append((name, output))
    return tuple(out)


__all__ = [
    "GatheredEvidence",
    "coerce_gathered_evidence",
    "iter_tool_result_blocks",
    "tool_results_from_executed",
]
