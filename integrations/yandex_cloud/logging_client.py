"""Reading Cloud Logging.

Two hosts are involved and they are not interchangeable. Log *groups* are
managed over REST on ``logging.api.cloud.yandex.net``. Log *entries* are read
from ``reader.logging.yandexcloud.net``, which speaks gRPC only — it has no
REST gateway and answers plain HTTP with 415. That is why entry reads need the
``yandexcloud`` package while everything else here does not, and why it is an
optional extra: an installation that never reads log entries should not carry
grpc and protobuf.

Reads are capped at five per second by Yandex. The limiter below keeps an
investigation under that on its own rather than discovering it through 429s.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from integrations.yandex_cloud.auth import YandexCloudAuth
from integrations.yandex_cloud.config import YandexCloudIntegrationConfig
from integrations.yandex_cloud.endpoints import resolve_endpoint
from integrations.yandex_cloud.rest_client import YandexCloudClient, sanitize

MANAGEMENT_SERVICE: Final = "logging"
READER_SERVICE: Final = "log-reading"
_READER_FALLBACK_HOST: Final = "reader.logging.yandexcloud.net"
_LOG_GROUPS_PATH: Final = "/logging/v1/logGroups"

#: Yandex caps reads at 5/s. 220ms between calls leaves a little headroom.
_MIN_SECONDS_BETWEEN_READS: Final = 0.22

#: Filter expressions are rejected above this length.
MAX_FILTER_LENGTH: Final = 1000
#: Entries are retained for at most 31 days; older windows return nothing.
MAX_RETENTION_DAYS: Final = 31

DEFAULT_PAGE_SIZE: Final = 100
DEFAULT_WINDOW_MINUTES: Final = 60
_READ_TIMEOUT_SECONDS: Final = 30.0

LEVELS: Final[tuple[str, ...]] = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")

_rate_lock = threading.Lock()
# The last-read timestamp lives on a mutable object rather than a bare module
# global: its value is consumed on the next call, not within _throttle, and a
# reassigned module global reads to static analysis as written-but-never-used.
_throttle_state = {"last_read_at": 0.0}


class LogReadingUnavailableError(RuntimeError):
    """Raised when log entries cannot be read in this installation."""


def _throttle() -> None:
    """Space reads out so a burst of tool calls stays under the rate limit."""
    with _rate_lock:
        wait = _MIN_SECONDS_BETWEEN_READS - (time.monotonic() - _throttle_state["last_read_at"])
        if wait > 0:
            time.sleep(wait)
        _throttle_state["last_read_at"] = time.monotonic()


def resolve_window(since: str, until: str, window_minutes: int) -> tuple[datetime, datetime]:
    """Return the window to read, defaulting to the recent past."""
    end = _parse_moment(until) or datetime.now(UTC)
    start = _parse_moment(since)
    if start is None:
        minutes = window_minutes if window_minutes > 0 else DEFAULT_WINDOW_MINUTES
        start = end - timedelta(minutes=minutes)
    return start, end


def _parse_moment(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def validate_filter(expression: str) -> str | None:
    """Return why *expression* is unusable, or None when it is fine."""
    if len(expression) > MAX_FILTER_LENGTH:
        return (
            f"Filter is {len(expression)} characters; Cloud Logging rejects anything "
            f"over {MAX_FILTER_LENGTH}. Narrow it, or filter on resource_ids instead."
        )
    return None


def retention_warning(start: datetime) -> str:
    """Return a note when the window reaches past what Cloud Logging keeps."""
    horizon = datetime.now(UTC) - timedelta(days=MAX_RETENTION_DAYS)
    if start < horizon:
        return (
            f"The window starts before Cloud Logging's {MAX_RETENTION_DAYS}-day retention, "
            "so early entries will be missing. Anything older lives in the log group's "
            "sink, if one is configured."
        )
    return ""


class YandexLoggingClient:
    """Lists log groups over REST and reads entries over gRPC."""

    def __init__(self, config: YandexCloudIntegrationConfig) -> None:
        self._config = config
        self._rest = YandexCloudClient(config)
        self._auth = YandexCloudAuth(config)

    @property
    def folder_id(self) -> str:
        return self._rest.folder_id

    def list_log_groups(self, page_token: str = "") -> dict[str, Any]:
        """List the folder's log groups."""
        return self._rest.get(
            MANAGEMENT_SERVICE,
            _LOG_GROUPS_PATH,
            {"folderId": self.folder_id},
            page_token=page_token,
        )

    def read_entries(
        self,
        log_group_id: str,
        *,
        since: datetime,
        until: datetime,
        levels: tuple[str, ...] = (),
        filter_expression: str = "",
        resource_types: tuple[str, ...] = (),
        resource_ids: tuple[str, ...] = (),
        page_size: int = DEFAULT_PAGE_SIZE,
        page_token: str = "",
    ) -> dict[str, Any]:
        """Read one page of log entries.

        Raises:
            LogReadingUnavailableError: when the gRPC dependency is missing.
        """
        stub, request = self._build_read_request(
            log_group_id,
            since=since,
            until=until,
            levels=levels,
            filter_expression=filter_expression,
            resource_types=resource_types,
            resource_ids=resource_ids,
            page_size=page_size,
            page_token=page_token,
        )

        import grpc

        _throttle()
        try:
            response = stub.Read(
                request,
                metadata=(("authorization", f"Bearer {self._auth.token()}"),),
                timeout=_READ_TIMEOUT_SECONDS,
            )
        except grpc.RpcError as exc:
            return {
                "success": False,
                "error": _describe_rpc_error(exc, self.folder_id),
                "entries": [],
                "next_page_token": "",
            }

        return {
            "success": True,
            "error": None,
            "entries": [_entry_as_dict(entry) for entry in response.entries],
            "next_page_token": response.next_page_token,
        }

    def _build_read_request(
        self,
        log_group_id: str,
        *,
        since: datetime,
        until: datetime,
        levels: tuple[str, ...],
        filter_expression: str,
        resource_types: tuple[str, ...],
        resource_ids: tuple[str, ...],
        page_size: int,
        page_token: str,
    ) -> tuple[Any, Any]:
        service_pb2, service_grpc, log_entry_pb2 = _load_logging_protos()

        import grpc
        from google.protobuf.timestamp_pb2 import Timestamp

        host = resolve_endpoint(READER_SERVICE) or _READER_FALLBACK_HOST
        channel = grpc.secure_channel(f"{host}:443", grpc.ssl_channel_credentials())
        stub = service_grpc.LogReadingServiceStub(channel)

        since_ts, until_ts = Timestamp(), Timestamp()
        since_ts.FromDatetime(since)
        until_ts.FromDatetime(until)

        level_values = [
            getattr(log_entry_pb2.LogLevel.Level, level) for level in levels if level in LEVELS
        ]
        criteria = service_pb2.Criteria(
            log_group_id=log_group_id,
            since=since_ts,
            until=until_ts,
            levels=level_values,
            filter=filter_expression,
            resource_types=list(resource_types),
            resource_ids=list(resource_ids),
            page_size=max(1, page_size),
        )
        if page_token:
            return stub, service_pb2.ReadRequest(page_token=page_token)
        return stub, service_pb2.ReadRequest(criteria=criteria)


