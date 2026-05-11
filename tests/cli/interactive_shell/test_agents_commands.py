"""Unit tests for /agents slash command and conflict renderer."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console
from rich.table import Table

from app.agents import config as config_mod
from app.agents.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    FileWriteConflict,
    render_conflicts,
)
from app.agents.registry import AgentRecord, AgentRegistry
from app.agents.tail import AttachUnsupported, TailBuffer
from app.cli.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.command_registry import agents as agents_mod
from app.cli.interactive_shell.command_registry.agents import _slice_to_utf8_boundary
from app.cli.interactive_shell.runtime.session import ReplSession


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


def _isolate_registry(monkeypatch: pytest.MonkeyPatch, path: Path) -> AgentRegistry:
    """Point the slash command's ``AgentRegistry()`` constructor at
    ``path`` so tests don't read the developer's real
    ``~/.config/opensre/agents.jsonl``. Returns the registry instance
    that the test can populate.
    """
    registry = AgentRegistry(path=path)

    monkeypatch.setattr(agents_mod, "AgentRegistry", lambda: AgentRegistry(path=path))
    monkeypatch.setattr(
        agents_mod,
        "registered_and_discovered_agents",
        lambda _registry=None: AgentRegistry(path=path).list(),
    )
    return registry


@pytest.fixture(autouse=True)
def isolated_agents_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Autouse: redirect ``agents_config_path()`` to a per-test tmp path so
    ``/agents`` (which now reads ``agents.yaml`` for the ``$/hr`` cell)
    and ``/agents budget`` never touch the developer's real
    ``~/.config/opensre/agents.yaml``.
    """
    target = tmp_path / "agents.yaml"
    monkeypatch.setattr(config_mod, "agents_config_path", lambda: target)
    return target


class TestAgentsRegistration:
    def test_agents_command_is_registered(self) -> None:
        assert "/agents" in SLASH_COMMANDS

    def test_agents_first_arg_completions_include_conflicts(self) -> None:
        cmd = SLASH_COMMANDS["/agents"]
        keywords = [pair[0] for pair in cmd.first_arg_completions]
        assert "conflicts" in keywords

    def test_agents_first_arg_completions_include_trace(self) -> None:
        cmd = SLASH_COMMANDS["/agents"]
        keywords = [pair[0] for pair in cmd.first_arg_completions]
        assert "trace" in keywords

    def test_default_window_constant_is_ten_seconds(self) -> None:
        assert DEFAULT_WINDOW_SECONDS == 10.0


class TestAgentsDispatch:
    def test_conflicts_with_empty_event_source_renders_empty_state(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents conflicts", session, console) is True
        assert "no conflicts detected" in buf.getvalue()

    def test_no_subcommand_with_empty_registry_renders_empty_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        session = ReplSession()
        console, buf = _capture()

        assert dispatch_slash("/agents", session, console) is True

        out = buf.getvalue()
        # Caption from agents_view.render_agents_table:
        assert "no agents discovered or registered" in out
        # Header row still rendered with the dashboard column structure:
        assert "agent" in out
        assert "pid" in out

    def test_no_subcommand_renders_registered_agents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))
        registry.register(AgentRecord(name="cursor-tab", pid=9133, command="cursor"))

        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents", session, console) is True

        out = buf.getvalue()
        assert "claude-code" in out
        assert "8421" in out
        assert "cursor-tab" in out
        assert "9133" in out

    def test_no_subcommand_renders_discovered_agents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.cli.interactive_shell.command_registry import agents as agents_mod

        monkeypatch.setattr(
            agents_mod,
            "registered_and_discovered_agents",
            lambda _registry=None: [
                AgentRecord(
                    name="cursor-claude-code",
                    pid=80435,
                    command="claude --output-format stream-json",
                    source="discovered",
                )
            ],
        )

        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents", session, console) is True

        out = buf.getvalue()
        assert "cursor-claude-code" in out
        assert "80435" in out
        assert "discovered" in out

    def test_unknown_subcommand_prints_error(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents bogus", session, console) is True
        out = buf.getvalue()
        assert "unknown subcommand" in out.lower()
        assert "bogus" in out

    def test_unknown_subcommand_message_lists_trace(self) -> None:
        # When the user types a bogus subcommand the help string should
        # advertise every supported one, including ``bus`` and ``trace``.
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents bogus", session, console) is True
        out = buf.getvalue().lower()
        assert "bus" in out
        assert "trace" in out

    def test_dollar_hr_cell_reads_from_agents_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))

        # Pre-seed the budget via the slash command itself so we exercise
        # the full write→read round-trip (set → list).
        session = ReplSession()
        write_console, _ = _capture()
        assert dispatch_slash("/agents budget claude-code 5", session, write_console) is True

        list_console, list_buf = _capture()
        assert dispatch_slash("/agents", session, list_console) is True
        assert "$5.00" in list_buf.getvalue()

    def test_bare_agents_does_not_crash_on_schema_invalid_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_agents_yaml: Path
    ) -> None:
        # Hand-edited agents.yaml with a typo'd field used to crash bare
        # /agents with a raw ValidationError traceback. The dashboard
        # must degrade gracefully (render with $/hr = '-') so the user
        # can still see their fleet while /agents budget surfaces the
        # actual error message.
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))
        isolated_agents_yaml.parent.mkdir(parents=True, exist_ok=True)
        isolated_agents_yaml.write_text(
            "agents:\n  claude-code:\n    hourly_budegt_usd: 5.0\n",
            encoding="utf-8",
        )

        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents", session, console) is True
        out = buf.getvalue()
        # Dashboard still renders the agent row.
        assert "claude-code" in out
        assert "8421" in out


class TestAgentsBudget:
    def test_no_args_empty_state_when_no_config(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget", session, console) is True
        assert "no per-agent budgets" in buf.getvalue().lower()

    def test_writes_and_round_trips_through_load(self, isolated_agents_yaml: Path) -> None:
        session = ReplSession()
        write_console, write_buf = _capture()
        assert dispatch_slash("/agents budget claude-code 5", session, write_console) is True

        # Confirmation message references the agent and amount.
        write_out = write_buf.getvalue()
        assert "claude-code" in write_out
        assert "$5.00" in write_out

        # Subsequent /agents budget lists the just-written entry.
        read_console, read_buf = _capture()
        assert dispatch_slash("/agents budget", session, read_console) is True
        read_out = read_buf.getvalue()
        assert "claude-code" in read_out
        assert "$5.00" in read_out

        # File on disk has the expected shape.
        assert isolated_agents_yaml.exists()

    def test_rejects_negative_budget(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code -3", session, console) is True
        out = buf.getvalue()
        assert "invalid budget" in out.lower()
        # Latest slash invocation should be marked failed.
        assert session.history[-1]["ok"] is False

    def test_rejects_zero_budget(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code 0", session, console) is True
        assert "invalid budget" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False

    def test_rejects_non_numeric_budget(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code five", session, console) is True
        assert "invalid budget" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False

    def test_rejects_nan_budget(self, isolated_agents_yaml: Path) -> None:
        # ``float("nan") <= 0`` is ``False``, so without ``math.isfinite``
        # ``nan`` would slip past the guard, hit set_agent_budget, and
        # poison agents.yaml so the next load raises ValidationError.
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code nan", session, console) is True
        assert "invalid budget" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False
        # The file must not exist — a single non-finite write can't be
        # allowed to leave agents.yaml in an unreadable state.
        assert not isolated_agents_yaml.exists()

    def test_rejects_inf_budget(self, isolated_agents_yaml: Path) -> None:
        # ``float("inf") <= 0`` is ``False`` and ``gt=0`` alone accepts
        # ``inf`` (``inf > 0`` is ``True``); only ``isfinite`` blocks it.
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code inf", session, console) is True
        assert "invalid budget" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False
        assert not isolated_agents_yaml.exists()

    def test_single_arg_prints_usage(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code", session, console) is True
        assert "usage" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False

    def test_first_arg_completions_include_budget(self) -> None:
        cmd = SLASH_COMMANDS["/agents"]
        keywords = [pair[0] for pair in cmd.first_arg_completions]
        assert "budget" in keywords

    def test_corrupt_config_surfaces_friendly_error(self, isolated_agents_yaml: Path) -> None:
        # Hand-edit an agents.yaml with a typo'd field. The loader
        # raises ValidationError; the slash handler catches it and
        # renders a "agents.yaml has invalid contents" message rather
        # than crashing the REPL.
        isolated_agents_yaml.parent.mkdir(parents=True, exist_ok=True)
        isolated_agents_yaml.write_text(
            "agents:\n  claude-code:\n    hourly_budegt_usd: 5.0\n",
            encoding="utf-8",
        )
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget", session, console) is True
        out = buf.getvalue()
        assert "invalid contents" in out.lower()
        assert session.history[-1]["ok"] is False


class TestRenderConflicts:
    def test_empty_list_returns_empty_state_string(self) -> None:
        assert render_conflicts([]) == "no conflicts detected"

    def test_non_empty_list_returns_table_with_paths_and_agents(self) -> None:
        conflicts = [
            FileWriteConflict(
                path="/repo/auth.py",
                agents=("claude-code:1", "cursor:2"),
                first_seen=100.0,
                last_seen=110.0,
            ),
        ]
        result = render_conflicts(conflicts)
        assert isinstance(result, Table)

        buf = io.StringIO()
        Console(file=buf, force_terminal=False, highlight=False, width=120).print(result)
        out = buf.getvalue()
        assert "/repo/auth.py" in out
        assert "claude-code:1" in out
        assert "cursor:2" in out

    def test_multiple_conflicts_each_rendered(self) -> None:
        conflicts = [
            FileWriteConflict(
                path="/new.py",
                agents=("claude-code:1", "cursor:2"),
                first_seen=140.0,
                last_seen=150.0,
            ),
            FileWriteConflict(
                path="/old.py",
                agents=("aider:3", "cursor:2"),
                first_seen=100.0,
                last_seen=105.0,
            ),
        ]
        result = render_conflicts(conflicts)
        assert isinstance(result, Table)

        buf = io.StringIO()
        Console(file=buf, force_terminal=False, highlight=False, width=120).print(result)
        out = buf.getvalue()
        assert "/new.py" in out
        assert "/old.py" in out
        assert "aider:3" in out


class _FakeSession:
    """Minimal :class:`AttachSession` stand-in for slash-command tests.

    Lets us drive ``_render_live_tail`` through ``dispatch_slash`` without
    spawning a reader thread or touching the filesystem.
    """

    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        raise_ki: bool = False,
        producer_exited: bool = False,
    ) -> None:
        self.buffer = TailBuffer()
        self._chunks = list(chunks or [])
        self._raise_ki = raise_ki
        self.producer_exited = producer_exited
        self.closed = False

    def __iter__(self) -> _FakeSession:
        return self

    def __next__(self) -> bytes:
        if self._raise_ki:
            self._raise_ki = False  # raise once, like a real Ctrl+C
            raise KeyboardInterrupt
        if not self._chunks:
            raise StopIteration
        chunk = self._chunks.pop(0)
        # Mirror :class:`AttachSession.__next__`: append-on-yield so the
        # slash-command renderer only ever needs to read ``sess.buffer``.
        self.buffer.append(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class TestSliceToUtf8Boundary:
    """``_slice_to_utf8_boundary`` enforces the same chunk-edge guarantee
    on the render side that :class:`TailBuffer` enforces on the consumer
    side. Without it, the 64 KiB cap in ``_render_live_tail`` would slice
    mid-codepoint and surface a U+FFFD at the top of the live view."""

    def test_returns_input_when_under_cap(self) -> None:
        data = b"\xf0\x9f\xa6\x80hello"
        assert _slice_to_utf8_boundary(data, max_bytes=64) is data

    def test_returns_input_when_exactly_at_cap(self) -> None:
        data = b"X" * 64
        assert _slice_to_utf8_boundary(data, max_bytes=64) is data

    def test_walks_past_continuation_bytes_at_slice_start(self) -> None:
        # 🦀 = b"\xf0\x9f\xa6\x80". Slicing at byte 2 of that codepoint
        # would leave two stray continuation bytes (\xa6\x80) at the
        # head; the boundary walk must skip them so the decoded string
        # has no leading replacement character.
        prefix = b"X" * 1000
        data = prefix + b"\xf0\x9f\xa6\x80 done"
        # max_bytes drops the first 998 bytes of prefix but keeps the
        # last two ASCII bytes of prefix plus the partial 4-byte 🦀
        # plus " done" — i.e. it lands inside the codepoint.
        sliced = _slice_to_utf8_boundary(data, max_bytes=8)
        # The decoded suffix must not start with U+FFFD.
        decoded = sliced.decode("utf-8", errors="replace")
        assert not decoded.startswith("�")
        # And it must end with the trailing ASCII so we know we kept
        # the suffix, not over-trimmed.
        assert decoded.endswith(" done")

    def test_drops_at_most_three_continuation_bytes(self) -> None:
        # If for some reason the slice starts with more than 3
        # continuation bytes (corrupt input), we don't loop forever.
        # The bound is the max UTF-8 codepoint length (4 bytes).
        bad = b"\x80\x80\x80\x80\x80hello"
        sliced = _slice_to_utf8_boundary(bad, max_bytes=len(bad))
        assert sliced is bad  # under cap, returned unchanged

        sliced = _slice_to_utf8_boundary(b"prefix" + bad, max_bytes=len(bad))
        # We walk forward at most 4 bytes; if all of those are still
        # continuation bytes, decoding will surface U+FFFD — the helper
        # is bounded, not magic.
        assert len(sliced) >= len(bad) - 4

    def test_pure_ascii_unchanged_after_slice(self) -> None:
        data = b"abcdefghij"
        sliced = _slice_to_utf8_boundary(data, max_bytes=5)
        assert sliced == b"fghij"

    def test_multibyte_decode_clean_after_slice(self) -> None:
        # Snapshot ends with several 2-byte codepoints (é = b"\xc3\xa9");
        # the slice must land on a leading byte so the decoded string
        # has no leading replacement character.
        prefix = b"a" * 100
        data = prefix + b"\xc3\xa9\xc3\xa9\xc3\xa9"
        sliced = _slice_to_utf8_boundary(data, max_bytes=5)
        decoded = sliced.decode("utf-8", errors="replace")
        assert "�" not in decoded


class TestAgentsTrace:
    def test_no_args_prints_usage(self) -> None:
        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace", sess_obj, console) is True
        out = buf.getvalue().lower()
        assert "usage" in out
        assert "<pid>" in out
        assert sess_obj.history[-1]["ok"] is False

    def test_non_numeric_pid_rejected(self) -> None:
        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace abc", sess_obj, console) is True
        out = buf.getvalue().lower()
        assert "invalid pid" in out
        assert sess_obj.history[-1]["ok"] is False

    def test_too_many_args_rejected(self) -> None:
        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace 1 2", sess_obj, console) is True
        assert "usage" in buf.getvalue().lower()
        assert sess_obj.history[-1]["ok"] is False

    def test_attach_unsupported_renders_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _refuse(_pid: int) -> _FakeSession:
            raise AttachUnsupported("stdout is on a terminal; live tail not supported")

        monkeypatch.setattr(agents_mod, "attach", _refuse)

        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace 8421", sess_obj, console) is True
        out = buf.getvalue()
        assert "cannot trace" in out
        assert "stdout is on a terminal" in out
        assert sess_obj.history[-1]["ok"] is False

    def test_unknown_pid_falls_back_to_pid_label(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Header says "pid <n>" when the pid is not in the registry.
        _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        monkeypatch.setattr(agents_mod, "attach", lambda _pid: _FakeSession(chunks=[]))

        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace 8421", sess_obj, console) is True
        out = buf.getvalue()
        assert "pid 8421" in out
        assert "trace ended" in out

    def test_known_pid_uses_registered_name_in_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))
        monkeypatch.setattr(agents_mod, "attach", lambda _pid: _FakeSession(chunks=[]))

        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace 8421", sess_obj, console) is True
        out = buf.getvalue()
        assert "claude-code" in out
        assert "8421" in out

    def test_renders_chunks_through_live(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Two chunks then StopIteration: the handler should append them
        # to the buffer and feed Live.update; the rendered text should
        # land in the captured console output.
        monkeypatch.setattr(
            agents_mod,
            "attach",
            lambda _pid: _FakeSession(chunks=[b"hello ", b"world\n"]),
        )

        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace 8421", sess_obj, console) is True
        out = buf.getvalue()
        assert "hello world" in out
        assert "trace ended" in out

    def test_swallows_keyboard_interrupt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Single Ctrl+C inside the Live block must not propagate out of
        # ``dispatch_slash`` — the REPL should return to its prompt.
        # This is the kubectl-logs-style UX, deliberately different from
        # ``stream_to_console``'s double-press pattern.
        monkeypatch.setattr(agents_mod, "attach", lambda _pid: _FakeSession(raise_ki=True))

        sess_obj = ReplSession()
        console, buf = _capture()
        # Must not raise:
        assert dispatch_slash("/agents trace 8421", sess_obj, console) is True
        assert "trace ended" in buf.getvalue()

    def test_session_is_closed_even_on_keyboard_interrupt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Lifecycle guard: the ``with`` block in the handler must close
        # the AttachSession (and thereby the reader thread) regardless
        # of whether the iteration completed naturally or was stopped
        # by a Ctrl+C.
        fake = _FakeSession(raise_ki=True)
        monkeypatch.setattr(agents_mod, "attach", lambda _pid: fake)

        sess_obj = ReplSession()
        console, _ = _capture()
        assert dispatch_slash("/agents trace 8421", sess_obj, console) is True
        assert fake.closed is True

    def test_renders_process_exited_when_producer_died(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When the agent process dies during a trace, the trailer should
        # surface that explicitly so an unattended trace doesn't look
        # the same as a Ctrl+C abort.
        monkeypatch.setattr(
            agents_mod,
            "attach",
            lambda _pid: _FakeSession(chunks=[b"goodbye\n"], producer_exited=True),
        )
        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace 8421", sess_obj, console) is True
        out = buf.getvalue()
        assert "process exited" in out
        assert "trace ended" in out

    def test_does_not_render_process_exited_on_user_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Producer is alive, user pressed Ctrl+C: only "trace ended"
        # should appear; "process exited" would be misleading.
        monkeypatch.setattr(
            agents_mod,
            "attach",
            lambda _pid: _FakeSession(raise_ki=True, producer_exited=False),
        )
        sess_obj = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents trace 8421", sess_obj, console) is True
        out = buf.getvalue()
        assert "process exited" not in out
        assert "trace ended" in out
