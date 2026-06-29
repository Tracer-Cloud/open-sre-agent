"""OpenSRE pipeline-span vocabulary and projection.

Owns two pieces of OpenSRE domain knowledge:

* ``_PIPELINE_SPAN_NAMES`` — the set of span names that count as
  named OpenSRE pipeline stages (``extract_data``, ``validate_data``,
  ``transform_data``, ``load_data``).
* :func:`extract_pipeline_spans` — projects those spans into the
  ``{span_name, execution_run_id, record_count}`` shape every
  downstream investigation node consumes.

Both ``integrations.grafana.tools`` (live Grafana / Tempo queries) and
``integrations.opensre.seed_evidence`` (offline OpenRCA / HuggingFace
fixtures) need this projection. Lives in ``core.domain`` so neither
side has to import the other, and so the OpenSRE-specific span-name
vocabulary stays out of the infrastructure layer (``platform/common``).
"""

from __future__ import annotations

from typing import Any

_PIPELINE_SPAN_NAMES: frozenset[str] = frozenset(
    {"extract_data", "validate_data", "transform_data", "load_data"}
)


def extract_pipeline_spans(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one record per pipeline-stage span found across ``traces``.

    Each record carries the span name, the originating ``execution.run_id``
    attribute, and the ``record_count`` attribute when present.
    """
    pipeline_spans: list[dict[str, Any]] = []
    for trace in traces:
        for span in trace.get("spans", []):
            if span.get("name") not in _PIPELINE_SPAN_NAMES:
                continue
            attributes = span.get("attributes", {})
            pipeline_spans.append(
                {
                    "span_name": span.get("name"),
                    "execution_run_id": attributes.get("execution.run_id"),
                    "record_count": attributes.get("record_count"),
                }
            )
    return pipeline_spans
