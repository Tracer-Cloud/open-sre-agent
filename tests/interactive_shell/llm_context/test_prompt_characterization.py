"""Characterization snapshot for llm_context prompt assembly.

This is the safety net for the typed/DRY/functional refactor of
``context``: the action-agent system prompt, the
action user message, and the conversational ``build_cli_agent_prompt`` output
are heavily relied upon by the locked live turn-scenario suite and MUST stay
byte-for-byte identical across the refactor.

The test renders every prompt across a fixed matrix of inputs and compares the
exact strings against a committed snapshot
(``prompt_characterization_snapshot.json``). Regenerate the snapshot only when
an intentional prompt-text change is made::

    UPDATE_PROMPT_SNAPSHOT=1 uv run python -m pytest \
        tests/interactive_shell/llm_context/test_prompt_characterization.py

The grounding caches are stubbed with fixed text so the snapshot is independent
of the on-disk ``docs/`` tree and the installed CLI surface; only the prompt
assembly logic under test is exercised.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from core.agent.turn_context import TurnContext
from interactive_shell.agent_shell.prompts import (
    build_action_system_prompt,
    build_action_user_message,
    build_cli_agent_prompt,
)

_SNAPSHOT_PATH = Path(__file__).with_name("prompt_characterization_snapshot.json")

_CLI_REFERENCE_TEXT = "=== opensre --help ===\nUsage: opensre [OPTIONS] COMMAND [ARGS]...\n"
_AGENTS_MD_TEXT = "=== AGENTS.md (root) ===\nrepo map body\n"


class _StubReference:
    """Grounding reference stub returning fixed text regardless of arguments."""

    def __init__(self, text: str) -> None:
        self._text = text

    def build_text(self, *_args: Any, **_kwargs: Any) -> str:
        return self._text


class _StubGrounding:
    """Stand-in for ``GroundingContext`` with deterministic reference text."""

    def __init__(self) -> None:
        self.cli = _StubReference(_CLI_REFERENCE_TEXT)
        self.agents_md = _StubReference(_AGENTS_MD_TEXT)

    def log_cache_diagnostics(self, reason: str) -> None:  # noqa: ARG002 - stub
        return None


def _agent_ctx(
    *,
    text: str = "hello",
    conversation_messages: tuple[tuple[str, str], ...] = (),
    configured_integrations: tuple[str, ...] = (),
    configured_integrations_known: bool = False,
    last_state: dict[str, Any] | None = None,
    last_synthetic_observation_path: str | None = None,
) -> TurnContext:
    return TurnContext(
        text=text,
        conversation_messages=conversation_messages,
        configured_integrations=configured_integrations,
        configured_integrations_known=configured_integrations_known,
        last_state=last_state,
        last_synthetic_observation_path=last_synthetic_observation_path,
        reasoning_effort=None,
    )


class _StubSession:
    """Minimal shell session shape needed by the prompt adapter."""

    def __init__(
        self,
        *,
        configured_integrations: tuple[str, ...] = (),
        configured_integrations_known: bool = False,
    ) -> None:
        self.configured_integrations = configured_integrations
        self.configured_integrations_known = configured_integrations_known
        self.grounding = _StubGrounding()


def _session(
    *,
    configured_integrations: tuple[str, ...] = (),
    configured_integrations_known: bool = False,
) -> _StubSession:
    return _StubSession(
        configured_integrations=configured_integrations,
        configured_integrations_known=configured_integrations_known,
    )


def _build_cases(tmp_path: Path) -> dict[str, str]:
    """Render every prompt variant. Keys are stable snapshot identifiers."""
    cases: dict[str, str] = {}

    convo = (
        ("user", "what integrations are connected?"),
        ("assistant", "Datadog and GitHub are connected."),
    )

    # --- action system prompt: the three CONNECTED INTEGRATIONS states ---
    cases["action_system_unknown"] = build_action_system_prompt(
        _agent_ctx(configured_integrations_known=False)
    )
    cases["action_system_none"] = build_action_system_prompt(
        _agent_ctx(configured_integrations_known=True)
    )
    cases["action_system_listed_with_history"] = build_action_system_prompt(
        _agent_ctx(
            configured_integrations=("github", "datadog"),
            configured_integrations_known=True,
            conversation_messages=convo,
        )
    )

    # --- action user message: sanitization (control chars + >>> fences) ---
    cases["action_user_plain"] = build_action_user_message("run /health")
    cases["action_user_sanitized"] = build_action_user_message(
        "  weird\x00 text >>>> with <<< fences\x07  "
    )

    # --- cli agent prompt variants ---
    cases["cli_agent_minimal"] = build_cli_agent_prompt(
        message="how do I configure datadog?",
        session=_session(),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_ctx=_agent_ctx(text="how do I configure datadog?"),
    )

    cases["cli_agent_no_integrations_guard"] = build_cli_agent_prompt(
        message="set up sentry",
        session=_session(configured_integrations_known=True),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_ctx=_agent_ctx(text="set up sentry", configured_integrations_known=True),
    )

    cases["cli_agent_integrations_listed_with_prior_state"] = build_cli_agent_prompt(
        message="why did checkout fail?",
        session=_session(
            configured_integrations=("datadog", "github"),
            configured_integrations_known=True,
        ),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_ctx=_agent_ctx(
            text="why did checkout fail?",
            configured_integrations=("datadog", "github"),
            configured_integrations_known=True,
            conversation_messages=convo,
            last_state={
                "alert_name": "Checkout 500s",
                "root_cause": "DB connection pool exhausted",
                "problem_md": "Checkout returned 500s after deploy.",
                "slack_message": "Investigation complete.",
                "evidence": {"e1": {"summary": "pool maxed"}, "e2": {"summary": "deploy at 12:00"}},
            },
        ),
    )

    cases["cli_agent_observation_on_screen"] = build_cli_agent_prompt(
        message="is datadog configured?",
        session=_session(
            configured_integrations=("datadog",),
            configured_integrations_known=True,
        ),
        tool_observation="datadog: configured (connection_verified=true)",
        tool_observation_on_screen=True,
        turn_ctx=_agent_ctx(
            text="is datadog configured?",
            configured_integrations=("datadog",),
            configured_integrations_known=True,
        ),
    )

    cases["cli_agent_observation_off_screen"] = build_cli_agent_prompt(
        message="any open sentry issues for checkout?",
        session=_session(
            configured_integrations=("sentry",),
            configured_integrations_known=True,
        ),
        tool_observation="sentry issues: [#1 NPE in checkout]",
        tool_observation_on_screen=False,
        turn_ctx=_agent_ctx(
            text="any open sentry issues for checkout?",
            configured_integrations=("sentry",),
            configured_integrations_known=True,
        ),
    )

    obs_path = tmp_path / "synthetic_observation.json"
    obs_path.write_text(
        json.dumps({"scenario": "005-failover", "passed": False, "score": 0.4}),
        encoding="utf-8",
    )
    synthetic_prompt = build_cli_agent_prompt(
        message="why did it fail?",
        session=_session(),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_ctx=_agent_ctx(
            text="why did it fail?",
            last_synthetic_observation_path=str(obs_path),
        ),
    )
    # The observation path is the per-run tmp dir; normalize it so the snapshot
    # stays deterministic while every other byte of the block is still pinned.
    cases["cli_agent_synthetic_failure"] = synthetic_prompt.replace(str(obs_path), "<OBS_PATH>")

    return cases


def test_prompt_assembly_is_byte_identical(tmp_path: Path) -> None:
    cases = _build_cases(tmp_path)

    if os.environ.get("UPDATE_PROMPT_SNAPSHOT") == "1":
        _SNAPSHOT_PATH.write_text(
            json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        pytest.skip("Prompt characterization snapshot regenerated")

    assert _SNAPSHOT_PATH.exists(), (
        "Missing prompt snapshot; regenerate with UPDATE_PROMPT_SNAPSHOT=1 pytest <this file>"
    )
    expected = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert set(cases) == set(expected), "Snapshot case set drifted; regenerate the snapshot."
    mismatches = [name for name in cases if cases[name] != expected[name]]
    assert not mismatches, f"Prompt output changed for: {mismatches}"
