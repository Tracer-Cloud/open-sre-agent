"""HTTP helpers for posting synthetic alerts to a remote investigation stream URL."""

from __future__ import annotations

from typing import Any

import requests

from tests.utils.conftest import REMOTE_RUN_LOCAL_STREAM_URL, REMOTE_RUN_REMOTE_STREAM_URL


def _select_endpoint() -> str:
    """Prefer a responding local stream URL, otherwise use the configured remote."""
    base = REMOTE_RUN_LOCAL_STREAM_URL.rsplit("/runs/", 1)[0]
    try:
        requests.get(f"{base}/ok", timeout=1)
        return REMOTE_RUN_LOCAL_STREAM_URL
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return REMOTE_RUN_REMOTE_STREAM_URL


def _post_alert(
    endpoint: str,
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict[str, Any],
    config_metadata: dict[str, Any] | None = None,
    stream_mode: list[str] | None = None,
    timeout: int = 300,
) -> requests.Response:
    payload = {
        "input": {
            "alert_name": alert_name,
            "pipeline_name": pipeline_name,
            "severity": severity,
            "raw_alert": raw_alert,
        },
        "config": {"metadata": config_metadata or {}},
        "stream_mode": stream_mode or ["values"],
    }

    response = requests.post(endpoint, json=payload, stream=True, timeout=timeout)
    response.raise_for_status()
    return response


def fire_alert_to_remote_run_stream(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict[str, Any],
    config_metadata: dict[str, Any] | None = None,
    stream_mode: list[str] | None = None,
    timeout: int = 300,
) -> requests.Response:
    """POST an alert payload to the configured remote ``/runs/stream`` URL."""
    return _post_alert(
        endpoint=REMOTE_RUN_REMOTE_STREAM_URL,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=raw_alert,
        config_metadata=config_metadata,
        stream_mode=stream_mode,
        timeout=timeout,
    )


def fire_alert_to_run_stream(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict[str, Any],
    config_metadata: dict[str, Any] | None = None,
    stream_mode: list[str] | None = None,
    timeout: int = 300,
) -> requests.Response:
    """POST an alert to local stream URL if reachable, else the remote stream URL."""
    endpoint = _select_endpoint()
    return _post_alert(
        endpoint=endpoint,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=raw_alert,
        config_metadata=config_metadata,
        stream_mode=stream_mode,
        timeout=timeout,
    )


def stream_investigation_results(response: requests.Response) -> None:
    """Stream and print lines from a streaming investigation HTTP response."""
    print("\nStreaming investigation:\n")

    for line in response.iter_lines():
        if line:
            decoded = line.decode("utf-8")
            if decoded.strip():
                print(decoded)
