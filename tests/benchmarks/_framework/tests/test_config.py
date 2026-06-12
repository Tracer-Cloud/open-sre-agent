"""Unit tests for BenchmarkConfig parsing, validation, and anti-pattern lint."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.benchmarks._framework.config import (
    BenchmarkConfig,
    FiltersConfig,
    load_config,
    validate_config_or_raise,
)

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _minimal_raw(tmp_path: Path, **overrides: object) -> dict[str, object]:
    """Honest minimal config dict that passes Pydantic + lint."""
    prereg = tmp_path / "prereg.yml"
    prereg.write_text("expected_a1_lift: 0.05\n")
    base: dict[str, object] = {
        "benchmark": "cloudopsbench",
        "modes": ["opensre+llm"],
        "llms": ["claude_sonnet"],
        "model_versions": {"claude_sonnet": "claude-sonnet-4-5-20250929"},
        "runs_per_case": 3,
        "workers": 4,
        "cost_budget_usd": 100.0,
        "seed": 42,
        "output_dir": str(tmp_path / "out"),
        "pre_registration_path": str(prereg),
    }
    base.update(overrides)
    return base


def _write_yaml(path: Path, raw: dict[str, object]) -> None:
    import yaml

    path.write_text(yaml.safe_dump(raw))


# --------------------------------------------------------------------------- #
# Pydantic validation                                                          #
# --------------------------------------------------------------------------- #


def test_minimal_config_parses(tmp_path: Path) -> None:
    config = BenchmarkConfig.model_validate(_minimal_raw(tmp_path))
    assert config.benchmark == "cloudopsbench"
    assert config.runs_per_case == 3


def test_model_versions_must_cover_every_llm(tmp_path: Path) -> None:
    raw = _minimal_raw(
        tmp_path,
        llms=["claude_sonnet", "gpt_4o"],
        model_versions={"claude_sonnet": "claude-sonnet-4-5-20250929"},
    )
    with pytest.raises(ValidationError) as exc_info:
        BenchmarkConfig.model_validate(raw)
    assert "gpt_4o" in str(exc_info.value)


def test_modes_must_be_non_empty(tmp_path: Path) -> None:
    raw = _minimal_raw(tmp_path, modes=[])
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


def test_llms_must_be_non_empty(tmp_path: Path) -> None:
    raw = _minimal_raw(tmp_path, llms=[], model_versions={})
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


def test_cost_budget_must_be_positive(tmp_path: Path) -> None:
    raw = _minimal_raw(tmp_path, cost_budget_usd=0)
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


def test_runs_per_case_must_be_ge_one(tmp_path: Path) -> None:
    raw = _minimal_raw(tmp_path, runs_per_case=0)
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


def test_workers_must_be_ge_one(tmp_path: Path) -> None:
    raw = _minimal_raw(tmp_path, workers=0)
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


def test_report_formats_default_is_json_and_markdown(tmp_path: Path) -> None:
    config = BenchmarkConfig.model_validate(_minimal_raw(tmp_path))
    assert config.report_formats == ["json", "markdown"]


def test_min_tool_calls_defaults_to_none_so_agent_class_default_wins(
    tmp_path: Path,
) -> None:
    """Default config does NOT set min_tool_calls — the CLI override path
    must skip and the BenchInvestigationAgent's import-time floor stands."""
    config = BenchmarkConfig.model_validate(_minimal_raw(tmp_path))
    assert config.min_tool_calls is None


def test_min_tool_calls_accepts_zero_for_drop_floor_experiment(
    tmp_path: Path,
) -> None:
    """Floor-ablation experiment knob: ``min_tool_calls=0`` lets the LLM stop
    when it decides. ge=0 constraint must accept 0 (not >0)."""
    raw = _minimal_raw(tmp_path, min_tool_calls=0)
    config = BenchmarkConfig.model_validate(raw)
    assert config.min_tool_calls == 0


def test_min_tool_calls_rejects_negative(tmp_path: Path) -> None:
    """Negative floor is incoherent — Pydantic ge=0 constraint must catch it."""
    raw = _minimal_raw(tmp_path, min_tool_calls=-1)
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


def test_agent_variant_defaults_to_default(tmp_path: Path) -> None:
    """Default config preserves the production-equivalent BenchInvestigationAgent.
    The CLI override path only fires when agent_variant is explicitly set,
    so the import-time class stands for any config not opting in."""
    config = BenchmarkConfig.model_validate(_minimal_raw(tmp_path))
    assert config.agent_variant == "default"


