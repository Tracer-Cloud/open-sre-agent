"""Tests for session runtime metadata injection."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import runtime_metadata as runtime_metadata_module
from config.runtime_metadata import (
    RUNTIME_INPUTS_KEY,
    _GitLayout,
    _local_tz_name,
    _read_git_head_sha,
    _read_latest_release_tag,
    _resolve_gitdir,
    build_runtime_metadata,
    capture_runtime_facts,
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
    # tz_name is OS-dependent but always present.
    assert isinstance(meta["tz_name"], str) and meta["tz_name"]


def test_build_runtime_metadata_populates_process_and_python_facts() -> None:
    """Session-init facts asked in #3950: python version, PID, PPID, tools
    manifest, kubeconfig — all pure-Python, none via subprocess."""
    import sys as _sys

    meta = build_runtime_metadata()
    assert meta["python_version"] == (
        f"{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}"
    )
    assert meta["pid"] == os.getpid()
    assert meta["ppid"] == os.getppid()
    assert isinstance(meta["tools"], dict)
    # Python itself must be on PATH (we're running under it right now).
    assert meta["tools"]["python"] or meta["tools"]["python3"], meta["tools"]
    assert isinstance(meta["kubeconfig"], str)


def test_build_runtime_metadata_reflects_kubeconfig_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``KUBECONFIG`` env var wins over the default under ``~/.kube/config``."""
    override = tmp_path / "mycluster.yaml"
    override.write_text("apiVersion: v1\n", encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", str(override))
    assert build_runtime_metadata()["kubeconfig"] == str(override)


def test_build_runtime_metadata_kubeconfig_takes_first_of_colon_separated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``KUBECONFIG`` may hold multiple paths joined by ``os.pathsep``; the
    first is the merged base and is what we report."""
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", f"{first}{os.pathsep}{second}")
    assert build_runtime_metadata()["kubeconfig"] == str(first)


def test_build_runtime_metadata_does_not_include_live_now_iso() -> None:
    """``now_iso`` must NOT live on the session-cached metadata: caching it at
    bootstrap would make the LLM report a stale clock every turn."""
    meta = build_runtime_metadata()
    assert "now_iso" not in meta


def test_capture_runtime_facts_adds_fresh_now_iso() -> None:
    meta = build_runtime_metadata()
    facts = capture_runtime_facts(metadata=meta)
    assert facts["opensre_version"] == meta["opensre_version"]
    assert facts["tz_name"] == meta["tz_name"]
    assert facts["now_iso"], "now_iso should always be populated"
    # ISO 8601 with offset (e.g. 2026-07-11T14:30:12+02:00 or ...Z-form).
    assert "T" in facts["now_iso"]


def test_local_tz_name_reads_iana_from_localtime_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The IANA name (``Europe/Berlin``) is much clearer to the LLM than the
    OS short code (``CEST``/``BST``), which is ambiguous across regions.
    Reading ``/etc/localtime``'s symlink target is the standard way to get it."""
    zonefile = tmp_path / "usr" / "share" / "zoneinfo" / "Europe" / "Berlin"
    zonefile.parent.mkdir(parents=True)
    zonefile.write_bytes(b"")
    fake_link = tmp_path / "localtime"
    os.symlink(zonefile, fake_link)
    monkeypatch.setattr(runtime_metadata_module, "_LOCALTIME_LINK", fake_link)
    assert _local_tz_name() == "Europe/Berlin"


def test_capture_runtime_facts_populates_uptime_seconds() -> None:
    """Uptime is a live fact — it must be present on the captured facts, not
    on the session-cached metadata (which would freeze it at bootstrap)."""
    meta = build_runtime_metadata()
    assert "uptime_seconds" not in meta
    facts = capture_runtime_facts(metadata=meta)
    assert isinstance(facts["uptime_seconds"], float)
    assert facts["uptime_seconds"] >= 0.0


def test_capture_runtime_facts_uptime_grows_over_time() -> None:
    """Uptime is monotonic — a later capture must be >= an earlier one, and
    grow by roughly the elapsed sleep. Regression guard against accidentally
    caching a snapshot in metadata."""
    import time as _t

    first = capture_runtime_facts()["uptime_seconds"]
    _t.sleep(0.05)
    second = capture_runtime_facts()["uptime_seconds"]
    assert second > first
    assert second - first >= 0.04


def test_capture_runtime_facts_refreshes_now_between_calls() -> None:
    """Live time slot must actually be live — two calls one second apart
    should differ. Regression guard against accidentally caching now_iso on
    the session metadata."""
    import time as _t

    first = capture_runtime_facts()["now_iso"]
    _t.sleep(1.05)
    second = capture_runtime_facts()["now_iso"]
    assert first != second


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


def test_environment_block_renders_current_time_and_timezone() -> None:
    """Time slot must land in the prompt as a quotable string with an anti-
    guessing instruction — the same shape that stopped the version being
    hallucinated from training data."""
    block = build_environment_block(
        integrations=(),
        known=False,
        opensre_version="0.1",
        now_iso="2026-07-11T14:30:12+02:00",
        tz_name="Europe/Berlin",
    )
    assert "current time is 2026-07-11T14:30:12+02:00" in block
    assert "local timezone is Europe/Berlin" in block
    assert "do NOT guess a date/time" in block.replace("Do NOT", "do NOT")


def test_environment_block_renders_python_process_and_tools_facts() -> None:
    """All the #3950 facts must land in the block as verbatim-quotable strings,
    each with a corresponding "do not shell out" instruction that names the
    reflex command the LLM would otherwise reach for."""
    block = build_environment_block(
        integrations=(),
        known=False,
        opensre_version="0.1",
        python_version="3.12.4",
        pid=12345,
        ppid=6789,
        uptime_seconds=42.5,
        installed_tools={"kubectl": "/usr/local/bin/kubectl", "helm": "", "git": "/usr/bin/git"},
        kubeconfig="/home/me/.kube/config",
    )
    assert "Python interpreter version is 3.12.4" in block
    assert "process id is 12345, parent 6789" in block
    assert "process uptime is 42.5 seconds" in block
    assert "installed tools on PATH are git, kubectl" in block, block
    assert "helm" not in block  # not-present tools are filtered
    assert "kubeconfig path is /home/me/.kube/config" in block
    # Anti-guess instruction names the actual shell commands the LLM would reach
    # for, in backticked form so a stray substring can't satisfy the check.
    assert "`python --version`" in block
    assert "`kubectl version`" in block
    assert "`which`" in block
    assert "`ps`" in block


def test_environment_block_omits_installed_tools_line_when_none_present() -> None:
    """When every probed tool is absent, the block must not render an
    empty ``installed tools on PATH are `` line."""
    block = build_environment_block(
        integrations=(),
        known=False,
        opensre_version="0.1",
        installed_tools={"kubectl": "", "helm": "", "git": ""},
    )
    assert "installed tools on PATH" not in block


def test_environment_block_omits_time_when_slot_empty() -> None:
    """Released wheels or pathological callers may pass no time; the block
    must not render an empty ``current time is`` line in that case."""
    block = build_environment_block(
        integrations=(),
        known=False,
        opensre_version="0.1",
        now_iso="",
        tz_name="",
    )
    assert "current time is" not in block
    assert "local timezone is" not in block


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


def test_python_tool_reports_current_time_via_injected_runtime_inputs() -> None:
    """Sandbox path should surface a fresh ``now_iso`` (not a bootstrap
    snapshot) so scripts asking for the current time never see a stale value."""
    result = execute_python_code.run(
        code="print(inputs['opensre_runtime']['now_iso'])",
    )
    assert result["success"] is True
    stdout = result["stdout"].strip()
    assert stdout, "now_iso should be non-empty"
    assert "T" in stdout, f"expected ISO 8601 datetime, got {stdout!r}"


def test_python_tool_reports_process_and_python_facts_via_injected_runtime_inputs() -> None:
    """The #3950 replacement path: a script asking for python version, PID,
    parent PID, uptime, kubeconfig, or the installed tools list should read
    them from ``inputs['opensre_runtime']`` — no ``subprocess`` needed."""
    result = execute_python_code.run(
        code=(
            "import json\n"
            "runtime = inputs['opensre_runtime']\n"
            "print(json.dumps({\n"
            "    'py': runtime['python_version'],\n"
            "    'pid': runtime['pid'],\n"
            "    'ppid': runtime['ppid'],\n"
            "    'uptime': runtime['uptime_seconds'],\n"
            "    'kubeconfig': runtime['kubeconfig'],\n"
            "    'tools': sorted(k for k, v in runtime['tools'].items() if v),\n"
            "}))\n"
        ),
    )
    assert result["success"] is True, result
    import json as _json

    payload = _json.loads(result["stdout"].strip())
    assert payload["pid"] == os.getpid()
    assert payload["py"].count(".") == 2
    assert isinstance(payload["uptime"], (int, float))
    assert payload["uptime"] >= 0.0


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
