"""Shared fixtures for composition-root tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def reset_process_runtime() -> Iterator[None]:
    """Clear per-profile idempotency state around each test.

    ``configure_process`` runs a profile once per process. Cross-package
    tests call :func:`bootstrap.process.reset_process_runtime_for_tests`;
    this fixture keeps bootstrap characterization suites isolated.
    """
    from bootstrap.process import reset_process_runtime_for_tests

    reset_process_runtime_for_tests()
    yield
    reset_process_runtime_for_tests()
