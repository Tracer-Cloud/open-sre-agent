"""Per-agent budget config loaded from ``~/.config/opensre/agents.yaml``.

The ``/agents`` dashboard reads ``hourly_budget_usd`` and surfaces it
in the ``$/hr`` column. ``progress_minutes`` and ``error_rate_pct``
are stored for the SLO watchdog landing in a later phase of the
monitor-local-agents roadmap; nothing consumes them today.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import Field

from app.constants import OPENSRE_HOME_DIR
from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)


class AgentBudget(StrictConfigModel):
    """Per-agent SLO thresholds.

    Every field is optional so users can set just one threshold
    (typically ``hourly_budget_usd``) without having to specify all of
    them.
    """

    # ``allow_inf_nan=False`` keeps non-finite floats out of the
    # config. ``inf > 0`` is ``True`` so ``gt=0`` alone wouldn't block
    # ``inf``; ``nan > 0`` is ``False`` but the explicit flag yields
    # a clearer error message either way.
    hourly_budget_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    progress_minutes: int | None = Field(default=None, ge=0)
    error_rate_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class AgentsConfig(StrictConfigModel):
    """Top-level shape of ``~/.config/opensre/agents.yaml``."""

    agents: dict[str, AgentBudget] = Field(default_factory=dict)


def agents_config_path() -> Path:
    """On-disk path. Indirected so tests can monkeypatch the location."""
    return OPENSRE_HOME_DIR / "agents.yaml"


def load_agents_config() -> AgentsConfig:
    """Return the parsed config, or an empty one if the file is absent.

    A corrupted (unparseable) YAML file is treated as absent and logs
    at debug level — the REPL keeps working with defaults rather than
    crashing on a hand-edit gone wrong. ``pydantic.ValidationError``
    is *not* swallowed: a schema mismatch (e.g. a misspelled field
    key) needs to surface so the user can fix the typo, otherwise
    every ``/agents budget`` write would silently overwrite their
    other data.
    """
    path = agents_config_path()
    if not path.exists():
        return AgentsConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        logger.debug("agents.yaml at %s is unparseable; using empty config", path)
        return AgentsConfig()
    if raw is None:
        return AgentsConfig()
    return AgentsConfig.model_validate(raw)


def save_agents_config(config: AgentsConfig) -> Path:
    """Persist ``config`` to disk, creating the parent directory if needed."""
    path = agents_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(exclude_none=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def set_agent_budget(name: str, hourly_budget_usd: float) -> AgentsConfig:
    """Set ``hourly_budget_usd`` for ``name`` and persist the result.

    Loads the current config, updates (or inserts) the entry for
    ``name`` while preserving any other fields it already had, writes
    back, and returns the new config. ``name`` is stripped of
    surrounding whitespace so callers don't accidentally split one
    logical agent into two dict keys (``"claude-code"`` vs
    ``" claude-code "``).

    Goes through ``model_validate`` rather than ``model_copy`` because
    the latter skips field validators — a programmatic caller passing
    ``float("nan")`` or ``float("inf")`` could otherwise persist a
    value that fails ``gt=0`` on the next load and corrupts
    ``agents.yaml``. Re-validation here keeps the public mutation
    surface consistent with the constructor's invariants.
    """
    name = name.strip()
    config = load_agents_config()
    existing = config.agents.get(name)
    if existing is None:
        existing = AgentBudget()
    merged = existing.model_dump(exclude_none=True)
    merged["hourly_budget_usd"] = hourly_budget_usd
    config.agents[name] = AgentBudget.model_validate(merged)
    save_agents_config(config)
    return config


__all__ = [
    "AgentBudget",
    "AgentsConfig",
    "agents_config_path",
    "load_agents_config",
    "save_agents_config",
    "set_agent_budget",
]
