"""Branch ownership and cross-agent coordination for local AI agent fleet.

Provides a lightweight ownership table to prevent two agents from racing
on the same branch. Persists claims to a JSONL file in the OpenSRE config
directory.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

_DEFAULT_CLAIMS_PATH = OPENSRE_HOME_DIR / "branch_claims.jsonl"


@dataclass(frozen=True)
class BranchClaim:
    """Immutable record of a branch claim by an agent."""

    branch: str
    agent_name: str
    pid: int
    claimed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BranchClaim:
        raw_pid = data["pid"]
        pid = int(str(raw_pid))
        return cls(
            branch=str(data["branch"]),
            agent_name=str(data["agent_name"]),
            pid=pid,
            claimed_at=str(data.get("claimed_at", datetime.now(UTC).isoformat())),
        )


class BranchClaims:
    """JSONL-backed registry of branch ownership claims by local AI agents.

    Persists to ``~/.config/opensre/branch_claims.jsonl`` by default.
    The file is fully rewritten on both claim (to avoid duplicates on re-claim)
    and release operations.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_CLAIMS_PATH
        self._claims: dict[str, BranchClaim] = {}
        self._load_from_disk()

    def claim(self, branch: str, agent_name: str, pid: int) -> BranchClaim | None:
        """Record that an agent has claimed a branch.

        Returns the new BranchClaim if successful, or None if the branch
        is already claimed by a different agent.
        """
        if branch in self._claims:
            existing = self._claims[branch]
            if existing.agent_name != agent_name or existing.pid != pid:
                return None  # Conflict: branch already held by someone else
            # Same agent re-claiming the same branch - allow it (update timestamp)
            return self._do_claim(branch, agent_name, pid, overwrite=True)
        return self._do_claim(branch, agent_name, pid, overwrite=False)

    def _do_claim(
        self, branch: str, agent_name: str, pid: int, *, overwrite: bool = False
    ) -> BranchClaim:
        """Internal method to perform the actual claim recording."""
        claim = BranchClaim(branch=branch, agent_name=agent_name, pid=pid)
        self._claims[branch] = claim
        if overwrite:
            self._rewrite()
        else:
            self._append(claim)
        return claim

    def release(self, branch: str) -> BranchClaim | None:
        """Release a branch claim. Returns the removed claim or None if not found."""
        removed = self._claims.pop(branch, None)
        if removed is not None:
            self._rewrite()
        return removed

    def get(self, branch: str) -> BranchClaim | None:
        """Get the claim for a branch, if any."""
        return self._claims.get(branch)

    def list(self) -> list[BranchClaim]:
        """List all branch claims."""
        return list(self._claims.values())

    def is_held(self, branch: str) -> bool:
        """Check if a branch is currently claimed."""
        return branch in self._claims

    def holder(self, branch: str) -> str | None:
        """Get the agent name holding a branch, or None if unclaimed."""
        claim = self._claims.get(branch)
        return claim.agent_name if claim else None

    def holder_pid(self, branch: str) -> int | None:
        """Get the PID of the agent holding a branch, or None if unclaimed."""
        claim = self._claims.get(branch)
        return claim.pid if claim else None

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            logger.warning("Failed to read branch claims from %s", self._path)
            return
        for line in lines:
            try:
                data = json.loads(line)
                claim = BranchClaim.from_dict(data)
                self._claims[claim.branch] = claim
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.warning("Skipping corrupt branch claims line: %s", line[:80])

    def _append(self, claim: BranchClaim) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(claim.to_dict()) + "\n")
        except OSError:
            logger.warning("Failed to append to branch claims at %s", self._path)

    def _rewrite(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for claim in self._claims.values():
                    fh.write(json.dumps(claim.to_dict()) + "\n")
            tmp.replace(self._path)
        except OSError:
            logger.warning("Failed to rewrite branch claims at %s", self._path)


__all__ = ["BranchClaim", "BranchClaims"]
