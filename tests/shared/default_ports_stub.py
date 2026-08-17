"""Test stand-in for ``DefaultPorts`` that routes construction through one ``build`` callable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def default_ports_stub(build: Callable[..., Any]) -> type:
    """Return a ``DefaultPorts`` stand-in whose ``agent(**ports)`` calls ``build(**fields, **ports)``.

    Patch it over ``<module>.DefaultPorts`` so a test that used to fake the
    agent factory keeps one hook: ``build`` sees the family's fields
    (``session``, ``output``, ``console``, ``logger``, ``surface``) merged
    with the ports the host passed to ``agent()`` (``tools``, ``prompts``,
    ``gather``, ``deps``).
    """

    class _DefaultPortsStub:
        def __init__(self, **fields: Any) -> None:
            self._fields = fields

        def agent(self, **ports: Any) -> Any:
            return build(**self._fields, **ports)

    return _DefaultPortsStub


__all__ = ["default_ports_stub"]
