"""Vertex AI provider helper tests."""

from __future__ import annotations

from types import SimpleNamespace

from core.llm.providers.vertex_ai import resolve_vertex_ai_request_kwargs


def _settings(**overrides: str) -> SimpleNamespace:
    base = {
        "vertex_ai_project": "my-gcp-project",
        "vertex_ai_location": "us-central1",
        "vertex_ai_labels": "",
        "vertex_ai_reasoning_model": "gemini-2.5-pro",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_vertex_ai_request_kwargs_parses_labels() -> None:
    kwargs = resolve_vertex_ai_request_kwargs(
        _settings(vertex_ai_labels='{"team": "sre", "env": "prod"}'),
        model_type="reasoning",
    )

    assert kwargs["labels"] == {"team": "sre", "env": "prod"}


def test_resolve_vertex_ai_request_kwargs_omits_labels_when_unset() -> None:
    kwargs = resolve_vertex_ai_request_kwargs(
        _settings(vertex_ai_labels=""),
        model_type="reasoning",
    )

    assert "labels" not in kwargs


def test_resolve_vertex_ai_request_kwargs_omits_labels_when_malformed_json() -> None:
    kwargs = resolve_vertex_ai_request_kwargs(
        _settings(vertex_ai_labels="not json"),
        model_type="reasoning",
    )

    assert "labels" not in kwargs


def test_resolve_vertex_ai_request_kwargs_omits_labels_when_not_an_object() -> None:
    kwargs = resolve_vertex_ai_request_kwargs(
        _settings(vertex_ai_labels="[1, 2, 3]"),
        model_type="reasoning",
    )

    assert "labels" not in kwargs


def test_resolve_vertex_ai_request_kwargs_drops_non_string_label_values() -> None:
    kwargs = resolve_vertex_ai_request_kwargs(
        _settings(vertex_ai_labels='{"team": "sre", "count": 5}'),
        model_type="reasoning",
    )

    assert kwargs["labels"] == {"team": "sre"}