def _load_logging_protos() -> tuple[Any, Any, Any]:
    """Import the generated log-reading stubs, or explain how to install them."""
    try:
        import grpc  # noqa: F401
        from yandex.cloud.logging.v1 import (
            log_entry_pb2,
            log_reading_service_pb2,
            log_reading_service_pb2_grpc,
        )
    except ImportError as exc:
        raise LogReadingUnavailableError(
            "Reading Cloud Logging entries needs the Yandex Cloud gRPC stubs, which "
            "are an optional dependency because nothing else in this integration "
            "uses them. Install with: pip install 'opensre[yandex_cloud_logs]' — or "
            "pip install yandexcloud. Listing log groups and reading metrics work "
            f"without it. ({exc})"
        ) from exc
    return log_reading_service_pb2, log_reading_service_pb2_grpc, log_entry_pb2


def _describe_rpc_error(exc: Any, folder_id: str) -> str:
    import grpc

    code = exc.code() if hasattr(exc, "code") else None
    detail = (exc.details() if hasattr(exc, "details") else str(exc)) or str(exc)
    if code == grpc.StatusCode.PERMISSION_DENIED:
        return (
            f"{detail} The service account cannot read this log group. Grant it with: "
            f"yc resource-manager folder add-access-binding {folder_id} "
            "--role logging.reader --service-account-name <name>"
        )
    if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
        return f"{detail} Cloud Logging allows five reads per second; slow down and retry."
    if code == grpc.StatusCode.NOT_FOUND:
        return f"{detail} Check the log group id with list_yc_log_groups."
    return detail


def _entry_as_dict(entry: Any) -> dict[str, Any]:
    """Return one log entry as plain JSON-safe data."""
    from google.protobuf.json_format import MessageToDict

    level_name = ""
    if entry.level:
        from yandex.cloud.logging.v1 import log_entry_pb2

        level_name = log_entry_pb2.LogLevel.Level.Name(entry.level)

    payload: dict[str, Any] = {
        "uid": entry.uid,
        "level": level_name,
        "message": entry.message,
        "stream_name": entry.stream_name,
        "timestamp": entry.timestamp.ToJsonString() if entry.HasField("timestamp") else "",
    }
    if entry.HasField("resource"):
        payload["resource"] = {
            "type": entry.resource.type,
            "id": entry.resource.id,
        }
    if entry.HasField("json_payload"):
        payload["json_payload"] = MessageToDict(entry.json_payload)
    return dict(sanitize(payload))


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_WINDOW_MINUTES",
    "LEVELS",
    "MANAGEMENT_SERVICE",
    "MAX_FILTER_LENGTH",
    "MAX_RETENTION_DAYS",
    "READER_SERVICE",
    "LogReadingUnavailableError",
    "YandexLoggingClient",
    "resolve_window",
    "retention_warning",
    "validate_filter",
]
