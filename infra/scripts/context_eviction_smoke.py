"""Force context-budget eviction and show which evidence survives.

Usage:
    uv run python infra/scripts/context_eviction_smoke.py --policy value
    uv run python infra/scripts/context_eviction_smoke.py --policy oldest
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from app.agent.tool_loop import _enforce_context_budget, _tag_context_message


def _tool_use_message(tool_id: str, name: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}],
    }


def _tool_result_message(tool_id: str, content: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}],
    }


def _build_messages() -> list[dict[str, Any]]:
    seed_assistant = _tag_context_message(
        _tool_use_message("seed", "query_logs"),
        protected=True,
        seed=True,
        iteration=-1,
        tool_names=["query_logs"],
    )
    seed_result = _tag_context_message(
        _tool_result_message("seed", "ROOT_CAUSE_CLUE " + ("s" * 5_000)),
        protected=True,
        seed=True,
        iteration=-1,
        tool_names=["query_logs"],
    )

    return [
        {"role": "user", "content": "alert"},
        seed_assistant,
        seed_result,
        _tool_use_message("later", "query_logs"),
        _tool_result_message("later", "x" * 18_000),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        choices=("value", "oldest"),
        default="value",
        help="Context eviction policy to test.",
    )
    args = parser.parse_args()

    os.environ["OPENSRE_CONTEXT_EVICTION_POLICY"] = args.policy
    messages = _build_messages()

    _enforce_context_budget(messages, ceiling=10_000)

    dumped = json.dumps(messages)
    print(f"policy: {args.policy}")
    print(f"seed kept: {'ROOT_CAUSE_CLUE' in dumped}")
    print(f"later dropped: {'later' not in dumped}")
    print(f"message count: {len(messages)}")


if __name__ == "__main__":
    main()
