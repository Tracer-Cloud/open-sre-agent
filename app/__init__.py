"""Agentic AI for Data Pipeline Incident Resolution Demo."""

# Install third-party warning filters before any submodule (transitively)
# imports ``langgraph`` — see issue #1779.
from app.utils.warning_filters import install_known_filters

install_known_filters()

from app.entrypoints.sdk import run_investigation  # noqa: E402

__all__ = ["run_investigation"]
