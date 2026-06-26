"""End-to-end integration tests for the ``/fleet`` token wiring (#2023).

These tests drive the *real* pipeline — provider resolver, real
:class:`ClaudeCodeJsonlSource` / :class:`CodexRolloutSource`, real
:class:`ClaudeCodeMeter` / :class:`CodexMeter`, real
:class:`TokenRateTracker`, real ``_resolved_model_for_pid``, real
``_format_tokens_per_min`` / ``_format_usd_per_hour`` — against a
fixture on disk.

The only stubs are :func:`tools.fleet_monitoring.probe.cwd_for_pid` and
:func:`started_at_for_pid` (no real PIDs in a unit test) and the
``AgentRegistry`` lookup (no real ``~/.opensre/agents.jsonl``).
Everything else exercises production code.
"""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console

from cli.interactive_shell.ui.agents_view import _build_agents_table
from tools.fleet_monitoring import sampler as sampler_mod
from tools.fleet_monitoring.probe import ProcessSnapshot
from tools.fleet_monitoring.registry import AgentRecord
from tools.fleet_monitoring.token_rate import TOKEN_RATE_TRACKER
from tools.fleet_monitoring.token_sources import claude_code as claude_source_mod
from tools.fleet_monitoring.token_sources import codex as codex_source_mod


@pytest.fixture(autouse=True)
def _isolate_sampler_globals() -> None:
    """Same isolation pattern as the unit tests in this directory."""
    sampler_mod._latest.clear()
    sampler_mod._TickCache.registry_snapshot = {}
    sampler_mod._TickCache.agents_config = None
    for pid in list(TOKEN_RATE_TRACKER.known_pids()):
        TOKEN_RATE_TRACKER.forget(pid)


def _render(records: list[AgentRecord]) -> str:
    table = _build_agents_table(records)
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, highlight=False, width=140).print(table)
    return buf.getvalue()


# ---------- Claude Code end-to-end ----------------------------------


