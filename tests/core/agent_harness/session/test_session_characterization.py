"""Characterization tests locking Session behavior before the #3690 god-object split.

These pin the behaviors the SessionCore/facet split must preserve but that had no
direct coverage: the field inventory (guards the ~89 access sites), the incoming-alert
cap, the integration warm-cache generation guard, turn-outcome-hint pop-once, and token
accounting. Behaviors already covered elsewhere (pending-turn staging in
``test_turn_accounting``, notice drain in ``test_background_runner``) are not duplicated.
"""

from __future__ import annotations

import dataclasses

from core.agent_harness.session.storage.memory import InMemorySessionStorage
from core.agent_harness.session.token_usage import TokenUsage
from core.domain.alerts.inbox import IncomingAlert
from surfaces.interactive_shell.session.session import Session


def _session() -> Session:
    return Session(storage=InMemorySessionStorage())


# --------------------------------------------------------------------------- #
# Field inventory — the god object's current surface (baseline for the split)  #
# --------------------------------------------------------------------------- #

# The 48 fields, by target bucket per the #3690 plan. When a field moves to a
# facet, move its name here and assert it on the facet instead — this stays the
# single inventory of "every Session field is accounted for".
_CORE_FIELDS = (
    "session_id",
    "started_at",
    "storage",
    "resumed_from_name",
    "history",
    "last_state",
    "last_investigation_id",
    "last_assistant_intent",
    "last_synthetic_observation_path",
    "configured_integrations",
    "configured_integrations_known",
    "resolved_integrations_cache",
    "github_repo_scope",
    "_integration_warm_lock",
    "_integration_warm_generation",
    "_integration_warm_task",
    "available_capabilities",
    "accumulated_context",
    "reasoning_effort",
    "tokens",
    "task_registry",
    "agent",
    "grounding",
    "_ACCUMULATED_KEYS",
)
# Shell-only state that still sits flat on Session, awaiting relocation to the
# terminal facet. Empty: every shell-only cluster now lives on session.terminal.
_TERMINAL_FIELDS: tuple[str, ...] = ()
# Extracted facets, each a single field on Session holding relocated state:
#   alert inbox: incoming_alerts + _INCOMING_ALERTS_MAX -> session.alerts (entries, _max)
#   terminal, theme cluster: active_theme_name + pending_theme_refresh + trust_mode
#       -> session.terminal
#   terminal, prompt-toolkit cluster: prompt_history_backend, pt_style_app, main_loop,
#       prompt_refresh_fn, fleet_sampler_starter -> session.terminal
#   terminal, pending-prompt/stdin cluster: pending_prompt_default, pending_prompt_autosubmit,
#       exclusive_stdin_active, agent_turn_executed_slashes -> session.terminal
#   terminal, background cluster: background_mode_enabled, background_investigations,
#       background_notification_preferences, background_notices, _background_notices_lock
#       -> session.terminal
#   terminal, metrics cluster: metrics, history_generation -> session.terminal
#     (task_registry stayed core — session task-state the manager owns; see _CORE_FIELDS)
#   terminal, analytics-staging cluster: _turn_outcome_hint, _pending_turn_llm,
#       _pending_turn_error -> session.terminal
_FACET_FIELDS = ("alerts", "terminal")


def test_field_inventory_is_exactly_26_top_level_fields() -> None:
    all_fields = _CORE_FIELDS + _TERMINAL_FIELDS + _FACET_FIELDS
    # 48 - 2 (alerts) - 2 (theme) - 5 (prompt-toolkit) - 4 (pending-prompt/stdin)
    #    - 5 (background) - 2 (metrics/history) - 3 (analytics-staging)
    #    - 1 (trust_mode) + 2 facet fields  (task_registry stayed core)
    assert len(all_fields) == 26
    assert len(set(all_fields)) == 26  # no duplicates across buckets


def test_every_inventoried_field_is_accessible_on_session() -> None:
    session = _session()
    missing = [
        f for f in _CORE_FIELDS + _TERMINAL_FIELDS + _FACET_FIELDS if not hasattr(session, f)
    ]
    assert missing == []


def test_alert_inbox_facet_holds_the_relocated_alert_state() -> None:
    inbox = _session().alerts
    assert hasattr(inbox, "entries")  # was Session.incoming_alerts
    assert hasattr(inbox, "_max")  # was Session._INCOMING_ALERTS_MAX


def test_session_is_a_session_core_and_core_has_no_facets() -> None:
    from core.agent_harness.session.session_core import SessionCore

    assert issubclass(Session, SessionCore)
    core_field_names = {f.name for f in dataclasses.fields(SessionCore)}
    # The core base carries every core field and none of the shell facets.
    assert set(_CORE_FIELDS) == core_field_names
    assert "terminal" not in core_field_names
    assert "alerts" not in core_field_names
    core = SessionCore()
    assert not hasattr(core, "terminal")
    assert not hasattr(core, "alerts")


def test_terminal_facet_holds_the_theme_cluster() -> None:
    terminal = _session().terminal
    assert hasattr(terminal, "active_theme_name")  # was Session.active_theme_name
    assert hasattr(terminal, "pending_theme_refresh")  # was Session.pending_theme_refresh
    assert hasattr(terminal, "trust_mode")  # was Session.trust_mode


def test_terminal_facet_holds_the_prompt_toolkit_cluster() -> None:
    terminal = _session().terminal
    for f in (
        "prompt_history_backend",
        "pt_style_app",
        "main_loop",
        "prompt_refresh_fn",
        "fleet_sampler_starter",
    ):
        assert hasattr(terminal, f)  # was Session.<f>


