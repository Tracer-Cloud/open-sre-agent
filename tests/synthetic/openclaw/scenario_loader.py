"""Load OpenClaw synthetic scenario fixtures from disk."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@dataclass
class OpenClawScenario:
    """A synthetic OpenClaw investigation scenario loaded from disk."""

    scenario_id: str
    scenario_dir: Path
    alert: dict[str, Any]
    fixture_conversations: list[dict[str, Any]]
    fixture_tools: list[dict[str, Any]]
    expected_root_cause_keywords: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def alert_name(self) -> str:
        title = self.alert.get("title", "")
        if not title:
            labels = self.alert.get("commonLabels", {})
            title = labels.get("alertname", self.scenario_id)
        return str(title)

    @property
    def pipeline_name(self) -> str:
        labels = self.alert.get("commonLabels", {})
        return str(labels.get("pipeline_name", "openclaw_synthetic"))

    @property
    def severity(self) -> str:
        labels = self.alert.get("commonLabels", {})
        return str(labels.get("severity", "critical"))


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_fixture_tools(scenario_dir: Path) -> list[dict[str, Any]]:
    tools_path = scenario_dir / "openclaw_tools.json"
    if not tools_path.exists():
        return [
            {
                "name": "conversations_list",
                "description": "List recent OpenClaw conversations.",
                "input_schema": None,
            },
            {
                "name": "conversations_get",
                "description": "Get a specific OpenClaw conversation by ID.",
                "input_schema": None,
            },
            {
                "name": "conversations_create",
                "description": "Create a new OpenClaw conversation.",
                "input_schema": None,
            },
            {
                "name": "message_send",
                "description": "Send a message into an OpenClaw conversation.",
                "input_schema": None,
            },
        ]
    data = _load_json(tools_path)
    return list(data) if isinstance(data, list) else []


def load_scenario(scenario_id: str) -> OpenClawScenario:
    """Load a single scenario by directory name."""
    scenario_dir = SCENARIOS_DIR / scenario_id
    if not scenario_dir.is_dir():
        raise FileNotFoundError(f"Scenario directory not found: {scenario_dir}")

    alert = _load_json(scenario_dir / "alert.json")
    conversations_path = scenario_dir / "openclaw_conversations.json"
    conversations = _load_json(conversations_path) if conversations_path.exists() else []

    fixture_tools = _load_fixture_tools(scenario_dir)

    meta_path = scenario_dir / "scenario.json"
    meta: dict[str, Any] = _load_json(meta_path) if meta_path.exists() else {}

    return OpenClawScenario(
        scenario_id=scenario_id,
        scenario_dir=scenario_dir,
        alert=alert,
        fixture_conversations=list(conversations),
        fixture_tools=fixture_tools,
        expected_root_cause_keywords=list(meta.get("expected_root_cause_keywords", [])),
        description=str(meta.get("description", "")),
    )


def load_all_scenarios() -> list[OpenClawScenario]:
    """Load every scenario directory under tests/synthetic/openclaw/scenarios/."""
    if not SCENARIOS_DIR.is_dir():
        return []
    scenarios: list[OpenClawScenario] = []
    for path in sorted(SCENARIOS_DIR.iterdir()):
        if path.is_dir() and not path.name.startswith("_"):
            try:
                scenarios.append(load_scenario(path.name))
            except Exception as exc:
                raise RuntimeError(f"Failed to load scenario '{path.name}': {exc}") from exc
    return scenarios
