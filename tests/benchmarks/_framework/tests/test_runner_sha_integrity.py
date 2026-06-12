"""Tests for the SHA-capture + promotable-run integrity gate.

These tests pin two related behaviors that the 2026-06-11 partial full-N
revealed were missing:

  1. ``_git_sha()`` must read the ``OPENSRE_SHA`` env var before falling
     back to ``git rev-parse``. The bench image build workflow stamps the
     real SHA into the container via this env var; without it, every
     Fargate run reports ``(no-git)`` (no .git directory in the image)
     and the promotable cycle stamps an unverifiable artifact.

  2. ``BenchmarkRunner.run()`` (the promotable entry point) must REJECT
     a run whose resolved SHA is ``(no-git)``. The pre-registration's
     ``committed_checkout_required: true`` is YAML text only; nothing in
     the previous code enforced it. A partial run with sha=(no-git) is
     not reproducible from artifacts and therefore not promotable.
"""

from __future__ import annotations

import pytest

from tests.benchmarks._framework.integrity import IntegrityViolation
from tests.benchmarks._framework.runner import _git_sha

# --------------------------------------------------------------------------- #
# _git_sha — env var fallback                                                 #
# --------------------------------------------------------------------------- #


def test_git_sha_reads_opensre_sha_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPENSRE_SHA env var must take precedence over the git command.

    This is the path the bench image build uses: ``ENV OPENSRE_SHA=<sha>``
    in the Dockerfile (or an ECS container override) makes the real SHA
    available to Fargate runs that have no .git directory."""
    monkeypatch.setenv("OPENSRE_SHA", "abc1234")
    assert _git_sha() == "abc1234"


def test_git_sha_strips_whitespace_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trailing newline (from ``$(git rev-parse HEAD)`` shell expansion in
    the build script) must be stripped so the captured SHA is clean."""
    monkeypatch.setenv("OPENSRE_SHA", "  abc1234  \n")
    assert _git_sha() == "abc1234"


