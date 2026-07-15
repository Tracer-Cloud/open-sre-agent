"""Unit tests for shell action-agent prompt context."""

from __future__ import annotations

from core.agent_harness.prompts import (
    _SYSTEM_PROMPT_BASE,
    build_action_system_prompt,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
)
from core.agent_harness.prompts.assistant import build_cli_agent_prompt_from_provider
from core.agent_harness.prompts.assistant_agent_prompt import build_handoff_guidance_block
from core.agent_harness.prompts.conversation_memory import NO_HISTORY_PLACEHOLDER
from core.agent_harness.prompts.skills_loader import (
    SKILLS_HEADER,
    load_skills_block,
    skills_dir,
)
from core.agent_harness.prompts.skills_loader import (
    load_skills_block as cached_load_skills_block,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _ctx(
    *,
    messages: list[tuple[str, str]] | None = None,
    integrations: tuple[str, ...] = (),
    integrations_known: bool = False,
) -> TurnSnapshot:
    return TurnSnapshot(
        text="",
        conversation_messages=tuple(messages or []),
        configured_integrations=integrations,
        configured_integrations_known=integrations_known,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )


def test_recent_conversation_block_contains_history_lines() -> None:
    ctx = _ctx(
        messages=[
            ("user", "how can I remove github integration"),
            ("assistant", "Use /integrations remove github or /integrations list."),
        ]
    )
    block = recent_conversation_block(ctx)
    assert "RECENT CONVERSATION" in block
    assert "User: how can I remove github integration" in block
    assert "Assistant: Use /integrations remove github or /integrations list." in block


def test_recent_conversation_block_placeholder_without_history() -> None:
    assert NO_HISTORY_PLACEHOLDER in recent_conversation_block(_ctx())


def test_prior_action_facts_block_surfaces_telegram_followup_values() -> None:
    ctx = _ctx(
        messages=[
            ("user", "Can you send the weather of both hawaii and antartica to slack?"),
            (
                "assistant",
                "Hawaii: +28C\n"
                "Antarctica: -24C\n"
                'slack_send_message input: {"message": "Hawaii: +28C\\nAntarctica: -24C"}\n'
                'slack_send_message result: {"sent": true}',
            ),
            ("user", "Write it in a nicer message and compare to London"),
            ("assistant", "London: +22C"),
        ]
    )

    block = prior_action_facts_block(ctx)
    assert "PRIOR ACTION FACTS" in block
    assert "Hawaii: +28C" in block
    assert "Antarctica: -24C" in block
    assert "London: +22C" in block
    assert "slack_send_message input" in block


def test_system_prompt_documents_followup_resolution() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "do both" in prompt
    assert "recent conversation" in prompt
    assert "assistant_handoff" in prompt
    assert "want me to: offering more slack roster" in prompt
    assert "yes — please" in prompt


def test_system_prompt_requires_same_response_for_slash_then_investigation() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "connect with /remote and then investigate" in prompt
    assert "same planner response" in prompt
    assert "do not stop after the slash command" in prompt
    assert "valid investigation payload" in prompt


def test_system_prompt_maps_setup_requests_to_slash_invoke() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "configure, connect, set up, add, or enable" in prompt
    assert 'slash_invoke(command="/integrations", args=["setup", "<service>"])' in prompt
    assert 'slash_invoke(command="/mcp", args=["connect", "<server>"])' in prompt
    assert "do not hand off just to tell the user" in prompt


def test_system_prompt_hands_off_natural_language_slash_status_questions() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    compact_prompt = " ".join(prompt.split())
    assert 'literal slash text like "/model"' in prompt
    assert '"run /model show"' in prompt
    assert "natural-language questions about the active model/provider" in compact_prompt
    assert "which model is being used now?" in compact_prompt
    assert "what tools can you use?" in compact_prompt
    assert "what is my session status?" in compact_prompt
    assert "must use assistant_handoff" in compact_prompt
    assert "unless a read-only discovery exception below explicitly maps" in compact_prompt
    assert (
        "do not run a slash command just because the command can display related information"
        in compact_prompt
    )
    assert "for model/provider shell-state questions specifically" in compact_prompt
    assert "unless the user explicitly typed a slash command" in compact_prompt
    assert "current llm settings in its environment context" in compact_prompt


def test_system_prompt_routes_slack_teammate_reads_to_action_tools() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    compact = prompt.replace(" ", "")
    assert "slack teammate requests are action tools" in prompt
    assert "not handoffs" in prompt
    assert 'slack_read_messages(channel="#opensre-slack-testing"' in compact
    assert "are not covered by the data-retrieval handoff rule" in prompt
    assert "do not emit assistant_handoff for these" in prompt
    assert "roster / people questions ignore channel_id" in prompt
    assert "slack_list_team_members only" in prompt
    assert "never slack_read_messages" in prompt


def test_system_prompt_keeps_bare_alert_blob_as_handoff() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "a bare pasted alert blob with no instruction remains assistant_handoff" in prompt
    assert "pasted alert blob / bare incident statement" in prompt
    assert "with no\ninstruction" in prompt
    assert "not such a question — hand it off" in prompt


def test_system_prompt_hands_off_when_delivery_tool_unavailable() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    compact_prompt = " ".join(prompt.split())
    assert "delivery tool unavailable — never fabricate a command to deliver" in prompt
    assert "matching send tool" in prompt
    assert "that channel is not configured" in compact_prompt
    assert "do not invent or guess a slash/cli subcommand to deliver" in compact_prompt
    assert "`/messaging send slack …` is not a real command" in compact_prompt
    assert "route the user to enable it" in compact_prompt
    assert "this applies even mid-chain" in compact_prompt


def test_system_prompt_preserves_bare_numeric_synthetic_mapping() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "run synthetic test 005 now" in prompt
    assert 'scenario="005-failover"' in prompt
    assert "never substitute a different numbered" in prompt


def test_connected_integrations_block_renders_state() -> None:
    assert "unknown" in connected_integrations_block(_ctx())

    none_block = connected_integrations_block(_ctx(integrations=(), integrations_known=True))
    assert "none" in none_block
    assert "explicit investigate instructions still emit investigation_start" in none_block.lower()

    listed = connected_integrations_block(
        _ctx(
            integrations=("sentry", "github", "posthog_mcp"),
            integrations_known=True,
        )
    )
    assert "github, posthog_mcp, sentry" in listed


def test_skills_loader_bundles_architecture_audit_skill() -> None:
    cached_load_skills_block.cache_clear()
    skill_dir = skills_dir() / "architecture_audit"
    skill = skill_dir / "SKILL.md"
    template = skill_dir / "architecture_audit_report.md"
    assert skill.is_file()
    assert template.is_file()

    block = load_skills_block()
    assert "SKILLS" in block
    assert "ARCHITECTURE AUDIT SKILL" in block
    assert "WHEN TO USE" in block
    assert "summarize this repo's architecture" in block
    assert "architecture_clone_repo" in block
    assert "scan_architecture_imports" not in block
    assert "scan_module_placement" not in block
    assert "architecture_cleanup_repo" in block
    assert "architecture_save_observations" in block
    assert "shell_run" in block
    assert "Never end the turn with shell_run" in block
    assert "quiet=true" in block
    assert "four separate shell_run" in block or "Four separate" in block or "IMPORT pass" in block
    assert "IMPORT pass" in block
    assert "PLACEMENT pass" in block
    assert "SIZE pass" in block
    assert "SHIM pass" in block
    assert "You write each bash" in block
    assert "about 15" in block
    assert "Budget: clone + ≤3 agent-scan shell_run + 4 heuristic shell passes + cleanup" in block
    assert "~/.opensre/{session_id}/{repo_name}-architecture-audit-{uuid}.md" in block
    assert "AGENTS-style docs" in block
    assert "AGENT SCAN" in block
    assert "deletion test" in block
    assert "CONTEXT.md" in block
    assert "max 3 shell_run" in block
    assert "BEFORE any" in block or "before heuristics" in block
    assert 'Decide what "large" means' in block
    assert "do NOT limit to Python" in block
    assert "do NOT skip non-Python" in block
    assert ".java" in block and ".rs" in block
    assert "find_architecture_violations" not in block
    report_path = (
        "core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md"
    )
    assert f"REPORT TEMPLATE from `{report_path}`" in block
    assert "### Repository summary" in block
    assert "### Coverage and limitations" in block
    assert "### Findings by severity" in block
    assert "| Severity | Path | Finding |" in block
    assert "### Recommended sequencing" in block
    assert "Fill this template VERBATIM" in block
    assert "Do NOT wrap filled values in backticks" in block
    assert "contract source" in block
    assert "calibrate to the repo" in block
    assert "grounded in AGENT SCAN context" in block
    assert report_path in block

    prompt = build_action_system_prompt(_ctx(messages=[("user", "audit architecture")]))
    assert "ARCHITECTURE AUDIT SKILL" in prompt
    assert "### Findings by severity" in prompt
    cached_load_skills_block.cache_clear()


def test_action_system_prompt_includes_context_blocks() -> None:
    prompt = build_action_system_prompt(
        _ctx(
            messages=[("user", "hello")],
            integrations=("github",),
            integrations_known=True,
        )
    )
    assert "CONNECTED INTEGRATIONS (this install, right now): github" in prompt
    assert "RECENT CONVERSATION" in prompt
    assert "ARCHITECTURE AUDIT SKILL" in prompt


def test_skills_loader_bundles_markdown_files() -> None:
    md_files = list(skills_dir().glob("*.md"))
    assert md_files, "expected at least one bundled skill markdown file"

    block = load_skills_block()
    assert block.startswith(SKILLS_HEADER)
    for path in md_files:
        body = path.read_text(encoding="utf-8").strip()
        if body:
            assert body in block


def test_action_system_prompt_includes_skills_block() -> None:
    prompt = build_action_system_prompt(_ctx())
    assert SKILLS_HEADER in prompt
    assert "MORNING REPORT SKILL" in prompt
    # Skills must sit after the base rules so the COMPOUND TURN RULE is set first.
    assert prompt.index("COMPOUND TURN RULE") < prompt.index(SKILLS_HEADER)
    # ...and before the per-turn context blocks that follow.
    assert prompt.index(SKILLS_HEADER) < prompt.index(
        "CONNECTED INTEGRATIONS (this install, right now):"
    )


def test_system_prompt_requires_local_llama_handoff_tag() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert 'assistant_handoff(content="provider:local_llama_connect")' in prompt
    assert "/integrations setup llama" in prompt
    assert "do not use slash_invoke for /remote" in prompt
    assert "provider:local_llama_connect for vague local-model connection requests" in prompt


class _FakePrompts:
    def cli_reference(self) -> str:
        return "cli reference"

    def agents_md(self) -> str:
        return ""

    def docs(self, query: str) -> str:
        _ = query
        return ""

    def investigation_flow(self) -> str:
        return ""

    def environment_block(self) -> str:
        return ""

    def suggested_synthetic_prompt(self) -> str:
        return ""

    def log_diagnostics(self, reason: str) -> None:
        _ = reason


def test_local_llama_handoff_guidance_block() -> None:
    block = build_handoff_guidance_block(("provider:local_llama_connect",))
    assert "opensre onboard local_llm" in block
    assert "/model set ollama" in block
    assert build_handoff_guidance_block(("docs:datadog_setup",)) == ""


def test_local_llama_handoff_injects_setup_guidance_into_assistant_prompt() -> None:
    turn_snapshot = TurnSnapshot(
        text="please connect to local llama",
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )
    prompt = build_cli_agent_prompt_from_provider(
        message="please connect to local llama",
        prompts=_FakePrompts(),
        tool_observation=None,
        tool_observation_on_screen=True,
        handoff_contents=("provider:local_llama_connect",),
        turn_snapshot=turn_snapshot,
    )

    assert "opensre onboard local_llm" in prompt
    assert "/onboard local_llm" in prompt
    assert "/model set ollama" in prompt


class _FakePromptsWithDocs(_FakePrompts):
    """Provider that echoes the retrieval query so the wiring can be asserted."""

    def __init__(self) -> None:
        self.docs_query: str | None = None

    def docs(self, query: str) -> str:
        self.docs_query = query
        return "=== docs/messaging/telegram.mdx ===\nCreate a bot with @BotFather"


def test_docs_grounding_reaches_assistant_prompt() -> None:
    """The user's question is retrieved against docs and injected into the prompt.

    Locks the wiring the harness-move refactor severed: a procedural setup
    question must carry its documentation page into the LLM grounding so the
    assistant answers with the out-of-tool steps instead of improvising.
    """
    message = "how do I set up the telegram bot?"
    turn_snapshot = TurnSnapshot(
        text=message,
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )
    prompts = _FakePromptsWithDocs()

    prompt = build_cli_agent_prompt_from_provider(
        message=message,
        prompts=prompts,
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_snapshot=turn_snapshot,
    )

    assert prompts.docs_query == message
    assert "--- Documentation reference (docs/) ---" in prompt
    assert "docs/messaging/telegram.mdx" in prompt
    assert "@BotFather" in prompt
