"""Tests for the wizard's interactive model selection prompt."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from cli.wizard import _ui, flow
from cli.wizard.config import PROVIDER_BY_VALUE


def _wire_prompts(
    monkeypatch: pytest.MonkeyPatch,
    select_values: list[str],
    text_values: list[str] | None = None,
) -> None:
    select_iter = iter(select_values)
    text_iter = iter(text_values or [])

    def _mock_select(*_args: Any, **_kwargs: Any) -> Any:
        m = MagicMock()
        m.ask.return_value = next(select_iter)
        return m

    def _mock_text(*_args: Any, **_kwargs: Any) -> Any:
        m = MagicMock()
        m.ask.return_value = next(text_iter)
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)


def test_choose_model_returns_curated_default(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = PROVIDER_BY_VALUE["anthropic"]
    _wire_prompts(monkeypatch, select_values=[provider.default_model])

    model = _ui._choose_model(provider, default=provider.default_model)

    assert model == provider.default_model


def test_choose_model_offers_full_curated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = PROVIDER_BY_VALUE["openai"]

    captured: dict[str, list[str]] = {}

    def _mock_select(_prompt: str, choices: list[Any], **_kwargs: Any) -> Any:
        captured["values"] = [c.value for c in choices]
        m = MagicMock()
        m.ask.return_value = provider.default_model
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)

    _ui._choose_model(provider, default="")

    expected_curated = [opt.value for opt in provider.models]
    assert captured["values"][:-1] == expected_curated
    assert captured["values"][-1] == _ui._CUSTOM_MODEL_SENTINEL


def test_choose_model_preserves_saved_model_not_in_curated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = PROVIDER_BY_VALUE["openai"]

    captured: dict[str, list[str]] = {}

    def _mock_select(_prompt: str, choices: list[Any], **_kwargs: Any) -> Any:
        captured["values"] = [c.value for c in choices]
        m = MagicMock()
        m.ask.return_value = "my-tuned-gpt"
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)

    model = _ui._choose_model(provider, default="my-tuned-gpt")

    assert model == "my-tuned-gpt"
    assert "my-tuned-gpt" in captured["values"]


def test_choose_model_accepts_custom_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = PROVIDER_BY_VALUE["anthropic"]
    _wire_prompts(
        monkeypatch,
        select_values=[_ui._CUSTOM_MODEL_SENTINEL],
        text_values=["claude-future-preview"],
    )

    model = _ui._choose_model(provider, default=provider.default_model)

    assert model == "claude-future-preview"


def test_choose_model_works_for_cli_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI providers (codex, claude-code, etc.) use the same curated picker."""
    provider = PROVIDER_BY_VALUE["codex"]
    _wire_prompts(monkeypatch, select_values=["gpt-5.4"])

    model = _ui._choose_model(provider, default="")

    assert model == "gpt-5.4"
