"""Per-run provenance capture — write `provenance.json` next to `report.json`.

A reviewer auditing a published benchmark report has to answer three
questions:

  1. *What code ran?* — git SHA, branch, dirty-state, list of changed files
  2. *Can I reproduce it?* — full config + pre-registration content inline,
     pinned model versions, Python + key package versions
  3. *Did any secrets leak into the artifact?* — env-var whitelist, never
     dump arbitrary env

This module is the single source of truth for those three answers. It runs
once at the start of every benchmark run, before any LLM call, and writes
its output to ``output_dir/provenance.json``. The runner cannot proceed
without succeeding here.

Security discipline:
  - API-key-shaped env vars (``*_API_KEY``, ``*_TOKEN``, ``*_SECRET``,
    ``*_PASSWORD``, ``*_KEY``) are NEVER captured, even if explicitly named
    in the whitelist
  - Hostname / username are NOT captured by default (would leak who ran it)
  - Uncommitted-changes capture is filename-only — no diff content
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from collections.abc import Iterable
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from tests.benchmarks._framework.adapters import BenchmarkAdapter
from tests.benchmarks._framework.config import BenchmarkConfig
from tests.benchmarks._framework.cost import lookup_pricing
from tests.benchmarks._framework.llm_dispatch import LLMDispatcher

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

# Bumped only when the provenance schema changes in a backwards-incompatible
# way. Reviewers diffing two runs use this to know which fields they can
# compare 1:1.
PROVENANCE_SCHEMA_VERSION = 1

# Env vars whose presence + value is safe to record. Anything else is dropped.
# The values themselves are still passed through the secret-pattern filter so
# a malicious entry on the allowlist can't smuggle a key.
_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "OPENSRE_BENCH_WORKERS",
        "OPENSRE_BENCH_COST_BUDGET_USD",
        "BENCH_MIN_TOOL_CALLS",
        "PYTHONPATH",
        "LANG",
        "LC_ALL",
        "CI",
        "GITHUB_ACTIONS",
        "GITHUB_RUN_ID",
        "GITHUB_SHA",
    }
)

# Substring patterns that mark an env var as secret-bearing. Belt-and-braces:
# any var matching these is dropped even if it slipped onto the allowlist.
_SECRET_NAME_PATTERNS: tuple[str, ...] = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_KEY",
    "PASSPHRASE",
    "PRIVATE",
    "CREDENTIALS",
    "AUTH",
)

# Key packages to record versions for. Failures (PackageNotFoundError) are
# captured as None rather than raising.
_KEY_PACKAGES: tuple[str, ...] = (
    "anthropic",
    "openai",
    "boto3",
    "pydantic",
    "pyyaml",
    "huggingface-hub",
    "datasets",
)


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def capture_provenance(
    *,
    config: BenchmarkConfig,
    adapter: BenchmarkAdapter,
    run_id: str,
    started_at: str,
    config_path: Path | None = None,
    pre_registration_path: Path | None = None,
) -> dict[str, Any]:
    """Snapshot everything needed to reproduce / audit this run.

    Args:
        config: the BenchmarkConfig the runner loaded
        adapter: the BenchmarkAdapter the runner will call
        run_id: framework-generated run id (e.g. ``2026-05-18T18-30-00Z_cloudopsbench``)
        started_at: ISO-8601 UTC timestamp the runner stamped
        config_path: optional path to the YAML the config was loaded from
            (passed when running via the CLI; absent when constructed inline)
        pre_registration_path: optional path to the pre-registration YAML

    Returns:
        Plain dict, JSON-serializable, suitable for ``json.dump`` to
        ``provenance.json`` in the run directory.
    """
    provenance: dict[str, Any] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": started_at,
        "code": _git_state(),
        "config": _config_section(config, config_path),
        "pre_registration": _pre_registration_section(
            config.pre_registration_path or pre_registration_path
        ),
        "models": _models_section(config),
        "environment": _environment_section(),
        "dataset": _dataset_section(adapter),
        "run_inputs": _run_inputs_section(config),
    }
    # Strategy hook — the framework's job is to assemble the standard
    # sections; adapters extend with their own keys (e.g. CloudOpsBench
    # adds ``min_tool_calls`` to ``run_inputs``). See BenchmarkAdapter.
    return adapter.extend_provenance(provenance)


# --------------------------------------------------------------------------- #
# Code (git) section                                                          #
# --------------------------------------------------------------------------- #


def _git_state() -> dict[str, Any]:
    """Best-effort git provenance. Falls back gracefully if no git available."""
    try:
        sha_full = _run_git("rev-parse", "HEAD") or "(unknown)"
        sha_short = _run_git("rev-parse", "--short", "HEAD") or "(unknown)"
        branch = _run_git("rev-parse", "--abbrev-ref", "HEAD") or "(unknown)"
        # NB: fetch porcelain UNSTRIPPED. ``git status --porcelain`` emits
        # ``XY PATH`` where column 0 (X = index state) is a SIGNIFICANT space for
        # unstaged-only changes (e.g. " M core/orchestration/node/investigate/agent.py"). The
        # default strip in ``_run_git`` would eat that leading space on the first
        # line, shifting it left so ``line[3:]`` slices into the path and drops
        # its first character (" M app…" → "pp/agent/…"). Keep the raw spacing so
        # the fixed-width [3:] slice lands exactly on the path.
        status_porcelain = _run_git("status", "--porcelain", strip=False) or ""
        changed_files = [
            line[3:].strip()
            for line in status_porcelain.splitlines()
            if len(line) > 3 and line[3:].strip()
        ]
        return {
            "opensre_sha": sha_full,
            "opensre_short_sha": sha_short,
            "opensre_branch": branch,
            "opensre_dirty": bool(changed_files),
            "opensre_changed_files": changed_files,
        }
    except (FileNotFoundError, OSError):
        return {
            "opensre_sha": "(no-git)",
            "opensre_short_sha": "(no-git)",
            "opensre_branch": "(no-git)",
            "opensre_dirty": False,
            "opensre_changed_files": [],
        }


def _run_git(*args: str, strip: bool = True) -> str | None:
    """Run a git command; return stdout (stripped by default) or None on failure.

    Pass ``strip=False`` for commands whose leading/trailing whitespace is
    significant — notably ``status --porcelain``, where column 0 is a meaningful
    space for unstaged changes.
    """
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).parent,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() if strip else result.stdout


# --------------------------------------------------------------------------- #
# Config / pre-registration sections — inline content so runs are self-contained
# --------------------------------------------------------------------------- #


def _config_section(config: BenchmarkConfig, config_path: Path | None) -> dict[str, Any]:
    section: dict[str, Any] = {
        "path": str(config_path) if config_path else None,
        "sha256": None,
        "content": None,
    }
    if config_path is not None and config_path.exists():
        content = config_path.read_text(encoding="utf-8")
        section["sha256"] = _sha256(content)
        section["content"] = content
    return section


def _pre_registration_section(pre_reg_path: Path | None) -> dict[str, Any]:
    section: dict[str, Any] = {
        "path": str(pre_reg_path) if pre_reg_path else None,
        "sha256": None,
        "content": None,
    }
    if pre_reg_path is not None and pre_reg_path.exists():
        content = pre_reg_path.read_text(encoding="utf-8")
        section["sha256"] = _sha256(content)
        section["content"] = content
    return section


# --------------------------------------------------------------------------- #
# Models section — per-LLM spec + pricing snapshot                            #
# --------------------------------------------------------------------------- #


def _models_section(config: BenchmarkConfig) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for llm in config.llms:
        configured_version = config.model_versions.get(llm, "")
        entry: dict[str, Any] = {
            "configured_version": configured_version,
        }
        try:
            spec = LLMDispatcher.spec(llm)
            entry["provider"] = str(spec.provider)
            entry["spec_reasoning_model"] = spec.reasoning_model
            entry["spec_classification_model"] = spec.classification_model
            entry["spec_toolcall_model"] = spec.toolcall_model
        except Exception as exc:
            entry["spec_error"] = f"{type(exc).__name__}: {exc}"

        try:
            pricing = lookup_pricing(configured_version)
            entry["pricing_snapshot"] = {
                "input_usd_per_mtok": pricing.input_usd_per_mtok,
                "output_usd_per_mtok": pricing.output_usd_per_mtok,
            }
        except Exception as exc:
            entry["pricing_error"] = f"{type(exc).__name__}: {exc}"

        out[llm] = entry
    return out


# --------------------------------------------------------------------------- #
# Environment section — Python + key packages + safe env-var snapshot         #
# --------------------------------------------------------------------------- #


def _environment_section() -> dict[str, Any]:
    return {
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "key_packages": _package_versions(_KEY_PACKAGES),
        "env": _safe_env_snapshot(),
    }


def _package_versions(packages: Iterable[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in packages:
        try:
            out[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            out[name] = None
    return out


def _safe_env_snapshot() -> dict[str, str]:
    """Whitelist + secret-pattern filter. Drops anything risky."""
    out: dict[str, str] = {}
    for name in _ENV_ALLOWLIST:
        if _looks_like_secret(name):
            # Belt-and-braces — never let a secret-shaped allowlist entry through.
            continue
        value = os.environ.get(name)
        if value is None:
            continue
        out[name] = value
    return out


def _looks_like_secret(name: str) -> bool:
    upper = name.upper()
    return any(pattern in upper for pattern in _SECRET_NAME_PATTERNS)


# --------------------------------------------------------------------------- #
# Dataset section — best-effort from adapter attrs                            #
# --------------------------------------------------------------------------- #


def _dataset_section(adapter: BenchmarkAdapter) -> dict[str, Any]:
    """Adapters can expose dataset provenance via well-known attributes; we
    grab what's available and leave the rest as None.
    """
    return {
        "adapter_name": adapter.name,
        "adapter_version": adapter.version,
        "hf_dataset": getattr(adapter, "hf_dataset", None),
        "hf_revision": getattr(adapter, "hf_revision", None),
        "local_path": _stringify_optional_path(getattr(adapter, "benchmark_dir", None)),
        "data_contamination_checked": bool(getattr(adapter, "data_contamination_checked", False)),
    }


def _stringify_optional_path(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


# --------------------------------------------------------------------------- #
# Run-inputs section — echo from config for quick scanning                    #
# --------------------------------------------------------------------------- #


def _run_inputs_section(config: BenchmarkConfig) -> dict[str, Any]:
    # Adapter-agnostic. Knobs that belong to a specific adapter (e.g.
    # CloudOpsBench's ``min_tool_calls``) are injected via the adapter's
    # ``extend_provenance`` hook, NOT by the framework reaching into the
    # adapter's internals. See BenchmarkAdapter.extend_provenance.
    return {
        "modes": list(config.modes),
        "llms": list(config.llms),
        "model_versions": dict(config.model_versions),
        "runs_per_case": config.runs_per_case,
        "workers": config.workers,
        "cost_budget_usd": config.cost_budget_usd,
        "seed": config.seed,
        "filters": config.filters.model_dump(),
        "report_formats": list(config.report_formats),
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
