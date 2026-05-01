"""Tests for the interactive shell loop helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.keys import Keys

from app.cli.interactive_shell import loop


def test_build_prompt_session_uses_persistent_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.constants as const_module

    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

    prompt = loop._build_prompt_session()

    assert isinstance(prompt.history, FileHistory)
    assert prompt.history.filename == str(tmp_path / "interactive_history")
    assert tmp_path.exists()
    assert isinstance(prompt.completer, loop.SlashCommandCompleter)
    assert prompt.app.key_bindings is not None


def test_prompt_toolbar_uses_current_provider_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "detect_provider_model", lambda: ("anthropic", "claude-sonnet-4-6"))

    toolbar = loop._format_prompt_toolbar()

    assert ("class:prompt-toolbar.value", "anthropic · claude-sonnet-4-6 ") in toolbar


def test_build_prompt_session_falls_back_to_memory_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.constants as const_module

    blocked_home = tmp_path / "not-a-directory"
    blocked_home.write_text("", encoding="utf-8")
    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", blocked_home)

    prompt = loop._build_prompt_session()

    assert isinstance(prompt.history, InMemoryHistory)


def test_slash_completer_previews_all_commands() -> None:
    completions = list(
        loop._build_slash_completer().get_completions(
            Document("/"),
            CompleteEvent(text_inserted=True),
        )
    )
    names = [completion.text for completion in completions]

    assert "/help" in names
    assert "/list" in names
    assert "/model" in names
    assert all(name.startswith("/") for name in names)


def test_slash_completer_filters_by_prefix() -> None:
    completions = list(
        loop._build_slash_completer().get_completions(
            Document("/li"),
            CompleteEvent(text_inserted=True),
        )
    )

    assert [completion.text for completion in completions] == ["/list"]


def test_slash_completer_ignores_subcommand_text() -> None:
    completions = list(
        loop._build_slash_completer().get_completions(
            Document("/list "),
            CompleteEvent(text_inserted=True),
        )
    )

    assert completions == []


def test_completion_menu_supports_up_down_navigation() -> None:
    key_bindings = loop._build_prompt_key_bindings()
    keys = {binding.keys for binding in key_bindings.bindings}

    assert (Keys.Down,) in keys
    assert (Keys.Up,) in keys


def test_completion_menu_current_item_uses_subtle_highlight() -> None:
    style = loop._build_prompt_style()
    attrs = style.get_attrs_for_style_str("class:completion-menu.completion.current")

    assert attrs.color == "ff7a45"
    assert attrs.bgcolor == "241913"
    assert attrs.reverse is False
    assert attrs.bold is False


def test_prompt_toolbar_uses_dark_background() -> None:
    style = loop._build_prompt_style()
    attrs = style.get_attrs_for_style_str("class:prompt-toolbar.value")

    assert attrs.color == "ffbe68"
    assert attrs.bgcolor == "141210"
