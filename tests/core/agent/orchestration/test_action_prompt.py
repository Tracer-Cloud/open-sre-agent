"""Unit tests for shell action-agent prompt context."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_harness.prompts import (
    _SYSTEM_PROMPT_BASE,
    build_action_system_prompt,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
)
from core.agent_harness.prompts.assistant import (
    build_cli_agent_prompt_from_provider,
    build_handoff_guidance_block,
)
from core.agent_harness.prompts.memory.conversation import NO_HISTORY_PLACEHOLDER
from core.agent_harness.prompts.skills.loader import (
    SKILLS_HEADER,
    list_action_skills,
    load_skill_body,
    load_skills_block,
    load_skills_index,
    skills_dir,
)
from core.agent_harness.prompts.skills.loader import (
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
            ("user", "yes schedule it"),
            ("assistant", "Queued via /cron."),
        ]
    )
    block = recent_conversation_block(ctx)
    assert "RECENT CONVERSATION" in block
    assert "newest first" in block
    # Newest turn leads so head-preserving truncation keeps follow-up context.
    assert block.index("yes schedule it") < block.index("how can I remove github")
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
    assert "want me to: offering more detail from a vendor tool" in prompt
    assert "yes — please" in prompt


def test_system_prompt_slack_fragment_documents_roster_followup() -> None:
    # Slack-specific "Want me to" roster follow-up now lives in
    # integrations.slack.action_prompt and is appended to the composed action
    # prompt via the harness-ports fragment registry, not hardcoded in core.
    prompt = build_action_system_prompt(_ctx()).lower()
    assert "want me to: offering more slack roster" in prompt
    assert "slack_list_team_members" in prompt


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
    # Vendor recipe now lives in integrations.slack.action_prompt and is
    # appended to the composed action prompt via the harness-ports fragment
    # registry (see integrations/harness_adapters.py), not hardcoded in core.
    prompt = build_action_system_prompt(_ctx()).lower()
    compact = prompt.replace(" ", "")
    assert "slack teammate requests are action tools" in prompt
    assert "not handoffs" in prompt
    assert 'slack_read_messages(channel="#opensre-slack-testing"' in compact
    assert "are not covered by the data-retrieval handoff rule" in prompt
    assert "do not emit assistant_handoff for these" in prompt
    assert "roster / people questions ignore channel_id" in prompt
    assert "slack_list_team_members only" in prompt
    assert "never slack_read_messages" in prompt


def test_system_prompt_routes_github_cli_to_action_tools() -> None:
    # Vendor recipe now lives in integrations.github.action_prompt and is
    # appended to the composed action prompt via the harness-ports fragment
    # registry (see integrations/harness_adapters.py), not hardcoded in core.
    prompt = build_action_system_prompt(_ctx()).lower()
    assert "github cli requests are action tools" in prompt
    assert "not handoffs" in prompt
    assert "call github_cli directly" in prompt
    assert "from this info create an issue on github" in prompt
    assert "github_cli is action-only" in prompt
    assert "exception: github issue/pr/repo" in prompt
    assert "get_github_star_history" in prompt
    assert "day-by-day stars" in prompt


def test_skills_loader_routes_star_history_away_from_github_cli() -> None:
    cached_load_skills_block.cache_clear()
    body = load_skill_body("github-cli").lower()
    assert "star history" in body
    assert "get_github_star_history" in body
    assert "assistant_handoff" in body
    assert "undercount" in body or "false zeros" in body


def test_system_prompt_bans_shell_placeholders_on_multisource_diagnosis() -> None:
    """Multi-source crash diagnosis is a single assistant_handoff (scenario 314)."""
    prompt = " ".join(_SYSTEM_PROMPT_BASE.lower().split())
    assert "do not emit investigation_start for a diagnostic question" in prompt
    assert "never invent placeholder shell commands" in prompt
    assert "posthog query requested" in prompt
    assert "never use shell_run as a stand-in for querying observability sources" in prompt
    composed = " ".join(build_action_system_prompt(_ctx()).lower().split())
    assert "assistant_handoff only" in composed
    assert "alone or paired with the handoff" in composed


def test_system_prompt_keeps_bare_alert_blob_as_handoff() -> None:
    prompt = " ".join(_SYSTEM_PROMPT_BASE.lower().split())
    assert (
        "an implicit diagnostic cause question and a bare pasted alert blob "
        "remain assistant_handoff" in prompt
    )
    assert "pasted alert blob / bare incident statement" in prompt
    assert "with no instruction and no diagnostic question" in prompt
    assert "is not a diagnostic question; it is also assistant_handoff" in prompt


def test_system_prompt_hands_off_when_delivery_tool_unavailable() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    compact_prompt = " ".join(prompt.split())
    assert "delivery tool unavailable — never fabricate a command to deliver" in prompt
    assert "matching send tool" in compact_prompt
    assert "that channel is not configured" in compact_prompt
    assert "do not invent or guess a slash/cli subcommand to deliver" in compact_prompt
    assert "route the user to enable it" in compact_prompt
    assert "this applies even mid-chain" in compact_prompt


def test_system_prompt_slack_fragment_documents_invented_command_example() -> None:
    # The Slack-specific invented-delivery-command example now lives in
    # integrations.slack.action_prompt, appended via the harness-ports
    # fragment registry, not hardcoded in core.
    prompt = build_action_system_prompt(_ctx()).lower()
    compact_prompt = " ".join(prompt.split())
    assert "`/messaging send slack …` is not a real command" in compact_prompt


def test_system_prompt_preserves_bare_numeric_synthetic_mapping() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "run synthetic test 005 now" in prompt
    assert 'scenario="005-failover"' in prompt
    assert "never substitute a different numbered" in prompt


def test_system_prompt_routes_durable_memory_requests_to_memory_tools() -> None:
    prompt = " ".join(_SYSTEM_PROMPT_BASE.lower().split())
    assert "memory_remember" in prompt
    assert "proactively save durable knowledge" in prompt
    assert "the user does not need to say" in prompt
    assert "memory_recall" in prompt
    assert "memory_forget" in prompt
    assert "do not save transient task state" in prompt
    assert "never save built-in sample/demo/synthetic/test alert output" in prompt


def test_system_prompt_offers_scheduled_deliveries_via_cron() -> None:
    """Stable-tier guidance must teach propose_scheduled_delivery, not prose scrape."""
    prompt = " ".join(_SYSTEM_PROMPT_BASE.lower().split())
    assert "scheduled deliveries" in prompt
    assert "propose_scheduled_delivery" in prompt
    assert "every morning" in prompt
    assert "slash_invoke /loops" in prompt
    assert "/loops add" in prompt
    assert "--prompt" in prompt
    assert "--run-now" in prompt
    assert "do not call /cron until they confirm" in prompt


def test_morning_report_skill_closes_with_schedule_offer() -> None:
    """A run-once morning report without an offer cannot drive repeat usage."""
    load_skills_block.cache_clear()
    body = " ".join(
        (skills_dir() / "morning_report" / "SKILL.md").read_text(encoding="utf-8").lower().split()
    )
    assert "propose_scheduled_delivery" in body
    assert "daily_summary" in body
    assert 'cron="0 8 * * 1-5"' in body or "cron='0 8 * * 1-5'" in body
    assert "do not call /cron yet" in body
    # Handing off after the fetches re-triggers gather → a second "morning
    # report" from K8s/GitHub that clobbers the weather+news briefing UX.
    assert "do not call assistant_handoff" in body
    assert "hand the real content to the assistant" not in body
    # Intermediate curls must be quiet so the user does not see weather/news
    # once as $ stdout and again in the composed OpenSRE briefing.
    assert "quiet=true" in body
    assert "briefing_text" in body


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

    index = load_skills_index()
    assert "architecture-audit" in index
    assert "SKILLS INDEX" in index
    # Fat body stays out of the thin harness index.
    assert "architecture_clone_repo" not in index

    body = load_skill_body("architecture-audit")
    assert "ARCHITECTURE AUDIT SKILL" in body
    assert "WHEN TO USE" in body
    assert "summarize this repo's architecture" in body
    assert "architecture_clone_repo" in body
    assert "scan_architecture_imports" not in body
    assert "scan_module_placement" not in body
    assert "architecture_cleanup_repo" in body
    assert "architecture_save_observations" in body
    assert "shell_run" in body
    assert "Never end the turn with shell_run" in body
    report_path = (
        "core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md"
    )
    assert f"REPORT TEMPLATE from `{report_path}`" in body
    assert "### Findings by severity" in body


def test_skills_loader_bundles_github_cli_skill() -> None:
    cached_load_skills_block.cache_clear()
    skill = skills_dir() / "github_cli" / "SKILL.md"
    assert skill.is_file()

    index = load_skills_index()
    assert "github-cli" in index
    assert "github_cli(args=" not in index

    body = load_skill_body("github-cli")
    assert "GITHUB CLI SKILL" in body
    assert "do NOT assistant_handoff" in body
    assert "github_cli(args=" in body
    assert "create an issue from that" in body

    prompt = build_action_system_prompt(_ctx(messages=[("user", "audit architecture")]))
    assert "architecture-audit" in prompt
    assert "ARCHITECTURE AUDIT SKILL" not in prompt
    assert "### Findings by severity" not in prompt
    cached_load_skills_block.cache_clear()


def test_skills_loader_bundles_github_security_fix_skill() -> None:
    cached_load_skills_block.cache_clear()
    skill = skills_dir() / "github_security_fix" / "SKILL.md"
    assert skill.is_file()

    # Index carries the one-line catalog; the skill body loads on demand, so the
    # detailed assertions from #4727 belong against the body, not the block.
    assert "github-security-fix" in load_skills_index()
    body = load_skill_body("github-security-fix")
    assert "GITHUB SECURITY AND QUALITY FIX SKILL" in body
    assert "fix_github_security_alert" in body
    assert "security and quality issues" in body
    assert "/security/code-scanning" in body
    assert "Secret-scanning remediation" in body
    assert 'alert_type="auto"' in body
    assert 'alert_type="code_scanning"' in body
    assert 'alert_type="code_quality"' in body
    assert "auto-detected" in body
    assert "Never add coding-agent advice" in body
    assert "output exactly that text and stop" in body
    assert "reply in one short line" in body
    assert 'Do not say "next steps"' in body
    cached_load_skills_block.cache_clear()


def test_skills_loader_bundles_github_ci_fix_skill() -> None:
    cached_load_skills_block.cache_clear()
    skill = skills_dir() / "github_ci_fix" / "SKILL.md"
    assert skill.is_file()

    assert "github-ci-fix" in load_skills_index()
    body = load_skill_body("github-ci-fix")
    assert "GITHUB PR CI FIX SKILL" in body
    assert "fix_github_pr_ci" in body
    assert "output exactly that text and stop" in body
    assert '"next steps"' in body
    assert "pushes to the existing PR head branch" in body
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
    assert "architecture-audit" in prompt
    assert "skill_view" in prompt
    assert "ARCHITECTURE AUDIT SKILL" not in prompt


def test_skills_index_is_thin_relative_to_full_bodies() -> None:
    cached_load_skills_block.cache_clear()
    index = load_skills_index()
    bodies = "".join(load_skill_body(skill.name) for skill in list_action_skills())
    assert index.startswith(SKILLS_HEADER)
    assert "skill_view" in index.lower()
    assert len(bodies) > 10 * len(index)
    names = {skill.name for skill in list_action_skills()}
    assert names >= {
        "morning-report",
        "architecture-audit",
        "github-cli",
        "github-security-fix",
        "github-ci-fix",
    }
    for skill in list_action_skills():
        assert skill.name in index
        assert skill.description.split()[0] in index or skill.name in index


def test_action_system_prompt_includes_skills_block() -> None:
    prompt = build_action_system_prompt(_ctx())
    assert SKILLS_HEADER in prompt
    assert "morning-report" in prompt
    assert "MORNING REPORT SKILL" not in prompt
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
    def surface(self) -> str:
        return "interactive_shell"

    def cli_reference(self) -> str:
        return "cli reference"

    def agents_md(self) -> str:
        return ""

    def docs(self, query: str) -> str:
        _ = query
        return ""

    def investigation_flow(self) -> str:
        return ""

    def runtime_facts(self) -> dict[str, object]:

        from config.runtime_metadata import capture_runtime_facts

        return capture_runtime_facts()

    def environment_block(self, runtime=None) -> str:  # noqa: ANN001, ARG002
        return ""

    def long_term_memory(self) -> str:
        return ""

    def setup_state(self) -> str:
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


def test_action_prompt_includes_long_term_memory_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
    from core.domain.memory import save_memory

    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    save_memory(
        slug="user-profile",
        memory_type="user",
        description="Name is Vaibhav",
        body="The user's name is Vaibhav on the platform team.",
    )
    prompt = build_action_system_prompt(_ctx())
    assert "LONG-TERM MEMORY" in prompt
    assert "user-profile" in prompt
    assert "platform team" in prompt


def test_scheduling_guidance_survives_prompt_assembly() -> None:
    """Stable half carries /cron + index; fat closer lives in the skill body.

    Thin harness: the weekday-8am closer is not inlined into every turn — it
    loads via skill_view when morning-report matches. The cacheable half must
    still name /cron and list morning-report as recurring so the agent knows
    to load and offer.
    """
    # Arrange
    from core.agent_harness.prompts import build_action_system_prompt_envelope

    snapshot = _ctx(messages=[("user", "give me a morning report")])

    # Act
    cached, _ephemeral = build_action_system_prompt_envelope(snapshot).render_split()
    assembled = " ".join(cached.lower().split())
    body = " ".join(load_skill_body("morning-report").lower().split())

    # Assert
    assert "scheduled deliveries" in assembled
    assert "propose_scheduled_delivery" in assembled
    assert "morning-report" in assembled
    assert "recurring: weekdays 08:00" in assembled
    assert "skill_view" in assembled
    assert "propose_scheduled_delivery" in body
    assert "propose_scheduled_delivery(" not in assembled


def test_the_slash_command_the_prompt_tells_the_agent_to_call_exists() -> None:
    """Guidance naming a command that is not registered would fail at run time.

    Pending offers expand to ``/cron add``; list/remove still use slash_invoke.
    Nothing else ties that string to the real command, so a rename of the CLI
    group would leave the agent confidently calling a command that is gone.
    """
    # Arrange
    from tools.interactive_shell.shared.slash_catalog import MCP_BY_COMMAND

    # Act
    entry = MCP_BY_COMMAND.get("/cron")
    loops_entry = MCP_BY_COMMAND.get("/loops")

    # Assert
    assert entry is not None, "the prompt offers /cron but it is not in the slash catalog"
    assert "add" in entry.llm_description.lower()
    assert loops_entry is not None, "the prompt offers /loops but it is not in the slash catalog"
    assert "add" in loops_entry.llm_description.lower()
    assert "delete" in loops_entry.llm_description.lower()
    assert "run once" in loops_entry.llm_description.lower()
    assert "stop" in loops_entry.llm_description.lower()
    assert "next fire time" in loops_entry.llm_description.lower()


def test_scheduling_is_never_offered_without_asking_first() -> None:
    """Creating a schedule unasked would be a surprise side effect.

    The business goal is an offer the user accepts, not silent automation. Both
    the stable-tier guidance and the recurring skill must gate creation on
    confirmation, so this asserts the consent rule in each place a schedule can
    be created from.
    """
    # Arrange
    load_skills_block.cache_clear()
    base = " ".join(_SYSTEM_PROMPT_BASE.lower().split())
    skill = " ".join(
        (skills_dir() / "morning_report" / "SKILL.md").read_text(encoding="utf-8").lower().split()
    )

    # Assert — structured propose tool; creation waits on confirm / yes
    assert "do not call /cron until they confirm" in base
    assert "do not call /cron yet" in skill


def test_the_active_instruction_survives_context_truncation() -> None:
    """An oversized turn must lose stale history, never the current request.

    ``core.context_budget`` shrinks a message with ``text[:keep]`` — it keeps the
    head and drops the tail. Ephemeral history therefore has to sit *after* the
    literal user message: with history first, a turn over budget loses the very
    instruction that started it and the planner acts on stale context instead.
    """
    # Arrange
    from core.agent_harness.prompts import build_action_user_message
    from core.context_budget import _shrink_text

    instruction = "zzmarker-delete-the-staging-database"
    history = "--- Recent conversation ---\n" + ("user: earlier chatter\n" * 200)
    message = build_action_user_message(instruction, prefix=history)

    # Act
    shrunk, truncated = _shrink_text(message, 500)

    # Assert
    assert truncated is True
    assert instruction in shrunk


def test_the_cron_guidance_teaches_structured_schedule_offers() -> None:
    """Morning report must propose via tool, not scrape Want-me-to into /cron."""
    # Arrange
    from core.agent_harness.prompts.skills.loader import skills_dir

    base = " ".join(_SYSTEM_PROMPT_BASE.lower().split())
    skill = " ".join(
        (skills_dir() / "morning_report" / "SKILL.md").read_text(encoding="utf-8").lower().split()
    )

    assert "propose_scheduled_delivery" in base
    assert "propose_scheduled_delivery" in skill
    assert "omit chat_id" in skill
    assert 'provider="slack"' in skill or "provider='slack'" in skill


# ── WAL: interrupted-turn recovery injection ─────────────────────────────────


def test_interrupted_turn_recovery_block_rides_the_ephemeral_tier() -> None:
    """The recovery note lands in the ephemeral half; the cached half is unchanged.

    Cache stability is the design constraint: the note rides exactly one turn,
    so it must never touch the byte-stable cached system prefix.
    """
    import dataclasses

    from core.agent_harness.prompts.action.assemble import (
        build_action_system_prompt_envelope,
        interrupted_turn_recovery_block,
    )

    note = (
        "A previous turn in this session was interrupted while tool calls were "
        "still executing (no result was recorded for them):\n"
        "- shell_run step-2 >> /tmp/demo_state.json (step 2)"
    )
    with_note = dataclasses.replace(_ctx(), recovery_note=note)

    assert interrupted_turn_recovery_block(_ctx()) == ""
    block = interrupted_turn_recovery_block(with_note)
    assert "INTERRUPTED-TURN RECOVERY" in block
    assert "shell_run step-2 >> /tmp/demo_state.json (step 2)" in block

    envelope_with = build_action_system_prompt_envelope(with_note)
    envelope_without = build_action_system_prompt_envelope(_ctx())
    assert envelope_with.render_cached() == envelope_without.render_cached()
    assert "INTERRUPTED-TURN RECOVERY" in envelope_with.render_ephemeral()
    assert "INTERRUPTED-TURN RECOVERY" not in envelope_without.render()
    assert "INTERRUPTED-TURN RECOVERY" in envelope_with.render()


def test_from_session_pops_the_pending_recovery_note() -> None:
    """The note is consumed by the first snapshot and never rides a second turn."""
    from types import SimpleNamespace

    session = SimpleNamespace(
        cli_agent_messages=[],
        configured_integrations=(),
        configured_integrations_known=False,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
        pending_recovery_note="previous turn was interrupted while executing shell_run step-2",
    )

    first = TurnSnapshot.from_session("continue", session, surface=None)
    second = TurnSnapshot.from_session("next", session, surface=None)

    assert first.recovery_note is not None
    assert "shell_run step-2" in first.recovery_note
    assert session.pending_recovery_note is None
    assert second.recovery_note is None


def test_sequential_steps_rule_teaches_two_phase_state_writes() -> None:
    """Task-level WAL: the state file records started/committed per step."""
    prompt = " ".join(_SYSTEM_PROMPT_BASE.lower().split())
    assert "two-phase" in prompt
    assert "`step n: started`" in prompt
    assert "`step n: committed`" in prompt
    assert "started-but-uncommitted step is re-run" in prompt
    assert "committed steps are never redone" in prompt
