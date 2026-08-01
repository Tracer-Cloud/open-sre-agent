"""Lazy re-export wrappers must expose everything they delegate to.

Several modules wrap a function purely to defer a heavy import: the wrapper
repeats the signature and calls the real one inside the body. Repeating keeps
type checking, at the cost of a manual sync obligation — and a parameter added
downstream but not to the wrapper is silently unreachable through the seam
callers actually import.

That failed twice in one change: ``surfaces.interactive_shell.run_repl`` dropped
``console``, so redirecting shell output raised TypeError through the public
import; and three ``surfaces.cli.telemetry`` wrappers dropped ``extra`` /
``tags`` / ``expected`` and discarded the return value, so the CLI could not
attach structured context to an error report or read back the event id.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

# (wrapper module, symbol, module it delegates to). Each pair is a function the
# wrapper re-declares rather than re-exports, so the two can drift apart.
LAZY_WRAPPERS = [
    ("surfaces.interactive_shell", "run_repl", "surfaces.interactive_shell.main"),
    ("surfaces.cli.telemetry", "capture_cli_invoked", "platform.analytics.cli"),
    ("surfaces.cli.telemetry", "build_cli_invoked_properties", "platform.analytics.cli"),
    ("surfaces.cli.telemetry", "shutdown_analytics", "platform.analytics.provider"),
    ("surfaces.cli.telemetry", "init_sentry", "platform.observability.errors.sentry"),
    ("surfaces.cli.telemetry", "capture_exception", "platform.observability.errors.sentry"),
    ("surfaces.cli.telemetry", "render_landing", "surfaces.interactive_shell.ui.layout"),
    (
        "surfaces.cli.telemetry",
        "report_exception",
        "surfaces.interactive_shell.utils.error_handling.exception_reporting",
    ),
    (
        "surfaces.cli.telemetry",
        "should_report_exception",
        "surfaces.interactive_shell.utils.error_handling.exception_reporting",
    ),
]

_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


@pytest.mark.parametrize(("wrapper_module", "symbol", "target_module"), LAZY_WRAPPERS)
def test_wrapper_accepts_every_parameter_of_its_target(
    wrapper_module: str, symbol: str, target_module: str
) -> None:
    """A parameter the target accepts must be reachable through the wrapper."""
    # Arrange
    wrapper = getattr(importlib.import_module(wrapper_module), symbol)
    target = getattr(importlib.import_module(target_module), symbol)

    # Act
    wrapper_params = inspect.signature(wrapper).parameters
    target_params = inspect.signature(target).parameters
    missing = [
        name
        for name, param in target_params.items()
        if name not in wrapper_params and param.kind not in _VARIADIC
    ]

    # Assert
    assert not missing, (
        f"{wrapper_module}.{symbol} cannot pass {missing} through to "
        f"{target_module}.{symbol}; callers of the wrapper cannot reach them"
    )


@pytest.mark.parametrize(("wrapper_module", "symbol", "target_module"), LAZY_WRAPPERS)
def test_wrapper_does_not_discard_a_returned_value(
    wrapper_module: str, symbol: str, target_module: str
) -> None:
    """A wrapper annotated ``-> None`` over a value-returning target drops it.

    ``capture_exception`` returned the Sentry event id and ``report_exception``
    a bool; both wrappers threw them away.
    """
    # Arrange
    wrapper = getattr(importlib.import_module(wrapper_module), symbol)
    target = getattr(importlib.import_module(target_module), symbol)

    # Act
    wrapper_returns = inspect.signature(wrapper).return_annotation
    target_returns = inspect.signature(target).return_annotation

    # Assert
    target_yields_value = target_returns not in (None, "None", inspect.Signature.empty)
    if target_yields_value:
        assert wrapper_returns not in (None, "None"), (
            f"{wrapper_module}.{symbol} is annotated -> None but "
            f"{target_module}.{symbol} returns {target_returns}"
        )
