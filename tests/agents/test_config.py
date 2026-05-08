"""Tests for the per-agent budget config loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.agents import config as config_mod
from app.agents.config import (
    AgentBudget,
    AgentsConfig,
    load_agents_config,
    save_agents_config,
    set_agent_budget,
)


@pytest.fixture(autouse=True)
def isolated_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Autouse: redirect ``agents_config_path`` at a per-test tmp file so
    tests never touch the developer's real ``~/.config/opensre/agents.yaml``.
    Tests that need the path itself can still request it by name.
    """
    target = tmp_path / "agents.yaml"
    monkeypatch.setattr(config_mod, "agents_config_path", lambda: target)
    return target


class TestAgentBudget:
    def test_all_fields_optional(self) -> None:
        assert AgentBudget().hourly_budget_usd is None
        assert AgentBudget().progress_minutes is None
        assert AgentBudget().error_rate_pct is None

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentBudget(hourly_budget_usd=-1.0)

    def test_zero_budget_rejected(self) -> None:
        # gt=0 keeps the model in lockstep with the CLI's `usd <= 0`
        # check so a hand-edit can't sneak in a $0/hr ceiling.
        with pytest.raises(ValidationError):
            AgentBudget(hourly_budget_usd=0.0)

    def test_negative_progress_minutes_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentBudget(progress_minutes=-5)

    def test_error_rate_above_100_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentBudget(error_rate_pct=150.0)

    def test_unknown_field_rejected_with_suggestion(self) -> None:
        with pytest.raises(ValidationError) as exc:
            AgentBudget.model_validate({"hourly_budegt_usd": 5.0})
        # StrictConfigModel surfaces a "did you mean" hint
        message = str(exc.value)
        assert "hourly_budegt_usd" in message
        assert "hourly_budget_usd" in message


class TestAgentsConfigModel:
    def test_default_is_empty_dict(self) -> None:
        assert AgentsConfig().agents == {}

    def test_top_level_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentsConfig.model_validate({"agents": {}, "version": 1})


class TestLoadAgentsConfig:
    def test_returns_empty_when_file_missing(self, isolated_path: Path) -> None:
        assert not isolated_path.exists()
        config = load_agents_config()
        assert config == AgentsConfig()

    def test_returns_empty_when_file_is_blank(self, isolated_path: Path) -> None:
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_path.write_text("", encoding="utf-8")
        assert load_agents_config() == AgentsConfig()

    def test_parses_existing_yaml(self, isolated_path: Path) -> None:
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_path.write_text(
            yaml.safe_dump(
                {
                    "agents": {
                        "claude-code": {"hourly_budget_usd": 5.0, "progress_minutes": 8},
                        "aider": {"hourly_budget_usd": 1.4},
                    }
                }
            ),
            encoding="utf-8",
        )
        config = load_agents_config()
        assert config.agents["claude-code"].hourly_budget_usd == 5.0
        assert config.agents["claude-code"].progress_minutes == 8
        assert config.agents["aider"].hourly_budget_usd == 1.4
        assert config.agents["aider"].progress_minutes is None

    def test_returns_empty_on_unparseable_yaml(self, isolated_path: Path) -> None:
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        # Unclosed brace: yaml.YAMLError on parse → loader falls back to empty.
        isolated_path.write_text("agents: {unclosed", encoding="utf-8")
        assert load_agents_config() == AgentsConfig()

    def test_raises_on_strict_typing_violation(self, isolated_path: Path) -> None:
        # Valid YAML, invalid schema (typo). The loader does NOT swallow
        # this because surfacing the error lets the user fix the typo
        # rather than silently overwriting it on the next write.
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_path.write_text(
            yaml.safe_dump({"agents": {"claude-code": {"hourly_budegt_usd": 5.0}}}),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_agents_config()


class TestSaveAgentsConfig:
    def test_creates_parent_directory(self, isolated_path: Path) -> None:
        # tmp_path is the parent of `isolated_path`; the loader writes
        # one level deeper to exercise mkdir(parents=True).
        save_agents_config(AgentsConfig(agents={"aider": AgentBudget(hourly_budget_usd=2.0)}))
        assert isolated_path.exists()
        on_disk = yaml.safe_load(isolated_path.read_text(encoding="utf-8"))
        assert on_disk == {"agents": {"aider": {"hourly_budget_usd": 2.0}}}

    def test_round_trips_through_load(self) -> None:
        original = AgentsConfig(
            agents={
                "claude-code": AgentBudget(hourly_budget_usd=5.0, progress_minutes=8),
                "aider": AgentBudget(error_rate_pct=10.0),
            }
        )
        save_agents_config(original)
        assert load_agents_config() == original


class TestSetAgentBudget:
    def test_writes_and_reloads(self) -> None:
        set_agent_budget("claude-code", 5.0)
        reloaded = load_agents_config()
        assert reloaded.agents["claude-code"].hourly_budget_usd == 5.0

    def test_preserves_other_agents(self) -> None:
        save_agents_config(AgentsConfig(agents={"aider": AgentBudget(hourly_budget_usd=1.4)}))
        set_agent_budget("claude-code", 5.0)
        reloaded = load_agents_config()
        assert reloaded.agents["aider"].hourly_budget_usd == 1.4
        assert reloaded.agents["claude-code"].hourly_budget_usd == 5.0

    def test_preserves_other_fields_on_same_agent(self) -> None:
        save_agents_config(
            AgentsConfig(
                agents={"claude-code": AgentBudget(progress_minutes=8, error_rate_pct=2.5)}
            )
        )
        set_agent_budget("claude-code", 5.0)
        reloaded = load_agents_config().agents["claude-code"]
        assert reloaded.hourly_budget_usd == 5.0
        assert reloaded.progress_minutes == 8
        assert reloaded.error_rate_pct == 2.5

    def test_overwrites_existing_hourly_value(self) -> None:
        set_agent_budget("claude-code", 3.0)
        set_agent_budget("claude-code", 7.5)
        assert load_agents_config().agents["claude-code"].hourly_budget_usd == 7.5

    def test_strips_surrounding_whitespace_from_name(self) -> None:
        set_agent_budget("  claude-code ", 5.0)
        reloaded = load_agents_config()
        assert "claude-code" in reloaded.agents
        # The padded variant did not become a separate key.
        assert "  claude-code " not in reloaded.agents

    def test_rejects_nan_at_api_layer(self) -> None:
        # ``model_copy`` would skip validators and silently persist a
        # ``nan`` that the next load can't parse. Re-validating in
        # ``set_agent_budget`` blocks corruption from any caller that
        # bypasses the CLI's pre-check.
        with pytest.raises(ValidationError):
            set_agent_budget("claude-code", float("nan"))

    def test_rejects_inf_at_api_layer(self) -> None:
        with pytest.raises(ValidationError):
            set_agent_budget("claude-code", float("inf"))
