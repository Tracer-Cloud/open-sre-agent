"""Shared helpers for the Amazon Bedrock Converse API (tool schemas and messages)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from anthropic import AnthropicBedrock

from config.constants.aws import (
    BEDROCK_AWS_EXTERNAL_ID_ENV,
    BEDROCK_AWS_PROFILE_ENV,
    BEDROCK_AWS_REGION_ENV,
    BEDROCK_AWS_ROLE_ARN_ENV,
)
from core.llm.shared.tool_schema_normalize import (
    BEDROCK_UNSUPPORTED_SCHEMA_KEYS,
    normalize_object_tool_input_schema,
)

if TYPE_CHECKING:
    import boto3
    import httpx

logger = logging.getLogger(__name__)


def require_aws_region() -> str:
    """Return configured AWS region or raise with a clear configuration error.

    ``BEDROCK_AWS_REGION`` takes precedence so a cross-account Bedrock setup
    (see :func:`resolve_bedrock_aws_session`) can target a region independent
    of the region investigation tools use.
    """
    region = (
        os.getenv(BEDROCK_AWS_REGION_ENV)
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or ""
    ).strip()
    if not region:
        raise RuntimeError(
            "Bedrock requires AWS_REGION, AWS_DEFAULT_REGION, or BEDROCK_AWS_REGION to be set."
        )
    return region


def _assumed_bedrock_role_credentials(region: str) -> dict[str, Any] | None:
    """Raw STS credentials from ``BEDROCK_AWS_ROLE_ARN``, or ``None`` if unset.

    A single ``sts:AssumeRole`` call, same shape as
    ``integrations/aws/verifier.py::_build_sts_client``. Used by
    :class:`_RefreshingBedrockAnthropicClient` to (re-)assume the role on
    construction and again whenever its credentials are near expiry. The
    ``boto3``-backed Bedrock clients (:func:`resolve_bedrock_aws_session`) use
    a separate mechanism, :func:`_refreshable_role_session`, that refreshes
    via botocore's own ``AssumeRoleCredentialFetcher`` instead.
    """
    role_arn = os.getenv(BEDROCK_AWS_ROLE_ARN_ENV, "").strip()
    if not role_arn:
        return None

    import boto3

    external_id = os.getenv(BEDROCK_AWS_EXTERNAL_ID_ENV, "").strip()
    sts = boto3.client("sts", region_name=region)
    assume_role_kwargs: dict[str, str] = {
        "RoleArn": role_arn,
        "RoleSessionName": "OpenSREBedrockInvoke",
    }
    if external_id:
        assume_role_kwargs["ExternalId"] = external_id
    credentials: dict[str, Any] = sts.assume_role(**assume_role_kwargs)["Credentials"]
    return credentials


def _refreshable_role_session(*, role_arn: str, external_id: str, region: str) -> boto3.Session:
    """A ``boto3.Session`` that re-assumes ``role_arn`` itself as credentials near expiry.

    Unlike a one-time ``sts:AssumeRole`` snapshot, this never goes stale for a
    long-lived cached client: ``DeferredRefreshableCredentials`` calls
    ``fetcher.fetch_credentials`` (issuing a fresh ``AssumeRole``) whenever
    boto3 asks for credentials and the cached ones are near or past their
    expiry, the same mechanism boto3 uses internally for a profile configured
    with ``role_arn`` + ``source_profile``. Requires some base ambient
    identity to perform the ``AssumeRole`` call itself (the same requirement
    the one-time snapshot already had); if there is none, the first refresh
    raises ``NoCredentialsError`` when a Bedrock call is actually made.
    """
    import boto3
    import botocore.session
    from botocore.credentials import AssumeRoleCredentialFetcher, DeferredRefreshableCredentials

    extra_args: dict[str, str] = {"RoleSessionName": "OpenSREBedrockInvoke"}
    if external_id:
        extra_args["ExternalId"] = external_id

    botocore_session = botocore.session.Session()
    fetcher = AssumeRoleCredentialFetcher(
        client_creator=botocore_session.create_client,  # type: ignore[arg-type]
        source_credentials=botocore_session.get_credentials(),
        role_arn=role_arn,
        extra_args=extra_args,
    )
    # Pre-seed the lazy credential slot get_credentials() checks before
    # resolving from scratch — the same mechanism botocore itself uses
    # internally for a profile configured with role_arn + source_profile.
    # Not part of the public stub surface, but a real, stable botocore
    # mechanism (see botocore.session.Session.get_credentials).
    botocore_session._credentials = DeferredRefreshableCredentials(  # type: ignore[attr-defined]
        method="assume-role",
        refresh_using=fetcher.fetch_credentials,
    )
    return boto3.Session(botocore_session=botocore_session, region_name=region)


def resolve_bedrock_aws_session(region: str) -> boto3.Session | None:
    """Isolate Bedrock's AWS identity from the ambient default credential chain.

    Investigation tools (EC2, CloudWatch, ...) already work correctly across
    accounts because they read the process's single ambient ``AWS_PROFILE``
    directly. Bedrock had no equivalent: every Bedrock client reached for the
    same ambient chain, so a laptop configured for a Bedrock account reachable
    only via an AssumeRole profile in a different account than the infra being
    investigated had no way to give Bedrock a *different* identity than
    whatever profile was active for everything else (#4482).

    ``region`` is the caller's own already-resolved region (each of the three
    Bedrock clients has a different region-resolution policy — two raise when
    unset, one defaults to ``us-east-1``); this function does not re-derive
    or require it, so it can't reject a config a caller would otherwise
    accept.

    ``BEDROCK_AWS_ROLE_ARN`` takes precedence — an explicit assume-role, for
    deployments with no ``~/.aws/config`` profile file (e.g. Fargate). The
    returned session self-refreshes (see :func:`_refreshable_role_session`),
    so a long-lived cached client built from it never goes stale. This is for
    the ``boto3``-backed Converse clients only; :func:`build_bedrock_anthropic_client`
    covers ``AnthropicBedrock``, which cannot accept a ``boto3.Session`` at all.
    ``BEDROCK_AWS_PROFILE`` is a named profile, which may itself be an
    AssumeRole + source_profile pair — the shape the issue's own setup used;
    boto3 refreshes that automatically too. Returns ``None`` when neither is
    set, so callers fall through to today's exact ambient-chain behavior;
    this function never changes behavior for a caller who hasn't opted in.
    """
    import boto3

    role_arn = os.getenv(BEDROCK_AWS_ROLE_ARN_ENV, "").strip()
    if role_arn:
        external_id = os.getenv(BEDROCK_AWS_EXTERNAL_ID_ENV, "").strip()
        return _refreshable_role_session(role_arn=role_arn, external_id=external_id, region=region)

    profile = os.getenv(BEDROCK_AWS_PROFILE_ENV, "").strip()
    if profile:
        return boto3.Session(profile_name=profile)

    return None


def _parse_expiration(expiration: Any) -> datetime:
    """Normalize an STS ``Expiration`` value to a timezone-aware ``datetime``.

    The real ``boto3`` client always returns a ``datetime`` already (botocore
    parses the API's timestamp shape automatically); a plain ``isoformat()``
    string is accepted too, so a hand-built fake in a test doesn't need a
    real ``datetime`` object.
    """
    if isinstance(expiration, datetime):
        return expiration
    return datetime.fromisoformat(str(expiration))


class _RefreshingBedrockAnthropicClient(AnthropicBedrock):
    """``AnthropicBedrock`` whose ``BEDROCK_AWS_ROLE_ARN`` credentials
    re-assume the role in place once they're near expiry.

    ``AnthropicBedrock`` has no hook for a refreshable credentials provider:
    ``aws_access_key``/``aws_secret_key``/``aws_session_token`` are plain
    instance attributes read fresh from ``self`` by ``_prepare_request``
    (called once per outgoing request, before every ``sts:AssumeRole``-signed
    call), never re-resolved after construction. Overriding
    ``_prepare_request`` to refresh those attributes in place first, before
    delegating to the real implementation, reaches the same effect as a
    refreshable provider would without needing one — the SDK re-reads
    ``self.aws_access_key`` etc. on every call regardless.
    """

    _REFRESH_BUFFER = timedelta(minutes=5)

    def __init__(self, *, region: str, timeout: float | None = None) -> None:
        self._region = region
        self._expiry: datetime | None = None
        self._refresh_lock = threading.Lock()
        credentials = self._assume_and_track_expiry()
        init_kwargs: dict[str, Any] = {
            "aws_access_key": credentials["AccessKeyId"],
            "aws_secret_key": credentials["SecretAccessKey"],
            "aws_session_token": credentials["SessionToken"],
            "aws_region": region,
        }
        if timeout is not None:
            init_kwargs["timeout"] = timeout
        super().__init__(**init_kwargs)

    def _assume_and_track_expiry(self) -> dict[str, Any]:
        credentials = _assumed_bedrock_role_credentials(self._region)
        assert credentials is not None, "BEDROCK_AWS_ROLE_ARN must be set to construct this client"
        expiration = credentials.get("Expiration")
        self._expiry = _parse_expiration(expiration) if expiration is not None else None
        return credentials

    def _refresh_if_needed(self) -> None:
        if self._expiry is None:
            return
        if datetime.now(UTC) < self._expiry - self._REFRESH_BUFFER:
            return
        with self._refresh_lock:
            if self._expiry is not None and datetime.now(UTC) < (
                self._expiry - self._REFRESH_BUFFER
            ):
                return
            credentials = self._assume_and_track_expiry()
            self.aws_access_key = credentials["AccessKeyId"]
            self.aws_secret_key = credentials["SecretAccessKey"]
            self.aws_session_token = credentials["SessionToken"]

    def _prepare_request(self, request: httpx.Request) -> None:
        self._refresh_if_needed()
        super()._prepare_request(request)


def build_bedrock_anthropic_client(
    *, region: str, timeout: float | None = None
) -> AnthropicBedrock:
    """The ``AnthropicBedrock`` client for the resolved Bedrock identity.

    - No override: today's exact ambient-chain client.
    - ``BEDROCK_AWS_PROFILE``: passed straight through as ``aws_profile``, so
      whatever refresh the named profile supports keeps working (a role_arn +
      source_profile pair in ``~/.aws/config`` auto-refreshes; ``AnthropicBedrock``
      resolves and refreshes a profile itself, unlike static credentials).
    - ``BEDROCK_AWS_ROLE_ARN``: a :class:`_RefreshingBedrockAnthropicClient`,
      since ``AnthropicBedrock`` has no other hook for a refreshable provider.

    ``timeout`` is forwarded only when given, so a caller that omits it (as
    ``BedrockLLMClient`` always has) gets the SDK's own default rather than a
    literal ``None`` overriding it to no-timeout.
    """
    role_arn = os.getenv(BEDROCK_AWS_ROLE_ARN_ENV, "").strip()
    if role_arn:
        return _RefreshingBedrockAnthropicClient(region=region, timeout=timeout)

    init_kwargs: dict[str, Any] = {"aws_region": region}
    if timeout is not None:
        init_kwargs["timeout"] = timeout

    profile = os.getenv(BEDROCK_AWS_PROFILE_ENV, "").strip()
    if profile:
        init_kwargs["aws_profile"] = profile

    return AnthropicBedrock(**init_kwargs)


def new_tool_use_id() -> str:
    """Return a short alphanumeric id suitable for Converse ``toolUseId`` fields."""
    return secrets.token_hex(5)


def normalize_tool_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a tool's public input schema for ``toolSpec.inputSchema.json``."""
    return normalize_object_tool_input_schema(
        schema,
        unsupported_keys=BEDROCK_UNSUPPORTED_SCHEMA_KEYS,
    )


def build_converse_tool_specs(tools: list[Any]) -> list[dict[str, Any]]:
    """Build ``toolConfig.tools`` entries from registered tool objects."""
    specs: list[dict[str, Any]] = []
    for tool in tools:
        specs.append(
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {"json": normalize_tool_input_schema(tool.public_input_schema)},
                }
            }
        )
    return specs


