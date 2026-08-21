"""The default agent must be able to run shell tools after process boot.

``AgentSession.start()`` is the documented Python entry point. With no
subprocess presenter, ``shell_run`` refuses every call ("subprocess presenter
is required for this action tool") and the agent silently degrades to writing
a plan it cannot execute — which reads as a reasoning failure but is a wiring
gap. Observed live: the 5-step goal prompt returned PLANNED=4 / EXECUTED=0.

The presenter cannot be a core default: it lives in ``tools`` (its process
helpers) and ``core.agent_harness`` may import neither ``tools`` nor
``gateway``. It is registered on this port at process boot instead.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import platform.harness_ports as harness_ports


@pytest.fixture(autouse=True)
def _restore_port() -> Iterator[None]:
    """Keep this module's writes out of every other test in the process."""
    previous = harness_ports.get_subprocess_presenter_factory()
    yield
    harness_ports.set_subprocess_presenter_factory(previous)


def test_process_boot_installs_a_subprocess_presenter_for_the_default_agent() -> None:
    """Boot leaves a presenter registered so the default port stack can execute."""
    # Arrange.
    harness_ports.set_subprocess_presenter_factory(None)

    # Act.
    from bootstrap.adapters import install_harness_adapters

    install_harness_adapters()

    # Assert.
    assert harness_ports.get_subprocess_presenter_factory() is not None


def test_resetting_the_harness_ports_clears_the_presenter() -> None:
    """A booted presenter must not leak into tests that reset the ports.

    ``reset_harness_ports`` is the suite's "back to noop defaults" call; a port
    it forgets stays registered for the rest of the process.
    """

    # Arrange: something registered, as after boot.
    def _presenter(*_args: object, **_kwargs: object) -> object:
        return object()

    harness_ports.set_subprocess_presenter_factory(_presenter)

    # Act.
    harness_ports.reset_harness_ports()

    # Assert.
    assert harness_ports.get_subprocess_presenter_factory() is None


def test_default_headless_build_injects_the_registered_presenter() -> None:
    """The default port stack takes the boot-registered factory at construction.

    Hosts that pass their own ``DefaultToolProvider`` keep that factory. The
    family's bare default is what ``AgentSession.start()`` uses, so it must
    receive the registered presenter or ``shell_run`` refuses every call. The
    provider itself must not look the factory up — a missing constructor arg
    stays missing even when one is registered.
    """
    # Arrange.
    from core.agent_harness.session import SessionCore
    from core.agent_harness.session.persistence.memory import InMemorySessionStore
    from core.agent_harness.tools.tool_provider import DefaultToolProvider
    from core.agent_harness.turns.headless_adapters import BufferOutputSink
    from core.agent_harness.turns.headless_build import DefaultHeadlessBuild

    def _registered(*_args: object, **_kwargs: object) -> object:
        return object()

    def _explicit(*_args: object, **_kwargs: object) -> object:
        return object()

    harness_ports.set_subprocess_presenter_factory(_registered)
    with_explicit = DefaultToolProvider(object(), object(), subprocess_presenter_factory=_explicit)
    without = DefaultToolProvider(object(), object())
    default_tools = DefaultHeadlessBuild(
        session=SessionCore(store=InMemorySessionStore()),
        output=BufferOutputSink(),
    ).tools()

    # Act / Assert.
    assert with_explicit._subprocess_presenter_factory is _explicit  # noqa: SLF001
    assert without._subprocess_presenter_factory is None  # noqa: SLF001
    assert isinstance(default_tools, DefaultToolProvider)
    assert default_tools._subprocess_presenter_factory is _registered  # noqa: SLF001
