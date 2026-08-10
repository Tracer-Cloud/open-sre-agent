"""Google Cloud credential resolution and API client construction.

Built on the discovery-based ``google-api-python-client`` rather than the
per-service ``google-cloud-*`` libraries. ``logging/v2``, ``monitoring/v3`` and
``cloudresourcemanager/v1`` are all reachable through discovery, so this
integration adds no new dependency to the project — which matters here: the
``google-cloud-aiplatform`` import chain has already caused one production
outage when a partner-model route hard-imported a package that was not
installed.

Imports go through :func:`importlib.import_module` behind a ``cast(Any, ...)``,
matching ``integrations/google_docs/client.py``. The Google libraries ship no
type stubs and ``mypy.ini`` sets ``ignore_missing_imports = False`` globally, so
a plain ``import googleapiclient.discovery`` would fail ``make typecheck``.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any, cast

from config.constants.gcp import GCP_HTTP_TIMEOUT_SECONDS
from integrations.config_models import GCPIntegrationConfig

logger = logging.getLogger(__name__)

#: Single scope covering read access to every API this integration touches.
#: Individual permissions are still enforced by IAM roles on the principal.
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: Discovery API names/versions used by the GCP tools.
LOGGING_API = ("logging", "v2")
MONITORING_API = ("monitoring", "v3")
#: Metrics Scopes live only in v1. ``monitoring/v3`` has no metricsScopes
#: resource at all — the two versions expose disjoint resource sets rather
#: than one superseding the other, so both clients are built.
MONITORING_SCOPE_API = ("monitoring", "v1")
RESOURCE_MANAGER_API = ("cloudresourcemanager", "v1")
CONTAINER_API = ("container", "v1")
COMPUTE_API = ("compute", "v1")
CLOUD_RUN_API = ("run", "v2")
CLOUD_SQL_API = ("sqladmin", "v1")
PUBSUB_API = ("pubsub", "v1")
ERROR_REPORTING_API = ("clouderrorreporting", "v1beta1")


class GCPClientError(RuntimeError):
    """Raised when credentials or an API client cannot be constructed."""


def _load_service_account_info(raw: str) -> dict[str, Any]:
    """Parse a service-account key given as either literal JSON or a file path.

    Operators supply this both ways in practice — a mounted key file path in
    Kubernetes, an inline JSON blob in a secret manager — and guessing wrong
    produces a confusing parse error, so both are accepted explicitly.
    """
    text = raw.strip()
    if not text.startswith("{"):
        try:
            text = Path(text).read_text(encoding="utf-8")
        except OSError as exc:
            raise GCPClientError(
                f"service_account_key points at a file that could not be read: {exc.strerror}"
            ) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # Deliberately no exc.msg and no slice of the input: a malformed value
        # here is very often a real credential pasted into the wrong variable,
        # and JSONDecodeError embeds the offending text.
        raise GCPClientError(
            f"service_account_key is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(parsed, dict):
        raise GCPClientError("service_account_key JSON must be an object")
    return parsed


def resolve_credentials(config: GCPIntegrationConfig) -> Any:
    """Return Google credentials for ``config``.

    Precedence: explicit service-account key, then impersonation layered over
    the ambient credential, then plain Application Default Credentials. On GKE
    with Workload Identity the ADC path needs no configuration at all.
    """
    if config.service_account_key:
        service_account = cast(Any, importlib.import_module("google.oauth2.service_account"))
        base = service_account.Credentials.from_service_account_info(
            _load_service_account_info(config.service_account_key),
            scopes=[CLOUD_PLATFORM_SCOPE],
        )
    else:
        google_auth = cast(Any, importlib.import_module("google.auth"))
        try:
            base, _detected_project = google_auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        except Exception as exc:
            raise GCPClientError(
                "no Google credentials available: set GCP_SERVICE_ACCOUNT_KEY, or run with "
                f"Workload Identity / Application Default Credentials ({type(exc).__name__})"
            ) from exc

    if config.impersonate_service_account:
        impersonated = cast(Any, importlib.import_module("google.auth.impersonated_credentials"))
        return impersonated.Credentials(
            source_credentials=base,
            target_principal=config.impersonate_service_account,
            target_scopes=[CLOUD_PLATFORM_SCOPE],
        )
    return base


def _timed_transport(config: GCPIntegrationConfig) -> Any:
    """Return an authorized ``httplib2`` transport with a socket timeout.

    ``httplib2`` accepts a timeout only at construction — there is no
    per-request override, which is what the "httplib2 transport does not
    support per-request timeout" warning is telling you. Left at the default it
    is *no* timeout at all, so a wedged control plane hangs the caller
    indefinitely. That is survivable on a tool call, where a human eventually
    gives up; it is not survivable on the background refresh loops, where a
    single hung call stops the loop for the life of the process and nothing
    reports it.

    Building the transport ourselves means passing ``http=`` rather than
    ``credentials=`` to ``discovery.build`` — the two are mutually exclusive,
    since an authorized transport already carries the credential.
    """
    httplib2 = cast(Any, importlib.import_module("httplib2"))
    auth_httplib2 = cast(Any, importlib.import_module("google_auth_httplib2"))
    return auth_httplib2.AuthorizedHttp(
        resolve_credentials(config),
        http=httplib2.Http(timeout=GCP_HTTP_TIMEOUT_SECONDS),
    )


def build_service(config: GCPIntegrationConfig, api: tuple[str, str]) -> Any:
    """Build a discovery client for ``api`` (an ``(name, version)`` pair).

    ``cache_discovery=False`` suppresses the oauth2client file-cache warning;
    the discovery document itself is bundled with the library, so this does not
    add a network round-trip.
    """
    name, version = api
    discovery = cast(Any, importlib.import_module("googleapiclient.discovery"))
    try:
        return discovery.build(
            name,
            version,
            http=_timed_transport(config),
            cache_discovery=False,
        )
    except GCPClientError:
        raise
    except Exception as exc:
        raise GCPClientError(
            f"could not build the {name}/{version} client ({type(exc).__name__})"
        ) from exc


#: ``ErrorInfo.reason`` Google sets when the API itself is off in the project,
#: as opposed to the caller lacking a permission on it.
_SERVICE_DISABLED = "SERVICE_DISABLED"


def _error_payload(exc: Exception) -> dict[str, Any]:
    """Decode the JSON error body a ``googleapiclient`` exception carries."""
    content = getattr(exc, "content", None)
    if not isinstance(content, (bytes, bytearray)):
        return {}
    try:
        payload = json.loads(content.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        return {}
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else {}


def api_status(exc: Exception) -> int | None:
    """Return the HTTP status a Google API exception carries, if any.

    ``googleapiclient`` exposes it in two places depending on the version, and
    a transport-level failure carries neither — so this never raises and the
    caller must handle ``None`` rather than reading ``exc.status_code``.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "resp", None)
        status = getattr(response, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def describe_api_error(exc: Exception) -> str:
    """Render a Google API exception as one actionable line.

    Google's own 403 text is kept because it names the exact missing
    permission, which is the single most useful thing an operator can be told.
    Stack traces and payload echoes are dropped. Note that a 403 here does not
    imply the resource exists — Vertex and several other APIs check IAM before
    existence, so "or it may not exist" in Google's message is not a hint.
    """
    status = api_status(exc)

    detail = str(_error_payload(exc).get("message", "")).strip()

    if status and detail:
        return f"HTTP {status}: {detail}"
    if status:
        return f"HTTP {status} from the Google API"
    return f"{type(exc).__name__} calling the Google API"


def api_not_enabled(exc: Exception) -> bool:
    """Whether Google refused because the API is switched off in that project.

    A project that has never enabled a service cannot hold resources of that
    kind, so when a caller sweeps many projects this means "nothing here", not a
    failure — reporting it buries the projects that did fail, and in a tool
    result it spends the model's context describing non-problems.

    It cannot be told from a genuine denial by status code or message: both
    arrive as HTTP 403 ``PERMISSION_DENIED`` and Google's prose for a disabled
    API is localised. Only the machine-readable ``ErrorInfo.reason`` separates
    them. Matching that one value keeps this fail-safe — a real
    ``IAM_PERMISSION_DENIED``, or any reason added later, is not recognised here
    and stays an error for the caller to report.
    """
    details = _error_payload(exc).get("details")
    if not isinstance(details, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("reason") == _SERVICE_DISABLED for entry in details
    )