def to_converse_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert investigation messages to Converse ``messages`` shape."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            converted.append({"role": message["role"], "content": [{"text": content}]})
        else:
            converted.append(message)
    return converted


def build_assistant_tool_use_message(tool_calls: list[Any]) -> dict[str, Any]:
    """Build a Converse assistant message containing ``toolUse`` blocks."""
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                }
            }
            for tc in tool_calls
        ],
    }


def build_tool_result_message(tool_calls: list[Any], results: list[Any]) -> dict[str, Any]:
    """Build the Converse ``toolResult`` user message for one round of tool calls."""
    content: list[dict[str, Any]] = []
    for tc, result in zip(tool_calls, results, strict=True):
        is_error = isinstance(result, dict) and bool(result.get("error"))
        if isinstance(result, dict):
            sanitized = json.loads(json.dumps(result, default=str))
            result_content: list[dict[str, Any]] = [{"json": sanitized}]
        else:
            result_content = [{"text": json.dumps(result, default=str)}]
        tool_result: dict[str, Any] = {
            "toolUseId": tc.id,
            "content": result_content,
        }
        if is_error:
            tool_result["status"] = "error"
        content.append({"toolResult": tool_result})
    return {"role": "user", "content": content}


def parse_converse_output(
    response: dict[str, Any],
) -> tuple[str, list[tuple[str, str, dict[str, Any]]], str, dict[str, Any]]:
    """Parse a Converse API response into text, tool calls, stop reason, and raw message."""
    output_message = response.get("output", {}).get("message", {})
    if not isinstance(output_message, dict):
        output_message = {"role": "assistant", "content": []}

    text_parts: list[str] = []
    tool_calls: list[tuple[str, str, dict[str, Any]]] = []
    for block in output_message.get("content", []):
        if not isinstance(block, dict):
            continue
        if "text" in block:
            text_parts.append(str(block["text"]))
            continue
        tool_use = block.get("toolUse")
        if not isinstance(tool_use, dict):
            continue
        raw_input = tool_use.get("input")
        tool_calls.append(
            (
                str(tool_use["toolUseId"]),
                str(tool_use["name"]),
                raw_input if isinstance(raw_input, dict) else {},
            )
        )

    stop_reason = str(response.get("stopReason", "end_turn"))
    return "".join(text_parts), tool_calls, stop_reason, output_message


