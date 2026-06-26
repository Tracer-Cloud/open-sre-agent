"""Tests for per-run provenance capture.

The provenance.json artifact is the contract between this run and any future
reviewer. These tests guard against three failure modes:

  1. **Drift** — schema changes that break downstream comparison tooling
  2. **Secret leakage** — API keys / tokens accidentally captured
  3. **Self-contained violation** — config / pre-reg content not inlined,
     forcing reviewers to chase external files that may have moved
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.benchmarks._framework.adapters import (
    AlertPayload,
    BenchmarkAdapter,
    BenchmarkCase,
    CaseFilters,
    CaseScore,
    MetricSchema,
    RunContext,
    RunResult,
)
from tests.benchmarks._framework.config import BenchmarkConfig
from tests.benchmarks._framework.provenance import (
    PROVENANCE_SCHEMA_VERSION,
    capture_provenance,
)

# --------------------------------------------------------------------------- #
# Honest fake adapter — passes ABC, exposes optional dataset attrs            #
# --------------------------------------------------------------------------- #


class _FakeAdapter(BenchmarkAdapter):
    name = "fakeops"
    version = "0.0.1"
    data_contamination_checked = True
    hf_dataset = "fakecorp/fakeops-dataset"
    hf_revision = "abc1234"
    benchmark_dir = Path("/tmp/fakeops-cache")

    def load_cases(self, _filters: CaseFilters):
        return iter([])

    def build_alert(self, _case: BenchmarkCase) -> AlertPayload:
        return AlertPayload(raw={}, normalized={})

    def build_opensre_integrations(self, _case: BenchmarkCase) -> dict[str, Any]:
        return {}

    def build_baseline_tools(self, _case: BenchmarkCase) -> dict[str, Any]:
        return {}

    def score_case(self, case: BenchmarkCase, _run: RunResult, _context: RunContext) -> CaseScore:
        return CaseScore(case_id=case.case_id, metrics={"a1": 1.0})

    def metric_schema(self) -> MetricSchema:
        return MetricSchema(
            outcome_metrics=["a1"],
            validity_metrics=["grounding"],
            higher_is_better={"a1": True, "grounding": True},
        )


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_config(
    tmp_path: Path,
    *,
    pre_reg_content: str | None = "# Empty prereg\n",
) -> tuple[BenchmarkConfig, Path, Path]:
    """Return (config, config_path, pre_reg_path) for a happy-path test.

    pre_reg_path is always a Path — if pre_reg_content is None, the path
    points at a non-existent file under tmp_path so type-narrowing is happy.
    """
    config_path = tmp_path / "config.yml"
    pre_reg_path = tmp_path / "prereg.yml"
    if pre_reg_content is not None:
        pre_reg_path.write_text(pre_reg_content)

    config_path.write_text("benchmark: fakeops\n")
    config = BenchmarkConfig.model_validate(
        {
            "benchmark": "fakeops",
            "modes": ["opensre+llm"],
            "llms": ["claude-4-sonnet"],
            "model_versions": {"claude-4-sonnet": "claude-sonnet-4-5-20250929"},
            "seed": 42,
            "cost_budget_usd": 10.0,
            "output_dir": str(tmp_path / "out"),
            "pre_registration_path": str(pre_reg_path) if pre_reg_content is not None else None,
        }
    )
    return config, config_path, pre_reg_path


# --------------------------------------------------------------------------- #
# Schema shape                                                                #
# --------------------------------------------------------------------------- #


def test_capture_provenance_has_expected_top_level_keys(tmp_path: Path) -> None:
    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="2026-01-01T00:00:00+00:00",
        config_path=config_path,
    )
    assert prov["schema_version"] == PROVENANCE_SCHEMA_VERSION
    assert prov["run_id"] == "r1"
    assert prov["started_at"] == "2026-01-01T00:00:00+00:00"
    expected = {
        "schema_version",
        "run_id",
        "started_at",
        "code",
        "config",
        "pre_registration",
        "models",
        "environment",
        "dataset",
        "run_inputs",
    }
    assert expected.issubset(prov.keys())


# --------------------------------------------------------------------------- #
# Code (git) section                                                          #
# --------------------------------------------------------------------------- #


def test_code_section_includes_sha_branch_dirty_files(tmp_path: Path) -> None:
    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    code = prov["code"]
    for key in (
        "opensre_sha",
        "opensre_short_sha",
        "opensre_branch",
        "opensre_dirty",
        "opensre_changed_files",
    ):
        assert key in code
    assert isinstance(code["opensre_dirty"], bool)
    assert isinstance(code["opensre_changed_files"], list)


def test_git_state_preserves_first_char_of_unstaged_changed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``git status --porcelain`` column 0 is a significant space for
    unstaged changes (" M app/…"). An earlier strip ate that space on the first
    line, so the fixed-width [3:] path slice dropped the path's first char
    ("app/…" → "pp/…"). Pin that the porcelain is read UNSTRIPPED and the path is
    intact. The fake asserts strip=False is actually passed: if it weren't, it
    would return the stripped form and the path would regress to "pp/…".
    """
    from tests.benchmarks._framework import provenance as prov_mod

    def fake_run_git(*args: str, strip: bool = True) -> str:
        if args[:2] == ("status", "--porcelain"):
            raw = (
                " M core/orchestration/node/investigate/agent.py\n?? tests/benchmarks/new_file.py\n"
            )
            return raw if not strip else raw.strip()
        return "deadbeef"

    monkeypatch.setattr(prov_mod, "_run_git", fake_run_git)

    state = prov_mod._git_state()

    assert state["opensre_dirty"] is True
    assert state["opensre_changed_files"] == [
        "core/orchestration/node/investigate/agent.py",
        "tests/benchmarks/new_file.py",
    ]


