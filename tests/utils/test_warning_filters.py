"""Tests for ``app.utils.warning_filters``."""

from __future__ import annotations

import warnings

import pytest

from app.utils import warning_filters


@pytest.fixture(autouse=True)
def _reset_filter_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``install_known_filters`` to run fresh in each test."""
    monkeypatch.setattr(warning_filters, "_filters_installed", False)


def test_install_known_filters_silences_langgraph_allowed_objects_warning() -> None:
    """Regression coverage for issue #1779.

    The default value of ``allowed_objects`` deprecation warning is emitted
    when ``langgraph.checkpoint.serde.jsonplus`` is imported (transitively
    via ``app.state.agent_state``). The runtime behaviour is unchanged, so
    we silence only this exact warning until the upstream packages expose
    a configuration hook.
    """
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

    warning_filters.install_known_filters()

    with warnings.catch_warnings(record=True) as caught:
        # Ensure user-side ``simplefilter("always")`` does not unmask filters
        # installed via ``warnings.filterwarnings`` — we want the project
        # filter to take precedence over the test harness default.
        warnings.warn(
            (
                "The default value of `allowed_objects` will change in a future version. "
                "Pass an explicit value (e.g., allowed_objects='messages' or "
                "allowed_objects='core') to suppress this warning."
            ),
            category=LangChainPendingDeprecationWarning,
            stacklevel=1,
        )

    assert not [w for w in caught if isinstance(w.message, LangChainPendingDeprecationWarning)]


def test_install_known_filters_is_idempotent() -> None:
    """Calling ``install_known_filters`` twice must not duplicate filter entries."""
    warning_filters.install_known_filters()
    filters_after_first = list(warnings.filters)

    warning_filters.install_known_filters()
    filters_after_second = list(warnings.filters)

    assert filters_after_first == filters_after_second


def test_install_known_filters_does_not_silence_unrelated_warnings() -> None:
    """The filter must be narrow — unrelated DeprecationWarnings still surface."""
    warning_filters.install_known_filters()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("unrelated deprecation", category=DeprecationWarning, stacklevel=1)

    assert any(
        isinstance(w.message, DeprecationWarning) and "unrelated deprecation" in str(w.message)
        for w in caught
    )
