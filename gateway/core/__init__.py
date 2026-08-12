"""Gateway core — process, storage, and leaf helpers shared by all surfaces.

Subpackages:

* ``runtime/`` — composition root, turn handler, approvals, daemon
* ``storage/`` — session bindings and investigation stores
* ``billing/`` — credits client
* ``attachments/`` — attachment helpers
* ``session/`` — gateway chat context helpers
* ``config/`` — logging and gateway config helpers

Must not import ``gateway.transports.*`` or ``gateway.web`` (surfaces).
Sole exception: :mod:`gateway.core.runtime.manager`, the composition root,
which wires the transports and the web surface together.
"""

from __future__ import annotations

__all__: list[str] = []
