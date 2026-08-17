"""Ollama model-tag rules shared by validation and the local-LLM wizard."""

from __future__ import annotations


def normalize_model_tag(model: str) -> str:
    """Append ``:latest`` when the tag is missing, matching Ollama's own default."""
    return model if ":" in model else f"{model}:latest"


__all__ = ["normalize_model_tag"]