# --------------------------------------------------------------------------- #
# Config + pre-registration: inline content + sha256                          #
# --------------------------------------------------------------------------- #


def test_config_content_is_inlined_with_sha256(tmp_path: Path) -> None:
    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    section = prov["config"]
    assert section["path"] == str(config_path)
    assert section["content"] == config_path.read_text()
    assert section["sha256"] is not None
    assert len(section["sha256"]) == 64  # sha256 hex


def test_pre_registration_content_is_inlined_with_sha256(tmp_path: Path) -> None:
    config, config_path, pre_reg_path = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    section = prov["pre_registration"]
    assert section["path"] == str(pre_reg_path)
    assert section["content"] == "# Empty prereg\n"
    assert section["sha256"] is not None


def test_missing_config_path_yields_null_content(tmp_path: Path) -> None:
    """When the runner is called inline (no YAML file), section should be
    structured but empty — never crash, never lie about path."""
    config, _, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        # config_path omitted intentionally
    )
    assert prov["config"]["path"] is None
    assert prov["config"]["content"] is None
    assert prov["config"]["sha256"] is None


# --------------------------------------------------------------------------- #
# Models section: spec + pricing snapshot per LLM                              #
# --------------------------------------------------------------------------- #


def test_models_section_records_spec_and_pricing(tmp_path: Path) -> None:
    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    models = prov["models"]
    assert "claude-4-sonnet" in models
    entry = models["claude-4-sonnet"]
    assert entry["configured_version"] == "claude-sonnet-4-5-20250929"
    assert entry["provider"] == "anthropic"
    assert entry["spec_reasoning_model"] == "claude-sonnet-4-5-20250929"
    assert entry["pricing_snapshot"] == {
        "input_usd_per_mtok": 3.0,
        "output_usd_per_mtok": 15.0,
    }


# --------------------------------------------------------------------------- #
# Environment section: python + key packages + safe env snapshot              #
# --------------------------------------------------------------------------- #


def test_environment_section_has_python_and_packages(tmp_path: Path) -> None:
    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    env = prov["environment"]
    assert env["python_version"]
    assert env["platform"]
    pkgs = env["key_packages"]
    # We pinned anthropic + openai + pydantic — at least one of these must
    # be installed for the test environment, else our pyproject is broken
    assert "anthropic" in pkgs
    assert "pydantic" in pkgs
    assert pkgs["pydantic"] is not None  # always present


# --------------------------------------------------------------------------- #
# SECRET REDACTION — the critical safety test                                  #
# --------------------------------------------------------------------------- #


def test_env_snapshot_excludes_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if an API key is set in the process env, provenance must NEVER
    record it. This is the most important test in the file — failure here
    would leak secrets into committed reports."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-this-must-never-appear")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-also-must-never-appear")
    monkeypatch.setenv("MYAPP_TOKEN", "tk-secret-token")
    monkeypatch.setenv("DB_PASSWORD", "hunter2")
    monkeypatch.setenv("OPENSRE_BENCH_WORKERS", "8")  # safe — on allowlist

    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    env_vars = prov["environment"]["env"]
    # Safe var DOES appear
    assert env_vars.get("OPENSRE_BENCH_WORKERS") == "8"
    # Secret vars MUST NOT appear under any key
    serialized = str(prov)
    assert "sk-this-must-never-appear" not in serialized
    assert "sk-also-must-never-appear" not in serialized
    assert "tk-secret-token" not in serialized
    assert "hunter2" not in serialized
    assert "ANTHROPIC_API_KEY" not in env_vars
    assert "OPENAI_API_KEY" not in env_vars
    assert "MYAPP_TOKEN" not in env_vars
    assert "DB_PASSWORD" not in env_vars


