"""Gateway slash-command routing for Telegram and other headless surfaces."""

from __future__ import annotations

import io
import logging
from typing import Any

import pytest
from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.tools.action_tools import get_action_tool
from infrastructure.turn_host.turn_runner import TurnRunner
from tests.core.agent.orchestration.cross_surface_parity_harness import (
    RecordingTurnOutput,
    headless_slash_ports,
)


def _gateway_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False, width=100)


def _run_gateway_slash(message: str) -> RecordingTurnOutput:
    session = SessionCore(store=InMemorySessionStore())
    sink = RecordingTurnOutput()
    handler = TurnRunner(
        console=_gateway_console(),
        slash_ports_factory=headless_slash_ports,
    )
    handler(message, session, sink, logging.getLogger("test.gateway.slash"))
    return sink


def test_gateway_registers_slash_invoke_tool() -> None:
    """Harness adapters registered at gateway boot must expose slash_invoke to action turns."""
    slash = get_action_tool("slash_invoke")
    assert slash is not None
    assert slash.name == "slash_invoke"


def test_gateway_status_slash_is_not_swallowed() -> None:
    """Literal /status must route through slash_invoke and return session diagnostics."""
    sink = _run_gateway_slash("/status")
    assert sink.finalized is not None
    assert "I didn't have anything to add for that." not in sink.finalized
    assert "interactions" in sink.finalized.lower()


def test_gateway_investigate_slash_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Literal /investigate <template> must run the investigation slash handler."""

    def _fake_run_sample_alert_for_session(**_kwargs: object) -> dict[str, object]:
        return {"status": "completed", "summary": "parity investigation ok"}

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.investigation_adapter.run_sample_alert_for_session",
        _fake_run_sample_alert_for_session,
    )

    sink = _run_gateway_slash("/investigate generic")
    assert sink.finalized is not None
    assert "I didn't have anything to add for that." not in sink.finalized
    assert "generic" in sink.finalized.lower()


def test_gateway_investigate_discord_alert_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discord maps slash alert text to /investigate alert:<text>."""

    def _fake_run_investigation_for_session(**_kwargs: object) -> dict[str, object]:
        return {"status": "completed", "summary": "discord alert ok"}

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.investigation_adapter.run_investigation_for_session",
        _fake_run_investigation_for_session,
    )

    sink = _run_gateway_slash("/investigate alert:High error rate on checkout")
    assert sink.finalized is not None
    assert "failed" not in (sink.finalized or "").lower()


def test_gateway_background_read_forms_are_not_swallowed() -> None:
    """Literal /background read forms run against SessionCore, which has no
    terminal facet; they must answer instead of raising AttributeError."""
    listed = _run_gateway_slash("/background list")
    assert listed.finalized is not None
    assert "no background investigations" in listed.finalized.lower()

    status = _run_gateway_slash("/background status")
    assert status.finalized is not None
    assert "background mode" in status.finalized.lower()


def test_gateway_background_write_forms_report_repl_only() -> None:
    """/background on toggles REPL-local state with no headless equivalent."""
    sink = _run_gateway_slash("/background on")
    assert sink.finalized is not None
    assert "uv run opensre" in sink.finalized


def test_gateway_background_use_is_refused_before_the_record_lookup() -> None:
    task_id = _seed_record(task_id="bg-use-chat")

    sink = _run_gateway_slash(f"/background use {task_id}")

    assert sink.finalized is not None
    assert "uv run opensre" in sink.finalized
    assert "unknown background task" not in sink.finalized


def test_gateway_background_show_reaches_a_record_past_the_listing_bound() -> None:
    # Oldest, then enough newer rows to push it out of any recent-N listing while
    # staying inside the store's own bound, so only a full read finds it.
    buried = _seed_record(task_id="bg-buried", root_cause="the oldest cause")
    for index in range(60):
        _seed_record(task_id=f"bg-bulk-{index:03d}", root_cause=f"cause {index}")

    sink = _run_gateway_slash(f"/background show {buried}")

    assert sink.finalized is not None
    assert "the oldest cause" in sink.finalized
    assert "unknown background task" not in sink.finalized