def map_bedrock_client_error(model: str, err: Any) -> RuntimeError:
    """Map a ``botocore`` ``ClientError`` to a user-facing ``RuntimeError``."""
    code = err.response.get("Error", {}).get("Code", "")
    message = err.response.get("Error", {}).get("Message", "") or str(err)

    if code == "ValidationException":
        return RuntimeError(f"Bedrock request rejected (HTTP 400): {message}")
    if code == "ResourceNotFoundException":
        return RuntimeError(
            f"Bedrock model '{model}' was not found in the configured region. "
            "Check the model ID, region, or inference profile."
        )
    if code == "ThrottlingException":
        return RuntimeError(
            f"Bedrock rate limit exceeded for model '{model}'. "
            "Reduce request frequency or request a quota increase."
        )
    if code in ("AccessDeniedException", "UnauthorizedException"):
        err_msg_str = str(message)
        if (
            "INVALID_PAYMENT_INSTRUMENT" in err_msg_str
            or "payment instrument" in err_msg_str.lower()
        ):
            aws_message = err_msg_str.strip().rstrip(".")
            detail = f" Cause: {aws_message}." if aws_message else ""
            return RuntimeError(
                f"Access denied for Bedrock model '{model}'.{detail} "
                "A valid AWS payment instrument is required."
            )
        aws_message = err_msg_str.strip().rstrip(".")
        detail = f" Cause: {aws_message}." if aws_message else ""
        return RuntimeError(
            f"Access denied for Bedrock model '{model}'.{detail} "
            "Check Bedrock model access (per-region opt-in), your "
            "AWS Marketplace subscription / payment method, and "
            "IAM permissions."
        )
    return RuntimeError(f"Bedrock API request failed: {message}")


def is_non_retryable_bedrock_code(code: str) -> bool:
    """Return True when retrying the same request will not help."""
    return code in (
        "ValidationException",
        "ResourceNotFoundException",
        "AccessDeniedException",
        "UnauthorizedException",
    )
