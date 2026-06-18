"""Shared fixtures for wizard tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_os_environ() -> Iterator[None]:
    """Restore ``os.environ`` after each wizard test.

    ``sync_provider_env`` mutates the live process environment directly
    (``os.environ.pop``/``update``) to drop stale provider keys — including
    other providers' API keys such as ``OPENAI_API_KEY``. Wizard tests that
    exercise the real ``sync_provider_env`` do not ``monkeypatch`` every key it
    touches, so without this snapshot the deletions leak across tests sharing an
    xdist worker and break later suites (e.g. live_llm planner contracts that
    then fall back off the configured openai provider).
    """
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
