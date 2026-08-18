"""Gateway core — process, storage, and leaf helpers shared by all surfaces.

Subpackages:

* ``host/`` — turn handler, session agents, cancel console, capacity
* ``process/`` — daemon supervision, polling thread, readiness
* ``runtime/`` — composition root, credential hydration, security audit
* ``middleware/`` — per-turn steps every transport runs
* ``storage/`` — session bindings and investigation stores
* ``billing/`` — credits client
* ``attachments/`` — attachment helpers
* ``session/`` — gateway chat context helpers
* ``config/`` — logging and gateway config helpers

Must not import ``gateway.transports.*`` or ``gateway.web`` (surfaces).
Sole exception: :mod:`gateway.core.runtime.controller`, the composition root,
which wires the transports and the web surface together.
"""

from __future__ import annotations

__all__: list[str] = []
