"""The bundled demo alert both terminal surfaces offer as ``alert.json``."""

from __future__ import annotations

from pathlib import Path

DEMO_ALERT_FILENAME = "alert.json"


def bundled_demo_alert_path() -> Path | None:
    """The packaged demo alert, or None when the data file is missing."""
    candidate = Path(__file__).resolve().parent / "sample_alerts" / DEMO_ALERT_FILENAME
    if candidate.is_file():
        return candidate
    return None


def resolve_alert_path(path_str: str) -> Path:
    """Resolve an alert path, using the bundled demo when ``alert.json`` is missing locally."""
    path = Path(path_str)
    if path.is_file():
        return path
    if path.name == DEMO_ALERT_FILENAME and not path.is_absolute() and path.parent == Path("."):
        bundled = bundled_demo_alert_path()
        if bundled is not None:
            return bundled
    return path


__all__ = ["DEMO_ALERT_FILENAME", "bundled_demo_alert_path", "resolve_alert_path"]
