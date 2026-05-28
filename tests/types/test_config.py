from __future__ import annotations

from pathlib import Path
from typing import cast

from app.types.config import NodeConfig, get_configurable


def test_get_configurable_returns_payload() -> None:
    config: NodeConfig = {"configurable": {"thread_id": "thread-1"}}

    assert get_configurable(config) == {"thread_id": "thread-1"}


def test_get_configurable_tolerates_missing_or_invalid_configurable() -> None:
    assert get_configurable(None) == {}
    assert get_configurable({}) == {}
    assert get_configurable(cast(NodeConfig, {"configurable": None})) == {}


def test_pipeline_does_not_import_runnable_config() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    targets = [
        repo_root / "app" / "agent",
        repo_root / "app" / "pipeline" / "runners.py",
        repo_root / "app" / "pipeline" / "pipeline.py",
    ]
    banned = ("RunnableConfig",)
    offenders: list[str] = []

    for target in targets:
        if not target.exists():
            continue
        files = target.rglob("*.py") if target.is_dir() else [target]
        for path in files:
            text = path.read_text(encoding="utf-8")
            if any(pattern in text for pattern in banned):
                offenders.append(str(path.relative_to(repo_root)))

    assert offenders == []
