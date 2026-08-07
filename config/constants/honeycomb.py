"""Honeycomb environment variable names."""

from __future__ import annotations

HONEYCOMB_API_KEY_ENV = "HONEYCOMB_API_KEY"
HONEYCOMB_DATASET_ENV = "HONEYCOMB_DATASET"
# Mirrors the ``base_url`` credential; the names deliberately differ.
HONEYCOMB_BASE_URL_ENV = "HONEYCOMB_API_URL"

__all__ = [
    "HONEYCOMB_API_KEY_ENV",
    "HONEYCOMB_BASE_URL_ENV",
    "HONEYCOMB_DATASET_ENV",
]
