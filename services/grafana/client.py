"""Unified Grafana Cloud client composed from mixins."""

from services.grafana.base import GrafanaClientBase
from services.grafana.loki import LokiMixin
from services.grafana.mimir import MimirMixin
from services.grafana.tempo import TempoMixin


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass
