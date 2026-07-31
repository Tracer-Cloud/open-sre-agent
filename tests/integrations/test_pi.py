"""Tests for the Pi coding integration (config, verifier, client)."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.pi import (
    is_pi_coding_enabled,
    pi_coding_model,
    pi_coding_timeout_seconds,
    pi_coding_workspace,
    run_pi_coding_task,
    verify_pi_coding,
)

_RESOLVE = "integrations.pi.client._resolve_pi_binary"
# git calls all funnel through integrations.git.local._run_git (subprocess.run);
# the pi process itself goes through agent_exec's Popen (polled to a deadline).
_RUN = "integrations.git.local.subprocess.run"
_POPEN = "integrations.llm_cli.agent_exec.subprocess.Popen"


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_is_pi_coding_enabled_truthy_values() -> None:
    assert is_pi_coding_enabled({"PI_CODING_ENABLED": "1"}) is True
    assert is_pi_coding_enabled({"PI_CODING_ENABLED": "true"}) is True
    assert is_pi_coding_enabled({"PI_CODING_ENABLED": "YES"}) is True
    assert is_pi_coding_enabled({"PI_CODING_ENABLED": "0"}) is False
    assert is_pi_coding_enabled({}) is False


def test_pi_coding_model_and_workspace() -> None:
    assert pi_coding_model({"PI_CODING_MODEL": "  groq/llama-3.1-8b-instant  "}) == (
        "groq/llama-3.1-8b-instant"
    )
    assert pi_coding_model({}) is None
    assert pi_coding_workspace({"PI_CODING_WORKSPACE": "/repo"}) == "/repo"


def test_pi_coding_timeout_clamped() -> None:
    with patch.dict("os.environ", {"PI_CODING_TIMEOUT_SECONDS": "5"}, clear=False):
        assert pi_coding_timeout_seconds() == 60.0  # clamped to minimum
    with patch.dict("os.environ", {"PI_CODING_TIMEOUT_SECONDS": "99999"}, clear=False):
        assert pi_coding_timeout_seconds() == 1800.0  # clamped to maximum


# --------------------------------------------------------------------------- #
# verifier
# --------------------------------------------------------------------------- #
@patch("integrations.pi.verifier.PiAdapter")
def test_verify_pi_coding_installed_and_authed(mock_cls: MagicMock) -> None:
    mock_cls.return_value.detect.return_value = MagicMock(
        installed=True, logged_in=True, detail="ok"
    )
    available, detail = verify_pi_coding()
    assert available is True
    assert detail == "ok"


@patch("integrations.pi.verifier.PiAdapter")
def test_verify_pi_coding_not_installed(mock_cls: MagicMock) -> None:
    mock_cls.return_value.detect.return_value = MagicMock(
        installed=False, logged_in=None, detail="not found"
    )
    available, _ = verify_pi_coding()
    assert available is False


@patch("integrations.pi.verifier.PiAdapter")
def test_verify_pi_coding_not_authed(mock_cls: MagicMock) -> None:
    mock_cls.return_value.detect.return_value = MagicMock(
        installed=True, logged_in=False, detail="not logged in"
    )
    available, _ = verify_pi_coding()
    assert available is False


# --------------------------------------------------------------------------- #
# client — git goes through integrations.git.local (subprocess.run); the pi
# process goes through agent_exec's Popen, so the two are mocked separately.
# --------------------------------------------------------------------------- #
class _FakePopen:
    """Minimal Popen stand-in: drainable stdout/stderr pipes + poll/wait."""

    def __init__(
        self, *, stdout: str = "", stderr: str = "", returncode: int = 0, hang: bool = False
    ) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self._rc, self._hang = returncode, hang
        # No real OS process behind the fake: pid=None steers _signal_process_group
        # away from os.killpg and onto the terminate()/kill() fallback.
        self.pid: int | None = None

    def poll(self) -> int | None:
        return None if self._hang else self._rc

    def terminate(self) -> None:
        self._hang = False

    def kill(self) -> None:
        self._hang = False

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self._rc

    @property
    def returncode(self) -> int | None:
        return None if self._hang else self._rc


def _git_run_side_effect(diff: str = "diff --git a/foo.py b/foo.py\n+changed\n") -> object:
    def side_effect(cmd: list[str], **_: object) -> MagicMock:
        sub = cmd[1]  # only git calls reach subprocess.run now
        if sub == "rev-parse":
            return MagicMock(returncode=0, stdout="true\n", stderr="")
        if sub == "status":
            if "-z" in cmd:
                return MagicMock(returncode=0, stdout=" M foo.py\0?? bar.py\0", stderr="")
            return MagicMock(returncode=0, stdout=" M foo.py\n?? bar.py\n", stderr="")
        if sub == "diff":
            return MagicMock(returncode=0, stdout=diff, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return side_effect


@patch(_POPEN)
@patch(_RUN)
@patch(_RESOLVE, return_value="/usr/bin/pi")
def test_run_pi_coding_task_success_captures_diff(
    _mock_resolve: MagicMock, mock_run: MagicMock, mock_popen: MagicMock, tmp_path: Path
) -> None:
    mock_run.side_effect = _git_run_side_effect()
    mock_popen.return_value = _FakePopen(stdout="Edited foo.py to fix the bug.\n", returncode=0)
    result = run_pi_coding_task(
        "fix the bug",
        workspace=str(tmp_path),
        model="anthropic/claude-haiku-4-5",
        timeout_sec=60,
    )
    assert result.success is True
    assert "foo.py" in result.changed_files
    assert "bar.py" in result.changed_files
    assert "diff --git" in result.diff
    assert "Edited foo.py" in result.summary
    assert result.error is None
    # the pi process carried the model flag and ran in the workspace
    argv = mock_popen.call_args.args[0]
    assert argv[0] == "/usr/bin/pi"
    assert "--model" in argv
    assert mock_popen.call_args.kwargs["cwd"] == str(tmp_path)


@patch(_RESOLVE, return_value=None)
def test_run_pi_coding_task_binary_missing(_mock_resolve: MagicMock, tmp_path: Path) -> None:
    result = run_pi_coding_task("x", workspace=str(tmp_path), model=None, timeout_sec=60)
    assert result.success is False
    assert "Pi CLI not found" in (result.error or "")


@patch(_POPEN)
@patch(_RUN)
@patch(_RESOLVE, return_value="/usr/bin/pi")
def test_run_pi_coding_task_timeout(
    _mock_resolve: MagicMock, mock_run: MagicMock, mock_popen: MagicMock, tmp_path: Path
) -> None:
    mock_run.side_effect = _git_run_side_effect()
    mock_popen.return_value = _FakePopen(hang=True)  # never finishes
    result = run_pi_coding_task("x", workspace=str(tmp_path), model=None, timeout_sec=0)
    assert result.success is False
    assert result.timed_out is True
    assert "timed out" in (result.error or "")


@patch(_POPEN)
@patch(_RUN)
@patch(_RESOLVE, return_value="/usr/bin/pi")
def test_run_pi_coding_task_nonzero_exit(
    _mock_resolve: MagicMock, mock_run: MagicMock, mock_popen: MagicMock, tmp_path: Path
) -> None:
    mock_run.side_effect = _git_run_side_effect()
    mock_popen.return_value = _FakePopen(stderr="model not found: bogus", returncode=1)
    result = run_pi_coding_task("x", workspace=str(tmp_path), model="bogus", timeout_sec=60)
    assert result.success is False
    assert "model not found" in (result.error or "")


@patch(_RESOLVE, return_value="/usr/bin/pi")
def test_run_pi_coding_task_non_git_workspace_fails(
    _mock_resolve: MagicMock, tmp_path: Path
) -> None:
    # Real directory, but not a git repo: must fail fast (before editing) rather
    # than return a misleading success with an empty diff.
    result = run_pi_coding_task("x", workspace=str(tmp_path), model=None, timeout_sec=60)
    assert result.success is False
    assert "not a git repository" in (result.error or "")


# --------------------------------------------------------------------------- #
# live (opt-in): real pi edits a temp git repo. Self-skips without pi/config.
# --------------------------------------------------------------------------- #
def _git_init_repo(repo: Path) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
        env=env,
    )


@pytest.mark.integration
@pytest.mark.live_llm
def test_live_pi_coding_edits_temp_repo(tmp_path: Path) -> None:
    binary = shutil.which("pi") or os.environ.get("PI_BIN", "").strip()
    if not binary:
        pytest.skip("pi binary not installed; skipping live Pi coding test")
    if not is_pi_coding_enabled():
        pytest.skip("PI_CODING_ENABLED not set; skipping live Pi coding test")
    model = pi_coding_model()
    if not model:
        pytest.skip("PI_CODING_MODEL not set; skipping live Pi coding test")

    _git_init_repo(tmp_path)
    result = run_pi_coding_task(
        "Append a new line containing exactly the word pong to hello.txt. Change nothing else.",
        workspace=str(tmp_path),
        model=model,
        timeout_sec=300,
    )

    # If Pi made no change the run is inconclusive for asserting edit mechanics
    # (most often a provider rate limit/quota, or an underpowered model that
    # declined the task) — skip rather than hard-fail the integration.
    if not result.changed_files:
        detail = result.error or result.summary[:160]
        pytest.skip(f"Pi made no changes; cannot assert edit mechanics: {detail!r}")

    assert result.success, result.error
    assert "hello.txt" in result.changed_files
    assert "pong" in (tmp_path / "hello.txt").read_text(encoding="utf-8").lower()
    # The tool must not commit: HEAD is still the single init commit.
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert len([ln for ln in log.stdout.splitlines() if ln.strip()]) == 1
