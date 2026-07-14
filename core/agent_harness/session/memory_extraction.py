"""Session-end extraction of durable facts into long-term memory.

One best-effort LLM pass over the session's chat transcript, run from
:meth:`SessionManager.close`. Never raises out: any failure (LLM unavailable,
malformed output, disk errors) is logged and the session teardown proceeds.
Gated by ``OPENSRE_MEMORY_AUTOEXTRACT_DISABLED`` / ``OPENSRE_MEMORY_DISABLED``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from core.domain.memory import (
    MEMORY_TYPES,
    auto_extract_enabled,
    render_prompt_index,
    save_memory,
)

logger = logging.getLogger(__name__)

MIN_CHAT_MESSAGES = 4
MAX_MEMORIES_PER_SESSION = 5
_MAX_TRANSCRIPT_TURNS = 30

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)

_EXTRACTION_PROMPT = """\
You maintain the long-term memory of an SRE assistant. Below is the transcript
of one terminal session and the index of memories already stored.

Extract ONLY durable knowledge worth keeping across future sessions:
- who the user is (name, role, how they like to work)
- infrastructure facts and conventions (cluster names, naming schemes, known-flaky services)
- stable preferences the user stated
- lessons learned from incidents or investigations

Do NOT extract: transient session state, one-off command outputs, secrets,
credentials, or anything already captured by an existing memory (unless it
needs updating — then reuse the existing name exactly).

Return a JSON array (no prose). Each item:
{{"name": "kebab-case-slug", "type": "user|infrastructure|preference|investigation_learning",
  "description": "one line, max 200 chars", "content": "full markdown body"}}

Return [] when nothing qualifies. At most {max_memories} items.

--- Existing memory index ---
{memory_index}

--- Session transcript ---
{transcript}
"""


class _ChatSession(Protocol):
    """The slice of :class:`SessionCore` the extractor reads."""

    cli_agent_messages: list[tuple[str, str]]


def extract_memories_from_session(session: _ChatSession) -> None:
    """Run one extraction pass; silent no-op when gated off or not worthwhile."""
    try:
        if not auto_extract_enabled():
            return
        messages = list(getattr(session, "cli_agent_messages", []) or [])
        if len(messages) < MIN_CHAT_MESSAGES:
            return
        response = _invoke_extraction_llm(messages)
        if not response:
            return
        _save_extracted(_parse_extraction(response))
    except Exception:
        logger.debug("[memory] session-end extraction failed", exc_info=True)


def _invoke_extraction_llm(messages: list[tuple[str, str]]) -> str:
    from core.agent_harness.prompts.conversation_memory import format_recent_conversation

    try:
        from core.llm.factory import LLMRole, get_llm

        llm = get_llm(LLMRole.CLASSIFICATION)
    except Exception:
        logger.debug("[memory] extraction LLM unavailable", exc_info=True)
        return ""

    prompt = _EXTRACTION_PROMPT.format(
        max_memories=MAX_MEMORIES_PER_SESSION,
        memory_index=render_prompt_index() or "(no memories stored yet)",
        transcript=format_recent_conversation(messages, max_turns=_MAX_TRANSCRIPT_TURNS),
    )
    result = llm.invoke(prompt)
    content = getattr(result, "content", result)
    return content if isinstance(content, str) else str(content)


def _parse_extraction(response: str) -> list[dict[str, Any]]:
    """Pull the JSON array out of the LLM response; [] for anything unusable."""
    text = response.strip()
    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return []
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _save_extracted(items: list[dict[str, Any]]) -> None:
    from core.domain.memory import is_valid_slug, slugify

    saved = 0
    for item in items:
        if saved >= MAX_MEMORIES_PER_SESSION:
            break
        name = item.get("name")
        memory_type = item.get("type")
        description = item.get("description")
        content = item.get("content")
        if not isinstance(name, str) or memory_type not in MEMORY_TYPES:
            continue
        if not isinstance(description, str) or not description.strip():
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        slug = slugify(name)
        if not is_valid_slug(slug):
            continue
        if save_memory(
            slug=slug,
            memory_type=memory_type,  # type: ignore[arg-type]
            description=description,
            body=content,
        ):
            saved += 1
    if saved:
        logger.debug("[memory] session-end extraction saved %d memories", saved)


__all__ = ["extract_memories_from_session"]
