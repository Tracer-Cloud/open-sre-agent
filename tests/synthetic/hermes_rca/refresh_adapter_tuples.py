from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCENARIO_DIR = ROOT / "050-surface-sprawl-unknown-adapter"
TUPLES_PATH = SCENARIO_DIR / "adapter_tuples.json"


REQUIRED_FIELDS = {
    "id",
    "messaging_adapter",
    "llm_provider",
    "execution_backend",
    "failing_surface",
    "failing_adapter",
    "expected_family",
    "expected_analog_id",
}


def load_tuples() -> list[dict[str, str]]:
    payload = json.loads(TUPLES_PATH.read_text(encoding="utf-8"))

    if not isinstance(payload, list):
        raise ValueError("adapter_tuples.json must contain a list")

    return payload


def validate_tuples(tuples: list[dict[str, str]]) -> None:
    seen_ids: set[str] = set()

    for item in tuples:
        missing = REQUIRED_FIELDS - set(item.keys())
        if missing:
            raise ValueError(
                f"Tuple {item.get('id', '<unknown>')} missing fields: {sorted(missing)}"
            )

        tuple_id = item["id"]

        if tuple_id in seen_ids:
            raise ValueError(f"Duplicate tuple id: {tuple_id}")

        seen_ids.add(tuple_id)

        for field in REQUIRED_FIELDS:
            value = item[field]

            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Tuple {tuple_id}: field '{field}' must be a non-empty string")


def main() -> int:
    tuples = load_tuples()

    validate_tuples(tuples)

    tuples = sorted(tuples, key=lambda item: item["id"])

    TUPLES_PATH.write_text(
        json.dumps(tuples, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    print(f"Validated and refreshed {len(tuples)} Hermes adapter tuples: {TUPLES_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
