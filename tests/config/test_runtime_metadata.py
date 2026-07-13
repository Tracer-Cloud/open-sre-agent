"""Tests for session runtime metadata injection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from config.runtime_metadata import (
    RUNTIME_INPUTS_KEY,
    _GitLayout,
    _read_git_head_sha,
    _read_latest_release_tag,
    _resolve_gitdir,
    build_runtime_metadata,
    merge_runtime_into_inputs,
)
from config.version import get_opensre_version
from core.agent_harness.prompts.assistant_agent_prompt import build_environment_block
from core.agent_harness.session import InMemorySessionStorage, SessionCore, SessionManager
from tools.system.python_execution_tool import execute_python_code


@pytest.fixture(autouse=True)
def _no_real_integration_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SessionCore, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(SessionCore, "hydrate_configured_integrations", lambda _self: None)


def test_build_runtime_metadata_uses_importlib_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_ENV", "staging")
    meta = build_runtime_metadata()
    assert meta["opensre_version"] == get_opensre_version()
    assert meta["runtime_env"] == "staging"
    # opensre_build is populated in git checkouts (dev), empty in installed wheels.
    # Just assert the key exists and is a string — the value varies by env.
    assert isinstance(meta["opensre_build"], str)


def test_build_runtime_metadata_populates_build_marker_in_git_checkout() -> None:
    """In a git checkout (this test tree), opensre_build should include a SHA
    or release tag so the LLM can quote a precise build identifier without
    shelling out. The exact string varies with head, but must be non-empty."""
    meta = build_runtime_metadata()
    # This test runs from the opensre checkout, so .git exists → build marker
    # is populated. If someone ever runs the test suite from an installed
    # wheel, this test would need adjusting.
    assert meta["opensre_build"], "opensre_build should be populated in a git checkout"
    assert meta["opensre_build"].startswith("dev"), meta["opensre_build"]


def test_merge_runtime_into_inputs_does_not_overwrite_caller_key() -> None:
    custom = {"opensre_version": "custom"}
    merged = merge_runtime_into_inputs({"x": 1, RUNTIME_INPUTS_KEY: custom})
    assert merged["x"] == 1
    assert merged[RUNTIME_INPUTS_KEY] == custom


def test_session_bootstrap_populates_runtime_metadata() -> None:
    manager = SessionManager(
        storage=InMemorySessionStorage(),
        repo=SimpleNamespace(load_session=lambda _sid: None),
    )
    session = manager.create(hydrate_integrations=False, persistent_tasks=False, open_storage=False)
    assert session.runtime_metadata["opensre_version"] == get_opensre_version()
    assert "runtime_env" in session.runtime_metadata
    assert "opensre_build" in session.runtime_metadata


def test_session_clear_repopulates_runtime_metadata() -> None:
    session = SessionCore()
    session.refresh_runtime_metadata()
    session.runtime_metadata = {}
    session.clear(rotate_identity=False)
    assert session.runtime_metadata["opensre_version"] == get_opensre_version()


def test_environment_block_includes_version_without_subprocess_hint() -> None:
    block = build_environment_block(
        integrations=(),
        known=False,
        opensre_version="9.9.9",
        runtime_env="development",
    )
    assert "OpenSRE version is 9.9.9" in block
    assert "runtime environment is development" in block
    assert "opensre --version" in block
    assert "subprocess" in block.lower()


def test_environment_block_renders_build_marker_when_provided() -> None:
    """In a git checkout the runtime metadata carries an opensre_build marker;
    the env block should render it inline with the version so the LLM can
    quote both parts."""
    block = build_environment_block(
        integrations=(),
        known=False,
        opensre_version="0.1",
        opensre_build="dev, v0.1.2026.7.11 @ abc1234",
        runtime_env="development",
    )
    assert "OpenSRE version is 0.1 (dev, v0.1.2026.7.11 @ abc1234)" in block


def test_environment_block_omits_build_parens_when_marker_empty() -> None:
    """Released wheels report opensre_build=''; version renders bare."""
    block = build_environment_block(
        integrations=(),
        known=False,
        opensre_version="0.1.2026.7.11",
        opensre_build="",
        runtime_env="production",
    )
    assert "OpenSRE version is 0.1.2026.7.11" in block
    assert "()" not in block


def test_environment_block_instructs_verbatim_quoting_not_field_names() -> None:
    """Regression guard: an earlier version of the prompt said 'including the
    build marker if present', which caused the LLM to treat 'build marker' as
    a field name and hallucinate a value like '0' when the slot was empty. The
    prompt now instructs verbatim quoting and explicitly forbids inventing field
    names or numbers not in the block."""
    block = build_environment_block(
        integrations=(),
        known=False,
        opensre_version="0.1",
        opensre_build="dev, v0.1.2026.7.11 @ abc1234",
        runtime_env="development",
    )
    assert "verbatim" in block
    assert "Do NOT invent field names" in block
    assert "build marker" not in block, "the 'build marker' phrase was a hallucination sink"


def test_resolve_gitdir_follows_linked_worktree_pointer_file(tmp_path: Path) -> None:
    """Linked worktrees (and submodules) store ``.git`` as a *file* that points
    at the real gitdir under the primary repo. Build metadata must resolve
    through it instead of returning ``None``."""
    real_gitdir = tmp_path / "primary" / ".git" / "worktrees" / "wt1"
    real_gitdir.mkdir(parents=True)
    pointer = tmp_path / "wt" / ".git"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(f"gitdir: {real_gitdir}\n", encoding="utf-8")
    assert _resolve_gitdir(pointer) == real_gitdir


def test_resolve_gitdir_returns_none_for_pointer_to_missing_dir(tmp_path: Path) -> None:
    pointer = tmp_path / ".git"
    pointer.write_text("gitdir: /does/not/exist\n", encoding="utf-8")
    assert _resolve_gitdir(pointer) is None


def test_latest_release_tag_reads_packed_refs_when_loose_missing(tmp_path: Path) -> None:
    """After ``git pack-refs`` there is no ``refs/tags/<name>`` file — the tag
    lives only in ``packed-refs``. Build metadata must fall back so packed
    repos still surface a build marker."""
    (tmp_path / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted \n"
        "abc1234abc1234abc1234abc1234abc1234abcd refs/tags/v0.1.2026.7.11\n"
        "def5678def5678def5678def5678def5678def56 refs/heads/main\n",
        encoding="utf-8",
    )
    assert _read_latest_release_tag(tmp_path) == "v0.1.2026.7.11"


def test_head_sha_reads_packed_refs_when_loose_ref_missing(tmp_path: Path) -> None:
    """A packed branch has no loose ``refs/heads/<name>`` file; the sha is in
    ``packed-refs``. Falling through instead of following packed-refs would
    drop the SHA from the build marker."""
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "packed-refs").write_text(
        "abc1234abc1234abc1234abc1234abc1234abcd refs/heads/main\n",
        encoding="utf-8",
    )
    layout = _GitLayout(gitdir=tmp_path, commondir=tmp_path)
    assert _read_git_head_sha(layout) == "abc1234"


def test_head_sha_resolves_branch_from_commondir_in_linked_worktree(tmp_path: Path) -> None:
    """In a linked worktree ``HEAD`` sits in the per-worktree gitdir but the
    branch ref lives in the shared commondir. Reading only the per-worktree
    gitdir would miss the sha and drop it from the build marker."""
    commondir = tmp_path / "primary" / ".git"
    (commondir / "refs" / "heads").mkdir(parents=True)
    (commondir / "refs" / "heads" / "main").write_text(
        "abc1234abc1234abc1234abc1234abc1234abcd\n", encoding="utf-8"
    )
    per_worktree = commondir / "worktrees" / "wt1"
    per_worktree.mkdir(parents=True)
    (per_worktree / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    layout = _GitLayout(gitdir=per_worktree, commondir=commondir)
    assert _read_git_head_sha(layout) == "abc1234"


def test_latest_release_tag_reads_from_commondir_in_linked_worktree(tmp_path: Path) -> None:
    """Tags are a shared ref: only the commondir's ``refs/tags/`` sees them.
    A worktree-local read would return ``None`` and drop the tag from the
    build marker."""
    commondir = tmp_path / "primary" / ".git"
    tags_dir = commondir / "refs" / "tags"
    tags_dir.mkdir(parents=True)
    (tags_dir / "v0.1.2026.7.11").write_text("sha\n", encoding="utf-8")

    assert _read_latest_release_tag(commondir) == "v0.1.2026.7.11"


def test_latest_release_tag_sorts_numerically_not_lexicographically(tmp_path: Path) -> None:
    """``v0.1.YYYY.M.D`` uses non-padded month/day, so a lexicographic sort
    would pick ``v0.1.2026.9.30`` over the later ``v0.1.2026.10.1`` (because
    ``'9' > '1'`` as ASCII). Regression guard: numeric tuple sort."""
    tags_dir = tmp_path / "refs" / "tags"
    tags_dir.mkdir(parents=True)
    for name in ("v0.1.2026.9.30", "v0.1.2026.10.1", "v0.1.2026.7.11"):
        (tags_dir / name).write_text("sha\n", encoding="utf-8")
    assert _read_latest_release_tag(tmp_path) == "v0.1.2026.10.1"


def test_python_tool_reports_version_via_injected_runtime_inputs() -> None:
    result = execute_python_code.run(
        code="print(inputs['opensre_runtime']['opensre_version'])",
    )
    assert result["success"] is True
    assert get_opensre_version() in result["stdout"]
    assert RUNTIME_INPUTS_KEY in result["inputs"]


def test_python_tool_reports_version_via_importlib_metadata() -> None:
    result = execute_python_code.run(
        code=("import importlib.metadata as m\nprint(m.version('opensre'))\n"),
    )
    assert result["success"] is True
    assert get_opensre_version() in result["stdout"]


def test_python_tool_still_blocks_subprocess_version_check() -> None:
    result = execute_python_code.run(
        code="import subprocess; subprocess.run(['opensre', '--version'])",
    )
    assert result["success"] is False
    assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]
