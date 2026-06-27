from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    services: tuple[str, ...]
    workflow: str | None
    description: str | None


@dataclass(frozen=True)
class FeatureOperatorHint:
    feature: str
    recently_shipped: bool
    scheduled_workflow: bool
    note: str | None


@dataclass(frozen=True)
class FeatureWorkflowConfig:
    endpoint_features: dict[str, tuple[str, ...]]
    features: dict[str, FeatureDefinition]
    operator_hints: dict[str, FeatureOperatorHint]


def load_feature_workflow_config(path: str | Path) -> FeatureWorkflowConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    endpoints_raw: dict[str, Any] = raw.get("endpoints", {})
    features_raw: dict[str, Any] = raw.get("features", {})
    hints_raw: dict[str, Any] = raw.get("operator_hints", {})

    endpoint_features = {
        endpoint: tuple(value.get("features", ())) for endpoint, value in endpoints_raw.items()
    }

    features = {
        name: FeatureDefinition(
            name=name,
            services=tuple(value.get("services", ())),
            workflow=value.get("workflow"),
            description=value.get("description"),
        )
        for name, value in features_raw.items()
    }

    operator_hints = {
        name: FeatureOperatorHint(
            feature=name,
            recently_shipped=bool(value.get("recently_shipped", False)),
            scheduled_workflow=bool(value.get("scheduled_workflow", False)),
            note=value.get("note"),
        )
        for name, value in hints_raw.items()
    }

    return FeatureWorkflowConfig(
        endpoint_features=endpoint_features,
        features=features,
        operator_hints=operator_hints,
    )


def resolve_feature_keywords(
    *,
    endpoint: str | None,
    service_name: str,
    config: FeatureWorkflowConfig,
) -> tuple[str, ...]:
    matched: set[str] = set()

    if endpoint and endpoint in config.endpoint_features:
        matched.update(config.endpoint_features[endpoint])

    normalized_service = service_name.lower()

    for feature_name, feature in config.features.items():
        if any(service.lower() in normalized_service for service in feature.services):
            matched.add(feature_name)
            if feature.workflow:
                matched.add(feature.workflow)

    for feature_name, hint in config.operator_hints.items():
        if feature_name in matched:
            if hint.recently_shipped:
                matched.add("recently_shipped")
            if hint.scheduled_workflow:
                matched.add("scheduled_workflow")
            if hint.note:
                matched.update(
                    token for token in hint.note.lower().replace("-", " ").split() if len(token) > 2
                )

    return tuple(sorted(matched))
