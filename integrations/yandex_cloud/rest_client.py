"""Read-only REST access to every Yandex Cloud service.

Yandex generates its REST API from the same protobuf definitions across all
services, which gives the client two properties worth leaning on:

- **Reads are GET, writes are not.** Every ``list``/``get`` is a GET; creating,
  updating and deleting are POST/PATCH/DELETE. Restricting the client to GET is
  therefore a structural read-only guarantee rather than a list of verbs someone
  has to keep current as Yandex ships new services.
- **Paging is uniform.** ``pageSize``/``pageToken`` in, ``nextPageToken`` out,
  everywhere.

The host is always looked up from the endpoint registry by service name, never
built from caller input, so a caller cannot steer a request at an arbitrary
address.
"""

from __future__ import annotations

import re
import threading
import time
from http import HTTPStatus
from typing import Any, Final

import httpx

from integrations.probes import ProbeResult
from integrations.yandex_cloud import metadata
from integrations.yandex_cloud.auth import YandexCloudAuth, YandexCloudAuthError
from integrations.yandex_cloud.config import YandexCloudIntegrationConfig
from integrations.yandex_cloud.endpoints import known_endpoints, resolve_endpoint

#: Longest list a response may carry before the tail is dropped. Investigation
#: payloads are re-serialized into the prompt, so an unbounded list is a context
#: cost as much as a transfer one.
MAX_LIST_ITEMS: Final = 100
#: Single strings are truncated at this length — serial-port dumps and embedded
#: certificates are the usual offenders.
MAX_STRING_LENGTH: Final = 10_000
MAX_DEPTH: Final = 10
DEFAULT_PAGE_SIZE: Final = 100
_VERSION_SEGMENT: Final = re.compile(r"v\d+(?:beta\d*|alpha\d*)?")

REQUEST_TIMEOUT_SECONDS: Final = 30.0
_RETRY_ATTEMPTS: Final = 3
_RETRY_BACKOFF_SECONDS: Final = 1.0

_RESOURCE_MANAGER_SERVICE: Final = "resource-manager"


class YandexCloudRequestError(RuntimeError):
    """Raised when a request is rejected before it is sent."""


_pool: httpx.Client | None = None
_pool_lock = threading.Lock()


def _pooled_client() -> httpx.Client:
    """Return the process-wide client, creating it on first use.

    Yandex Cloud terminates TLS per service host, so without a pool every read
    pays for a fresh handshake. Measured on an instance against the compute API,
    that was about 26 ms per call.
    """
    global _pool

    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    return _pool


def send_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Issue one HTTP request on the shared pooled client.

    A seam as much as a pool: tests replace this rather than reaching into
    ``httpx``, which keeps a fake independent of how the request is sent.
    """
    return _pooled_client().request(method, url, **kwargs)


#: Action suffixes that change something even though Yandex binds them to GET.
#: ``/operations/{id}:cancel`` is the one that exists today: a plain GET there
#: cancels a running operation. Read-only rests on the index carrying no write
#: path, and this is the backstop for when one slips in anyway - the generator
#: also drops mutating verbs, and a test asserts the index stays clean.
_MUTATING_ACTIONS: Final = (":cancel", ":stop", ":start", ":restart", ":delete", ":move")


def _reject_path(path: str) -> str | None:
    """Return why *path* is unusable, or None when it is fine."""
    if not path.startswith("/"):
        return "path must start with '/'"
    if "://" in path:
        return "path must be a path, not a URL"
    if ".." in path:
        return "path must not contain '..'"
    action = path.rsplit("/", 1)[-1]
    lowered = action.lower()
    for mutating in _MUTATING_ACTIONS:
        if lowered.endswith(mutating):
            return f"'{mutating}' changes state, and this integration only reads"
    return None


def reject_path(path: str) -> str | None:
    """Return why *path* may not be read, or None when it is fine.

    Public so a caller that never reaches the client - the synthetic backend
    path - can apply the same rule.
    """
    return _reject_path(path)


def is_collection_path(path: str) -> bool:
    """Whether *path* reads a collection rather than one named resource.

    Callers that build a query generically need this because Yandex Cloud
    rejects ``folderId`` and ``pageSize`` on a single-resource read with a bare
    ``404`` — no message, nothing that reads like a rejected parameter. Adding
    either one to ``GET /compute/v1/instances/{id}`` makes the instance look
    like it does not exist.

    The REST convention decides it: after the ``/v1/`` segment, an odd number of
    segments is a collection and an even number names a resource inside one.
    ``clusters`` (1) and ``clusters/{id}/hosts`` (3) are collections;
    ``clusters/{id}`` (2) and ``clusters/{id}/hosts/{name}`` (4) are not. An
    action suffix such as ``instances/{id}:serialPortOutput`` stays even, which
    is correct — it reads one instance.
    """
    segments = [segment for segment in path.split("/") if segment]
    for index, segment in enumerate(segments):
        if _VERSION_SEGMENT.fullmatch(segment):
            return len(segments[index + 1 :]) % 2 == 1
    return False


def is_folder_scoped_collection(path: str) -> bool:
    """Whether a folder is what scopes this collection, rather than a parent resource.

    ``/compute/v1/instances`` lists everything in a folder, so it needs one.
    ``/compute/v1/instances/{id}/operations`` is already scoped by the instance
    named in the path, and Yandex answers a stray ``folderId`` there with the
    same bare ``404`` it gives a single-resource read — so the operations of a
    perfectly healthy instance come back as if the instance did not exist.

    The distinction is one segment deep: a collection directly under the version
    is folder-scoped, anything nested beneath a named resource is not.
    """
    segments = [segment for segment in path.split("/") if segment]
    for index, segment in enumerate(segments):
        if _VERSION_SEGMENT.fullmatch(segment):
            return len(segments[index + 1 :]) == 1
    return False


def accepts_folder_scope(service: str) -> bool:
    """Whether *service* scopes its collections by ``folderId``.

    Almost all of them do. Resource Manager is the exception, and not by
    accident: it manages folders and clouds themselves, so a folder cannot scope
    it. Its collections take ``cloudId`` or nothing, and answer a stray
    ``folderId`` with the same bare ``404`` that a single-resource read gives.
    """
    return service != _RESOURCE_MANAGER_SERVICE


def sanitize(value: Any, depth: int = 0) -> Any:
    """Return *value* trimmed to something safe to hand back to the agent."""
    if depth > MAX_DEPTH:
        return "... (max depth reached)"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            return f"{value[:MAX_STRING_LENGTH]}... ({len(value) - MAX_STRING_LENGTH} more chars)"
        return value
    if isinstance(value, dict):
        return {key: sanitize(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple):
        if len(value) > MAX_LIST_ITEMS:
            head = [sanitize(item, depth + 1) for item in value[:MAX_LIST_ITEMS]]
            head.append(f"... ({len(value) - MAX_LIST_ITEMS} more items truncated)")
            return head
        return [sanitize(item, depth + 1) for item in value]
    return str(value)


def _error_detail(response: httpx.Response) -> tuple[str, str]:
    """Return a readable message and Yandex's error code for a failed response."""
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:300], ""
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("error") or "").strip()
        code = str(payload.get("code", "")).strip()
        if message:
            return message, code
    return response.text.strip()[:300], ""


