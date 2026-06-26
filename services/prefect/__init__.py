"""Prefect service client exports."""

from vendors.prefect.client import PrefectClient, PrefectConfig, make_prefect_client

__all__ = ["PrefectClient", "PrefectConfig", "make_prefect_client"]
