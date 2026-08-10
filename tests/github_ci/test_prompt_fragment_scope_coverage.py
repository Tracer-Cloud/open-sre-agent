"""Guards that every vendor prompt fragment reaches the prompt snapshot in CI.

The committed snapshot in ``tests/core/agent/prompts/`` renders the action,
gather and assistant prompts byte-for-byte. Every vendor fragment registered by
``integrations/harness_adapters.py`` is part of that render, so editing one
without regenerating the snapshot goes red in the full suite — but ``make
test-scope`` only runs the snapshot test if a rule routes the changed file to
it. Four fragment files went unrouted for months and only surfaced when someone
happened to run the full suite.

This derives the file list from the *registration site* rather than a filename
glob, so a fragment added under a new name is caught rather than silently
inheriting the convention.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTERS = _REPO_ROOT / "integrations" / "harness_adapters.py"
_SNAPSHOT_TEST = "tests/core/agent/prompts/test_prompt_characterization.py"
_REGISTRARS = frozenset(
    {
        "register_action_prompt_fragment",
        "register_assistant_prompt_fragment",
        "register_gather_prompt_fragment",
    }
)

sys.path.insert(0, str(_REPO_ROOT / ".github" / "ci"))
from test_scope_rules import classify  # type: ignore[import-not-found]  # noqa: E402


def _registered_fragment_sources() -> list[str]:
    """Repo-relative paths of every module supplying a registered prompt fragment."""
    tree = ast.parse(_ADAPTERS.read_text(encoding="utf-8"))

    registered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in _REGISTRARS or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            registered.add(first.id)

    sources: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if not any(alias.name in registered for alias in node.names):
            continue
        sources.add(node.module.replace(".", "/") + ".py")
    return sorted(sources)


def test_every_registered_prompt_fragment_routes_to_the_snapshot_test() -> None:
    sources = _registered_fragment_sources()
    # A parse that finds nothing would make this test vacuously green.
    assert len(sources) >= 10, f"fragment discovery looks broken: {sources}"

    unrouted = []
    for source in sources:
        assert (_REPO_ROOT / source).exists(), f"{source} does not exist"
        escalates, targets, _areas = classify([source])
        if not escalates and _SNAPSHOT_TEST not in targets:
            unrouted.append(source)

    assert not unrouted, (
        "these prompt-fragment sources do not route to the prompt snapshot test, "
        "so editing one passes `make test-scope` and fails the full suite: "
        f"{unrouted}"
    )


@pytest.mark.parametrize(
    "path",
    [
        "integrations/harness_adapters.py",
        "tools/harness_adapters.py",
    ],
)
def test_the_registration_sites_themselves_route_to_the_snapshot_test(path: str) -> None:
    """Reordering registrations reorders the rendered prompt, changing the snapshot."""
    escalates, targets, _areas = classify([path])
    assert escalates or _SNAPSHOT_TEST in targets