def _permission_hint(status: int, folder_id: str) -> str:
    if status == HTTPStatus.UNAUTHORIZED:
        return " The IAM token was rejected — the credential may have expired or been revoked."
    if status == HTTPStatus.FORBIDDEN:
        return (
            f" The service account has no read access to folder {folder_id}. Grant it with: "
            f"yc resource-manager folder add-access-binding {folder_id} "
            "--role viewer --service-account-name <name>"
        )
    return ""


class YandexCloudClient:
    """Issues read-only requests against any Yandex Cloud service."""

    def __init__(self, config: YandexCloudIntegrationConfig) -> None:
        self._config = config
        self._auth = YandexCloudAuth(config)
        self._resolved_folder_id = config.folder_id

    @property
    def config(self) -> YandexCloudIntegrationConfig:
        return self._config

    @property
    def auth(self) -> YandexCloudAuth:
        return self._auth

    @property
    def folder_id(self) -> str:
        """Return the folder to scope reads to.

        An instance running inside Yandex Cloud knows which folder it is in, so
        when none was configured the metadata service is asked once. Callers
        should use this rather than ``config.folder_id``, which is only what the
        user typed.
        """
        if not self._resolved_folder_id and self._config.folder_id_is_discoverable:
            self._resolved_folder_id = metadata.fetch_folder_id() or ""
        return self._resolved_folder_id

    @property
    def cloud_id(self) -> str:
        """Return the cloud, discovered from the instance when not configured."""
        if self._config.cloud_id:
            return self._config.cloud_id
        if self._config.folder_id_is_discoverable:
            return metadata.fetch_cloud_id() or ""
        return ""

    def get(
        self,
        service: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        page_token: str = "",
        page_size: int | None = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Read one page from *service* at *path*.

        Returns the standard envelope: ``success``, ``service``, ``path``,
        ``data``, ``error`` and ``metadata``. Errors are reported in the
        envelope rather than raised, so a failed call is something the agent can
        reason about instead of an exception that ends the investigation.
        """
        host = resolve_endpoint(service)
        if host is None:
            return self._failure(
                service,
                path,
                f"Unknown Yandex Cloud service '{service}'. Call find_yc_api "
                "to see what is available.",
                metadata={"unknown_service": True},
            )
        rejected = _reject_path(path)
        if rejected is not None:
            return self._failure(service, path, f"Invalid path: {rejected}.")

        query: dict[str, Any] = dict(params or {})
        if page_size is not None:
            query.setdefault("pageSize", page_size)
        if page_token:
            query["pageToken"] = page_token

        return self._request(service, f"https://{host}{path}", path, query)

    def post(
        self,
        service: str,
        path: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read via POST, for the few APIs whose reads take a request body.

        Monitoring's metric read and Cloud Logging's reader are the two that
        need this. It is not reachable from the generic tool - callers are the
        curated per-service tools in this package, which name the exact endpoint
        they talk to, so read-only stays a property of the client.
        """
        host = resolve_endpoint(service)
        if host is None:
            return self._failure(service, path, f"Unknown Yandex Cloud service '{service}'.")
        rejected = _reject_path(path)
        if rejected is not None:
            return self._failure(service, path, f"Invalid path: {rejected}.")
        return self._request(
            service, f"https://{host}{path}", path, dict(params or {}), body=body, method="POST"
        )

    def _request(
        self,
        service: str,
        url: str,
        path: str,
        params: dict[str, Any],
        *,
        body: dict[str, Any] | None = None,
        method: str = "GET",
    ) -> dict[str, Any]:
        last_error = ""
        status = 0
        code = ""
        request_id = ""

        for attempt in range(_RETRY_ATTEMPTS):
            try:
                headers = self._auth.auth_headers()
            except YandexCloudAuthError as exc:
                return self._failure(service, path, str(exc), metadata={"error_type": "auth"})

            try:
                response = send_request(
                    method,
                    url,
                    params=params,
                    json=body,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                last_error = f"Request to {service} failed: {exc}"
                if attempt + 1 < _RETRY_ATTEMPTS:
                    time.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
                    continue
                return self._failure(
                    service, path, last_error, metadata={"error_type": "transport"}
                )

            status = response.status_code
            request_id = response.headers.get("x-request-id", "")

            if status == HTTPStatus.OK:
                return self._success(service, path, response, request_id)

            message, code = _error_detail(response)

            if status == HTTPStatus.UNAUTHORIZED and attempt + 1 < _RETRY_ATTEMPTS:
                # The token may have been revoked mid-run; mint a new one once.
                self._auth.invalidate()
                continue

            if status == HTTPStatus.TOO_MANY_REQUESTS and attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(self._retry_delay(response, attempt))
                continue

            if status >= HTTPStatus.INTERNAL_SERVER_ERROR and attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
                continue

            hint = _permission_hint(status, self.folder_id)
            return self._failure(
                service,
                path,
                f"{service} returned {status}: {message}{hint}",
                metadata={
                    "status_code": status,
                    "error_code": code,
                    "request_id": request_id,
                },
            )

        return self._failure(
            service,
            path,
            last_error or f"{service} returned {status} after {_RETRY_ATTEMPTS} attempts.",
            metadata={"status_code": status, "error_code": code, "request_id": request_id},
        )

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "").strip()
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return float(_RETRY_BACKOFF_SECONDS * (2**attempt))

    @staticmethod
    def _success(
        service: str, path: str, response: httpx.Response, request_id: str
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}
        next_page_token = ""
        if isinstance(payload, dict):
            next_page_token = str(payload.get("nextPageToken", "") or "")
        return {
            "success": True,
            "service": service,
            "path": path,
            "data": sanitize(payload),
            "error": None,
            "metadata": {
                "status_code": response.status_code,
                "request_id": request_id,
                "next_page_token": next_page_token,
            },
        }

    @staticmethod
    def _failure(
        service: str, path: str, error: str, *, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "success": False,
            "service": service,
            "path": path,
            "data": None,
            "error": error,
            "metadata": metadata or {},
        }

    def probe_access(self) -> ProbeResult:
        """Check that the credentials mint a token and can read the folder."""
        if not self._config.is_configured:
            return ProbeResult.missing("Missing folder_id or a Yandex Cloud credential.")
        try:
            self._auth.token()
        except YandexCloudAuthError as exc:
            return ProbeResult.failed(str(exc))

        folder_id = self.folder_id
        if not folder_id:
            return ProbeResult.failed(
                "No folder is configured and the instance metadata service did not "
                "report one. This mode only works on a Yandex Cloud VM, function, or "
                "container; elsewhere, set YC_FOLDER_ID."
            )
        response = self.get(
            _RESOURCE_MANAGER_SERVICE,
            f"/resource-manager/v1/folders/{folder_id}",
            page_size=None,
        )
        if not response["success"]:
            return ProbeResult.failed(str(response["error"]))

        data = response["data"] if isinstance(response["data"], dict) else {}
        name = str(data.get("name", "")).strip() or folder_id
        services = len(known_endpoints(refresh=False))
        return ProbeResult.passed(
            f"Connected to Yandex Cloud as {self._config.auth_mode}; "
            f"folder '{name}' ({folder_id}) is readable across {services} service endpoints.",
            folder_id=folder_id,
            auth_mode=self._config.auth_mode or "",
        )


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "accepts_folder_scope",
    "is_collection_path",
    "reject_path",
    "is_folder_scoped_collection",
    "MAX_LIST_ITEMS",
    "MAX_STRING_LENGTH",
    "YandexCloudClient",
    "YandexCloudRequestError",
    "sanitize",
]
