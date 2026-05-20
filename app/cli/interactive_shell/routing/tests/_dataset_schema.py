"""Shared dataset schema guards for routing prompt fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

TESTS_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = TESTS_DIR / "prompts"


def prompt_dataset_path(filename: str) -> Path:
    """Build the absolute path to a routing prompt dataset file."""
    top_level = TESTS_DIR / filename
    if top_level.exists():
        return top_level
    return PROMPTS_DIR / filename


def load_yaml_dataset(filename: str) -> list[dict[str, object]]:
    """Load a YAML dataset file and enforce top-level list-of-mapping shape."""
    dataset_path = prompt_dataset_path(filename)
    if not dataset_path.exists():
        msg = (
            f"Missing canonical routing dataset: {dataset_path}. "
            f"Expected file {filename!r} under {TESTS_DIR} or {PROMPTS_DIR}."
        )
        raise AssertionError(msg)

    payload = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        msg = (
            f"Routing dataset {dataset_path} must contain a top-level YAML list, "
            f"got {type(payload).__name__}."
        )
        raise AssertionError(msg)

    records: list[dict[str, object]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            msg = (
                f"Routing dataset {dataset_path} entry #{index} must be a mapping, "
                f"got {type(item).__name__}."
            )
            raise AssertionError(msg)
        records.append(cast(dict[str, object], item))
    return records


def validate_prompt_dataset(
    dataset: list[dict[str, object]],
    *,
    dataset_name: str,
    required_fields: tuple[str, ...],
    non_empty_string_fields: tuple[str, ...],
) -> None:
    """Validate required fields, non-empty strings, and unique ids."""
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()

    for index, record in enumerate(dataset, start=1):
        for field in required_fields:
            if field not in record:
                msg = (
                    f"{dataset_name} entry #{index} is missing required field {field!r}. "
                    f"Required fields: {required_fields}."
                )
                raise AssertionError(msg)

        for field in non_empty_string_fields:
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                msg = (
                    f"{dataset_name} entry #{index} field {field!r} must be a non-empty "
                    f"string, got {value!r}."
                )
                raise AssertionError(msg)

        case_id = record.get("id")
        if isinstance(case_id, str) and case_id.strip():
            if case_id in seen_ids:
                duplicate_ids.add(case_id)
            seen_ids.add(case_id)

    if duplicate_ids:
        duplicates = ", ".join(sorted(duplicate_ids))
        msg = f"{dataset_name} contains duplicate id values: {duplicates}."
        raise AssertionError(msg)
