"""Session-end extraction of durable facts into long-term memory.

One best-effort LLM pass over a chat transcript. Lifecycle callers schedule it
via :func:`schedule_memory_extraction`. Process-exit close runs extraction
synchronously (after resources are released) so durable facts always persist.
Rotation paths use a daemon thread so inbound handling is not stalled. Never
raises out: any failure (LLM unavailable, malformed output, disk errors) is
logged and ignored. Environment gates can disable the whole feature or only
the extraction pass.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
from typing import Any, Protocol

from core.domain.memory import (
    MEMORY_TYPES,
    auto_extract_enabled,
    find_memory_safety_issues,
    redact_memory_unsafe_text,
    render_prompt_index,
    save_memory,
)

logger = logging.getLogger(__name__)

MIN_CHAT_MESSAGES = 2
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


def schedule_memory_extraction(
    messages: list[tuple[str, str]],
    *,
    wait_for_completion: bool = False,
) -> None:
    """Snapshot ``messages`` and extract memories.

    When ``wait_for_completion`` is true (session ``close`` / process exit), run
    synchronously so durable facts always land before the process ends. Rotation
    paths leave it false and use a daemon thread because the process keeps
    running and must not stall inbound handling on the provider.
    """
    if not auto_extract_enabled():
        return
    snapshot = list(messages)
    if len(snapshot) < MIN_CHAT_MESSAGES:
        return
    if wait_for_completion:
        _extract_memories_safe(snapshot)
        return
    # Run the daemon thread inside a copy of the current context so the per-turn
    # storage scope (ContextVar set by bound_storage_scope) is inherited: without
    # it current_scope() is None on the thread and save_memory() would resolve to
    # the org root instead of users/<actor_id>/memory/, misfiling the user's
    # extracted facts where their in-scope turns never read them.
    ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=ctx.run,
        args=(_extract_memories_safe, snapshot),
        name="opensre-memory-extraction",
        daemon=True,
    )
    thread.start()


def extract_memories_from_session(session: _ChatSession) -> None:
    """Run one extraction pass synchronously; silent no-op when gated off."""
    messages = list(getattr(session, "cli_agent_messages", []) or [])
    extract_memories_from_messages(messages)


def extract_memories_from_messages(messages: list[tuple[str, str]]) -> None:
    """Run one extraction pass over ``messages``; never raises."""
    _extract_memories_safe(list(messages))


def _extract_memories_safe(messages: list[tuple[str, str]]) -> None:
    try:
        if not auto_extract_enabled():
            return
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

    transcript = redact_memory_unsafe_text(
        format_recent_conversation(messages, max_turns=_MAX_TRANSCRIPT_TURNS)
    )
    prompt = _EXTRACTION_PROMPT.format(
        max_memories=MAX_MEMORIES_PER_SESSION,
        memory_index=render_prompt_index() or "(no memories stored yet)",
        transcript=transcript,
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
        issues = find_memory_safety_issues(description, content)
        if issues:
            logger.debug(
                "[memory] skipped extracted memory %r due to safety rules: %s",
                name,
                ",".join(issue.rule for issue in issues),
            )
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


__all__ = [
    "extract_memories_from_messages",
    "extract_memories_from_session",
    "schedule_memory_extraction",
]
