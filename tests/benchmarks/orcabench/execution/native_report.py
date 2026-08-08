"""Exact native OpenSRE report persistence for ORCA."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class NativeReportPolicy:
    """Map OpenSRE's structured disposition to ORCA's report-file contract."""

    def write(self, payload: dict[str, Any], destination: Path) -> bytes:
        """Write an empty control report; otherwise preserve OpenSRE's report."""
        report = payload.get("report")
        if not isinstance(report, str):
            raise ValueError("native OpenSRE payload must contain a string report")
        category = payload.get("root_cause_category")
        if not isinstance(category, str):
            raise ValueError("native OpenSRE payload must contain a root-cause category")
        data = b"" if category == "healthy" else report.encode("utf-8")

        # ORCA pre-creates /app/report.md as mode 0666 but keeps /app root-owned,
        # so an atomic sibling rename is not available to the non-root agent.
        # Flush and fsync the exact pre-created output file instead.
        with destination.open("wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        return data