def test_gateway_onboard_slash_returns_headless_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Literal /onboard on SessionCore must not spawn a blocking interactive wizard."""
    recorded: list[list[str]] = []

    def _fake_run_cli_command(*_args: object, **_kwargs: object) -> bool:
        recorded.append(["onboard"])
        return True

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.cli_parity.run_cli_command",
        _fake_run_cli_command,
    )

    sink = _run_gateway_slash("/onboard")
    assert recorded == []
    assert sink.finalized is not None
    assert "interactive wizard" in sink.finalized.lower()
    assert "uv run opensre onboard" in sink.finalized


def test_gateway_integrations_setup_returns_headless_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Literal /integrations setup must not spawn a blocking credential wizard on gateway."""
    recorded: list[list[str]] = []

    def _fake_run_cli_command(
        _console: Any,
        args: list[str],
        **_kwargs: object,
    ) -> bool:
        recorded.append(list(args))
        return True

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.integrations.run_cli_command",
        _fake_run_cli_command,
    )

    sink = _run_gateway_slash("/integrations setup grafana")
    assert recorded == []
    assert sink.finalized is not None
    assert "grafana" in sink.finalized.lower()
    assert "succeeded" not in sink.finalized.lower()
    assert "timed out" not in sink.finalized.lower()
    assert "uv run opensre integrations setup grafana" in sink.finalized
    assert "Launching" not in (sink.finalized or "")


def test_gateway_integrations_setup_returns_headless_guidance_even_with_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway SessionCore returns headless guidance even when stdin is a TTY (e.g. tmux)."""
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.choice_menu.repl_tty_interactive",
        lambda: True,
    )
    recorded: list[list[str]] = []

    def _fake_run_cli_command(
        _console: Any,
        args: list[str],
        **_kwargs: object,
    ) -> bool:
        recorded.append(list(args))
        return True

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.integrations.run_cli_command",
        _fake_run_cli_command,
    )

    sink = _run_gateway_slash("/integrations setup grafana")
    assert recorded == []
    assert sink.finalized is not None
    assert "Launching" not in (sink.finalized or "")


def test_gateway_manager_registers_harness_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway boot must register harness adapters so production turns see slash_invoke."""
    from bootstrap.process import reset_process_runtime_for_tests

    calls: list[str] = []

    def _record(name: str):
        def _step() -> None:
            calls.append(name)

        return _step

    # Boot runs once per profile; clear it so this test sees the real sequence.
    reset_process_runtime_for_tests()
    # Registration happens in bootstrap.process via GATEWAY_PROFILE.
    monkeypatch.setattr("bootstrap.process.install_harness_adapters", _record("adapters"))
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.llm.internal.preload.preload_llm_clients",
        lambda: None,
    )
    monkeypatch.setattr(
        "infrastructure.safety.sandbox.capabilities.boot_capability_warnings",
        lambda: [],
    )
    from gateway.startup import StartedGateway

    monkeypatch.setattr(
        "gateway.core.lifecycle.controller.gateway_startup.start_gateway",
        lambda **_kwargs: StartedGateway(),
    )
    # Keep this test focused on adapter registration (life-cycle tests cover scheduler).
    monkeypatch.setattr(
        "gateway.core.lifecycle.controller.GatewayController.start_scheduler",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "gateway.core.lifecycle.controller.GatewayController._publish_status",
        lambda *_args, **_kwargs: None,
    )

    from gateway.core.lifecycle.controller import GatewayController

    GatewayController().start_gateway(wait=False)

    # GATEWAY_PROFILE registers adapters at boot; scheduler runners come later,
    # when the scheduler stage starts.
    assert "adapters" in calls
    assert "runners" not in calls
    reset_process_runtime_for_tests()


# Rich draws tables with these; nothing on the chat path converts them, so they
# reach Telegram as literal characters inside an 80-column hard-wrapped block.
_BOX_DRAWING = set("─│┃━╭╮╰╯┏┓┗┛┿┼┤├┬┴┌┐└┘╡╞═")


