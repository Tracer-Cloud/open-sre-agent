"""Schema definitions for Hermes incident-identification synthetic fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NotRequired

from typing_extensions import TypedDict

from app.types.root_cause_categories import VALID_ROOT_CAUSE_CATEGORIES

VALID_HERMES_FAILURE_MODES = frozenset(
    {
        "healthy",
        # Part 1/5
        "provider_empty_response",
        "provider_http_400",
        "provider_overload_529",
        "provider_imds_override",
        "provider_headers_dropped",
        "sse_line_overflow",
        # Part 2/5
        "agent_state_corruption",
        "agent_hang",
        "delivery_hang",
        "performance_degradation",
        "ghost_session",
    }
)

VALID_HERMES_EVIDENCE_SOURCES = frozenset(
    {
        "hermes_session_log",
        "hermes_provider_traffic",
        "hermes_config",
        "hermes_runtime_state",
        "hermes_message_history",
        "hermes_kv_cache_state",
        "hermes_cron_state",
        "hermes_session_topology",
    }
)

VALID_HERMES_TRAJECTORY_ACTIONS = frozenset(
    {
        "get_hermes_session_log",
        "get_hermes_provider_traffic",
        "get_hermes_config",
        "get_hermes_message_history",
        "get_hermes_kv_cache_state",
        "get_hermes_runtime_state",
        "get_hermes_cron_state",
        "get_hermes_session_topology",
        "get_hermes_logs",
    }
)


class HermesAlertLabels(TypedDict, total=False):
    alertname: str
    severity: str
    pipeline_name: str
    service: str


class HermesAlertAnnotations(TypedDict, total=False):
    summary: str
    description: str
    context_sources: str
    hermes_session_id: str
    hermes_provider: str
    hermes_model: str
    failure_mode: str


class HermesAlertFixture(TypedDict):
    title: str
    state: str
    alert_source: str
    commonLabels: HermesAlertLabels
    commonAnnotations: HermesAlertAnnotations


class HermesSessionEvent(TypedDict, total=False):
    ts: str
    kind: str
    payload: dict[str, Any]


class HermesSessionLogFixture(TypedDict):
    session_id: str
    events: list[HermesSessionEvent]


class HermesMessageEntry(TypedDict):
    role: str
    content: str | dict[str, Any]
    tool_call_id: str | None
    ts: str


class HermesMessageHistorySnapshots(TypedDict):
    pre_compression: list[HermesMessageEntry]
    post_compression: list[HermesMessageEntry]


class HermesMessageHistoryFixture(TypedDict):
    session_id: str
    messages: list[HermesMessageEntry]
    snapshots: NotRequired[HermesMessageHistorySnapshots]


class HermesCacheMissEntry(TypedDict):
    message_index: int
    expected_prefix_bytes: int
    actual_prefix_bytes: int
    diff_kind: str


class HermesKVCacheStateFixture(TypedDict):
    session_id: str
    cache_hits: int
    cache_misses: int
    last_cached_prefix_bytes: int
    last_invalidated_reason: str
    messages_with_cache_miss: list[HermesCacheMissEntry]


class HermesBlockingCall(TypedDict):
    tool_name: str
    started_at: str
    duration_s: int


class HermesRuntimeStateFixture(TypedDict):
    pid: int
    started_at: str
    frozen_now_ts: str
    interrupt_queue_depth: int
    last_progress_ts: str
    is_blocked: bool
    blocking_call: HermesBlockingCall | None
    imds_fingerprint: NotRequired[dict[str, Any] | None]
    resolved_aws_role_arn: NotRequired[str | None]


class HermesCronLastRun(TypedDict):
    started_at: str
    agent_completed_at: str | None
    delivery_started_at: str | None
    delivery_completed_at: str | None
    delivery_status: str


class HermesCronStateFixture(TypedDict):
    schedule_cron: str
    last_run: HermesCronLastRun


class HermesSessionNode(TypedDict):
    session_id: str
    parent_session_id: str | None
    continuation_of: str | None
    last_message_ts: str
    message_count: int


class HermesSessionTopologyFixture(TypedDict):
    visible_session_id: str
    all_sessions: list[HermesSessionNode]


class HermesScenarioAnswerKeySchema(TypedDict):
    root_cause_category: str
    required_keywords: list[str]
    model_response: str
    forbidden_categories: NotRequired[list[str]]
    forbidden_keywords: NotRequired[list[str]]
    required_evidence_sources: NotRequired[list[str]]
    optimal_trajectory: NotRequired[list[str]]
    max_investigation_loops: NotRequired[int]


class HermesScenarioMetadataSchema(TypedDict):
    schema_version: str
    scenario_id: str
    failure_mode: str
    severity: str
    available_evidence: list[str]
    scenario_difficulty: NotRequired[int]


@dataclass(frozen=True)
class HermesScenarioEvidence:
    hermes_session_log: HermesSessionLogFixture | None
    hermes_provider_traffic: dict[str, Any] | None
    hermes_config: dict[str, Any] | None
    hermes_runtime_state: HermesRuntimeStateFixture | None
    hermes_message_history: HermesMessageHistoryFixture | None
    hermes_kv_cache_state: HermesKVCacheStateFixture | None
    hermes_cron_state: HermesCronStateFixture | None
    hermes_session_topology: HermesSessionTopologyFixture | None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if value is not None:
                result[key] = value
        return result


def validate_hermes_alert(data: dict[str, Any]) -> HermesAlertFixture:
    _require_str(data, "title", "alert.json")
    _require_str(data, "state", "alert.json")
    _require_str(data, "alert_source", "alert.json")
    if not isinstance(data.get("commonLabels"), dict):
        raise ValueError("alert.json: 'commonLabels' must be an object")
    if not isinstance(data.get("commonAnnotations"), dict):
        raise ValueError("alert.json: 'commonAnnotations' must be an object")
    return data  # type: ignore[return-value]


def validate_hermes_session_log(data: dict[str, Any]) -> HermesSessionLogFixture:
    ctx = "hermes_session_log.json"
    _require_str(data, "session_id", ctx)
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError(f"{ctx}: 'events' must be a list")
    for index, event in enumerate(events):
        ectx = f"{ctx}:events[{index}]"
        _require_str(event, "ts", ectx)
        _require_str(event, "kind", ectx)
        if not isinstance(event.get("payload"), dict):
            raise ValueError(f"{ectx}: 'payload' must be an object")
    return data  # type: ignore[return-value]


def validate_hermes_provider_traffic(data: dict[str, Any]) -> dict[str, Any]:
    ctx = "hermes_provider_traffic.json"
    calls = data.get("calls")
    if not isinstance(calls, list):
        raise ValueError(f"{ctx}: 'calls' must be a list")

    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            raise ValueError(f"{ctx}:calls[{index}] must be an object")

        cctx = f"{ctx}:calls[{index}]"
        for field in ("ts", "provider", "model", "endpoint"):
            _require_str(call, field, cctx)

        request = call.get("request")
        if not isinstance(request, dict):
            raise ValueError(f"{cctx}: 'request' must be an object")
        for field in ("method", "url"):
            _require_str(request, field, f"{cctx}:request")

        if not isinstance(request.get("body_sample"), str):
            raise ValueError(f"{cctx}:request: 'body_sample' must be a string")
        if not isinstance(request.get("headers"), dict):
            raise ValueError(f"{cctx}:request: 'headers' must be an object")

        response = call.get("response")
        if not isinstance(response, dict):
            raise ValueError(f"{cctx}: 'response' must be an object")
        if not isinstance(response.get("status"), int):
            raise ValueError(f"{cctx}:response: 'status' must be an integer")
        if not isinstance(response.get("headers"), dict):
            raise ValueError(f"{cctx}:response: 'headers' must be an object")
        if not isinstance(response.get("body_sample"), str):
            raise ValueError(f"{cctx}:response: 'body_sample' must be a string")
        if not isinstance(response.get("duration_ms"), int):
            raise ValueError(f"{cctx}:response: 'duration_ms' must be an integer")

    return data


def validate_hermes_config(data: dict[str, Any]) -> dict[str, Any]:
    ctx = "hermes_config.json"
    for field in ("provider", "model", "region"):
        _require_str(data, field, ctx)

    providers = data.get("providers")
    if not isinstance(providers, list):
        raise ValueError(f"{ctx}: 'providers' must be a list")
    for index, provider in enumerate(providers):
        if not isinstance(provider, dict):
            raise ValueError(f"{ctx}:providers[{index}] must be an object")
        pctx = f"{ctx}:providers[{index}]"
        for field in ("name", "base_url", "auth_kind"):
            _require_str(provider, field, pctx)

    transport = data.get("transport")
    if not isinstance(transport, dict):
        raise ValueError(f"{ctx}: 'transport' must be an object")
    if not isinstance(transport.get("sse_max_line_bytes"), int):
        raise ValueError(f"{ctx}:transport: 'sse_max_line_bytes' must be an integer")
    if not isinstance(transport.get("request_timeout_s"), int):
        raise ValueError(f"{ctx}:transport: 'request_timeout_s' must be an integer")

    return data


def validate_hermes_message_history(data: dict[str, Any]) -> HermesMessageHistoryFixture:
    ctx = "hermes_message_history.json"
    _require_str(data, "session_id", ctx)
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise ValueError(f"{ctx}: 'messages' must be a list")
    _validate_hermes_messages(messages, f"{ctx}:messages")

    snapshots = data.get("snapshots")
    if snapshots is not None:
        if not isinstance(snapshots, dict):
            raise ValueError(f"{ctx}: 'snapshots' must be an object")
        pre_compression = snapshots.get("pre_compression")
        post_compression = snapshots.get("post_compression")
        if not isinstance(pre_compression, list) or not isinstance(post_compression, list):
            raise ValueError(
                f"{ctx}: 'snapshots.pre_compression' and 'snapshots.post_compression' must be lists"
            )
        _validate_hermes_messages(pre_compression, f"{ctx}:snapshots.pre_compression")
        _validate_hermes_messages(post_compression, f"{ctx}:snapshots.post_compression")
    return data  # type: ignore[return-value]


def _validate_hermes_messages(messages: list[Any], ctx: str) -> None:
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"{ctx}[{index}]: message must be an object")
        mctx = f"{ctx}[{index}]"
        role = str(message.get("role", "")).strip()
        if role not in {"system", "user", "assistant", "tool_call", "tool"}:
            raise ValueError(f"{mctx}: invalid role {role!r}")
        if not isinstance(message.get("content"), (str, dict)):
            raise ValueError(f"{mctx}: 'content' must be string or object")
        _require_str(message, "ts", mctx)
        tool_call_id = message.get("tool_call_id")
        if tool_call_id is not None and not isinstance(tool_call_id, str):
            raise ValueError(f"{mctx}: 'tool_call_id' must be a string or null")


def validate_hermes_kv_cache_state(data: dict[str, Any]) -> HermesKVCacheStateFixture:
    ctx = "hermes_kv_cache_state.json"
    _require_str(data, "session_id", ctx)
    for field in ("cache_hits", "cache_misses", "last_cached_prefix_bytes"):
        if not isinstance(data.get(field), int):
            raise ValueError(f"{ctx}: '{field}' must be an integer")
    _require_str(data, "last_invalidated_reason", ctx)

    misses = data.get("messages_with_cache_miss")
    if not isinstance(misses, list):
        raise ValueError(f"{ctx}: 'messages_with_cache_miss' must be a list")
    for index, miss in enumerate(misses):
        mctx = f"{ctx}:messages_with_cache_miss[{index}]"
        for field in ("message_index", "expected_prefix_bytes", "actual_prefix_bytes"):
            if not isinstance(miss.get(field), int):
                raise ValueError(f"{mctx}: '{field}' must be an integer")
        diff_kind = miss.get("diff_kind")
        if diff_kind not in {"whitespace", "role_format", "content"}:
            raise ValueError(f"{mctx}: invalid diff_kind {diff_kind!r}")
    return data  # type: ignore[return-value]


def validate_hermes_runtime_state(data: dict[str, Any]) -> HermesRuntimeStateFixture:
    ctx = "hermes_runtime_state.json"
    if not isinstance(data.get("pid"), int):
        raise ValueError(f"{ctx}: 'pid' must be an integer")
    for field in ("started_at", "frozen_now_ts", "last_progress_ts"):
        _require_str(data, field, ctx)
    if not isinstance(data.get("interrupt_queue_depth"), int):
        raise ValueError(f"{ctx}: 'interrupt_queue_depth' must be an integer")
    if not isinstance(data.get("is_blocked"), bool):
        raise ValueError(f"{ctx}: 'is_blocked' must be a boolean")

    blocking_call = data.get("blocking_call")
    if blocking_call is not None:
        if not isinstance(blocking_call, dict):
            raise ValueError(f"{ctx}: 'blocking_call' must be an object or null")
        _require_str(blocking_call, "tool_name", f"{ctx}:blocking_call")
        _require_str(blocking_call, "started_at", f"{ctx}:blocking_call")
        if not isinstance(blocking_call.get("duration_s"), int):
            raise ValueError(f"{ctx}:blocking_call: 'duration_s' must be an integer")

    imds_fingerprint = data.get("imds_fingerprint")
    if imds_fingerprint is not None and not isinstance(imds_fingerprint, dict):
        raise ValueError(f"{ctx}: 'imds_fingerprint' must be an object or null")

    resolved_aws_role_arn = data.get("resolved_aws_role_arn")
    if resolved_aws_role_arn is not None and not isinstance(resolved_aws_role_arn, str):
        raise ValueError(f"{ctx}: 'resolved_aws_role_arn' must be a string or null")

    return data  # type: ignore[return-value]


def validate_hermes_cron_state(data: dict[str, Any]) -> HermesCronStateFixture:
    ctx = "hermes_cron_state.json"
    _require_str(data, "schedule_cron", ctx)
    last_run = data.get("last_run")
    if not isinstance(last_run, dict):
        raise ValueError(f"{ctx}: 'last_run' must be an object")
    _require_str(last_run, "started_at", f"{ctx}:last_run")
    delivery_status = last_run.get("delivery_status")
    if delivery_status not in {"ok", "hung", "failed", "never_started"}:
        raise ValueError(f"{ctx}:last_run: invalid delivery_status {delivery_status!r}")
    for field in ("agent_completed_at", "delivery_started_at", "delivery_completed_at"):
        value = last_run.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{ctx}:last_run: '{field}' must be a string or null")
    return data  # type: ignore[return-value]


def validate_hermes_session_topology(data: dict[str, Any]) -> HermesSessionTopologyFixture:
    ctx = "hermes_session_topology.json"
    _require_str(data, "visible_session_id", ctx)
    sessions = data.get("all_sessions")
    if not isinstance(sessions, list):
        raise ValueError(f"{ctx}: 'all_sessions' must be a list")
    for index, session in enumerate(sessions):
        sctx = f"{ctx}:all_sessions[{index}]"
        _require_str(session, "session_id", sctx)
        for field in ("parent_session_id", "continuation_of"):
            value = session.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{sctx}: '{field}' must be a string or null")
        _require_str(session, "last_message_ts", sctx)
        if not isinstance(session.get("message_count"), int):
            raise ValueError(f"{sctx}: 'message_count' must be an integer")
    return data  # type: ignore[return-value]


def validate_hermes_answer_key(data: dict[str, Any]) -> HermesScenarioAnswerKeySchema:
    ctx = "answer.yml"
    _require_str(data, "root_cause_category", ctx)
    root_cause_category = str(data["root_cause_category"]).strip()
    if root_cause_category not in VALID_ROOT_CAUSE_CATEGORIES:
        raise ValueError(
            f"{ctx}: unknown root_cause_category {root_cause_category!r}; "
            f"expected one of {sorted(VALID_ROOT_CAUSE_CATEGORIES)}"
        )
    _require_non_empty_str_list(data, "required_keywords", ctx, required=True)
    _require_str(data, "model_response", ctx)

    for field in (
        "forbidden_categories",
        "forbidden_keywords",
        "required_evidence_sources",
        "optimal_trajectory",
    ):
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{ctx}: '{field}' must be a list of strings")

    forbidden_categories = {
        item.strip()
        for item in (data.get("forbidden_categories") or [])
        if isinstance(item, str) and item.strip()
    }
    if root_cause_category in forbidden_categories:
        raise ValueError(
            f"{ctx}: root_cause_category {root_cause_category!r} cannot also appear in "
            "'forbidden_categories'"
        )

    trajectory = data.get("optimal_trajectory")
    if isinstance(trajectory, list):
        unknown_actions = [
            item for item in trajectory if item not in VALID_HERMES_TRAJECTORY_ACTIONS
        ]
        if unknown_actions:
            raise ValueError(
                f"{ctx}: unknown trajectory action(s) {unknown_actions}; expected subset of "
                f"{sorted(VALID_HERMES_TRAJECTORY_ACTIONS)}"
            )

    max_loops = data.get("max_investigation_loops")
    if max_loops is not None and (not isinstance(max_loops, int) or max_loops < 1):
        raise ValueError(f"{ctx}: 'max_investigation_loops' must be a positive integer")
    return data  # type: ignore[return-value]


def validate_hermes_scenario_metadata(data: dict[str, Any]) -> HermesScenarioMetadataSchema:
    ctx = "scenario.yml"
    for field in ("schema_version", "scenario_id", "failure_mode", "severity"):
        _require_str(data, field, ctx)

    failure_mode = data["failure_mode"]
    if failure_mode not in VALID_HERMES_FAILURE_MODES:
        raise ValueError(
            f"{ctx}: unknown failure_mode {failure_mode!r}; expected one of {sorted(VALID_HERMES_FAILURE_MODES)}"
        )

    sources = data.get("available_evidence")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{ctx}: 'available_evidence' must be a non-empty list")

    unknown_sources = [source for source in sources if source not in VALID_HERMES_EVIDENCE_SOURCES]
    if unknown_sources:
        raise ValueError(
            f"{ctx}: unknown evidence source(s) {unknown_sources}; expected subset of "
            f"{sorted(VALID_HERMES_EVIDENCE_SOURCES)}"
        )
    return data  # type: ignore[return-value]


def _require_str(obj: dict[str, Any], key: str, ctx: str) -> None:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{ctx}: missing or empty required string field '{key}'")


def _require_non_empty_str_list(
    obj: dict[str, Any],
    key: str,
    ctx: str,
    *,
    required: bool = False,
) -> None:
    value = obj.get(key)
    if value is None:
        if required:
            raise ValueError(f"{ctx}: '{key}' must be a non-empty list")
        return
    if not isinstance(value, list) or not value:
        raise ValueError(f"{ctx}: '{key}' must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{ctx}: all '{key}' entries must be non-empty strings")
