"""Process-wide third-party warning filters.

Some upstream packages emit deprecation warnings during ordinary
``import`` of one of their submodules. Filtering those at call time
(e.g. inside ``init_sentry``) is too late, because the import — and
therefore the ``warnings.warn`` call — has already happened by the time
``init_sentry`` runs.

The filters here are intentionally narrow: they target a single, exact
upstream warning that we have already triaged and decided to silence
until the upstream packages expose a configuration hook. Adding a
filter here should always be paired with a comment naming the upstream
issue or change that motivates the filter.
"""

from __future__ import annotations

import warnings
from contextlib import suppress

_LANGGRAPH_ALLOWED_OBJECTS_PATTERN = (
    r"The default value of `allowed_objects` will change in a future version\..*"
)

_filters_installed = False


def install_known_filters() -> None:
    """Register the project-wide warning filters once per process.

    Safe to call from multiple entry points; subsequent calls are no-ops.
    Call this as early as possible — before any module that transitively
    imports ``langgraph`` — otherwise the filtered warning has already
    fired by the time the filter is in place.
    """
    global _filters_installed
    if _filters_installed:
        return

    # langgraph.checkpoint.serde.jsonplus imports langchain_core.load.dump,
    # which currently emits a LangChainPendingDeprecationWarning about the
    # default value of ``allowed_objects`` (see issue #1779). The runtime
    # behaviour does not change, so silence only this exact warning until
    # the upstream packages expose an explicit configuration hook.
    category: type[Warning] = Warning
    with suppress(Exception):
        from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

        category = LangChainPendingDeprecationWarning
    warnings.filterwarnings(
        "ignore",
        message=_LANGGRAPH_ALLOWED_OBJECTS_PATTERN,
        category=category,
    )

    _filters_installed = True
