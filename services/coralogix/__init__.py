"""Coralogix API client module."""

from vendors.coralogix.client import (
    CoralogixClient,
    build_coralogix_logs_query,
)

__all__ = ["CoralogixClient", "build_coralogix_logs_query"]