def _seed_record(**overrides: Any) -> str:
    from core.domain.background_investigations import (
        BackgroundInvestigationRecord,
    )
    from infrastructure.scheduling.background_investigations.store import (
        open_record_store,
    )

    fields: dict[str, Any] = {
        "task_id": "bg-chat-1",
        "status": "completed",
        "command": "/investigate checkout-latency",
        "root_cause": "connection pool saturation on the payments replica",
        "top_analysis": ("rds cpu spike at 14:02",),
        "next_steps": ("raise the pool ceiling to 64",),
    }
    fields.update(overrides)
    record = BackgroundInvestigationRecord(**fields)
    open_record_store().save(record)
    return record.task_id


def test_gateway_background_show_returns_the_rca_as_plain_text() -> None:
    """A completed RCA is retrievable from a chat transport.

    Asserting the absence of box-drawing is the load-bearing half. The Rich table
    renders fine into the captured console and would pass a content-only
    assertion while arriving in Telegram as an 80-column hard-wrapped grid of
    ━ and │ that no sink converts.
    """
    task_id = _seed_record()

    sink = _run_gateway_slash(f"/background show {task_id}")

    assert sink.finalized is not None
    assert "connection pool saturation" in sink.finalized
    assert "raise the pool ceiling" in sink.finalized
    assert not _BOX_DRAWING & set(sink.finalized), sink.finalized


def test_gateway_background_show_does_not_leak_delivery_exception_detail() -> None:
    """Every free-form tail is dropped, whichever adapter produced it.

    ``deliver_telegram_notification`` interpolates ``str(exc)`` from credential
    loading, so a rule keyed on the ``failed:`` prefix passes it straight to
    Telegram. The category before the colon is what gets reported; a rule that
    allowlisted known-safe strings instead would fail open for the next adapter.
    """
    task_id = _seed_record(
        task_id="bg-chat-redact",
        notification_results={
            "email": "failed: SMTPRecipientsRefused",
            "telegram": "missing telegram integration: no bot token in /home/ops/.opensre",
            "buzz": "missing buzz integration: Buzz is not configured.",
            "rocketchat": "sent",
        },
    )

    sink = _run_gateway_slash(f"/background show {task_id}")

    assert sink.finalized is not None
    assert "email:failed" in sink.finalized
    assert "SMTPRecipientsRefused" not in sink.finalized
    assert "telegram:missing telegram integration" in sink.finalized
    assert "/home/ops/.opensre" not in sink.finalized
    assert "buzz:missing buzz integration" in sink.finalized
    assert "Buzz is not configured" not in sink.finalized
    # A tail-free outcome is reported whole; there is nothing to strip.
    assert "rocketchat:sent" in sink.finalized


def test_gateway_background_list_is_plain_and_bounded() -> None:
    """One line per record, and the root cause is trimmed. An unbounded list of
    twenty folded RCAs overruns the 4096-character message cap, and the transports
    tail-truncate, so the closing hint would be the first thing lost."""
    for index in range(3):
        _seed_record(task_id=f"bg-many-{index}", root_cause="saturation " * 60)

    sink = _run_gateway_slash("/background list")

    assert sink.finalized is not None
    assert "bg-many-2" in sink.finalized
    assert not _BOX_DRAWING & set(sink.finalized), sink.finalized
    assert len(sink.finalized) < 4096
    assert "/background show" in sink.finalized


def test_gateway_background_list_stays_under_the_cap_on_worst_case_records() -> None:
    """The cap has to hold on the record a real incident produces, not a short one.

    ``command`` is operator-supplied alert text and was previously untrimmed, so
    a listing of long-command records reached ~5.5k characters. The closing hint
    is the assertion that matters: the transports cut the tail, so overrunning
    loses exactly the line telling the reader how to read one of these.
    """
    alert = "alert:payments checkout latency spike on the eu-west-1 replica " * 6
    for index in range(20):
        _seed_record(
            task_id=f"bg-worst-{index:02d}",
            command=f"/investigate {alert}",
            root_cause="saturation " * 60,
        )

    sink = _run_gateway_slash("/background list")

    assert sink.finalized is not None
    assert len(sink.finalized) <= 4096, len(sink.finalized)
    assert sink.finalized.endswith("Use /background show <task_id> for the full RCA.")
    assert "more" in sink.finalized