def test_agent_variant_accepts_trimmed_prompt(tmp_path: Path) -> None:
    """The trimmed-prompt experiment knob: enables the
    BenchInvestigationAgentTrimmedPrompt swap at CLI startup."""
    raw = _minimal_raw(tmp_path, agent_variant="trimmed_prompt")
    config = BenchmarkConfig.model_validate(raw)
    assert config.agent_variant == "trimmed_prompt"


def test_agent_variant_rejects_unknown_value(tmp_path: Path) -> None:
    """Literal type constraint must reject typo'd or unknown variants —
    otherwise a config typo could silently fall through to the default."""
    raw = _minimal_raw(tmp_path, agent_variant="trimedprompt")
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


def test_agent_variant_non_default_rejected_for_adapter_without_capability(
    tmp_path: Path,
) -> None:
    """Cross-field guard: ``agent_variant`` is honored only by adapters
    that declare ``supports_agent_variant=True`` in their
    ``AdapterCapabilities``. A config that sets agent_variant on an
    adapter without that flag (or on an unknown adapter — the lint path
    treats unknown as all-False to surface typos) would silently run
    the default agent. The lint gate must reject so the intent is
    explicit. The error message names ``supports_agent_variant`` so
    operators see exactly what to fix in the adapter declaration."""
    raw = _minimal_raw(tmp_path, benchmark="some_other_bench", agent_variant="trimmed_prompt")
    config = BenchmarkConfig.model_validate(raw)
    errors = config.lint()
    assert any("agent_variant" in e and "supports_agent_variant" in e for e in errors), (
        f"Expected a capability-based agent_variant error; got: {errors}"
    )


def test_agent_variant_default_passes_lint_for_any_benchmark(tmp_path: Path) -> None:
    """The default value must not block adapters without the capability —
    only non-default values trigger the cross-field guard."""
    raw = _minimal_raw(tmp_path, benchmark="some_other_bench")
    config = BenchmarkConfig.model_validate(raw)
    errors = config.lint()
    assert not any("agent_variant" in e for e in errors)


def test_agent_variant_non_default_passes_lint_for_capable_adapter(tmp_path: Path) -> None:
    """The lint guard must accept ``agent_variant`` on adapters that
    declare ``supports_agent_variant=True``. CloudOpsBench is the
    current truth source. Pin so a future capability regression on the
    CloudOpsBench adapter immediately surfaces here rather than later
    when bench configs start failing in CI."""
    raw = _minimal_raw(tmp_path, benchmark="cloudopsbench", agent_variant="trimmed_prompt")
    config = BenchmarkConfig.model_validate(raw)
    errors = config.lint()
    assert not any("agent_variant" in e for e in errors), errors


def test_predictor_variant_structured_rejected_for_adapter_without_capability(
    tmp_path: Path,
) -> None:
    """Cross-field guard: ``predictor_variant=structured`` requires the
    adapter to declare ``supports_predictor_variant=True``. An adapter
    without a predictor stage (or an unknown one) is refused."""
    raw = _minimal_raw(tmp_path, benchmark="some_other_bench", predictor_variant="structured")
    config = BenchmarkConfig.model_validate(raw)
    errors = config.lint()
    assert any("predictor_variant" in e and "supports_predictor_variant" in e for e in errors), (
        f"Expected a capability-based predictor_variant error; got: {errors}"
    )


def test_predictor_variant_structured_passes_lint_for_capable_adapter(
    tmp_path: Path,
) -> None:
    """The lint guard must accept ``predictor_variant=structured`` on
    adapters that declare ``supports_predictor_variant=True`` AND use
    an OpenAI-compatible LLM (the second guard is independent and lives
    next to the capability check). Pin both at once."""
    raw = _minimal_raw(tmp_path, benchmark="cloudopsbench", predictor_variant="structured")
    config = BenchmarkConfig.model_validate(raw)
    errors = config.lint()
    # The OpenAI-LLM guard still fires on its own merits if the LLM list
    # is non-OpenAI, but the capability guard must NOT fire when the
    # adapter declares it.
    assert not any(
        "predictor_variant" in e and "supports_predictor_variant" in e for e in errors
    ), errors


def test_report_formats_must_be_non_empty(tmp_path: Path) -> None:
    raw = _minimal_raw(tmp_path, report_formats=[])
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


def test_invalid_mode_rejected(tmp_path: Path) -> None:
    raw = _minimal_raw(tmp_path, modes=["llm_with_unicorns"])
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate(raw)


# --------------------------------------------------------------------------- #
# lint() — anti-pattern enforcement                                           #
# --------------------------------------------------------------------------- #


