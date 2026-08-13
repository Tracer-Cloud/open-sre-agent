"""Load balancer tools.

Credentials come from the ``yandex_cloud`` integration record rather than one
of this package's own — see ``integrations/yandex_cloud/availability.py``.
"""

from __future__ import annotations

SOURCE = "yc_network"

__all__ = ["SOURCE"]
