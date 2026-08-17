"""The harness's import doors are pinned exactly.

* ``core.agent_harness`` — embedder API: entry point, config, session, results, sink.
* ``core.agent_harness.ports`` — what a host implements: the port protocols.
* ``core.agent_harness.spi.<role>`` — what a host calls around a turn, by role;
  the package itself exports nothing. Import-cheap.
* ``core.agent_harness.runtime`` — build and run the agent; loads ``core.agent``.

Adding a name to any door is an API change made here.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import core.agent_harness as root
import core.agent_harness.ports as ports
import core.agent_harness.runtime as runtime
import core.agent_harness.spi as spi_pkg
from tests.shared.harness_doors import PUBLIC_DOORS, SPI_ROLES

PUBLIC_API = frozenset(
    {
        "AgentSession",
        "OutputSink",
        "SessionConfig",
        "SessionCore",
        "SessionManager",
        "ToolCallingTurnResult",
        "TurnResult",
    }
)

PORTS = frozenset(
    {
        "AnswerRequest",
        "ConfirmFn",
        "ConsoleBindable",
        "ErrorReporter",
        "EvidenceGatherer",
        "ExecuteActions",
        "GatheredEvidence",
        "LlmFactory",
        "OutputBindable",
        "OutputSink",
        "PromptContextProvider",
        "ReasoningClientProvider",
        "RunRecordFactory",
        "SessionBindable",
        "SessionState",
        "StreamAnswerFn",
        "SubprocessPresenterFactory",
        "ToolEventObserver",
        "ToolProvider",
        "ToolRegistry",
        "TurnAccounting",
        "TurnBinding",
    }
)

SPI_ROLE_NAMES: dict[str, frozenset[str]] = {
    "session_goal": frozenset(
        {
            "MAX_GOAL_CONDITION_CHARS",
            "SessionGoal",
            "SessionGoalReason",
            "SessionGoalStatus",
            "attach_session_goal",
            "clear_session_goal",
            "format_session_goal_progress",
            "format_session_goal_status_line",
            "run_until_session_goal",
            "session_goal_is_active",
            "session_goal_is_attached",
            "session_goal_is_paused",
            "strip_session_goal_progress_tags",
        }
    ),
    "session_flags": frozenset(
        {
            "background_investigations",
            "background_mode_enabled",
            "background_notification_channels",
            "clear_pending_autosubmit",
            "pop_turn_outcome_hint",
            "session_terminal",
            "set_auto_command",
            "set_turn_outcome_hint",
            "trust_mode_enabled",
        }
    ),
    "cancel": frozenset({"ensure_turn_cancel", "host_cancel_requested"}),
    "accounting": frozenset(
        {
            "DefaultTurnAccounting",
            "LlmRunInfo",
            "ToolCallingAccountingStatus",
            "format_token_total",
            "record_llm_turn",
        }
    ),
    "prompt_chrome": frozenset(
        {"WANT_ME_TO_MARKER", "closer_tail_from", "strip_shell_prompt_chrome"}
    ),
    "integrations": frozenset({"has_resolved_integrations", "resolve_integrations"}),
    "grounding": frozenset(
        {"CacheStats", "GroundingSource", "list_action_skills", "log_grounding_cache_diagnostics"}
    ),
    "defaults": frozenset(
        {
            "DefaultErrorReporter",
            "DefaultPromptContextProvider",
            "default_session_repo",
            "default_session_store",
        }
    ),
}

RUNTIME = frozenset(
    {
        "ActionTurnRunner",
        "DefaultPorts",
        "DefaultToolProvider",
        "GatherPorts",
        "HeadlessAgent",
        "TurnBinding",
    }
)

#: Names an embedder passes into or gets back from the harness; they are also
#: contracts, so they appear in both the api and ports doors on purpose.
API_PORTS_OVERLAP = frozenset({"OutputSink"})
#: TurnBinding is a value a host builds (runtime) and a contract (ports).
RUNTIME_PORTS_OVERLAP = frozenset({"TurnBinding"})


def test_root_exports_exactly_the_public_api() -> None:
    assert set(root.__all__) == PUBLIC_API


def test_ports_exports_exactly_its_list() -> None:
    assert set(ports.__all__) == PORTS


def test_each_spi_role_exports_exactly_its_list() -> None:
    for role, names in SPI_ROLE_NAMES.items():
        module = importlib.import_module(f"core.agent_harness.spi.{role}")
        assert set(module.__all__) == names, role


def test_the_spi_package_itself_exports_nothing() -> None:
    """Hosts import a role, never the grab bag."""
    assert not hasattr(spi_pkg, "__all__")
    assert not any(hasattr(spi_pkg, n) for names in SPI_ROLE_NAMES.values() for n in names)


def test_runtime_exports_exactly_its_list() -> None:
    assert set(runtime.__all__) == RUNTIME


def test_the_doors_do_not_overlap_except_where_stated() -> None:
    all_spi = frozenset().union(*SPI_ROLE_NAMES.values())
    assert PUBLIC_API & PORTS == API_PORTS_OVERLAP
    assert RUNTIME & PORTS == RUNTIME_PORTS_OVERLAP
    assert not (PUBLIC_API & all_spi)
    assert not (PUBLIC_API & RUNTIME)
    assert not (all_spi & RUNTIME)
    assert not (all_spi & PORTS)
    roles = list(SPI_ROLE_NAMES.values())
    for i, a in enumerate(roles):
        for b in roles[i + 1 :]:
            assert not (a & b)


def test_every_name_resolves_through_its_door() -> None:
    for name in PUBLIC_API:
        assert getattr(root, name) is not None
    for name in PORTS:
        assert getattr(ports, name) is not None
    for role, names in SPI_ROLE_NAMES.items():
        module = importlib.import_module(f"core.agent_harness.spi.{role}")
        for name in names:
            assert getattr(module, name) is not None
    for name in RUNTIME:
        assert getattr(runtime, name) is not None


def test_the_border_door_list_matches_the_pinned_roles() -> None:
    """The border tests allow exactly the roles this module pins — no prefix rule."""
    assert frozenset(SPI_ROLE_NAMES) == SPI_ROLES
    assert {
        "core.agent_harness",
        "core.agent_harness.ports",
        "core.agent_harness.runtime",
        *(f"core.agent_harness.spi.{r}" for r in SPI_ROLE_NAMES),
    } == PUBLIC_DOORS


def test_the_root_has_no_lazy_attribute_machinery() -> None:
    assert not hasattr(root, "_LAZY_EXPORTS")
    assert "__getattr__" not in vars(root)


def test_root_ports_and_spi_do_not_load_the_agent_loop() -> None:
    roles = ", ".join(f"core.agent_harness.spi.{r}" for r in SPI_ROLE_NAMES)
    code = (
        f"import sys, core.agent_harness, core.agent_harness.ports, {roles}; "
        "print('core.agent.agent' in sys.modules, "
        "'core.agent_harness.turns.action_driver' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False"], out.stdout
