from __future__ import annotations

from tests.synthetic.hermes_rca.analog_registry import (
    ANALOG_SCENARIOS,
    analog_ids,
    analogs_by_family,
    find_analog_by_id,
)
from tests.synthetic.hermes_rca.surface_scoring import (
    score_adapter_family,
    score_analog_identification,
    score_diagnostic_question,
    score_surface_response,
)


def test_analog_registry_has_parts_1_to_4_coverage() -> None:
    ids = analog_ids()

    assert "001-codex-empty-response" in ids
    assert "010-compression-invalid-tool-ordering" in ids
    assert "020-multi-agent-orchestration-missing" in ids
    assert "040-determinism-engine-missing" in ids


def test_analog_registry_has_no_duplicate_ids() -> None:
    ids = [scenario.scenario_id for scenario in ANALOG_SCENARIOS]

    assert len(ids) == len(set(ids))


def test_find_analog_by_id_returns_expected_scenario() -> None:
    scenario = find_analog_by_id("002-openrouter-400-all-models")

    assert scenario is not None
    assert scenario.family == "llm_provider"
    assert scenario.failure_mode == "provider_http_400"


def test_analogs_by_family_filters_registry() -> None:
    scenarios = analogs_by_family("memory")

    assert scenarios
    assert all(scenario.family == "memory" for scenario in scenarios)


def test_score_adapter_family_accepts_aliases() -> None:
    assert score_adapter_family("This is an llm provider failure.", "llm_provider")
    assert score_adapter_family("This is a model provider failure.", "llm_provider")
    assert score_adapter_family("The runtime backend crashed.", "execution_backend")
    assert score_adapter_family("The messaging adapter failed.", "messaging")


def test_score_analog_identification_accepts_exact_or_spaced_id() -> None:
    assert score_analog_identification(
        "Closest analog is 002-openrouter-400-all-models.",
        "002-openrouter-400-all-models",
    )
    assert score_analog_identification(
        "Closest analog is 002 openrouter 400 all models.",
        "002-openrouter-400-all-models",
    )


def test_score_diagnostic_question_requires_single_actionable_question() -> None:
    assert score_diagnostic_question("Can you fetch the raw response body?")
    assert not score_diagnostic_question("This has no question.")
    assert score_diagnostic_question("Is the adapter unknown? Can you fetch the raw response body?")


def test_score_surface_response_requires_two_of_three_dimensions() -> None:
    score = score_surface_response(
        output=(
            "This is an LLM provider surface issue, closest to "
            "002-openrouter-400-all-models. Can you fetch the response body?"
        ),
        expected_family="llm_provider",
        expected_analog_id="002-openrouter-400-all-models",
    )

    assert score.passed
    assert score.passed_dimensions == 3


def test_score_surface_response_fails_when_too_vague() -> None:
    score = score_surface_response(
        output="This seems bad and should be investigated.",
        expected_family="llm_provider",
        expected_analog_id="002-openrouter-400-all-models",
    )

    assert not score.passed
    assert score.passed_dimensions == 0


def test_score_adapter_family_rejects_cross_family_generic_terms() -> None:
    assert not score_adapter_family(
        "This appears to be a memory backend issue.",
        "execution_backend",
    )
    assert not score_adapter_family(
        "The runtime backend is healthy but provider routing failed.",
        "agent_runtime",
    )
