from __future__ import annotations

import json

from tests.synthetic.hermes_rca.run_suite import HISTORY_DIR


def main() -> int:
    snapshots = sorted(HISTORY_DIR.glob("*.json"))

    if not snapshots:
        print("No Hermes benchmark snapshots found.")
        return 0

    latest = snapshots[-1]

    payload = json.loads(latest.read_text(encoding="utf-8"))

    print(f"Snapshot: {latest.name}")
    print(f"Pass rate: {payload['pass_rate']:.2%}")
    print(f"Passed: {payload['passed']}")
    print(f"Failed: {payload['failed']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
