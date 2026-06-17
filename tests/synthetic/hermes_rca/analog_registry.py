from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HermesAnalogScenario:
    scenario_id: str
    family: str
    failure_mode: str
    keywords: tuple[str, ...]
    diagnostic_question: str


ANALOG_SCENARIOS: tuple[HermesAnalogScenario, ...] = (
    # Part 1/5: provider / transport
    HermesAnalogScenario(
        scenario_id="001-codex-empty-response",
        family="llm_provider",
        failure_mode="provider_empty_response",
        keywords=("codex", "empty response", "malformed body", "retry"),
        diagnostic_question="Can you fetch the raw provider response body for the failing call?",
    ),
    HermesAnalogScenario(
        scenario_id="002-openrouter-400-all-models",
        family="llm_provider",
        failure_mode="provider_http_400",
        keywords=("openrouter", "400", "payload", "bad request"),
        diagnostic_question="Can you fetch the request payload and response body for the failing provider call?",
    ),
    HermesAnalogScenario(
        scenario_id="003-minimax-529-overload",
        family="llm_provider",
        failure_mode="provider_overload_529",
        keywords=("minimax", "529", "overload", "fail over"),
        diagnostic_question="Can you confirm whether another provider can be used as failover?",
    ),
    HermesAnalogScenario(
        scenario_id="004-bedrock-imds-override",
        family="llm_provider",
        failure_mode="provider_imds_override",
        keywords=("bedrock", "imds", "override", "aws"),
        diagnostic_question="Can you confirm whether IMDS credentials overrode the configured provider?",
    ),
    HermesAnalogScenario(
        scenario_id="005-codex-headers-dropped",
        family="llm_provider",
        failure_mode="provider_headers_dropped",
        keywords=("headers", "authorization", "dropped", "request"),
        diagnostic_question="Can you inspect the outbound headers on the failing provider request?",
    ),
    HermesAnalogScenario(
        scenario_id="006-sse-line-overflow",
        family="llm_provider",
        failure_mode="sse_line_overflow",
        keywords=("sse", "line", "overflow", "stream"),
        diagnostic_question="Can you capture the raw SSE frame size from the failing stream?",
    ),
    # Part 2/5: long-running / agent reliability
    HermesAnalogScenario(
        scenario_id="010-compression-invalid-tool-ordering",
        family="agent_runtime",
        failure_mode="agent_state_corruption",
        keywords=("compression", "tool ordering", "state corruption"),
        diagnostic_question="Can you compare message history before and after compression?",
    ),
    HermesAnalogScenario(
        scenario_id="011-cli-hang-no-interrupt-drain",
        family="execution_backend",
        failure_mode="agent_hang",
        keywords=("cli hang", "interrupt", "blocked", "queue"),
        diagnostic_question="Can you inspect the interrupt queue depth and blocking call?",
    ),
    HermesAnalogScenario(
        scenario_id="012-cron-hang-post-output",
        family="execution_backend",
        failure_mode="delivery_hang",
        keywords=("cron", "delivery", "post output", "hang"),
        diagnostic_question="Can you verify whether delivery started after the agent completed?",
    ),
    HermesAnalogScenario(
        scenario_id="013-kv-cache-invalidation-format-drift",
        family="agent_runtime",
        failure_mode="performance_degradation",
        keywords=("kv cache", "format drift", "cache miss", "performance"),
        diagnostic_question="Can you compare cached prefix bytes before and after the format change?",
    ),
    HermesAnalogScenario(
        scenario_id="014-tui-compression-ghost-session",
        family="agent_runtime",
        failure_mode="ghost_session",
        keywords=("ghost session", "tui", "continuation", "invisible fork"),
        diagnostic_question="Can you follow the continuation_of session chain?",
    ),
    # Part 3/5: orchestration / memory
    HermesAnalogScenario(
        scenario_id="020-multi-agent-orchestration-missing",
        family="orchestration",
        failure_mode="orchestration_missing",
        keywords=("orchestration", "planner", "worker", "reviewer"),
        diagnostic_question="Can you inspect whether roles ran in separate execution contexts?",
    ),
    HermesAnalogScenario(
        scenario_id="021-a2a-protocol-unsupported",
        family="messaging",
        failure_mode="protocol_unsupported",
        keywords=("a2a", "protocol", "unsupported", "peer agent"),
        diagnostic_question="Can you capture the protocol negotiation response from the peer adapter?",
    ),
    HermesAnalogScenario(
        scenario_id="022-multi-model-routing-ignored",
        family="llm_provider",
        failure_mode="routing_ignored",
        keywords=("routing", "capability", "default model", "ignored"),
        diagnostic_question="Can you inspect the routing decision for each capability category?",
    ),
    HermesAnalogScenario(
        scenario_id="023-acp-orchestration-missing",
        family="messaging",
        failure_mode="orchestration_missing",
        keywords=("acp", "coordinator", "isolated", "orchestration"),
        diagnostic_question="Can you verify whether the ACP coordinator was actually registered?",
    ),
    HermesAnalogScenario(
        scenario_id="030-memory-external-unavailable",
        family="memory",
        failure_mode="memory_unavailable",
        keywords=("memory", "external", "unavailable", "fallback"),
        diagnostic_question="Can you check whether the external memory backend is reachable?",
    ),
    HermesAnalogScenario(
        scenario_id="031-memory-backup-missing",
        family="memory",
        failure_mode="memory_corruption",
        keywords=("memory", "backup", "corruption", "filesystem"),
        diagnostic_question="Can you verify whether a clean memory backup exists?",
    ),
    HermesAnalogScenario(
        scenario_id="032-memory-tool-json-parse-llama-cpp",
        family="memory",
        failure_mode="memory_parse_failure",
        keywords=("json", "parse", "llama.cpp", "memory tool"),
        diagnostic_question="Can you capture the exact malformed memory-tool JSON output?",
    ),
    # Part 4/5: controls / security
    HermesAnalogScenario(
        scenario_id="040-determinism-engine-missing",
        family="controls",
        failure_mode="missing_determinism_control",
        keywords=("determinism", "same input", "different outputs", "workflow replay"),
        diagnostic_question="Can you compare output hashes from two same-input workflow runs?",
    ),
    HermesAnalogScenario(
        scenario_id="041-approval-lock-missing-dangerous-command",
        family="controls",
        failure_mode="missing_approval_gate",
        keywords=("approval", "destructive command", "approval gate"),
        diagnostic_question="Can you fetch the approval event for the destructive command?",
    ),
    HermesAnalogScenario(
        scenario_id="042-audit-trail-missing",
        family="controls",
        failure_mode="missing_audit_trail",
        keywords=("audit", "signature", "hash chain", "tamper"),
        diagnostic_question="Can you inspect whether the audit event has a valid signature and hash chain?",
    ),
    HermesAnalogScenario(
        scenario_id="043-rbac-gateway-missing-multi-user",
        family="controls",
        failure_mode="missing_rbac",
        keywords=("rbac", "tenant", "scope", "authorization"),
        diagnostic_question="Can you inspect whether a tenant scope check was performed?",
    ),
    HermesAnalogScenario(
        scenario_id="044-credential-proxy-missing",
        family="controls",
        failure_mode="missing_credential_isolation",
        keywords=("credential", "proxy", "isolation", "in-process"),
        diagnostic_question="Can you inspect whether credentials were loaded in-process or through the proxy?",
    ),
)


def analog_ids() -> set[str]:
    return {scenario.scenario_id for scenario in ANALOG_SCENARIOS}


def analogs_by_family(family: str) -> tuple[HermesAnalogScenario, ...]:
    normalized = family.strip().lower()
    return tuple(scenario for scenario in ANALOG_SCENARIOS if scenario.family == normalized)


def find_analog_by_id(scenario_id: str) -> HermesAnalogScenario | None:
    normalized = scenario_id.strip()
    for scenario in ANALOG_SCENARIOS:
        if scenario.scenario_id == normalized:
            return scenario
    return None
