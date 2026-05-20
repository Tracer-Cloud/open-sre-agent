"""On-disk JSONL source for Codex CLI rollouts.

Codex writes ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl``
(default ``$CODEX_HOME = ~/.codex/``). Rollouts are partitioned only
by date, so multiple codex processes sharing a day share a
directory; we disambiguate by filtering candidates with mtime
``>= started_at - 5 s`` and picking the newest. Best-effort when
two codex processes start within seconds of each other.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.agents.probe import open_files_for_pid, started_at_for_pid
from app.agents.token_sources import IncrementalJsonlSource, _PerPidState, safe_mtime

logger = logging.getLogger(__name__)

_MTIME_SLACK_SECONDS = 5.0


def _default_codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".codex"


class CodexRolloutSource(IncrementalJsonlSource):
    def __init__(self, codex_home: Path | None = None) -> None:
        super().__init__()
        # Lazy capture so tests can set CODEX_HOME after construction.
        self._codex_home: Path | None = codex_home

    def _resolve(self, pid: int) -> _PerPidState | None:
        started_at = started_at_for_pid(pid)
        if started_at is None:
            logger.debug("codex source: create_time unavailable for pid %d", pid)
            return None

        # Codex partitions rollouts by *local* time, not UTC. Verified
        # empirically: a rollout created at 2026-02-03T00:54 CET lives
        # in ``sessions/2026/02/03/`` even though the UTC date is Feb 2.
        started_dt = datetime.fromtimestamp(started_at)
        candidates: list[Path] = []
        for date_dir in self._candidate_date_dirs(started_dt):
            try:
                entries = list(date_dir.iterdir())
            except OSError:
                continue
            for path in entries:
                if not path.name.startswith("rollout-") or path.suffix != ".jsonl":
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                # A rollout older than the process by more than the
                # mtime slack belongs to a previous session.
                if mtime + _MTIME_SLACK_SECONDS < started_at:
                    continue
                candidates.append(path)

        if not candidates:
            return None
        rollout = _rollout_for_pid(pid, candidates)
        if rollout is None:
            # No fd-level evidence that this PID owns any candidate
            # rollout. Falling back to the global newest would
            # silently misattribute another codex session's tokens —
            # better to render ``-`` honestly.
            return None
        return self._initial_state_for(rollout)

    def _candidate_date_dirs(self, started_dt: datetime) -> list[Path]:
        # Cover yesterday, today, and tomorrow (all local time) so a
        # rollout file created just before or after a clock-skew or
        # day-roll boundary still resolves.
        sessions_root = self._sessions_root()
        result: list[Path] = []
        for day_offset in (-1, 0, 1):
            dt = started_dt + timedelta(days=day_offset)
            result.append(sessions_root / dt.strftime("%Y") / dt.strftime("%m") / dt.strftime("%d"))
        return result

    def _sessions_root(self) -> Path:
        home = self._codex_home if self._codex_home is not None else _default_codex_home()
        return home / "sessions"


def _rollout_for_pid(pid: int, candidates: list[Path]) -> Path | None:
    """Return the rollout this PID has open, or ``None`` if undetermined.

    Codex partitions rollouts by date only, so several PIDs alive on
    the same day would collapse to the same ``newest-by-mtime``
    rollout. ``open_files_for_pid`` disambiguates: each live codex
    holds an fd on its own rollout. When no PID-specific match
    exists (helper process, codex transient between writes), the
    source returns ``None`` and the dashboard renders ``-`` rather
    than misattributing another session's tokens.
    """
    open_paths = {
        path
        for path in open_files_for_pid(pid)
        if path.suffix == ".jsonl" and path.name.startswith("rollout-")
    }
    matching = [path for path in candidates if path in open_paths]
    if matching:
        return max(matching, key=safe_mtime)
    return None


__all__ = ["CodexRolloutSource"]
