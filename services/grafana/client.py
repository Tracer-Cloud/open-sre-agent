"""Unified Grafana Cloud client composed from mixins."""

from vendors.grafana.base import GrafanaClientBase
from vendors.grafana.loki import LokiMixin
from vendors.grafana.mimir import MimirMixin
from vendors.grafana.tempo import TempoMixin


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass
