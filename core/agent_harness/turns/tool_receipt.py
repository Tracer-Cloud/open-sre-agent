"""Tool-result rendering for the local console versus an external chat surface.

A tool whose payload carries no summary-ish key falls back to being dumped
verbatim into user-visible text. Locally that is what you want. Delivered to a
chat surface it publishes whatever the tool read — pod logs are the worst case,
because their content is customer data the agent merely relayed.

So a result is rendered twice: in full for the terminal, and as a short receipt
for chat. This is the boundary redaction AGENTS.md already requires for
exception detail (CWE-209 / ``py/stack-trace-exposure``), applied to the same
class of problem — the shared turn engine keeps detail for local dev, and only
the external surface is reduced.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Arguments listed in a receipt before the rest are elided.
RECEIPT_MAX_ARGS: int = 4
#: Per-argument value budget in a receipt; identifiers stay legible, blobs do not.
RECEIPT_MAX_ARG_VALUE_CHARS: int = 40
#: Appended once per response, not once per receipt — a turn can produce many.
NO_ANSWER_FOOTER: str = "The turn produced no answer, so the raw output was not sent here."

_RECEIPT_WITH_ARGS = "{tool_name} ({args}) returned {size}. No summary available."
_RECEIPT_NO_ARGS = "{tool_name} returned {size}. No summary available."
_SIZE_WITH_COUNT = "{count} records ({size})"
_ARGS_ELIDED = "…"
_VALUE_ELIDED = "…"


@dataclass(frozen=True)
class RenderedToolResult:
    """One generic tool result, rendered for the local console and for chat.

    ``external`` differs from ``local`` only when ``local`` is the raw-payload
    fallback; when the tool produced a real summary the two are the same string.
    """

    local: str
    external: str

    @classmethod
    def summary(cls, text: str) -> RenderedToolResult:
        """Both surfaces get ``text`` — the tool spoke for itself."""
        return cls(local=text, external=text)


def format_tool_dump_receipt(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    payload_chars: int,
    item_count: int | None = None,
) -> str:
    """Describe a tool result that is too raw to send to a chat surface.

    Deliberately carries no success or failure marker. Every result that
    reaches here ran fine — an error payload is rendered from its own ``error``
    key well before this — so a ``✗`` would report a failure that did not
    happen.
    """
    size = format_char_size(payload_chars)
    if item_count is not None:
        size = _SIZE_WITH_COUNT.format(count=item_count, size=size)
    args = _format_arguments(arguments)
    if not args:
        return _RECEIPT_NO_ARGS.format(tool_name=tool_name, size=size)
    return _RECEIPT_WITH_ARGS.format(tool_name=tool_name, args=args, size=size)


def _format_arguments(arguments: Mapping[str, Any]) -> str:
    """Render the identifying arguments as ``k=v`` pairs, bounded on both axes."""
    rendered = [
        f"{key}={_clamp(str(value), RECEIPT_MAX_ARG_VALUE_CHARS)}"
        for key, value in list(arguments.items())[:RECEIPT_MAX_ARGS]
    ]
    if len(arguments) > RECEIPT_MAX_ARGS:
        rendered.append(_ARGS_ELIDED)
    return ", ".join(rendered)


def _clamp(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - len(_VALUE_ELIDED)] + _VALUE_ELIDED


def format_char_size(chars: int) -> str:
    """Render a character count as ``"812 chars"`` / ``"60.0 KB"`` / ``"1.1 MB"``.

    Decimal units, and the rounding decides the unit: 999_999 chars reads as
    ``"1.0 MB"``, never ``"1000.0 KB"``.
    """
    if chars < 1_000:
        return f"{chars} chars"
    if round(chars / 1_000, 1) < 1_000:
        return f"{chars / 1_000:.1f} KB"
    return f"{chars / 1_000_000:.1f} MB"
