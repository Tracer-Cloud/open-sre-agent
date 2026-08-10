"""Reader-facing labels for the investigation pipeline's progress stages.

The keys are the node names the pipeline passes to ``ProgressReporter.start``,
not a description of the pipeline. A key matching nothing degrades to
``DEFAULT_STAGE_LABEL`` silently, so a reader watching a five-stage run would
see "Processing" five times and learn nothing. ``tests/config/`` pins the table
against the real call sites for that reason.
"""

from collections.abc import Mapping

__all__ = ["INVESTIGATION_STAGE_LABELS", "DEFAULT_STAGE_LABEL"]


INVESTIGATION_STAGE_LABELS: Mapping[str, str] = {
    "extract_alert": "Reading the alert",
    "resolve_integrations": "Connecting to your integrations",
    "investigation_agent": "Gathering evidence",
    "correlate_upstream": "Correlating upstream signals",
    "diagnose_root_cause": "Working out the root cause",
}

DEFAULT_STAGE_LABEL = "Processing"
