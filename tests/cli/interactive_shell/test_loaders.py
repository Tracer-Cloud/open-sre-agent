"""Tests for the shared interactive-shell LLM loader."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from app.cli.interactive_shell import loaders
from app.cli.interactive_shell.loaders import llm_loader


def _terminal_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return (
        Console(file=buf, force_terminal=True, color_system=None, width=80, highlight=False),
        buf,
    )


def _plain_console() -> tuple[Console, io.StringIO]:
    """Console that reports ``is_terminal == False`` — the CI / piped case."""
    buf = io.StringIO()
    return (Console(file=buf, force_terminal=False, color_system=None, width=80), buf)


class TestLLMLoader:
    def test_yields_to_caller_and_runs_wrapped_block(self) -> None:
        console, _ = _terminal_console()
        ran: list[bool] = []
        with llm_loader(console):
            ran.append(True)
        assert ran == [True]

    def test_skips_spinner_when_console_is_not_a_terminal(self) -> None:
        """In CI / piped output we must NOT pollute logs with spinner frames."""
        console, buf = _plain_console()
        with llm_loader(console):
            pass
        # Nothing should be written — no label, no escape sequences, nothing.
        assert buf.getvalue() == ""

    def test_loader_uses_orange_spinner_style(self, monkeypatch: Any) -> None:
        """The loader must call ``console.status`` with an orange spinner
        style — guards against accidental colour drift."""
        captured: dict[str, Any] = {}

        # Fake context manager so we can introspect the kwargs without
        # triggering Rich's Live renderer in the test.
        class _FakeStatus:
            def __enter__(self) -> _FakeStatus:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        def _fake_status(text: str, **kwargs: Any) -> _FakeStatus:
            captured["text"] = text
            captured["kwargs"] = kwargs
            return _FakeStatus()

        console, _ = _terminal_console()
        monkeypatch.setattr(console, "status", _fake_status)

        with llm_loader(console, label="consulting the model"):
            pass

        assert "orange1" in captured["text"]
        assert "consulting the model" in captured["text"]
        assert captured["kwargs"]["spinner"] == "dots12"
        assert "orange1" in captured["kwargs"]["spinner_style"]


def test_module_exports_loader_and_default_label() -> None:
    assert "llm_loader" in loaders.__all__
    assert "DEFAULT_LOADER_LABEL" in loaders.__all__