def test_env_snapshot_only_returns_allowlisted_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitelist is closed-form: anything not on it must be dropped."""
    monkeypatch.setenv("RANDOM_UNRELATED_VAR", "something")
    monkeypatch.setenv("OPENSRE_BENCH_COST_BUDGET_USD", "250.50")

    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    env_vars = prov["environment"]["env"]
    assert "RANDOM_UNRELATED_VAR" not in env_vars
    assert env_vars.get("OPENSRE_BENCH_COST_BUDGET_USD") == "250.50"


# --------------------------------------------------------------------------- #
# Dataset section: best-effort from adapter attrs                              #
# --------------------------------------------------------------------------- #


def test_dataset_section_pulls_from_adapter_attrs(tmp_path: Path) -> None:
    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    dataset = prov["dataset"]
    assert dataset["adapter_name"] == "fakeops"
    assert dataset["adapter_version"] == "0.0.1"
    assert dataset["hf_dataset"] == "fakecorp/fakeops-dataset"
    assert dataset["hf_revision"] == "abc1234"
    assert dataset["local_path"] == "/tmp/fakeops-cache"
    assert dataset["data_contamination_checked"] is True


def test_dataset_section_tolerates_adapters_without_dataset_attrs(
    tmp_path: Path,
) -> None:
    class _Bare(_FakeAdapter):
        hf_dataset = None  # type: ignore[assignment]
        hf_revision = None  # type: ignore[assignment]
        benchmark_dir = None  # type: ignore[assignment]
        data_contamination_checked = False

    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_Bare(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    dataset = prov["dataset"]
    assert dataset["hf_dataset"] is None
    assert dataset["hf_revision"] is None
    assert dataset["local_path"] is None
    assert dataset["data_contamination_checked"] is False


# --------------------------------------------------------------------------- #
# Run-inputs section                                                          #
# --------------------------------------------------------------------------- #


def test_run_inputs_section_echoes_key_config_fields(tmp_path: Path) -> None:
    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    inputs = prov["run_inputs"]
    assert inputs["modes"] == ["opensre+llm"]
    assert inputs["llms"] == ["claude-4-sonnet"]
    assert inputs["seed"] == 42
    assert inputs["cost_budget_usd"] == 10.0


# --------------------------------------------------------------------------- #
# Determinism (no randomness in capture itself)                               #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# extend_provenance hook (Phase 4 of framework decoupling)                    #
# --------------------------------------------------------------------------- #


def test_default_extend_provenance_returns_dict_unchanged(tmp_path: Path) -> None:
    """The ABC's default ``extend_provenance`` is identity. An adapter
    that doesn't override the hook must leave the framework's standard
    provenance shape intact — no extra keys, no missing keys."""
    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    # The framework's run_inputs section is adapter-agnostic. The
    # previous ``min_tool_calls`` key (which used to be injected by the
    # framework directly importing CloudOpsBench) must be absent for an
    # adapter that doesn't opt in via extend_provenance.
    assert "min_tool_calls" not in prov["run_inputs"]


def test_extend_provenance_can_inject_adapter_specific_keys(tmp_path: Path) -> None:
    """An adapter that overrides ``extend_provenance`` can add keys to
    any section of the provenance dict. The framework respects the
    returned dict — this is the strategy-pattern hook that replaces the
    Phase 3 direct import of CloudOpsBench from the framework."""

    class _ExtendingAdapter(_FakeAdapter):  # type: ignore[misc]
        def extend_provenance(self, provenance: dict[str, Any]) -> dict[str, Any]:
            provenance["run_inputs"]["my_adapter_knob"] = "sentinel-value"
            provenance["my_top_level_key"] = "another-sentinel"
            return provenance

    config, config_path, _ = _make_config(tmp_path)
    prov = capture_provenance(
        config=config,
        adapter=_ExtendingAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    assert prov["run_inputs"]["my_adapter_knob"] == "sentinel-value"
    assert prov["my_top_level_key"] == "another-sentinel"


def test_cloudopsbench_adapter_injects_min_tool_calls_into_run_inputs() -> None:
    """The CloudOpsBench adapter overrides ``extend_provenance`` to
    inject ``min_tool_calls`` into the ``run_inputs`` section. Phase 4
    moved this capture out of the framework and into the adapter that
    owns the knob; this test pins the contract end-to-end."""
    from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter

    adapter = CloudOpsBenchAdapter.__new__(CloudOpsBenchAdapter)
    provenance: dict[str, Any] = {"run_inputs": {}}
    extended = adapter.extend_provenance(provenance)
    # The key must be present. The value may be ``None`` (when the
    # bench_agent import fails in a sandbox) or an int (when opensre
    # deps are available) — both are valid; the contract is "key exists".
    assert "min_tool_calls" in extended["run_inputs"]


def test_two_captures_of_same_state_are_equivalent(tmp_path: Path) -> None:
    """Capture is pure (modulo git state) — back-to-back calls return
    equivalent data."""
    config, config_path, _ = _make_config(tmp_path)
    first = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    second = capture_provenance(
        config=config,
        adapter=_FakeAdapter(),
        run_id="r1",
        started_at="x",
        config_path=config_path,
    )
    # Drop the code section since dirty-status snapshots include file lists
    # that can fluctuate if files change between calls.
    first.pop("code")
    second.pop("code")
    assert first == second