def test_git_sha_empty_env_var_falls_through_to_git_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty OPENSRE_SHA must NOT short-circuit to an empty SHA — fall
    through to git rev-parse. Prevents a misconfigured build pipeline
    (e.g. ``ENV OPENSRE_SHA=``) from silently producing empty-string SHAs."""
    monkeypatch.setenv("OPENSRE_SHA", "")
    # In a checked-out source tree the git command returns a real SHA,
    # not an empty string. In a no-git environment (e.g. CI sandbox) it
    # returns "(no-git)". Either is acceptable — the contract is "does
    # NOT return empty".
    result = _git_sha()
    assert result != ""


# --------------------------------------------------------------------------- #
# Promotable-run integrity check                                              #
# --------------------------------------------------------------------------- #


def test_run_rejects_no_git_sha() -> None:
    """The promotable entry point must raise IntegrityViolation when the
    captured SHA is ``(no-git)``. Mirrors the partial-full-N failure mode
    where Fargate stamped (no-git) and the integrity gate let it through
    because no code path enforced ``committed_checkout_required: true``."""
    from unittest.mock import MagicMock

    from tests.benchmarks._framework.runner import BenchmarkRunner

    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.config = MagicMock()
    runner.adapter = MagicMock()
    runner.integrity = MagicMock()
    runner.integrity.pre_flight = MagicMock(return_value=None)
    runner._opensre_sha = "(no-git)"

    with pytest.raises(IntegrityViolation) as excinfo:
        runner.run()

    violations = excinfo.value.violations
    assert any("no-git" in v.lower() for v in violations), violations


def test_run_rejects_unknown_sha() -> None:
    """``(unknown)`` is what _git_sha returns when git exists but
    ``rev-parse HEAD`` returns empty (e.g. a fresh repo with no commits).
    Same promotability risk as (no-git); reject the same way."""
    from unittest.mock import MagicMock

    from tests.benchmarks._framework.runner import BenchmarkRunner

    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.config = MagicMock()
    runner.adapter = MagicMock()
    runner.integrity = MagicMock()
    runner.integrity.pre_flight = MagicMock(return_value=None)
    runner._opensre_sha = "(unknown)"

    with pytest.raises(IntegrityViolation):
        runner.run()


@pytest.mark.parametrize(
    "bad_sha",
    [
        "hotfix-june",  # human-friendly tag from workflow_dispatch
        "v1.0",  # semver tag
        "my-test-run",  # arbitrary string
        "main",  # branch name
        "ABC1234",  # uppercase (git uses lowercase)
        "abc123",  # too short (6 chars; minimum is 7)
        "a" * 41,  # too long (41 chars; maximum is 40)
        "g1234567",  # 'g' is not hex
        "abc 1234",  # contains an embedded space
    ],
)
def test_run_rejects_non_sha_strings(bad_sha: str) -> None:
    """Defense-in-depth: the original gate only rejected the (no-git) /
    (unknown) / empty sentinels. A user-supplied image tag like
    ``hotfix-june`` or ``v1.0`` would have stamped that string into
    provenance.json as the opensre_sha, passing the original gate but
    failing the reproducibility contract (you cannot git-checkout
    ``hotfix-june`` to reproduce the run). The hardened gate validates
    SHA shape (7-40 lowercase hex)."""
    from unittest.mock import MagicMock

    from tests.benchmarks._framework.runner import BenchmarkRunner

    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.config = MagicMock()
    runner.adapter = MagicMock()
    runner.integrity = MagicMock()
    runner.integrity.pre_flight = MagicMock(return_value=None)
    runner._opensre_sha = bad_sha

    with pytest.raises(IntegrityViolation) as excinfo:
        runner.run()

    # The error message must surface the bad value so operators see what
    # was rejected, not just "an integrity violation occurred".
    msg = "\n".join(excinfo.value.violations)
    assert bad_sha in msg, f"violation message did not include the rejected value {bad_sha!r}"


@pytest.mark.parametrize(
    "good_sha",
    [
        "abc1234",  # 7-char short SHA (git rev-parse --short HEAD default)
        "abc1234567",  # 10-char short SHA
        "abc1234abcdef5678",  # mid-length
        "a" * 40,  # full 40-char SHA
        "0123456789abcdef0123456789abcdef01234567",  # full hex SHA
    ],
)
def test_run_accepts_valid_sha_shapes(good_sha: str) -> None:
    """Mirror of the rejection test: valid SHA shapes (7-40 lowercase
    hex chars) must pass the gate. Pins both ends of the allowed range
    so the regex doesn't silently tighten."""
    from unittest.mock import MagicMock, patch

    from tests.benchmarks._framework.runner import BenchmarkRunner

    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.config = MagicMock()
    runner.adapter = MagicMock()
    runner.integrity = MagicMock()
    runner.integrity.pre_flight = MagicMock(return_value=None)
    runner._opensre_sha = good_sha

    sentinel = object()
    with patch.object(runner, "_run_inner", return_value=sentinel) as mocked:
        result = runner.run()
    assert result is sentinel
    mocked.assert_called_once_with(dev_mode=False)


def test_run_without_integrity_accepts_no_git_sha() -> None:
    """The dev-mode path (``run_without_integrity``) must NOT reject
    (no-git); it's the explicit escape hatch for exploratory local runs
    that haven't committed yet. Symmetric with the run() rejection above."""
    from unittest.mock import MagicMock, patch

    from tests.benchmarks._framework.runner import BenchmarkRunner

    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.config = MagicMock()
    runner.adapter = MagicMock()
    runner.integrity = MagicMock()
    runner._opensre_sha = "(no-git)"

    # Patch _run_inner so we don't need a full case loop; we only care that
    # the SHA check does NOT raise on the dev path.
    sentinel = object()
    with patch.object(runner, "_run_inner", return_value=sentinel) as mocked:
        result = runner.run_without_integrity()
    assert result is sentinel
    mocked.assert_called_once_with(dev_mode=True)
