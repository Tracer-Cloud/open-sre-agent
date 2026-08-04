"""Structured schedule offers — yes expands without scraping Want-me-to prose."""

from __future__ import annotations

from core.agent_harness.prompts.conversation_memory import expand_affirmative_follow_up
from core.agent_harness.session.pending_offer import PendingScheduleOffer
from core.agent_harness.tools.tool_context import ActionToolContext
from core.agent_harness.turns.headless_adapters import InMemorySessionStore, NoopTurnAccounting
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from tools.interactive_shell.actions.propose_scheduled_delivery import (
    execute_propose_scheduled_delivery_tool,
)


def test_pending_offer_to_slash_omits_slack_chat_id() -> None:
    offer = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 8 * * 1-5",
        timezone="Europe/Amsterdam",
        provider="slack",
    )
    assert offer.to_slash_command() == (
        "/cron add --kind daily_summary --cron '0 8 * * 1-5' --tz Europe/Amsterdam --provider slack"
    )


def test_yes_uses_pending_schedule_not_prose() -> None:
    pending = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 9 * * 1",
        timezone="UTC",
        provider="telegram",
        chat_id="-100123",
    )
    history = [
        (
            "assistant",
            "Delivered.\nWant me to: schedule this as a daily_summary every "
            "weekday at 8am to the same channel?",
        ),
    ]
    expanded = expand_affirmative_follow_up("yes", history, pending_schedule=pending)
    assert expanded == (
        "/cron add --kind daily_summary --cron '0 9 * * 1' "
        "--tz UTC --provider telegram --chat-id -100123"
    )
    assert "1-5" not in expanded
    assert "same channel" not in expanded


def test_propose_tool_sets_session_pending_offer() -> None:
    session = InMemorySessionStore()
    ctx = ActionToolContext(session=session, console=object())
    result = execute_propose_scheduled_delivery_tool(
        {
            "kind": "daily_summary",
            "cron": "0 8 * * 1-5",
            "timezone": "UTC",
            "provider": "slack",
        },
        ctx,
    )
    assert result["ok"] is True
    assert session.pending_schedule_offer is not None
    assert session.pending_schedule_offer.kind == "daily_summary"
    assert result["closer"].startswith("**Want me to:**")


def test_run_turn_consumes_pending_schedule_on_yes() -> None:
    session = InMemorySessionStore()
    session.pending_schedule_offer = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="slack",
    )
    seen: list[str] = []

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        seen.append(text)
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="ok",
        )

    run_turn(
        "yes",
        session,
        execute_actions=execute_actions,
        answer=lambda *_a, **_k: None,
        gather=lambda *_a, **_k: None,
        accounting=NoopTurnAccounting(),
    )

    assert len(seen) == 1
    assert seen[0].startswith("/cron add")
    assert session.pending_schedule_offer is None


def test_a_confirmed_schedule_survives_the_literal_slash_dispatcher() -> None:
    """The cron expression must reach the CLI as ONE argument.

    ``PendingScheduleOffer`` removes the model from string-building, but the
    confirmed offer still travels as slash text that
    ``_literal_slash_tool_call`` tokenises. Space-joined, the five-field cron
    expression arrives as five arguments and ``cron add`` dies with "Got
    unexpected extra arguments" — the same failure, now deterministic.
    """
    # Arrange
    from click.testing import CliRunner

    from core.agent_harness.session.pending_offer import PendingScheduleOffer
    from core.agent_harness.turns.action_driver import _literal_slash_tool_call
    from surfaces.cli.commands.cron import cron_add

    class _SlashTool:
        name = "slash_invoke"

    offer = PendingScheduleOffer(
        kind="daily_summary", cron="0 8 * * 1-5", timezone="UTC", provider="slack"
    )

    # Act
    call = _literal_slash_tool_call(offer.to_slash_command(), [_SlashTool()])

    # Assert
    assert call is not None
    args = call.input["args"]
    assert "0 8 * * 1-5" in args, f"cron expression was fragmented: {args}"
    result = CliRunner().invoke(cron_add, args[1:])
    assert result.exit_code == 0, result.output


def test_slash_tool_rebuild_keeps_cron_expression_for_dispatch() -> None:
    """execute_slash_tool must not flatten args into an unquoted command line.

    Real failure mode from the shell: yes → /cron add … printed as
    ``$ /cron add --cron 0 8 * * 1-5`` then Click saw five extra positionals.
    The tool call already had the cron as one arg; joining with spaces for
    dispatch/display was what destroyed it.
    """
    import shlex

    from core.agent_harness.session.pending_offer import PendingScheduleOffer
    from core.agent_harness.turns.action_driver import _literal_slash_tool_call
    from tools.interactive_shell.actions.slash import execute_slash_tool

    class _SlashTool:
        name = "slash_invoke"

    class _Ports:
        def command_exists(self, name: str) -> bool:
            return name == "/cron"

        def tty_interactive(self) -> bool:
            return False

        def format_turn_outcome(self, command: str, *, ok: bool) -> str:
            return f"{command}:{ok}"

        def execution_allowed(self, **_kwargs: object) -> bool:
            return True

        def dispatch(self, command: str, **_kwargs: object) -> bool:
            dispatched.append(command)
            return True

    offer = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 8 * * 1-5",
        timezone="Europe/Amsterdam",
        provider="slack",
    )
    call = _literal_slash_tool_call(offer.to_slash_command(), [_SlashTool()])
    assert call is not None

    dispatched: list[str] = []
    from rich.console import Console

    from core.agent_harness.tools.tool_context import ActionToolContext
    from core.agent_harness.turns.headless_adapters import InMemorySessionStore

    ctx = ActionToolContext(
        session=InMemorySessionStore(),
        console=Console(force_terminal=False, highlight=False),
        slash_ports=_Ports(),
        is_tty=False,
    )
    execute_slash_tool(call.input, ctx)

    assert len(dispatched) == 1
    line = dispatched[0]
    tokens = shlex.split(line, posix=True)
    assert "0 8 * * 1-5" in tokens, f"cron fragmented in dispatched line: {line!r} → {tokens}"
    # Naive space-join shows up as an unquoted `--cron 0 8`; quoted rebuild keeps one word.
    assert "'0 8 * * 1-5'" in line or '"0 8 * * 1-5"' in line, line


def test_an_apostrophe_in_a_typed_slash_command_still_dispatches() -> None:
    """Tokenising must not start rejecting ordinary prose after a slash."""
    # Arrange
    from core.agent_harness.turns.action_driver import _literal_slash_tool_call

    class _SlashTool:
        name = "slash_invoke"

    # Act
    call = _literal_slash_tool_call("/investigate don't know why", [_SlashTool()])

    # Assert
    assert call is not None
    assert call.input["command"] == "/investigate"
