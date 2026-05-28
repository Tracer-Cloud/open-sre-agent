from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.cli.tests.catalog import TestCatalogItem
from app.cli.tests.discover import REPO_ROOT, load_test_catalog


def format_command(item: TestCatalogItem) -> str:
    return item.command_display


def find_test_item(item_id: str) -> TestCatalogItem | None:
    return load_test_catalog().find(item_id)


def get_preflight_messages(item: TestCatalogItem) -> tuple[str, ...]:
    """Return user-facing preflight messages for a catalog item."""
    if "openclaw" not in item.tags:
        return ()

    try:
        from app.integrations.openclaw import build_openclaw_config, validate_openclaw_config
        from app.integrations.verify import resolve_effective_integrations
    except Exception:
        return ()

    effective_integrations = resolve_effective_integrations()
    integration = effective_integrations.get("openclaw")
    if not isinstance(integration, dict):
        return (
            "OpenClaw preflight: no local OpenClaw integration is configured. "
            "This test will not use live OpenClaw context.",
            "Run `uv run opensre integrations setup openclaw` first if you want live OpenClaw data.",
        )

    config_payload = integration.get("config")
    if not isinstance(config_payload, dict):
        return (
            "OpenClaw preflight: local OpenClaw config is unreadable. "
            "This test will not use live OpenClaw context.",
        )

    try:
        config = build_openclaw_config(config_payload)
    except Exception as err:
        return (
            "OpenClaw preflight: local OpenClaw config is invalid. "
            "This test will not use live OpenClaw context.",
            f"Reason: {err}",
        )

    result = validate_openclaw_config(config)
    if result.ok:
        endpoint = config.command if config.mode == "stdio" else config.url
        return (
            "OpenClaw preflight: live OpenClaw context is ready for this test.",
            f"Bridge: {config.mode} ({endpoint})",
        )

    first_line = next(
        (line.strip() for line in result.detail.splitlines() if line.strip()), result.detail
    )
    return (
        "OpenClaw preflight: live OpenClaw context is unavailable, so OpenClaw actions may be skipped.",
        f"Reason: {first_line}",
        "Fix: run `uv run opensre integrations verify openclaw` and make it pass before rerunning this test.",
    )


def run_catalog_item(
    item: TestCatalogItem,
    *,
    dry_run: bool = False,
    working_directory: Path | None = None,
) -> int:
    if not item.command:
        raise ValueError(f"Test item '{item.id}' does not define a runnable command")

    if dry_run:
        print(format_command(item))
        return 0

    for message in get_preflight_messages(item):
        print(message, file=sys.stderr)

    result = subprocess.run(
        list(item.command),
        cwd=working_directory or REPO_ROOT,
        check=False,
    )
    return int(result.returncode)


def run_catalog_items(
    items: list[TestCatalogItem],
    *,
    dry_run: bool = False,
    working_directory: Path | None = None,
) -> int:
    """Run multiple catalog items sequentially. Returns worst (max) exit code.

    Non-runnable items are skipped so callers can safely pass mixed selections.
    """
    worst = 0
    for item in items:
        if not item.is_runnable:
            print(f"Skipping '{item.id}' — no runnable command defined.", file=sys.stderr)
            continue
        code = run_catalog_item(item, dry_run=dry_run, working_directory=working_directory)
        worst = max(worst, code)
    return worst
