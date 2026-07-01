"""Run the live-LLM turn scenario suite sharded across local processes.

This mirrors the CI ``turn-live`` job
(``.github/workflows/interactive-shell-live.yml``): each shard sets
``TURN_SHARD_TOTAL`` / ``TURN_SHARD_INDEX`` and runs the live pytest selection
``-m live_llm -k "test_live_turn_execution_oracle or test_live_action_planning"``
against ``tests/core/agent/test_turn_scenarios.py``.

The suite is IO-bound (it waits on real LLM API calls), so running all shards
concurrently finishes in roughly one shard's wall time instead of the serial
total. Each shard runs as its own pytest process; per-shard output is streamed
to a log file and the exit codes are aggregated into a final summary.

Usage:

    uv run python .github/ci/run_live_turn_shards.py            # all 8 shards
    uv run python .github/ci/run_live_turn_shards.py --shards 4
    uv run python .github/ci/run_live_turn_shards.py --indexes 0,3
    uv run python .github/ci/run_live_turn_shards.py -- -x      # extra pytest args
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

_TARGET = "tests/core/agent/test_turn_scenarios.py"
_K_EXPR = "test_live_turn_execution_oracle or test_live_action_planning"
_LOG_DIR = Path(".turn-shard-logs")


@dataclass(frozen=True)
class ShardResult:
    index: int
    exit_code: int
    duration_s: float
    log_path: Path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shards",
        type=int,
        default=int(os.getenv("TURN_SHARD_TOTAL", "8")),
        help="Total number of shards to split the suite into (default: 8).",
    )
    parser.add_argument(
        "--indexes",
        type=str,
        default="",
        help="Comma-separated subset of shard indexes to run (default: all).",
    )
    parser.add_argument(
        "--workers-per-shard",
        type=str,
        default="auto",
        help="pytest-xdist worker count per shard, passed to -n (default: auto).",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=os.getenv("LLM_PROVIDER", "openai"),
        help="LLM provider for the live run (default: $LLM_PROVIDER or openai).",
    )
    parser.add_argument(
        "pytest_args",
        nargs="*",
        help="Extra args forwarded to each pytest shard (after a -- separator).",
    )
    return parser.parse_args(argv)


def _resolve_indexes(shards: int, indexes: str) -> list[int]:
    if shards < 1:
        raise SystemExit("--shards must be >= 1")
    if not indexes.strip():
        return list(range(shards))
    selected = sorted({int(part) for part in indexes.split(",") if part.strip()})
    out_of_range = [i for i in selected if i < 0 or i >= shards]
    if out_of_range:
        raise SystemExit(f"shard indexes out of range for --shards {shards}: {out_of_range}")
    return selected


def _build_command(workers: str, pytest_args: list[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "-n",
        workers,
        "-v",
        "-m",
        "live_llm",
        _TARGET,
        "-k",
        _K_EXPR,
        *pytest_args,
    ]


def _shard_env(*, shard_total: int, shard_index: int, provider: str) -> dict[str, str]:
    env = os.environ.copy()
    env["TURN_SHARD_TOTAL"] = str(shard_total)
    env["TURN_SHARD_INDEX"] = str(shard_index)
    env["LLM_PROVIDER"] = provider
    env.setdefault("OPENSRE_DISABLE_KEYRING", "1")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _run_shards(
    *, shard_total: int, indexes: list[int], workers: str, provider: str, pytest_args: list[str]
) -> list[ShardResult]:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    command = _build_command(workers, pytest_args)
    print(f"Launching {len(indexes)} shard(s) of {shard_total} (provider={provider}):")
    print(f"  {' '.join(command)}\n")

    started: dict[int, tuple[subprocess.Popen[bytes], float, Path, IO[bytes]]] = {}
    for shard_index in indexes:
        log_path = _LOG_DIR / f"shard-{shard_index}.log"
        log_handle: IO[bytes] = log_path.open("wb")
        process = subprocess.Popen(
            command,
            env=_shard_env(shard_total=shard_total, shard_index=shard_index, provider=provider),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        started[shard_index] = (process, time.monotonic(), log_path, log_handle)
        print(f"  shard {shard_index} -> pid {process.pid}, log {log_path}")

    print("\nWaiting for shards to finish...\n")
    results: list[ShardResult] = []
    pending = set(started)
    while pending:
        for shard_index in sorted(pending):
            process, start_time, log_path, log_handle = started[shard_index]
            code = process.poll()
            if code is None:
                continue
            log_handle.close()
            duration = time.monotonic() - start_time
            status = "PASS" if code == 0 else f"FAIL (exit {code})"
            print(f"  shard {shard_index} {status} in {duration:.0f}s")
            results.append(
                ShardResult(
                    index=shard_index,
                    exit_code=code,
                    duration_s=duration,
                    log_path=log_path,
                )
            )
            pending.discard(shard_index)
        if pending:
            time.sleep(2.0)
    return sorted(results, key=lambda r: r.index)


def _print_summary(results: list[ShardResult]) -> int:
    failures = [r for r in results if r.exit_code != 0]
    print("\n==================== live turn shard summary ====================")
    for result in results:
        status = "PASS" if result.exit_code == 0 else f"FAIL (exit {result.exit_code})"
        print(f"  shard {result.index}: {status}  [{result.duration_s:.0f}s]  {result.log_path}")
    if failures:
        print(f"\n{len(failures)} shard(s) failed. Inspect the logs above.")
        return 1
    print("\nAll shards passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    indexes = _resolve_indexes(args.shards, args.indexes)
    results = _run_shards(
        shard_total=args.shards,
        indexes=indexes,
        workers=args.workers_per_shard,
        provider=args.provider,
        pytest_args=list(args.pytest_args),
    )
    return _print_summary(results)


if __name__ == "__main__":
    raise SystemExit(main())