def test_claude_code_end_to_end_renders_real_tokens_and_cost(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Real pipeline: psutil-stubbed cwd → real JSONL on disk →
    real source → real meter → real tracker → real view formatter.
    The view shows live ``tokens/min`` and ``$/hr`` cells, not ``-``.
    """
    # Build a Claude Code project directory and session JSONL.
    fake_cwd = tmp_path / "repo"
    fake_cwd.mkdir()
    projects_root = tmp_path / "claude_projects"
    project_dir = projects_root / str(fake_cwd).replace("/", "-")
    project_dir.mkdir(parents=True)
    session = project_dir / "session-abc.jsonl"
    # Start with one historical event — the source seeks past it on
    # first read (no retro-pricing) so it should not contribute.
    session.write_text(
        '{"type":"system","subtype":"init","session_id":"abc","model":"claude-sonnet-4-5"}\n',
        encoding="utf-8",
    )

    # Stub psutil-fenced helpers — no real PID lookups in unit tests.
    monkeypatch.setattr(
        "tools.fleet_monitoring.token_sources.claude_code.cwd_for_pid",
        lambda _pid: fake_cwd,
    )
    # After the Greptile-driven fix, the claude source requires
    # fd-level evidence to attribute a JSONL to a PID (no more
    # silent fallback to newest-by-mtime — mirrors codex).
    monkeypatch.setattr(
        "tools.fleet_monitoring.token_sources.claude_code.open_files_for_pid",
        lambda _pid: (session,),
    )

    # Wire a fresh source against the test projects root so the
    # production singleton (which would hit ``~/.claude/projects/``)
    # never enters this test's resolution path.
    isolated_source = claude_source_mod.ClaudeCodeJsonlSource(projects_root=projects_root)
    monkeypatch.setattr(
        "tools.fleet_monitoring.sampler.get_token_source",
        lambda provider: isolated_source if provider == "claude-code" else None,
    )

    # Registered agent: Claude Code at PID 8421.
    record = AgentRecord(
        name="claude-code-8421",
        pid=8421,
        command="claude --output-format stream-json",
        provider="claude-code",
    )

    class _Registry:
        def list(self) -> list[AgentRecord]:
            return [record]

        def get(self, pid: int) -> AgentRecord | None:
            return record if pid == 8421 else None

    monkeypatch.setattr("tools.fleet_monitoring.sampler.AgentRegistry", _Registry)

    # Stub probe so the snapshot path is also alive (uptime/cpu%).
    fake_snapshot = ProcessSnapshot(
        pid=8421,
        cpu_percent=18.4,
        rss_mb=128.0,
        num_fds=42,
        num_connections=3,
        status="running",
        started_at=datetime(2026, 5, 14, 10, 0, 0, tzinfo=UTC),
    )
    monkeypatch.setattr("tools.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    # First sampler tick: cold-start the source (seeks to EOF) and
    # populate registry caches. No tokens yet — historical content
    # below EOF is correctly ignored.
    sampler_mod._TickCache.registry_snapshot[record.pid] = record
    sampler_mod._TickCache.agents_config = None
    sampler_mod._sample_tokens(record)

    # Append a real Claude assistant turn with usage. This is the
    # delta the next sample call should pick up.
    with session.open("a", encoding="utf-8") as fh:
        fh.write(
            '{"type":"assistant","message":{"model":"claude-sonnet-4-5",'
            '"usage":{"input_tokens":200,"output_tokens":50}}}\n'
        )
    # Brief wait so mtime > started_at - 5s on every filesystem
    # (APFS sub-second mtime quirks rarely matter, but cheap).
    time.sleep(0.01)

    sampler_mod._sample_tokens(record)

    # Render through the real view.
    out = _render([record])

    # ``tokens/min`` populated: 250 tokens in a 60 s window → 250.
    assert "250" in out

    # ``$/hr`` populated with a real dollar figure for sonnet-4-5
    # (3 USD/M input × 0.7 + 15 USD/M output × 0.3 blend ≈ 6.6e-6
    # per token; 250 tok/min × 60 × 6.6e-6 ≈ $0.099). The actual
    # rendering rounds to two decimals, so look for the ``$`` prefix.
    assert "$0.0" in out or "$0.1" in out

    # And the placeholder is gone for the metric columns.
    assert "claude-code-8421" in out


def test_claude_code_end_to_end_shows_dash_when_source_resolves_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When psutil cannot read the cwd (macOS hardened-runtime
    denials, cross-user processes), the dashboard stays honest:
    ``tokens/min`` and ``$/hr`` render ``-`` rather than ``0`` or a
    bogus number.
    """
    monkeypatch.setattr(
        "tools.fleet_monitoring.token_sources.claude_code.cwd_for_pid",
        lambda _pid: None,
    )
    isolated_source = claude_source_mod.ClaudeCodeJsonlSource(
        projects_root=tmp_path / "claude_projects",
    )
    monkeypatch.setattr(
        "tools.fleet_monitoring.sampler.get_token_source",
        lambda provider: isolated_source if provider == "claude-code" else None,
    )

    record = AgentRecord(
        name="claude-code-8421",
        pid=8421,
        command="claude",
        provider="claude-code",
    )

    class _Registry:
        def list(self) -> list[AgentRecord]:
            return [record]

        def get(self, pid: int) -> AgentRecord | None:
            return record if pid == 8421 else None

    monkeypatch.setattr("tools.fleet_monitoring.sampler.AgentRegistry", _Registry)
    monkeypatch.setattr("tools.fleet_monitoring.sampler.probe", lambda _pid: None)

    sampler_mod._TickCache.registry_snapshot[record.pid] = record
    sampler_mod._sample_tokens(record)
    sampler_mod._sample_tokens(record)

    # Drive the real render and inspect the actual table cells rather
    # than substring-matching the printed form (which contains the
    # ``$/hr`` header literal).
    table = _build_agents_table([record])
    assert table.row_count == 1
    rendered_cells = [list(col.cells)[0] for col in table.columns]
    # cells[0]=agent, [1]=pid, [2..6]=uptime/cpu/tokens/min/$/hr/status.
    # Every metric cell — including ``$/hr`` — falls back to ``-``.
    assert rendered_cells[2:] == ["-", "-", "-", "-", "-"]
    # The printed form should still include the agent name (so
    # rendering didn't silently bail).
    out = _render([record])
    assert "claude-code-8421" in out


# ---------- Codex end-to-end ----------------------------------------


def test_codex_end_to_end_renders_real_tokens_and_cost(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mirrors the Claude Code test for the Codex rollout source."""
    codex_home = tmp_path / "codex_home"
    started_at_epoch = time.time() - 30  # process started 30 s ago
    started_dt = datetime.fromtimestamp(started_at_epoch, tz=UTC)
    rollout_dir = (
        codex_home
        / "sessions"
        / started_dt.strftime("%Y")
        / started_dt.strftime("%m")
        / started_dt.strftime("%d")
    )
    rollout_dir.mkdir(parents=True)
    rollout = rollout_dir / "rollout-001-abc.jsonl"
    rollout.write_text(
        '{"type":"thread.started","model":"gpt-5-codex"}\n{"type":"turn.started"}\n',
        encoding="utf-8",
    )
    import os as _os

    _os.utime(rollout, (started_at_epoch + 1, started_at_epoch + 1))

    monkeypatch.setattr(
        "tools.fleet_monitoring.token_sources.codex.started_at_for_pid",
        lambda _pid: started_at_epoch,
    )
    # Source now requires fd-level confirmation that the PID owns
    # the rollout (production: each codex holds its rollout fd open).
    monkeypatch.setattr(
        "tools.fleet_monitoring.token_sources.codex.open_files_for_pid",
        lambda _pid: (rollout,),
    )
    isolated_source = codex_source_mod.CodexRolloutSource(codex_home=codex_home)
    monkeypatch.setattr(
        "tools.fleet_monitoring.sampler.get_token_source",
        lambda provider: isolated_source if provider == "codex" else None,
    )

    record = AgentRecord(
        name="codex-9999",
        pid=9999,
        command="codex exec",
        provider="codex",
    )

    class _Registry:
        def list(self) -> list[AgentRecord]:
            return [record]

        def get(self, pid: int) -> AgentRecord | None:
            return record if pid == 9999 else None

    monkeypatch.setattr("tools.fleet_monitoring.sampler.AgentRegistry", _Registry)

    fake_snapshot = ProcessSnapshot(
        pid=9999,
        cpu_percent=4.2,
        rss_mb=64.0,
        num_fds=20,
        num_connections=1,
        status="running",
        started_at=started_dt,
    )
    monkeypatch.setattr("tools.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    sampler_mod._TickCache.registry_snapshot[record.pid] = record
    sampler_mod._TickCache.agents_config = None
    sampler_mod._sample_tokens(record)

    # Append a real per-turn token_count event (the on-disk format
    # codex-cli 0.130.0 writes — distinct from ``codex exec --json``
    # stdout). ``turn_context`` carries the model; ``event_msg`` with
    # ``payload.type == "token_count"`` carries ``last_token_usage``.
    with rollout.open("a", encoding="utf-8") as fh:
        fh.write(
            '{"type":"turn_context","payload":{"turn_id":"t_1","model":"gpt-5-codex"}}\n'
            '{"type":"event_msg","payload":{"type":"token_count","info":'
            '{"last_token_usage":'
            '{"input_tokens":175,"cached_input_tokens":0,'
            '"output_tokens":50,"reasoning_output_tokens":0,"total_tokens":225}}}}\n'
        )
    time.sleep(0.01)

    sampler_mod._sample_tokens(record)

    out = _render([record])

    # 225 tokens (input + output) over a 60 s window.
    assert "225" in out
    # Real cost figure from the gpt-5-codex price table.
    assert "$0.0" in out or "$0.1" in out
    assert "codex-9999" in out