def test_terminal_facet_holds_the_pending_prompt_cluster() -> None:
    terminal = _session().terminal
    for f in (
        "pending_prompt_default",
        "pending_prompt_autosubmit",
        "exclusive_stdin_active",
        "agent_turn_executed_slashes",
    ):
        assert hasattr(terminal, f)  # was Session.<f>


def test_terminal_facet_holds_the_background_cluster() -> None:
    terminal = _session().terminal
    for f in (
        "background_mode_enabled",
        "background_investigations",
        "background_notification_preferences",
        "background_notices",
        "_background_notices_lock",
    ):
        assert hasattr(terminal, f)  # was Session.<f>


def test_terminal_facet_holds_the_metrics_cluster() -> None:
    terminal = _session().terminal
    for f in ("metrics", "history_generation"):
        assert hasattr(terminal, f)  # was Session.<f>


def test_task_registry_stayed_on_the_core_session() -> None:
    from core.agent_harness.session.session_core import SessionCore

    assert "task_registry" in {f.name for f in dataclasses.fields(SessionCore)}
    assert not hasattr(_session().terminal, "task_registry")


def test_terminal_facet_holds_the_analytics_staging_cluster() -> None:
    terminal = _session().terminal
    for f in ("_turn_outcome_hint", "_pending_turn_llm", "_pending_turn_error"):
        assert hasattr(terminal, f)  # was Session.<f>


def test_analytics_staging_pop_methods_delegate_to_the_facet() -> None:
    session = _session()
    session.terminal.set_turn_outcome_hint("queued")
    assert session.terminal._turn_outcome_hint == "queued"
    assert session.terminal.pop_turn_outcome_hint() == "queued"
    assert session.terminal._turn_outcome_hint is None


# --------------------------------------------------------------------------- #
# Alert-inbox facet — incoming-alert cap                                      #
# --------------------------------------------------------------------------- #


def test_incoming_alerts_are_capped_and_drop_oldest_first() -> None:
    session = _session()
    cap = session.alerts._max
    for i in range(cap + 5):
        session.record_incoming_alert(IncomingAlert(text=f"alert-{i}"))
    assert len(session.alerts.entries) == cap
    # FIFO: the first 5 were dropped, newest retained.
    assert session.alerts.entries[0].text == "alert-5"
    assert session.alerts.entries[-1].text == f"alert-{cap + 4}"


# --------------------------------------------------------------------------- #
# Core — integration warm-cache generation guard                              #
# --------------------------------------------------------------------------- #


class TestWarmCacheGeneration:
    def test_stale_generation_is_ignored(self) -> None:
        session = _session()
        session._integration_warm_generation = 5
        session._store_warm_cache({"datadog": {"connection_verified": True}}, generation=3)
        assert session.resolved_integrations_cache is None

    def test_empty_resolve_is_not_cached(self) -> None:
        session = _session()
        session._store_warm_cache({}, generation=session._integration_warm_generation)
        assert session.resolved_integrations_cache is None

    def test_current_generation_stores_the_cache(self) -> None:
        session = _session()
        gen = session._integration_warm_generation
        session._store_warm_cache({"datadog": {"connection_verified": True}}, generation=gen)
        assert session.resolved_integrations_cache is not None
        assert "datadog" in session.resolved_integrations_cache


# --------------------------------------------------------------------------- #
# Terminal — turn-outcome-hint staging (consumed exactly once)                #
# --------------------------------------------------------------------------- #


class TestTurnOutcomeHint:
    def test_pop_returns_then_clears(self) -> None:
        session = _session()
        session.terminal.set_turn_outcome_hint("handled")
        assert session.terminal.pop_turn_outcome_hint() == "handled"
        assert session.terminal.pop_turn_outcome_hint() is None  # consumed once

    def test_blank_hint_is_dropped(self) -> None:
        session = _session()
        session.terminal.set_turn_outcome_hint("   ")
        assert session.terminal.pop_turn_outcome_hint() is None
        session.terminal.set_turn_outcome_hint(None)
        assert session.terminal.pop_turn_outcome_hint() is None


# --------------------------------------------------------------------------- #
# Core — token accounting (the /cost totals)                                  #
# --------------------------------------------------------------------------- #


class TestTokenAccounting:
    def test_record_accumulates_totals_and_call_count(self) -> None:
        tokens = TokenUsage()
        tokens.record(input_tokens=10, output_tokens=5)
        tokens.record(input_tokens=3, output_tokens=0)
        assert tokens.totals["input"] == 13
        assert tokens.totals["output"] == 5
        assert tokens.call_count == 2

    def test_zero_record_is_a_no_op(self) -> None:
        tokens = TokenUsage()
        tokens.record(input_tokens=0, output_tokens=0)
        assert tokens.call_count == 0
        assert tokens.totals == {}

    def test_estimated_and_measured_are_bucketed_separately(self) -> None:
        tokens = TokenUsage()
        tokens.record(input_tokens=4, output_tokens=0, estimated=True)
        tokens.record(input_tokens=6, output_tokens=0, estimated=False)
        assert tokens.totals["input_estimated"] == 4
        assert tokens.totals["input_measured"] == 6
        assert tokens.totals["input"] == 10

    def test_reset_clears_all(self) -> None:
        tokens = TokenUsage()
        tokens.record(input_tokens=10, output_tokens=5)
        tokens.reset()
        assert tokens.totals == {}
        assert tokens.call_count == 0
