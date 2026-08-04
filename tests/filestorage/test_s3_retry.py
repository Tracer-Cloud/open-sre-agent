"""Transient S3 failures retry with backoff; permanent ones surface immediately.

These exercise :class:`~platform.filestorage.providers.aws.S3ObjectStore`
against fake clients only — no cloud. The sleep is patched so the backoff does
not actually wait.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
)

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.providers.aws import S3ObjectStore

if TYPE_CHECKING:
    from collections.abc import Iterator

_BUCKET = "test-bucket"
_KEY = "sessions/abc.jsonl"
_BODY = b'{"turn": 1}\n'
# RemoteSyncConfig's default prefix; a listing returns keys under "<prefix>/".
_PREFIX = "opensre"
_LISTED_AT = datetime(2026, 1, 1, tzinfo=UTC)


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


def _page(*keys: str) -> dict[str, object]:
    """One ``list_objects_v2`` page carrying ``keys`` under the default prefix."""
    return {
        "Contents": [
            {
                "Key": f"{_PREFIX}/{k}",
                "Size": len(k),
                "LastModified": _LISTED_AT,
                "ETag": f'"{k}"',
            }
            for k in keys
        ]
    }


class _ListingClient:
    """A fake S3 client that serves ``list_objects_v2`` pagination.

    Acts as its own paginator. A transient ``blip`` raised on the first pass
    (after the first page) forces the retry wrapper to re-run pagination from
    the top, since the real S3 paginator cannot resume mid-listing.
    """

    def __init__(self, pages: list[dict[str, object]], *, blip: Exception | None = None) -> None:
        self._pages = pages
        self._blip = blip
        self.passes = 0

    def get_paginator(self, _name: str) -> _ListingClient:
        return self

    def paginate(self, **_kwargs: object) -> Iterator[dict[str, object]]:
        self.passes += 1
        first_pass = self.passes == 1
        for index, page in enumerate(self._pages):
            if first_pass and self._blip is not None and index == 1:
                raise self._blip
            yield page


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


def test_missing_credentials_are_not_retried() -> None:
    """NoCredentialsError is a misconfiguration — fail fast, exactly one call.

    It subclasses BotoCoreError, which is otherwise treated as a transient
    network blip; without the permanent-botocore guard it would be retried
    four times with backoff before surfacing the same error.
    """
    # Arrange
    client = _AlwaysRaises(NoCredentialsError())
    store = _store(client)

    # Act / Assert
    with pytest.raises(RemoteSyncUnavailableError):
        store.get_object(_KEY)
    assert client.calls == 1


def test_partial_credentials_are_not_retried() -> None:
    """PartialCredentialsError (e.g. key id but no secret) is also permanent."""
    # Arrange
    incomplete = PartialCredentialsError(provider="env", cred_var="aws_secret_access_key")
    client = _AlwaysRaises(incomplete)
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


def test_the_user_facing_message_names_the_key_and_operation() -> None:
    """The surfaced error identifies the failed operation and key.

    Sync runs on local surfaces, so :class:`RemoteSyncUnavailableError`
    intentionally carries the AWS cause for the operator (pinned by
    ``test_aws_failures_name_their_cause``). CWE-209 redaction of that detail
    happens at the *external* gateway sink boundary, not in this exception, so
    the local message legitimately includes the boto reason.
    """
    # Arrange
    client = _AlwaysRaises(_client_error("SlowDown"))
    store = _store(client)

    # Act
    with pytest.raises(RemoteSyncUnavailableError) as caught:
        store.get_object(_KEY)

    # Assert: the message identifies the failed operation and key.
    message = str(caught.value)
    assert _KEY in message
    assert "cannot read" in message


def test_the_surfaced_message_excludes_the_raw_response_payload() -> None:
    """The local message carries the curated boto reason, not the raw payload.

    ``_reason`` renders only the ClientError Code and Message. Secret-shaped
    values a real S3 response can carry elsewhere (request ids, response
    headers) must never be spilled into the surfaced message by a future change
    that interpolates ``exc.response`` wholesale — defence in depth for CWE-209
    even though the local surface is allowed the curated reason.
    """
    # Arrange: a permanent secret marker planted in the response metadata.
    secret = "AKIAIOSFODNN7EXAMPLE/wJalrXUtnFEMI-should-not-leak"
    error: dict[str, object] = {
        "Error": {"Code": "AccessDenied", "Message": "AccessDenied"},
        "ResponseMetadata": {"RequestId": secret, "HTTPHeaders": {"x-amz-id-2": secret}},
    }
    client = _AlwaysRaises(ClientError(error, "GetObject"))
    store = _store(client)

    # Act
    with pytest.raises(RemoteSyncUnavailableError) as caught:
        store.get_object(_KEY)

    # Assert: curated detail is present, the raw response payload is not.
    message = str(caught.value)
    assert _KEY in message
    assert "AccessDenied" in message
    assert secret not in message


def test_list_objects_aggregates_keys_across_pages() -> None:
    """A multi-page listing returns every object with the prefix stripped."""
    # Arrange: two pages of keys under the default prefix.
    client = _ListingClient([_page("a.jsonl", "b.jsonl"), _page("c.jsonl")])
    store = _store(client)

    # Act
    objects = store.list_objects("")

    # Assert: all keys aggregated in order, listed exactly once.
    assert [o.key for o in objects] == ["a.jsonl", "b.jsonl", "c.jsonl"]
    assert client.passes == 1


def test_a_transient_blip_during_listing_retries_the_whole_listing() -> None:
    """A throttle partway through pagination retries from the top, not partial."""
    # Arrange: the first pass throttles after page one; the retry pass is clean.
    client = _ListingClient(
        [_page("a.jsonl", "b.jsonl"), _page("c.jsonl")],
        blip=_client_error("SlowDown"),
    )
    store = _store(client)

    # Act
    objects = store.list_objects("")

    # Assert: the retry returned the complete listing — nothing dropped or doubled.
    assert [o.key for o in objects] == ["a.jsonl", "b.jsonl", "c.jsonl"]
    assert client.passes == 2
