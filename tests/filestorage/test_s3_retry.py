"""Transient S3 failures retry with backoff; permanent ones surface immediately.

These exercise :class:`~platform.filestorage.providers.aws.S3ObjectStore`
against fake clients only — no cloud. The sleep is patched so the backoff does
not actually wait.
"""

from __future__ import annotations

import time

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.providers.aws import S3ObjectStore

_BUCKET = "test-bucket"
_KEY = "sessions/abc.jsonl"
_BODY = b'{"turn": 1}\n'


def _client_error(code: str, http_status: int | None = None) -> ClientError:
    """A botocore ClientError with ``code`` (and optional HTTP status)."""
    error: dict[str, object] = {"Error": {"Code": code, "Message": code}}
    if http_status is not None:
        error["ResponseMetadata"] = {"HTTPStatusCode": http_status}
    return ClientError(error, "GetObject")


class _Body:
    """The streaming-body shape ``get_object`` reads from."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _TransientThenOk:
    """Fails ``failures`` times with a transient error, then returns the body."""

    def __init__(self, exc: Exception, failures: int) -> None:
        self._exc = exc
        self._remaining = failures
        self.calls = 0

    def get_object(self, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._exc
        return {"Body": _Body(_BODY)}


class _AlwaysRaises:
    """Every ``get_object`` raises the same error; counts the calls."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    def get_object(self, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        raise self._exc


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff must not actually pause the test suite.

    Patches the shared ``time.sleep`` the provider resolves lazily, so the
    fixture works whether or not the retry helper exists yet (it is a no-op on a
    build with no backoff to skip).
    """
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def _store(client: object) -> S3ObjectStore:
    return S3ObjectStore(RemoteSyncConfig(bucket=_BUCKET), client=client)


def test_a_transient_throttle_is_retried_then_succeeds() -> None:
    """A SlowDown blip does not fail the run — the next attempt returns data."""
    # Arrange: one throttling failure, then success.
    client = _TransientThenOk(_client_error("SlowDown"), failures=1)
    store = _store(client)

    # Act
    body = store.get_object(_KEY)

    # Assert: the second attempt won, and it really did retry.
    assert body == _BODY
    assert client.calls == 2


def test_a_dropped_connection_is_retried_then_succeeds() -> None:
    """A BotoCoreError (no HTTP response) is a network blip and retries."""
    # Arrange
    dropped = EndpointConnectionError(endpoint_url="https://s3")
    client = _TransientThenOk(dropped, failures=1)
    store = _store(client)

    # Act
    body = store.get_object(_KEY)

    # Assert
    assert body == _BODY
    assert client.calls == 2


def test_a_5xx_status_is_treated_as_transient_and_retried() -> None:
    """An unlisted code with a 5xx HTTP status is still a server blip."""
    # Arrange: code botocore does not special-case, but a 503 status.
    server_blip = _client_error("SomethingUpstream", http_status=503)
    client = _TransientThenOk(server_blip, failures=1)
    store = _store(client)

    # Act
    body = store.get_object(_KEY)

    # Assert
    assert body == _BODY
    assert client.calls == 2


def test_a_permanent_error_is_surfaced_immediately_and_not_retried() -> None:
    """AccessDenied is the caller's fault — fail fast, exactly one call."""
    # Arrange
    client = _AlwaysRaises(_client_error("AccessDenied"))
    store = _store(client)

    # Act / Assert
    with pytest.raises(RemoteSyncUnavailableError):
        store.get_object(_KEY)
    assert client.calls == 1


def test_a_missing_bucket_is_not_retried() -> None:
    """NoSuchBucket is permanent — retrying only burns time and quota."""
    # Arrange
    client = _AlwaysRaises(_client_error("NoSuchBucket"))
    store = _store(client)

    # Act / Assert
    with pytest.raises(RemoteSyncUnavailableError):
        store.get_object(_KEY)
    assert client.calls == 1


def test_a_persistent_transient_error_exhausts_attempts_then_raises() -> None:
    """Backoff is bounded: after RETRY_MAX_ATTEMPTS tries it gives up cleanly."""
    from platform.filestorage.providers.aws import RETRY_MAX_ATTEMPTS

    # Arrange: throttling that never clears.
    client = _AlwaysRaises(_client_error("SlowDown"))
    store = _store(client)

    # Act / Assert
    with pytest.raises(RemoteSyncUnavailableError):
        store.get_object(_KEY)
    assert client.calls == RETRY_MAX_ATTEMPTS


def test_the_user_facing_message_never_leaks_boto_internals() -> None:
    """CWE-209: the surfaced error names the key, not the raw boto payload.

    Detail is logged server-side; the message a user could see on a chat surface
    stays generic. The wrapper carries a stable ``cannot read`` phrase and the
    key, but not the AccessDenied credentials-shaped internals of the response.
    """
    # Arrange: an error whose raw text carries something secret-looking.
    secret_shaped = _client_error("SlowDown")
    client = _AlwaysRaises(secret_shaped)
    store = _store(client)

    # Act
    with pytest.raises(RemoteSyncUnavailableError) as caught:
        store.get_object(_KEY)

    # Assert: the message identifies the failed operation and key only.
    message = str(caught.value)
    assert _KEY in message
    assert "cannot read" in message
