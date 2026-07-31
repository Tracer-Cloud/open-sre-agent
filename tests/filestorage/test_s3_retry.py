"""S3 backend retry behavior: transient failures recover, permanent ones fail fast.

Uses fake clients only — no cloud account needed. See issue #4555: a dropped
connection or a throttling response must not fail the whole sync run, but a
misconfigured bucket or bad credentials must not spin through retries first.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ReadTimeoutError,
    UnknownEndpointError,
)

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.providers.aws import S3ObjectStore


def _client_error(code: str, operation: str, *, status: HTTPStatus) -> ClientError:
    """A ``ClientError`` shaped like a real S3 response — ``ResponseMetadata`` included.

    ``_is_transient`` indexes ``response["ResponseMetadata"]["HTTPStatusCode"]``
    directly (no ``.get`` default), so a fake missing that key would raise
    ``KeyError`` instead of exercising the classification it is meant to test.
    """
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {
                "HTTPStatusCode": status,
                "RequestId": "test-request-id",
                "HostId": "test-host-id",
                "HTTPHeaders": {},
                "RetryAttempts": 0,
            },
        },
        operation,
    )


class _FlakyThenOk:
    """Fails with a transient error ``fail_times`` times, then succeeds."""

    def __init__(self, fail_times: int, exc: Exception) -> None:
        self._remaining = fail_times
        self._exc = exc
        self.calls = 0

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._exc

    def get_object(self, **_kwargs: object) -> dict[str, object]:
        self._maybe_fail()

        class _Body:
            def read(self) -> bytes:
                return b"payload"

        return {"Body": _Body()}

    def put_object(self, **_kwargs: object) -> None:
        self._maybe_fail()


class _AlwaysFails:
    """Fails with the same error on every call — for permanent errors and exhaustion."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    def get_object(self, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        raise self._exc

    def put_object(self, **_kwargs: object) -> None:
        self.calls += 1
        raise self._exc

    def get_paginator(self, _name: str) -> object:
        raise self._exc


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.filestorage.providers.aws.time.sleep", lambda _s: None)


def test_transient_error_retries_then_succeeds() -> None:
    """A dropped connection is retried, not surfaced as a failure."""
    client = _FlakyThenOk(fail_times=2, exc=EndpointConnectionError(endpoint_url="s3"))
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    body = store.get_object("sessions/a.jsonl")

    assert body == b"payload"
    assert client.calls == 3


def test_transient_error_exhausts_retries_and_raises() -> None:
    """Retries are bounded — a permanently dead endpoint still fails, eventually."""
    client = _AlwaysFails(EndpointConnectionError(endpoint_url="s3"))
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    with pytest.raises(RemoteSyncUnavailableError):
        store.put_object("sessions/a.jsonl", b"data")

    assert client.calls > 1


def test_permanent_error_fails_immediately_without_retry() -> None:
    """Bad credentials are not a fluke — retrying them wastes time to no end."""
    client = _AlwaysFails(_client_error("AccessDenied", "PutObject", status=HTTPStatus.FORBIDDEN))
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    with pytest.raises(RemoteSyncUnavailableError, match="AccessDenied"):
        store.put_object("sessions/a.jsonl", b"data")

    assert client.calls == 1


def test_missing_bucket_fails_immediately_without_retry() -> None:
    client = _AlwaysFails(_client_error("NoSuchBucket", "GetObject", status=HTTPStatus.NOT_FOUND))
    store = S3ObjectStore(RemoteSyncConfig(bucket="missing"), client=client)

    with pytest.raises(RemoteSyncUnavailableError, match="NoSuchBucket"):
        store.get_object("sessions/a.jsonl")

    assert client.calls == 1


def test_throttling_response_is_retried() -> None:
    """A SlowDown response is transient even though it is a ClientError."""
    client = _FlakyThenOk(
        fail_times=1,
        exc=_client_error("SlowDown", "GetObject", status=HTTPStatus.SERVICE_UNAVAILABLE),
    )
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    body = store.get_object("sessions/a.jsonl")

    assert body == b"payload"
    assert client.calls == 2


def test_read_timeout_is_retried_though_not_a_connection_error() -> None:
    """ReadTimeoutError is not a ConnectionError subclass — its own branch must fire."""
    client = _FlakyThenOk(fail_times=1, exc=ReadTimeoutError(endpoint_url="s3"))
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    body = store.get_object("sessions/a.jsonl")

    assert body == b"payload"
    assert client.calls == 2


def test_unknown_endpoint_error_is_retried() -> None:
    client = _FlakyThenOk(
        fail_times=1, exc=UnknownEndpointError(service_name="s3", region_name="us-east-1")
    )
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    body = store.get_object("sessions/a.jsonl")

    assert body == b"payload"
    assert client.calls == 2


def test_unlisted_client_error_code_is_treated_as_permanent() -> None:
    """Anything not on the throttling allowlist, at a non-5xx status, fails fast."""
    client = _AlwaysFails(
        _client_error("ValidationException", "PutObject", status=HTTPStatus.BAD_REQUEST)
    )
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    with pytest.raises(RemoteSyncUnavailableError, match="ValidationException"):
        store.put_object("sessions/a.jsonl", b"data")

    assert client.calls == 1


def test_unlisted_code_with_5xx_status_falls_back_to_transient() -> None:
    """A code that isn't explicitly allowlisted still retries in the 500-504 band."""
    client = _FlakyThenOk(
        fail_times=1,
        exc=_client_error("InternalError", "PutObject", status=HTTPStatus.INTERNAL_SERVER_ERROR),
    )
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    store.put_object("sessions/a.jsonl", b"data")

    assert client.calls == 2


def test_5xx_status_outside_retry_band_is_permanent() -> None:
    """The fallback band is 500-504, not "any 5xx" — 505 must not retry."""
    client = _AlwaysFails(
        _client_error(
            "HTTPVersionNotSupported",
            "PutObject",
            status=HTTPStatus.HTTP_VERSION_NOT_SUPPORTED,
        )
    )
    store = S3ObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    with pytest.raises(RemoteSyncUnavailableError):
        store.put_object("sessions/a.jsonl", b"data")

    assert client.calls == 1
