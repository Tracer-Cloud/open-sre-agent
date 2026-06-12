"""Closed-vocabulary constants — re-export shim.

Canonical definitions live in ``tests.benchmarks.cloudopsbench.closed_vocabulary``
so ``taxonomy`` and ``scoring`` can import without loading ``predictor/__init__.py``.

This module is intentionally a thin re-export so existing
``from tests.benchmarks.cloudopsbench.predictor.vocabulary import X``
callers keep working without change. ``__all__`` declares the
re-exported names so ruff F401 / ``from x import *`` both behave
correctly.
"""

from __future__ import annotations

from tests.benchmarks.cloudopsbench.closed_vocabulary import (
    _FAULT_OBJECT_NAMESPACES,
    _FAULT_OBJECT_NODES,
    _FAULT_OBJECT_SERVICES,
    _ROOT_CAUSES,
    _TAXONOMY_CATEGORIES,
)

__all__ = [
    "_FAULT_OBJECT_NAMESPACES",
    "_FAULT_OBJECT_NODES",
    "_FAULT_OBJECT_SERVICES",
    "_ROOT_CAUSES",
    "_TAXONOMY_CATEGORIES",
]