def test_lint_passes_on_honest_config(tmp_path: Path) -> None:
    config = BenchmarkConfig.model_validate(_minimal_raw(tmp_path))
    assert config.lint() == []


def test_lint_rejects_runs_per_case_below_three(tmp_path: Path) -> None:
    config = BenchmarkConfig.model_validate(_minimal_raw(tmp_path, runs_per_case=2))
    errors = config.lint()
    assert any("runs_per_case=2" in e for e in errors)


def test_lint_rejects_missing_pre_registration_path(tmp_path: Path) -> None:
    raw = _minimal_raw(tmp_path)
    raw.pop("pre_registration_path", None)
    config = BenchmarkConfig.model_validate(raw)
    errors = config.lint()
    assert any("pre_registration_path" in e for e in errors)


def test_lint_warns_on_oversized_grid(tmp_path: Path) -> None:
    """452 cases × 5 LLMs × 2 modes × 5 runs = 22,600 — over the 20k cap."""
    config = BenchmarkConfig.model_validate(
        _minimal_raw(
            tmp_path,
            llms=["a", "b", "c", "d", "e"],
            model_versions={
                "a": "claude-sonnet-4-5-20250929",
                "b": "claude-sonnet-4-5-20250929",
                "c": "claude-sonnet-4-5-20250929",
                "d": "claude-sonnet-4-5-20250929",
                "e": "claude-sonnet-4-5-20250929",
            },
            modes=["opensre+llm", "llm_alone"],
            runs_per_case=5,
        )
    )
    errors = config.lint()
    assert any("too large" in e for e in errors)


def test_lint_warns_on_oversized_cost_budget(tmp_path: Path) -> None:
    config = BenchmarkConfig.model_validate(_minimal_raw(tmp_path, cost_budget_usd=50_000))
    errors = config.lint()
    assert any("unusually" in e for e in errors)


def test_lint_rejects_system_path_output_dir(tmp_path: Path) -> None:
    config = BenchmarkConfig.model_validate(_minimal_raw(tmp_path, output_dir="/etc/opensre"))
    errors = config.lint()
    assert any("system path" in e for e in errors)


# --------------------------------------------------------------------------- #
# load_config — YAML parsing + env var overrides                              #
# --------------------------------------------------------------------------- #


def test_load_config_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    _write_yaml(config_path, _minimal_raw(tmp_path))
    loaded = load_config(config_path)
    assert loaded.benchmark == "cloudopsbench"


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "no-such-file.yml")


def test_load_config_non_mapping_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("- not\n- a\n- mapping\n")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_config(config_path)


def test_load_config_env_var_overrides_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yml"
    _write_yaml(config_path, _minimal_raw(tmp_path, workers=2))
    monkeypatch.setenv("OPENSRE_BENCH_WORKERS", "16")
    loaded = load_config(config_path)
    assert loaded.workers == 16


def test_load_config_env_var_overrides_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yml"
    _write_yaml(config_path, _minimal_raw(tmp_path, cost_budget_usd=10.0))
    monkeypatch.setenv("OPENSRE_BENCH_COST_BUDGET_USD", "250.5")
    loaded = load_config(config_path)
    assert loaded.cost_budget_usd == 250.5


# --------------------------------------------------------------------------- #
# validate_config_or_raise — combined load + lint                             #
# --------------------------------------------------------------------------- #


def test_validate_config_or_raise_returns_honest_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    _write_yaml(config_path, _minimal_raw(tmp_path))
    config = validate_config_or_raise(config_path)
    assert config.benchmark == "cloudopsbench"


def test_validate_config_or_raise_surfaces_all_lint_errors(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    raw = _minimal_raw(tmp_path, runs_per_case=1)
    raw.pop("pre_registration_path", None)
    _write_yaml(config_path, raw)
    with pytest.raises(ValueError) as exc_info:
        validate_config_or_raise(config_path)
    msg = str(exc_info.value)
    assert "runs_per_case=1" in msg
    assert "pre_registration_path" in msg


# --------------------------------------------------------------------------- #
# FiltersConfig                                                                #
# --------------------------------------------------------------------------- #


def test_filters_config_defaults_to_empty() -> None:
    filters = FiltersConfig()
    assert filters.systems == []
    assert filters.fault_categories == []
    assert filters.difficulty == []
    assert filters.seen_shape == []
    assert filters.case_ids == []
    assert filters.limit is None


def test_filters_config_rejects_invalid_difficulty() -> None:
    with pytest.raises(ValidationError):
        FiltersConfig(difficulty=["catastrophic"])  # type: ignore[list-item]
