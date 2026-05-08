"""Tests for branch ownership and cross-agent coordination."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

import app.cli.interactive_shell.command_registry.agents as agents_shell
from app.agents.coordination import BranchClaim, BranchClaims
from app.agents.registry import AgentRecord, AgentRegistry


@pytest.fixture
def claims(tmp_path: Path) -> BranchClaims:
    return BranchClaims(path=tmp_path / "branch_claims.jsonl")


@pytest.fixture
def session() -> object:
    return SimpleNamespace(mark_latest=lambda ok, kind: None)  # noqa: ARG005


@pytest.fixture
def console() -> Console:
    # ``color_system=None`` keeps output free of ANSI segments that split
    # visible text (e.g. "/agents") when CI sets ``FORCE_COLOR`` / TTY-like env.
    return Console(file=io.StringIO(), color_system=None)


@pytest.fixture
def sample_claim() -> BranchClaim:
    return BranchClaim(
        branch="auth-refactor",
        agent_name="aider",
        pid=7702,
        claimed_at="2026-05-07T12:00:00+00:00",
    )


class TestBranchClaim:
    def test_frozen(self, sample_claim: BranchClaim) -> None:
        with pytest.raises(AttributeError):
            sample_claim.branch = "main"  # type: ignore[misc]

    def test_round_trip_dict(self, sample_claim: BranchClaim) -> None:
        restored = BranchClaim.from_dict(sample_claim.to_dict())
        assert restored == sample_claim

    def test_from_dict_missing_claimed_at(self) -> None:
        claim = BranchClaim.from_dict({"branch": "main", "agent_name": "aider", "pid": 1234})
        assert claim.branch == "main"
        assert claim.agent_name == "aider"
        assert claim.pid == 1234
        assert claim.claimed_at  # auto-populated

    def test_from_dict_coerces_types(self) -> None:
        claim = BranchClaim.from_dict({"branch": "main", "agent_name": "codex", "pid": "9999"})
        assert claim.pid == 9999
        assert isinstance(claim.pid, int)


class TestBranchClaims:
    def test_claim_and_get(self, claims: BranchClaims, sample_claim: BranchClaim) -> None:
        result = claims.claim(sample_claim.branch, sample_claim.agent_name, sample_claim.pid)
        assert result is not None
        assert result.branch == sample_claim.branch
        assert result.agent_name == sample_claim.agent_name
        assert result.pid == sample_claim.pid

        retrieved = claims.get(sample_claim.branch)
        assert retrieved == result

    def test_claim_returns_none_on_conflict(self, claims: BranchClaims) -> None:
        claims.claim("main", "aider", 7702)
        result = claims.claim("main", "claude-code", 8421)
        assert result is None

    def test_claim_same_agent_same_branch_updates(self, claims: BranchClaims) -> None:
        first = claims.claim("main", "aider", 7702)
        assert first is not None
        second = claims.claim("main", "aider", 7702)
        assert second is not None
        assert claims.get("main") == second

    def test_release_removes_claim(self, claims: BranchClaims, sample_claim: BranchClaim) -> None:
        claims.claim(sample_claim.branch, sample_claim.agent_name, sample_claim.pid)
        removed = claims.release(sample_claim.branch)
        assert removed is not None
        assert removed.branch == sample_claim.branch
        assert claims.get(sample_claim.branch) is None

    def test_release_nonexistent_returns_none(self, claims: BranchClaims) -> None:
        assert claims.release("nonexistent") is None

    def test_is_held(self, claims: BranchClaims) -> None:
        assert not claims.is_held("main")
        claims.claim("main", "aider", 7702)
        assert claims.is_held("main")

    def test_holder_returns_agent_name(self, claims: BranchClaims) -> None:
        claims.claim("main", "aider", 7702)
        assert claims.holder("main") == "aider"
        assert claims.holder("nonexistent") is None

    def test_holder_pid_returns_pid(self, claims: BranchClaims) -> None:
        claims.claim("main", "aider", 7702)
        assert claims.holder_pid("main") == 7702
        assert claims.holder_pid("nonexistent") is None

    def test_list_returns_all_claims(self, claims: BranchClaims) -> None:
        claims.claim("main", "aider", 7702)
        claims.claim("dev", "claude-code", 8421)
        listed = claims.list()
        assert len(listed) == 2
        branches = {c.branch for c in listed}
        assert branches == {"main", "dev"}

    def test_persistence_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "branch_claims.jsonl"
        reg1 = BranchClaims(path=path)
        reg1.claim("main", "aider", 7702)
        reg1.claim("dev", "claude-code", 8421)

        reg2 = BranchClaims(path=path)
        assert len(reg2.list()) == 2
        assert reg2.holder("main") == "aider"
        assert reg2.holder("dev") == "claude-code"

    def test_release_updates_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "branch_claims.jsonl"
        reg1 = BranchClaims(path=path)
        reg1.claim("main", "aider", 7702)
        reg1.claim("dev", "claude-code", 8421)
        reg1.release("main")

        reg2 = BranchClaims(path=path)
        assert len(reg2.list()) == 1
        assert reg2.holder("main") is None
        assert reg2.holder("dev") == "claude-code"

    def test_jsonl_file_format(self, tmp_path: Path) -> None:
        path = tmp_path / "branch_claims.jsonl"
        reg = BranchClaims(path=path)
        reg.claim("main", "aider", 7702)

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["branch"] == "main"
        assert parsed["agent_name"] == "aider"
        assert parsed["pid"] == 7702

    def test_corrupt_line_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "branch_claims.jsonl"
        path.write_text(
            '{"branch":"main","agent_name":"aider","pid":7702,"claimed_at":"2026-05-07T12:00:00+00:00"}\n'
            "this is not json\n"
            '{"branch":"dev","agent_name":"claude-code","pid":8421,"claimed_at":"2026-05-07T12:00:00+00:00"}\n',
            encoding="utf-8",
        )
        reg = BranchClaims(path=path)
        assert len(reg.list()) == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "branch_claims.jsonl"
        path.write_text("", encoding="utf-8")
        reg = BranchClaims(path=path)
        assert reg.list() == []

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        path = tmp_path / "does_not_exist.jsonl"
        reg = BranchClaims(path=path)
        assert reg.list() == []

    def test_claim_same_branch_different_pid_is_conflict(self, claims: BranchClaims) -> None:
        """Same agent name but different PID should be treated as a conflict."""
        claims.claim("main", "aider", 7702)
        result = claims.claim("main", "aider", 9999)  # Same name, different PID
        assert result is None

    def test_claim_different_agent_same_pid_is_conflict(self, claims: BranchClaims) -> None:
        """Different agent name but same PID - this is unlikely but still a conflict."""
        claims.claim("main", "aider", 7702)
        result = claims.claim("main", "claude-code", 7702)  # Different name, same PID
        assert result is None

    def test_reclaim_does_not_duplicate_jsonl_entry(self, tmp_path: Path) -> None:
        """Re-claiming the same branch should not add duplicate lines to JSONL file."""
        path = tmp_path / "branch_claims.jsonl"
        reg = BranchClaims(path=path)

        # First claim
        reg.claim("main", "aider", 7702)
        lines1 = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines1) == 1

        # Re-claim same branch
        reg.claim("main", "aider", 7702)
        lines2 = path.read_text(encoding="utf-8").strip().splitlines()
        # Should still be 1 line (rewrite replaced the file, not appended)
        assert len(lines2) == 1, f"Expected 1 line, got {len(lines2)}: {lines2}"

        # Verify content is still valid
        reg2 = BranchClaims(path=path)
        assert reg2.holder("main") == "aider"

    def test_reclaim_same_agent_succeeds(self, claims: BranchClaims) -> None:
        """Same agent re-claiming the same branch should succeed."""
        first = claims.claim("main", "aider", 7702)
        assert first is not None

        # Re-claim should succeed (refreshes timestamp via _rewrite)
        second = claims.claim("main", "aider", 7702)
        assert second is not None
        assert second.branch == "main"
        assert second.agent_name == "aider"
        assert second.pid == 7702


class TestCliCommands:
    """Tests for /agents claim and /agents release CLI commands."""

    def test_claim_usage_error_missing_args(self, session: object, console: Console) -> None:
        """Missing arguments should print usage error."""
        result = agents_shell._cmd_agents_claim(session, console, [])
        assert result is False
        output = console.file.getvalue()
        assert "Usage:" in output
        assert "/agents claim" in output

    def test_claim_usage_error_one_arg(self, session: object, console: Console) -> None:
        """Only one argument should print usage error."""
        result = agents_shell._cmd_agents_claim(session, console, ["main"])
        assert result is False
        output = console.file.getvalue()
        assert "Usage:" in output

    def test_claim_agent_not_found(self, session: object, console: Console, tmp_path: Path) -> None:
        """Claiming with non-existent agent should print error."""
        reg_path = tmp_path / "agents.jsonl"
        AgentRegistry(path=reg_path)  # Empty registry

        original_registry = agents_shell.AgentRegistry
        agents_shell.AgentRegistry = lambda *args, **kwargs: AgentRegistry(  # noqa: ARG005
            path=reg_path
        )

        try:
            result = agents_shell._cmd_agents_claim(session, console, ["main", "unknown-agent"])
            assert result is False
            output = console.file.getvalue()
            assert "not found in registry" in output
        finally:
            agents_shell.AgentRegistry = original_registry

    def test_claim_success(self, session: object, console: Console, tmp_path: Path) -> None:
        """Successful claim should print success message."""
        reg_path = tmp_path / "agents.jsonl"
        reg = AgentRegistry(path=reg_path)
        reg.register(AgentRecord(name="aider", pid=7702, command="aider"))

        claims_path = tmp_path / "branch_claims.jsonl"

        original_registry = agents_shell.AgentRegistry
        original_claims = agents_shell.BranchClaims
        agents_shell.AgentRegistry = lambda *args, **kwargs: AgentRegistry(  # noqa: ARG005
            path=reg_path
        )
        agents_shell.BranchClaims = lambda *args, **kwargs: BranchClaims(  # noqa: ARG005
            path=claims_path
        )

        try:
            result = agents_shell._cmd_agents_claim(session, console, ["main", "aider"])
            assert result is True
            output = console.file.getvalue()
            assert "Branch main now held by aider" in output
            assert "pid 7702" in output
        finally:
            agents_shell.AgentRegistry = original_registry
            agents_shell.BranchClaims = original_claims

    def test_claim_conflict(self, session: object, console: Console, tmp_path: Path) -> None:
        """Claiming a branch held by another agent should print conflict error."""
        reg_path = tmp_path / "agents.jsonl"
        reg = AgentRegistry(path=reg_path)
        reg.register(AgentRecord(name="aider", pid=7702, command="aider"))
        reg.register(AgentRecord(name="claude-code", pid=8421, command="claude"))

        claims_path = tmp_path / "branch_claims.jsonl"
        claims = BranchClaims(path=claims_path)
        claims.claim("main", "aider", 7702)

        original_registry = agents_shell.AgentRegistry
        original_claims = agents_shell.BranchClaims
        agents_shell.AgentRegistry = lambda *args, **kwargs: AgentRegistry(  # noqa: ARG005
            path=reg_path
        )
        agents_shell.BranchClaims = lambda *args, **kwargs: BranchClaims(  # noqa: ARG005
            path=claims_path
        )

        try:
            result = agents_shell._cmd_agents_claim(session, console, ["main", "claude-code"])
            assert result is False
            output = console.file.getvalue()
            assert "Cannot claim" in output
            assert "already held by aider" in output
        finally:
            agents_shell.AgentRegistry = original_registry
            agents_shell.BranchClaims = original_claims

    def test_release_usage_error(self, session: object, console: Console) -> None:
        """Missing branch argument should print usage error."""
        result = agents_shell._cmd_agents_release(session, console, [])
        assert result is False
        output = console.file.getvalue()
        assert "Usage:" in output
        assert "/agents release" in output

    def test_release_not_held(self, session: object, console: Console, tmp_path: Path) -> None:
        """Releasing unclaimed branch should print error."""
        claims_path = tmp_path / "branch_claims.jsonl"

        original_claims = agents_shell.BranchClaims
        agents_shell.BranchClaims = lambda *args, **kwargs: BranchClaims(  # noqa: ARG005
            path=claims_path
        )

        try:
            result = agents_shell._cmd_agents_release(session, console, ["nonexistent"])
            assert result is False
            output = console.file.getvalue()
            assert "is not currently held" in output
        finally:
            agents_shell.BranchClaims = original_claims

    def test_release_success(self, session: object, console: Console, tmp_path: Path) -> None:
        """Successful release should print success message."""
        claims_path = tmp_path / "branch_claims.jsonl"
        claims = BranchClaims(path=claims_path)
        claims.claim("main", "aider", 7702)

        original_claims = agents_shell.BranchClaims
        agents_shell.BranchClaims = lambda *args, **kwargs: BranchClaims(  # noqa: ARG005
            path=claims_path
        )

        try:
            result = agents_shell._cmd_agents_release(session, console, ["main"])
            assert result is True
            output = console.file.getvalue()
            assert "Released main" in output
            assert "aider" in output
        finally:
            agents_shell.BranchClaims = original_claims

    def test_claim_reclaim_same_agent(
        self, session: object, console: Console, tmp_path: Path
    ) -> None:
        """Re-claiming by the same agent should succeed (refreshes timestamp)."""
        reg_path = tmp_path / "agents.jsonl"
        reg = AgentRegistry(path=reg_path)
        reg.register(AgentRecord(name="aider", pid=7702, command="aider"))

        claims_path = tmp_path / "branch_claims.jsonl"
        claims = BranchClaims(path=claims_path)
        claims.claim("main", "aider", 7702)

        original_registry = agents_shell.AgentRegistry
        original_claims = agents_shell.BranchClaims
        agents_shell.AgentRegistry = lambda *args, **kwargs: AgentRegistry(  # noqa: ARG005
            path=reg_path
        )
        agents_shell.BranchClaims = lambda *args, **kwargs: BranchClaims(  # noqa: ARG005
            path=claims_path
        )

        try:
            result = agents_shell._cmd_agents_claim(session, console, ["main", "aider"])
            assert result is True
            output = console.file.getvalue()
            assert "Branch main now held by aider" in output
        finally:
            agents_shell.AgentRegistry = original_registry
            agents_shell.BranchClaims = original_claims
