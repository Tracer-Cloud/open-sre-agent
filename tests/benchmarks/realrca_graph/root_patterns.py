from __future__ import annotations

import re
from collections import Counter
from typing import Any

from tests.benchmarks.realrca_graph.features import clip_text, entity_features, text_for_features

TABLE_RE = re.compile(r"\b(?:from|join|update|into)\s+([a-zA-Z_][\w.$-]{2,80})\b", re.I)
TOPIC_RE = re.compile(r"\b(?:topic|Topic)[:= ]+([A-Za-z0-9_.:-]{4,120})\b")
RDS_STYLE_RE = re.compile(r"\br-[0-9a-zA-Z-]+\b")
HOST_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
ECS_INSTANCE_RE = re.compile(r"\bi-[0-9a-z]{8,}\b", re.I)
DOMAIN_RE = re.compile(r"\b[a-zA-Z0-9.-]+\.(?:cn|com|net|org)\b")
URL_HOST_RE = re.compile(r"https?://([^/\s:]+)", re.I)
SERVICEISH_RE = re.compile(
    r"\b(?:com|org|net|io|cn)\.[A-Za-z0-9_.$]+(?::[\w.-]+)?(?:[@#/][\w.$~:-]+)?\b", re.I
)
PSEUDO_DOMAINS = {"java.net"}
SECURITY_STRONG_RE = re.compile(
    r"(?<![a-z0-9])(?:ssrf|rce)(?![a-z0-9])"
    r"|安全扫描|恶意|攻击|payload|路径穿越"
    r"|RASP has block|real attack"
    r"|fastjson.{0,160}SecurityException|SecurityException.{0,160}fastjson",
    re.I,
)
SECURITY_TECH_MARKERS = ("heimdall", "security-fourier", "fourier_check", "bx-x5action")
LIMIT_TRIGGER_RE = re.compile(
    r"sentinel(?:block|_block)(?:exception)?|ump_sentinel_block|blockexception"
    r"|sentinel限流|rate limit|throttle|接口限流|限流|流控|熔断",
    re.I,
)
THREADPOOL_TRIGGER_RE = re.compile(
    r"threadpool_busy|thread pool is full|threadpool|hsf[-_ ]?thread|HSF线程|线程池(?:打满|满|达到上限)?",
    re.I,
)
JVM_MEMORY_TRIGGER_RE = re.compile(
    r"metaspace|full[_ -]?gc|\bfgc\b|gc overhead|outofmemory|内存不足",
    re.I,
)
JVM_GC_PRESSURE_TRIGGER_RE = re.compile(
    r"jvm_gc_(?:count|time)_delta|g1_(?:young_generation|concurrent_gc|old_generation)|"
    r"YoungGC|ConcurrentGC|OldGC|GC次数|GC耗时|GC time|GC count",
    re.I,
)
JVM_GC_ROOT_LABEL_RE = re.compile(
    r"\blabel[:=]\s*(jvm_gc_[^\s;,\]}]+)|\b(jvm_gc_[a-z0-9_.-]*:[^\s;,\]}]+)",
    re.I,
)
EXTERNAL_DEPENDENCY_TRIGGER_RE = re.compile(
    r"NoRouteToHostException|no route to host|UnknownHostException|host unreachable|connection reset"
    r"|connection refused|connection timed out|connect timeout|read timed out|连接超时|连接异常|TCP探测失败|不可达",
    re.I,
)
SEARCH_DEPENDENCY_TRIGGER_RE = re.compile(
    r"IGraphServerException|IGraphQueryException|igraph search error|queryIgraph_error",
    re.I,
)
CONNECTION_POOL_TRIGGER_RE = re.compile(
    r"(?:connection pool|DruidDataSource|pool exhausted|get connection|连接池|获取连接).{0,80}"
    r"(?:TDDL|JDBC|MySQL|SQL|DataSource|数据库)"
    r"|(?:TDDL|JDBC|MySQL|SQL|DataSource|数据库).{0,80}"
    r"(?:connection pool|DruidDataSource|pool exhausted|get connection|连接池|获取连接)",
    re.I,
)
SINGLE_HOST_SQL_FAILURE_RE = re.compile(r"SQL执行失败|sql execution failed|sql_failure", re.I)
DATA_QUALITY_TRIGGER_RE = re.compile(
    r"NumberFormatException|parseLong|Illegal mix of collations|Duplicate entry|unique key"
    r"|collation_mismatch|data_quality"
    r"|BadRequestException|Assert\.notNull|PARAM_ILLEGAL|NO_QUALIFICATION"
    r"|唯一键|脏数据|字符集|参数非法|主数据缺失|不存在|余额不足|资格",
    re.I,
)
AUTH_STATUS_RE = re.compile(
    r"\b(?:http_status|status|statusCode|result(?:_code)?|resultStr|rc)['\"]?\s*[:=/]\s*['\"]?401\b|"
    r"\b401/UNAUTHORIZED\b|\bUNAUTHORIZED\b",
    re.I,
)
AUTH_CONTEXT_RE = re.compile(
    r"\b(?:BUC|SSO|token|login_for_sunfire|tenant key|auth|unauthori[sz]ed|goc-pass|wagbridge)\b|"
    r"认证|鉴权|登录态|登录|tr\.alibaba-inc\.com|/goc[A-Za-z0-9]+/innerApi/",
    re.I,
)
AUTH_PATH_RE = re.compile(
    r"https?://[^/\s]+(?P<url_path>/[A-Za-z0-9_./:-]{3,180})|"
    r"\b(?:path|request_uri|originalUrl|service)=(?P<field_path>/[A-Za-z0-9_./:-]{3,180})",
    re.I,
)
APP_PUBLISH_TRIGGER_RE = re.compile(
    r"change_system=(?:normandy|aone)|sourceProduct=CHANGEFREE|source=changefree|"
    r"APP_PUBLISH|AONE应用发布|Aone发布部署|应用[^;\n]{0,80}部署|deploy_id=|deploy_version=",
    re.I,
)
CONFIG_MQ_CHANGE_RE = re.compile(
    r"(?:diamond|APP_CONFIG_PUSH|CONFIG_PUSH|配置推送).{0,260}"
    r"(?:result\.notice\.config|dataId[^;\n]{0,80}notice|Diamond配置)",
    re.I | re.S,
)
CONFIG_MQ_FAILURE_RE = re.compile(
    r"middleware_metaq_receive_success_rate|MQRecv|ConsumeMessageThread|BIZ_ERROR|"
    r"ChannelGatewayCallback|LOAN_DISCOUNT|metaq消费成功率|消费失败",
    re.I,
)
CONFIG_MQ_CONTEXT_RE = re.compile(
    r"kind=metaq_business_failure|metric=middleware_metaq|middleware_metaq_|"
    r"metaq消费|消息消费|消费消息|log_path=[^\s;]*metaq\.log",
    re.I,
)
CONFIG_NAME_RE = re.compile(r"\b(result\.notice\.config|[a-z0-9_.-]{3,80}\.config)\b", re.I)
CONFIG_CR_RE = re.compile(r"\bcrIds?[^0-9]{0,30}(?P<cr>[0-9]{5,})", re.I)
CONFIG_TAG_RE = re.compile(r"\b(LOAN_DISCOUNT|[A-Z][A-Z0-9_]{3,80})\b")
CONFIG_EXTERNAL_ORG_RE = re.compile(
    r"external_orgs?=\[['\"]?([a-zA-Z0-9_-]{2,80})['\"]?\]"
    r"|\blender(?:ChannelCode)?=([a-zA-Z0-9_-]{2,80})\b",
    re.I,
)
CONFIG_API_NAME_RE = re.compile(r"api_names?=\[['\"]?([a-zA-Z0-9_.-]{6,160})['\"]?\]", re.I)
METAQ_BROKER_FAILURE_RE = re.compile(
    r"broker_hints=\{[^}]+\}|fetch name server address exception|name server address exception|"
    r"RemotingConnectException[^\n;]{0,160}(?:broker|connect to)|"
    r"MQClientException[^\n;]{0,160}broker\[[^\]]+\]|"
    r"broker\[[^\]]+\][^\n;]{0,160}(?:not exist|connect|failed|exception)|"
    r"updateConsumeOffsetToBroker|pullKernelImpl",
    re.I,
)
METAQ_BROKER_NAME_RE = re.compile(r"\bbroker\[([^\]]{2,120})\]", re.I)
METAQ_TOPIC_TOKEN_RE = re.compile(
    r"\b([A-Za-z0-9][A-Za-z0-9_.:-]{2,120}(?:_metaq|_TOPIC|_topic|Topic|TOPIC)[A-Za-z0-9_.:-]*)\b"
)
METAQ_DUPLICATE_UPDATE_RE = re.compile(
    r"kind=metaq_duplicate_update_conflict|duplicate_update_conflict|UPDATE_ERROR|"
    r"updateWithVersion|optimistic(?: lock)?|version conflict|乐观锁|更新失败",
    re.I,
)
METAQ_CONSUME_CONTEXT_RE = re.compile(
    r"MetaQ|RocketMQ|MQRecv|ConsumeMessageThread|BaseMetaQListener|_TOPIC|metaq消费成功率|消费失败",
    re.I,
)
METAQ_MAIL_NO_RE = re.compile(r"\bmailNo=([0-9A-Za-z_-]{4,80})", re.I)
METAQ_ACTION_RE = re.compile(r"\baction=([A-Z][A-Z0-9_]{1,40})", re.I)
QUALIFICATION_FAILURE_RE = re.compile(
    r"NO_QUALIFICATION|资格(?:判断|校验|不通过|失败)|定品未创建|DP_CREATE",
    re.I,
)
DEPLOY_ID_RE = re.compile(r"\bdeploy_id=([0-9]{3,})\b", re.I)
DEPLOY_VERSION_RE = re.compile(r"\bdeploy_version=([0-9]{3,})\b", re.I)
APP_FIELD_RE = re.compile(
    r"\b(?:alarm\s+)?app(?:name)?=\[?\"?(?P<app>[a-z0-9][a-z0-9-]{1,80})\"?\]?"
    r"|change_summary=应用(?P<summary_app>[a-z0-9][a-z0-9-]{1,80})部署",
    re.I,
)
CHANGE_APP_RE = re.compile(
    r"\bchange_app=(?P<app>[a-z0-9][a-z0-9-]{1,80})\b",
    re.I,
)
CHANGE_SUMMARY_APP_RE = re.compile(
    r"\bchange_summary=(?P<app>[a-z0-9][a-z0-9-]{1,80})"
    r"(?:\s+应用变更|\|[^;\n]{0,80}|应用通过|服务配置发布)",
    re.I,
)
CHANGE_ID_RE = re.compile(r"\bid=([0-9]{6,})\b", re.I)
DOWNSTREAM_OFFLINE_CHANGE_RE = re.compile(
    r"change_type=OFFLINE_HOST|action/res/offline|机器下线|资源下线|下线",
    re.I,
)
INSTANCE_COUNT_DROP_RE = re.compile(
    r"机器(?:存活)?(?:数|数量)|存活(?:机器|实例|节点)?(?:数|数量)|"
    r"实例(?:数|数量)|机器数量|机器数|cnt[^\n;]{0,80}(?:同比|环比)?下跌|"
    r"(?:同比|环比)?下跌[^\n;]{0,80}(?:机器|实例|存活|cnt)",
    re.I,
)
NORMANDY_OFFLINE_RE = re.compile(
    r"(?:system=normandy-director|normandy-director)"
    r"(?:(?!\bid=).){0,260}?"
    r"(?:change_type=OFFLINE_HOST|type=OFFLINE_HOST|action/res/offline|机器下线|资源下线|下线)"
    r"|(?:change_type=OFFLINE_HOST|type=OFFLINE_HOST|action/res/offline|机器下线|资源下线|下线)"
    r"(?:(?!\bid=).){0,260}?"
    r"(?:system=normandy-director|normandy-director)",
    re.I | re.S,
)
APP_GROUP_RE = re.compile(
    r"\b(?:app[_-]?group|appGroup)=\[?\"?(?P<group>[a-z0-9][a-z0-9._:-]{1,120})",
    re.I,
)
BRACKET_APP_GROUP_RE = re.compile(r"\[(?P<group>[a-z0-9][a-z0-9._:-]{1,120})[,\\]]", re.I)
TDDL_WRITE_FAILURE_RE = re.compile(
    r"\bTDDL_(?:INSERT|UPDATE|DELETE)@(?P<db>[^\s:\x1a;\]]+)"
    r"(?::(?P<table>[^\s\x1a;\]]+))?"
    r"(?:\x1a[0-9a-zA-Z_.$-]+)?"
    r"(?:(?!\bTDDL_).){0,160}?"
    r"\bresult(?:_code)?=(?P<result>[^\s;,\]]+)",
    re.I | re.S,
)
TDDL_SPAN_ENTITY_RE = re.compile(
    r"\bTDDL_(?:QUERY|INSERT|UPDATE|DELETE|KV_[A-Z]+)@(?P<db>[^\s:\x1a;\]]+)"
    r"(?::(?P<table>[^\s\x1a;\]]+))?",
    re.I,
)
SQL_TABLE_SUMMARY_BLOCK_RE = re.compile(r"\bsql_tables=\{(?P<body>[^}]{0,2000})\}", re.I | re.S)
SUMMARY_SQL_TABLE_RE = re.compile(
    r"['\"](?P<table>[a-zA-Z0-9_.$-]{2,80})['\"]\s*:\s*(?P<count>\d+)"
)
NOTIFY_RECV_RE = re.compile(
    r"Notify@recv~[^:\s;]*:(?P<topic>[A-Za-z0-9_.:-]{3,120}):"
    r"(?P<tag>[A-Za-z0-9_.:-]{0,120}):(?P<group>[A-Za-z0-9_.:-]{0,160})",
    re.I,
)
TRACE_CALL_RE = re.compile(
    r"client=(?P<client>[a-z0-9][a-z0-9-]*):[^\s;]*\s+"
    r"server=(?P<server>[a-z0-9][a-z0-9-]*):[^\s;]*\s+"
    r"service=(?P<service>(?:com|org|net|io|cn)\.[^\s;]+[@#][^\s;]+)"
    r"(?:(?!\bclient=).){0,120}?\bresult=(?P<result>[^\s;]+)",
    re.I | re.S,
)
MDM_APP_RE = re.compile(r"\b(?P<app>[a-z0-9][a-z0-9-]*mdm[a-z0-9-]*):", re.I)
FACADE_METHOD_RE = re.compile(
    r"\b(?P<class>[A-Z][A-Za-z0-9_$]*Facade)[,\s]+(?P<method>[a-z][A-Za-z0-9_$]*)\b"
)
MDM_TABLE_NAME_RE = re.compile(r"\bmdm_[a-z0-9_]{2,80}\b", re.I)
BUSINESS_ERROR_RE = re.compile(
    r"BIZ_ERROR|result=0?1(?:/ERR)?|resultStr['\"]?\s*[:=]\s*['\"]?0?1|成功率[^，,;]*0|失败数",
    re.I,
)
UNIQUE_WRITE_CONFLICT_RE = re.compile(
    r"Duplicate entry|duplicate_key|unique key|唯一键|uk_[a-z0-9_]+",
    re.I,
)

SLOW_SQL_MARKERS = ("慢sql", "慢查询", "slow sql", "slowqueries", "full scan", "全表扫描")
NOISY_SQL_TABLES = {"ERROR", "THE", "WHERE"}
MQ_SPIKE_MARKERS = ("metaq", "rocketmq", "消息量", "消息堆积", "topic", "group_id")
MQ_METRIC_MARKERS = ("metric=middleware_metaq", "metric_middleware_metaq", "middleware_metaq_")
METRIC_SERIES_BLOCK_RE = re.compile(r"\[([^\]]+)\]")
ZERO_ONLY_METRIC_RE = re.compile(
    r"\bmin=0(?:\.0+)?(?:,|\b).*?\bmax=0(?:\.0+)?(?:,|\b).*?\bavg=0(?:\.0+)?(?:,|\b).*?\blast=0(?:\.0+)?(?:,|\b)",
    re.I | re.S,
)
CACHE_SYSTEM_MARKERS = ("redis", "tair", "pipeline", "cache", "缓存")
CACHE_TIMEOUT_MARKERS = ("read timed out", "timeout", "超时")
HOST_MARKERS = ("单机", "doom_host", "机器", "ecs", "pod", "驱逐", "实例")
TRACE_TARGET_HOST_MARKERS = ("server_ip=", "serverip=", "server_ip:", "serverip:")
SPECIAL_HOST_GROUP_MARKERS = ("doomhost", "doom_host", "_offline_host", "_none_core_host")
HOST_ABNORMAL_MARKERS = (
    "doom_host",
    "doomhost",
    "full gc",
    "stop-the-world",
    "threadpool_busy",
    "timeout",
    "rpc_err",
    "oom",
    "cpu",
    "load",
    "下线",
    "重启",
    "驱逐",
    "线程池",
    "打满",
    "停顿",
    "单机处理异常",
)
TRACE_ABNORMAL_RESULT_RE = re.compile(
    r"(?:\brc=0?[234]\b|\bresult(?:type)?=0?[234]\b|\bresult=0?[234]\b|timeout|rpc_err)",
    re.I,
)
OFFLINE_SERVICE_METHOD_RE = re.compile(
    r"\bservice=(?P<service>[^,\]\s]+?\.offline)\s*,\s*method=(?P<method>[^,\]\s]+)",
    re.I,
)
OFFLINE_SERVER_RE = re.compile(
    r"\bserver=(?P<app>[a-z0-9][a-z0-9-]*):[^\s,;]*_offline_host\b", re.I
)
NONE_CORE_SERVER_RE = re.compile(
    r"(?:\bserver=|->\s*)(?P<app>[a-z0-9][a-z0-9-]*):(?P<group>[^\s,;|\]]*none_core_host)\b",
    re.I,
)
NONE_CORE_GROUP_RE = re.compile(r"\b(?P<group>[a-z0-9][a-z0-9-]*_none_core_host)\b", re.I)
GRAYHOST_GROUP_RE = re.compile(r"\b(?P<group>[a-z0-9][a-z0-9._-]*_grayhost)\b", re.I)
CHANGE_COUNT_RE = re.compile(r"\bchanges=(?P<count>[1-9]\d*)\b", re.I)
METRIC_MAX_RE = re.compile(
    r'(?:\bmax=|["\']max["\']\s*:\s*)(?P<max>[0-9.]+(?:e[+-]?\d+)?)\b',
    re.I,
)
METRIC_AVG_RE = re.compile(
    r'(?:\bavg=|["\']avg["\']\s*:\s*)(?P<avg>[0-9.]+(?:e[+-]?\d+)?)\b',
    re.I,
)
METRIC_TREND_RE = re.compile(
    r'(?:\btrend=|["\']trend["\']\s*:\s*["\']?)(?P<trend>[a-z_]+)',
    re.I,
)
METRIC_JSON_BLOCK_RE = re.compile(
    r'["\']labels["\']\s*:\s*\{(?P<labels>[^{}]*)\}\s*,\s*'
    r'["\']summary["\']\s*:\s*\{(?P<summary>[^{}]*)\}',
    re.I | re.S,
)
HSF_PROVIDER_ERROR_QPS_MIN_MAX = 200.0
HSF_PROVIDER_ERROR_QPS_MIN_AVG = 3.0
HSF_PROVIDER_ERROR_QPS_HARD_LIMIT_MAX = 500.0
HSF_PROVIDER_ERROR_QPS_HARD_LIMIT_AVG = 50.0
HSF_TOPOLOGY_SEGMENT_RE = re.compile(
    r"(?:topology path:|\|)\s*(?P<client>[^|\n]+?)\s*->\s*"
    r"(?P<server>[a-z0-9][^\s|]+)\s+"
    r"(?P<service>(?:com|org|net|io|cn)\.[^\s|]+[@#][^\s|]+)\s+"
    r"(?P<duration>[0-9.]+)ms\s+rc=(?P<rc>0?[234])"
    r"(?:\s+server_ip=(?P<server_ip>(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)))?",
    re.I,
)
HSF_TRACE_ERROR_SEGMENT_RE = re.compile(
    r"\bhsf_error(?:_top=|\s+)client=(?P<client>[^\s;]+)\s+server=(?P<server>[^\s;]+)\s+"
    r"service=(?P<service>(?:com|org|net|io|cn)\.[^\s;]+[@#][^\s;]+)\s+"
    r"failures=(?P<failures>\d+)\s+max_duration_ms=(?P<duration>[0-9.]+)\s+"
    r"result_codes=\{(?P<result_codes>[^}]*)\}\s+provider_ips=\{(?P<provider_ips>[^}]*)\}"
    r"(?:\s+consumer_ips=\{(?P<consumer_ips>[^}]*)\})?",
    re.I,
)
COUNT_PAIR_RE = re.compile(r"['\"]?(?P<key>[^,'\":{} ]+)['\"]?\s*:\s*(?P<count>\d+)")
SQL_REPEATED_QUERY_MIN_COUNT = 10
SQL_REPEATED_QUERY_STRONG_COUNT = 25
INFRA_EVENT_STRONG_RE = re.compile(
    r"HostRisk|hardware(?:_error)?|Memory error|local_disk_nc_down_hardware_error|"
    r"硬件(?:故障|异常|风险)?|内存(?:故障|错误)|宿主机(?:故障|风险)",
    re.I,
)
INFRA_EVENT_WEAK_RE = re.compile(
    r"InstanceFailure|HealthStatusChange|InsufficientData|Initializing|"
    r"TCP探测失败|不可达|container|pod",
    re.I,
)
SCHEDULERX_TRIGGER_RE = re.compile(r"sourceProduct=SchedulerX|schedulerx\.job", re.I)
SCHEDULERX_JOB_ID_RE = re.compile(r"\b(?:subject|range_instance_id)=([0-9]{6,13})_[0-9]+\b", re.I)
SCHEDULERX_EVENT_SEGMENT_RE = re.compile(
    r"\bsubject=(?P<subject>(?P<job>[0-9]{6,13})_[0-9]+)\b"
    r"(?:(?!\bsubject=).)*?\btype=com\.alibaba\.schedulerx\.job\.(?P<phase>start|end)\b"
    r"(?:(?!\bsubject=).)*?\btime=(?P<time>[0-9]{4,13})\b",
    re.I | re.S,
)
RDS_CPU_CONTEXT_RE = re.compile(
    r"cms[._]acs_rds_dashboard[._]CpuUsage|RDS.*CPU|CPU.*RDS|数据库.*CPU|rds_cpu",
    re.I,
)


def pattern_root_candidates(graph_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Infer high-confidence RCA mechanism candidates from visible graph text only."""

    chunks = _visible_chunks(graph_context)
    text = "\n".join(chunks)
    if not text.strip():
        return []
    candidates: list[dict[str, Any]] = []
    case = graph_context.get("case") if isinstance(graph_context.get("case"), dict) else {}
    case_type = str(case.get("type") or case.get("case_type") or "").lower()
    candidates.extend(_security_context_candidates(text, case_type=case_type))
    candidates.extend(_security_sql_conflict_candidates(text, case_type=case_type))
    candidates.extend(_notify_business_failure_candidates(text, case_type=case_type))
    candidates.extend(_mdm_master_data_candidates(text, case_type=case_type))
    candidates.extend(_tddl_read_traffic_source_candidates(text, case_type=case_type))
    candidates.extend(_config_mq_failure_candidates(text, case_type=case_type))
    candidates.extend(_metaq_broker_failure_candidates(text, case_type=case_type))
    candidates.extend(_auth_session_failure_candidates(text, case_type=case_type))
    candidates.extend(_app_publish_data_quality_candidates(text, case_type=case_type))
    candidates.extend(_instance_count_offline_change_candidates(text, case_type=case_type))
    candidates.extend(_downstream_offline_change_candidates(text, case_type=case_type))
    candidates.extend(_hsf_capacity_change_candidates(text, case_type=case_type))
    candidates.extend(_hsf_cold_start_capacity_candidates(text, case_type=case_type))
    candidates.extend(_schedulerx_batch_load_candidates(text, case_type=case_type))
    candidates.extend(_infra_event_candidates(text))
    candidates.extend(_tddl_repeated_query_fanout_candidates(text, case_type=case_type))
    candidates.extend(_hsf_threadpool_timeout_candidates(text, case_type=case_type))
    candidates.extend(_hsf_provider_subset_rpc_error_candidates(text, case_type=case_type))
    candidates.extend(_hsf_provider_error_qps_spike_candidates(text, case_type=case_type))
    for chunk in chunks:
        if _is_effectively_empty(chunk):
            continue
        candidates.extend(_security_candidates(chunk, case_type=case_type))
        candidates.extend(_limit_candidates(chunk))
        candidates.extend(_threadpool_candidates(chunk))
        candidates.extend(_jvm_memory_candidates(chunk))
        candidates.extend(_jvm_gc_pressure_candidates(chunk, case_type=case_type))
        candidates.extend(_search_dependency_candidates(chunk))
        candidates.extend(_connection_pool_candidates(chunk, case_type=case_type))
        candidates.extend(_external_dependency_candidates(chunk))
        candidates.extend(_auth_session_failure_candidates(chunk, case_type=case_type))
        candidates.extend(_data_quality_candidates(chunk, case_type=case_type))
        candidates.extend(_slow_sql_candidates(chunk, case_type=case_type))
        candidates.extend(_metaq_broker_failure_candidates(chunk, case_type=case_type))
        candidates.extend(_metaq_duplicate_update_conflict_candidates(chunk, case_type=case_type))
        candidates.extend(_mq_spike_candidates(chunk))
        candidates.extend(_cache_timeout_candidates(chunk))
        candidates.extend(_host_candidates(chunk, case_type=case_type))
    return _dedupe_candidates(candidates)


def _visible_text(graph_context: dict[str, Any]) -> str:
    return "\n".join(_visible_chunks(graph_context))


def _visible_chunks(graph_context: dict[str, Any]) -> list[str]:
    case = graph_context.get("case") if isinstance(graph_context.get("case"), dict) else {}
    has_structured_context = bool(
        graph_context.get("evidence")
        or graph_context.get("root_candidates")
        or graph_context.get("nodes")
    )
    chunks = [
        text_for_features(
            {
                "case": {
                    "type": case.get("type") or case.get("case_type"),
                    "input": case.get("input"),
                }
            }
        )
    ]
    if not has_structured_context:
        chunks.append(
            text_for_features({"retrieval_summary": graph_context.get("retrieval_summary")})
        )
    for item in graph_context.get("evidence") or []:
        if isinstance(item, dict):
            name = item.get("name")
            command = item.get("command")
            summary = item.get("summary")
            hsf_json_metric = "middleware_hsf_provider_service_method_error_qps" in (
                f"{name} {command}".lower()
            )
            evidence_payload = (
                {"name": name, "command": command, "summary": summary}
                if hsf_json_metric
                else {"summary": summary}
            )
            chunks.append(text_for_features(evidence_payload))
    for item in _strip_local_refs(graph_context.get("root_candidates")) or []:
        if isinstance(item, dict):
            chunks.append(text_for_features(item))
    for item in _strip_local_refs(graph_context.get("nodes")) or []:
        if isinstance(item, dict):
            chunks.append(text_for_features(item))
    return [chunk for chunk in chunks if chunk.strip()]


def _is_effectively_empty(text: str) -> bool:
    compact = text.strip().lower()
    if not compact:
        return True
    field_empty_markers = ('"content": ""', '"app": ""', '"title": ""', "root_candidates: []")
    observation_empty_markers = (
        "series_count=0",
        '"series_count": 0',
        "app_logs count=0",
        "access_logs count=0",
        "sql_logs count=0",
        "spans=0",
    )
    if any(marker in compact for marker in observation_empty_markers):
        return True
    signal_markers = (
        "trace",
        "metric",
        "log",
        "event",
        "sql",
        "redis",
        "tair",
        "metaq",
        "exception",
        "超时",
        "慢",
        "发布",
        "变更",
    )
    return any(marker in compact for marker in field_empty_markers) and not any(
        marker in compact for marker in signal_markers
    )


def _security_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if not _has_security_scan_signal(text):
        return []
    if case_type.upper() == "HSF" and "mtop" not in text.lower():
        return []
    if case_type.upper() in {"HSF", "TAIR"} and not SECURITY_STRONG_RE.search(text):
        return []
    if (
        case_type.upper() in {"TDDL", "SQL", "RDS"}
        and "mtop" not in text.lower()
        and not SECURITY_STRONG_RE.search(text)
    ):
        return []
    domains = [
        value
        for value in DOMAIN_RE.findall(text)
        if not value.endswith(("alibaba-inc.com", "aliyuncs.com"))
    ]
    label = _security_label(text, domains)
    return [
        _candidate(
            "pattern_security_scan",
            label,
            7.0,
            "visible alarm/log text indicates malicious security scan payload",
            text,
        )
    ]


def _security_context_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type not in {"", "tddl", "sql", "rds", "other", "自定义监控"}:
        return []
    if not _has_security_context_signal(text):
        return []
    if case_type in {"tddl", "sql", "rds"} and (
        _tddl_write_failures(text) or UNIQUE_WRITE_CONFLICT_RE.search(text)
    ):
        return []
    label = "mtop security_scan" if "mtop" in text.lower() else _security_label(text, [])
    return [
        _candidate(
            "pattern_security_scan",
            label,
            9.7,
            (
                "visible trace/log graph links mtop traffic with security scanner or "
                "RASP attack-block evidence"
            ),
            text,
            extra_props={
                "entry_app": "mtop" if "mtop" in text.lower() else "",
                "security_context": True,
            },
        )
    ]


def _has_security_context_signal(text: str) -> bool:
    lower = text.lower()
    if "mtop" not in lower:
        return False
    if SECURITY_STRONG_RE.search(text):
        return True
    has_security_runtime = any(marker in lower for marker in SECURITY_TECH_MARKERS)
    has_attack_action = any(marker in lower for marker in ("heimdall", "bx-x5action"))
    return has_security_runtime and has_attack_action


def _has_security_scan_signal(text: str) -> bool:
    lower = text.lower()
    if SECURITY_STRONG_RE.search(text):
        return True
    has_probe_action = any(marker in lower for marker in ("heimdall", "bx-x5action"))
    if "mtop" in lower and has_probe_action:
        return True
    marker_count = sum(
        1 for marker in ("heimdall", "security-fourier", "bx-x5action") if marker in lower
    )
    return marker_count >= 2 and has_probe_action


def _security_label(text: str, domains: list[str]) -> str:
    lower = text.lower()
    if "mtop" in lower:
        return "mtop security_scan"
    if "security-fourier" in lower or "fourier_check" in lower:
        return "security-fourier security_scan"
    if "heimdall" in lower:
        return "heimdall security_scan"
    if domains:
        return domains[0]
    return _entity_label(text) or "security_scan"


def _security_sql_conflict_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type not in {"tddl", "sql", "rds", "自定义监控"}:
        return []
    if not _has_security_scan_signal(text):
        return []
    failures = _tddl_write_failures(text)
    has_unique_conflict = bool(UNIQUE_WRITE_CONFLICT_RE.search(text))
    if not failures and not has_unique_conflict:
        return []
    entities = entity_features(text)
    table = ""
    if failures:
        table = failures[0][1]
    if not table:
        table = (entities.get("sql_tables") or [""])[0]
    if not table:
        return []
    label = f"{table} unique_key_conflict"
    reason = (
        "visible mtop/security probe plus failing TDDL write indicates a "
        "security-scan-triggered duplicate or unique-key write conflict"
    )
    return [
        _candidate(
            "pattern_security_sql_conflict",
            label,
            8.35,
            reason,
            text,
            extra_props={
                "sql_table": table,
                "write_failure": bool(failures),
                "unique_conflict": has_unique_conflict,
            },
        )
    ]


def _tddl_write_failures(text: str) -> list[tuple[str, str, str]]:
    failures: list[tuple[str, str, str]] = []
    for match in TDDL_WRITE_FAILURE_RE.finditer(text):
        result = match.group("result").strip().strip("'\"")
        if result.lower() in {"0", "00", "ok", "success", "true"}:
            continue
        table = (match.group("table") or "").strip("`'\"[](){}<>，,.;:").lower()
        db = (match.group("db") or "").strip("`'\"[](){}<>，,.;:").lower()
        failures.append((db, table, result))
    return failures


def _notify_business_failure_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    lower = text.lower()
    if case_type not in {"metaq", "自定义监控"}:
        return []
    if "notify" not in lower:
        return []
    if "middleware_notify_receive_success_rate" not in lower and "notify消费成功率" not in text:
        return []
    if not (
        BUSINESS_ERROR_RE.search(text)
        or re.search(r"\bNotify@recv[^\n;]{0,180}\bresult=0?1\b", text, re.I)
    ):
        return []
    match = NOTIFY_RECV_RE.search(text)
    topic = match.group("topic") if match else ""
    if not topic:
        return []
    app = _alarm_app(text) or _notify_server_app(text) or "notify_handler"
    label = f"{app} {topic} business_consume_failure"
    return [
        _candidate(
            "pattern_notify_business_failure",
            label,
            8.85,
            (
                "visible Notify receive success-rate drop plus Notify@recv result=1/BIZ_ERROR "
                "indicates the application handler returned a business consume failure, "
                "业务逻辑异常 and 消费失败"
            ),
            text,
            extra_props={
                "app": app,
                "topic": topic,
                "business_error": True,
                "consume_failure": True,
            },
        )
    ]


def _config_mq_failure_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type not in {"metaq", "other", "自定义监控"}:
        return []
    if not CONFIG_MQ_CHANGE_RE.search(text):
        return []
    if not CONFIG_MQ_FAILURE_RE.search(text):
        return []
    if case_type != "metaq" and not CONFIG_MQ_CONTEXT_RE.search(text):
        return []
    app = _app_from_change_context(text) or _alarm_app(text) or _entity_label(text)
    config_name = _first_match(CONFIG_NAME_RE, text)
    cr_id = _first_match(CONFIG_CR_RE, text)
    business_tag = _config_business_tag(text)
    external_org = _config_external_org(text)
    api_name = _first_match(CONFIG_API_NAME_RE, text)
    label = " ".join(
        part
        for part in (
            app,
            config_name,
            f"CR={cr_id}" if cr_id else "",
            business_tag,
            f"lender={external_org}" if external_org else "",
            "config_mq_business_failure",
        )
        if part
    )
    return [
        _candidate(
            "pattern_config_mq_failure",
            label or "config_mq_business_failure",
            9.15,
            (
                "visible Diamond/config push overlaps MQ/MetaQ receive failure and BIZ_ERROR, "
                "indicating a configuration rollout changed message handling or callback routing"
            ),
            text,
            extra_props={
                "app": app,
                "config_name": config_name,
                "cr_id": cr_id,
                "business_tag": business_tag,
                "external_org": external_org,
                "api_name": api_name,
                "config_change": True,
                "consume_failure": True,
            },
        )
    ]


def _config_business_tag(text: str) -> str:
    preferred = re.search(r"\bLOAN_DISCOUNT\b", text)
    if preferred:
        return preferred.group(0)
    ignored = {
        "APP_CONFIG_PUSH",
        "BIZ_ERROR",
        "CHANGEFREE_EXE",
        "CONFIG_PUSH",
        "EXCEPTION",
        "PRODUCTION",
    }
    for match in CONFIG_TAG_RE.finditer(text):
        value = match.group(1)
        if value not in ignored and not value.startswith("TDDL_"):
            return value
    return ""


def _config_external_org(text: str) -> str:
    for match in CONFIG_EXTERNAL_ORG_RE.findall(text):
        for value in match:
            if value:
                return value.lower()
    return ""


def _metaq_broker_failure_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type not in {"hsf", "metaq", "other", "自定义监控"}:
        return []
    lower = text.lower()
    if not METAQ_BROKER_FAILURE_RE.search(text):
        return []
    if not any(
        marker in lower for marker in ("metaq", "rocketmq", "broker", "name server", "nameserver")
    ):
        return []
    broker = _first_match(METAQ_BROKER_NAME_RE, text)
    topic = _metaq_topic_label(text)
    if broker:
        label = f"{broker} broker_connectivity_failure"
    elif topic:
        label = f"{topic} broker_connectivity_failure"
    elif "name server" in lower or "nameserver" in lower:
        label = "rocketmq_name_server broker_connectivity_failure"
    else:
        label = "rocketmq_broker broker_connectivity_failure"
    return [
        _candidate(
            "pattern_metaq_broker_failure",
            label,
            9.05,
            (
                "visible RocketMQ/MetaQ name-server, broker connection, offset update, "
                "or message-pull errors indicate broker-side message-queue failure"
            ),
            text,
            extra_props={
                "broker": broker,
                "topic": topic,
                "broker_failure": True,
            },
        )
    ]


def _metaq_duplicate_update_conflict_candidates(
    text: str, *, case_type: str = ""
) -> list[dict[str, Any]]:
    if case_type not in {"metaq", "other", "自定义监控"}:
        return []
    if not METAQ_DUPLICATE_UPDATE_RE.search(text) or not METAQ_CONSUME_CONTEXT_RE.search(text):
        return []
    topic = _metaq_topic_label(text)
    app = _alarm_app(text) or _entity_label(text)
    mail_no = _first_match(METAQ_MAIL_NO_RE, text)
    action = _first_match(METAQ_ACTION_RE, text)
    label = " ".join(
        part
        for part in (
            app,
            topic,
            f"mailNo={mail_no}" if mail_no else "",
            f"action={action}" if action else "",
            "duplicate_update_conflict",
        )
        if part
    )
    return [
        _candidate(
            "pattern_metaq_duplicate_update_conflict",
            label or "metaq duplicate_update_conflict",
            9.1,
            (
                "visible MetaQ consume logs show duplicate/retried message handling and "
                "update-with-version conflict, indicating an application idempotency gap"
            ),
            text,
            extra_props={
                "app": app,
                "topic": topic,
                "mail_no": mail_no,
                "action": action,
                "duplicate_consume": True,
                "version_conflict": True,
            },
        )
    ]


def _metaq_topic_label(text: str) -> str:
    ignored_prefixes = ("MQCLIENT", "ROCKETMQ", "MQRECV", "MQSEND")
    for value in METAQ_TOPIC_TOKEN_RE.findall(text):
        cleaned = value.strip(" .,:;()[]{}<>\"'")
        if cleaned.upper().startswith(ignored_prefixes):
            continue
        return cleaned
    return ""


def _auth_session_failure_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type not in {"", "hsf", "other", "自定义监控"}:
        return []
    if not AUTH_STATUS_RE.search(text) or not AUTH_CONTEXT_RE.search(text):
        return []
    paths = _auth_paths(text)
    app = _alarm_app(text) or _auth_server_app(text) or _first_app_token(text)
    if app in {"alibaba-inc"}:
        app = ""
    scope = _auth_scope(paths)
    marker = "buc_sso_token" if _has_buc_sso_context(text) else "http_401"
    label = " ".join(part for part in (app, scope, marker, "auth_session_failure") if part)
    return [
        _candidate(
            "pattern_auth_session_failure",
            label or "http_401_auth_session_failure",
            8.35,
            (
                "visible HTTP 401/UNAUTHORIZED responses on the proxy or trace path indicate "
                "an authentication/session failure rather than the affected business API itself"
            ),
            text,
            extra_props={
                "app": app,
                "path": scope,
                "status": "401",
                "auth_failure": True,
                "buc_sso_context": _has_buc_sso_context(text),
            },
        )
    ]


def _auth_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in AUTH_PATH_RE.finditer(text):
        value = match.group("url_path") or match.group("field_path") or ""
        path = value.split("?", 1)[0].strip(" ,;")
        if path.startswith("/") and len(path) <= 180:
            paths.append(path)
    return _unique(paths, 8)


def _auth_scope(paths: list[str]) -> str:
    for path in paths:
        parts = [part for part in path.split("/") if part]
        if not parts:
            continue
        if parts[0].lower().startswith("goc"):
            return parts[0]
    if paths:
        return paths[0]
    return ""


def _has_buc_sso_context(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "buc",
            "sso",
            "bucrefreshssotokenerror",
            "token could not be hit",
            "tenant key",
            "login_for_sunfire",
        )
    )


def _auth_server_app(text: str) -> str:
    matches = re.findall(r"\bserver=(?P<app>[a-z0-9][a-z0-9-]*):[^\s;]*", text, re.I)
    for app in matches:
        if app.lower() in {"goc-pass", "wagbridge"}:
            return app.lower()
    return matches[0].lower() if matches else ""


def _mdm_master_data_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type not in {"hsf", "自定义监控", "metaq"}:
        return []
    lower = text.lower()
    if "mdm" not in lower:
        return []
    tables = _mdm_sql_tables(text)
    if not tables:
        return []
    if not BUSINESS_ERROR_RE.search(text):
        return []
    app = _mdm_app(text)
    service = _mdm_service_alias(text)
    table = tables[0]
    label = " ".join(
        part
        for part in (
            app,
            table,
            service,
            "master_data_missing",
        )
        if part
    )
    return [
        _candidate(
            "pattern_mdm_master_data_missing",
            label,
            8.8,
            (
                "visible BIZ_ERROR on the caller plus MDM service/table access indicates "
                "an upstream MDM master-data contract miss, 主数据缺失, causing the interface failure"
            ),
            text,
            extra_props={
                "app": app,
                "sql_table": table,
                "service_method": service,
                "master_data_missing": True,
                "business_error": True,
            },
        )
    ]


def _tddl_read_traffic_source_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type != "tddl":
        return []
    lower = text.lower()
    if "middleware_tddl_read_qps" not in lower and "tddl读qps" not in text:
        return []
    if not any(marker in lower for marker in ("trend=rising", "读qps", "read_qps")):
        return []
    calls = _trace_calls(text)
    if not calls:
        return []
    tables = _sql_table_entities(text)
    if not tables:
        return []
    best_call = _best_traffic_call(calls)
    if not best_call:
        return []
    client, server, service = best_call
    if client == server:
        return []
    table = _best_table_for_service(tables, server, service)
    if not table:
        return []
    alias = _service_method_alias(service) or service
    label = f"{client} -> {server} {alias} {table} read_qps_traffic_source"
    return [
        _candidate(
            "pattern_tddl_read_traffic_source",
            label,
            8.95,
            (
                "visible TDDL read_qps spike plus trace path identifies 流量来源: "
                "an upstream application repeatedly calls the provider interface, which issues "
                "TDDL reads against the database table"
            ),
            text,
            extra_props={
                "client_app": client,
                "server_app": server,
                "service_method": service,
                "sql_table": table,
                "traffic_source": True,
                "read_qps_spike": True,
            },
        )
    ]


def _schedulerx_batch_load_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    lower = text.lower()
    if case_type not in {"tddl", "sql", "rds", "自定义监控"}:
        return []
    if not SCHEDULERX_TRIGGER_RE.search(text):
        return []
    if not RDS_CPU_CONTEXT_RE.search(text) and not any(
        marker in lower for marker in ("rds", "数据库", "db cpu")
    ):
        return []
    job_ids = _scheduler_job_ids(text)
    if not job_ids:
        return []
    app = _alarm_app(text) or _first_app_token(text) or "schedulerx"
    label = f"{app} SchedulerX job {job_ids[0]} rds_cpu_load"
    return [
        _candidate(
            "pattern_schedulerx_batch_load",
            label,
            8.85,
            (
                "visible SchedulerX job start/end events near an RDS CPU or TDDL alarm indicate "
                "a scheduled batch job driving database load"
            ),
            text,
            extra_props={
                "app": app,
                "job_id": job_ids[0],
                "job_ids": job_ids,
                "schedulerx": True,
                "batch_job": True,
                "rds_cpu_load": True,
            },
        )
    ]


def _scheduler_job_ids(text: str) -> list[str]:
    events: dict[str, dict[str, Any]] = {}
    for match in SCHEDULERX_EVENT_SEGMENT_RE.finditer(text):
        subject = match.group("subject")
        phase = match.group("phase").lower()
        try:
            event_time = int(match.group("time"))
        except ValueError:
            continue
        item = events.setdefault(subject, {"job": match.group("job"), "start": [], "end": []})
        item[phase].append(event_time)
    durations: list[tuple[int, str]] = []
    for item in events.values():
        starts = item.get("start") or []
        ends = item.get("end") or []
        if not starts or not ends:
            continue
        duration = max(ends) - min(starts)
        if duration > 0:
            durations.append((duration, str(item.get("job") or "")))
    if durations:
        durations.sort(key=lambda item: (-item[0], item[1]))
        return _unique([job for _duration, job in durations if job], 8)
    return _unique(SCHEDULERX_JOB_ID_RE.findall(text), 8)


def _alarm_app(text: str) -> str:
    match = re.search(r"\balarm\s+app=([a-z0-9][a-z0-9-]*)\b", text, re.I)
    return match.group(1).lower() if match else ""


def _first_app_token(text: str) -> str:
    match = re.search(r"\b([a-z][a-z0-9]+(?:-[a-z0-9]+){1,8})\b", text, re.I)
    return match.group(1).lower() if match else ""


def _notify_server_app(text: str) -> str:
    match = re.search(r"\bserver=([a-z0-9][a-z0-9-]*):[^\s;]*\s+service=Notify@recv", text, re.I)
    return match.group(1).lower() if match else ""


def _mdm_app(text: str) -> str:
    match = MDM_APP_RE.search(text)
    return match.group("app").lower() if match else ""


def _mdm_service_alias(text: str) -> str:
    fallback: str = ""
    for match in FACADE_METHOD_RE.finditer(text):
        alias = f"{match.group('class')}.{match.group('method')}"
        if match.group("method").lower() == "sync":
            return alias
        if not fallback:
            fallback = alias
    for service in _unique(SERVICEISH_RE.findall(text), 24):
        alias = _service_method_alias(service)
        if alias and ("mdm" in service.lower() or "facade" in alias.lower()):
            return alias
    if fallback:
        return fallback
    return ""


def _trace_calls(text: str) -> list[tuple[str, str, str, str]]:
    calls: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in TRACE_CALL_RE.finditer(text):
        key = (
            match.group("client").lower(),
            match.group("server").lower(),
            match.group("service").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        calls.append(
            (
                match.group("client").lower(),
                match.group("server").lower(),
                match.group("service"),
                match.group("result"),
            )
        )
    return calls


def _best_traffic_call(calls: list[tuple[str, str, str, str]]) -> tuple[str, str, str] | None:
    best: tuple[int, tuple[str, str, str]] | None = None
    for client, server, service, result in calls:
        service_lower = service.lower()
        if "tddl_" in service_lower or "notify@" in service_lower or "mqrecv@" in service_lower:
            continue
        if "-" not in client or "-" not in server:
            continue
        score = 0
        if "supplier" in service_lower or "query" in service_lower or "get" in service_lower:
            score += 2
        if result.lower() not in {"0", "00", "200", "302", "00/ok", "0/ok"}:
            score += 1
        if client != server:
            score += 1
        candidate = (client, server, service)
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best else None


def _sql_table_entities(text: str) -> list[tuple[str, str, int]]:
    counts: Counter[tuple[str, str]] = Counter()
    for match in TDDL_SPAN_ENTITY_RE.finditer(text):
        db = _clean_sql_part(match.group("db"))
        table = _clean_sql_part(match.group("table") or "")
        if table:
            counts[(db, table)] += 1
    for block in SQL_TABLE_SUMMARY_BLOCK_RE.finditer(text):
        for match in SUMMARY_SQL_TABLE_RE.finditer(block.group("body")):
            table = _clean_sql_part(match.group("table"))
            if table:
                counts[("", table)] += int(match.group("count"))
    return [(db, table, count) for (db, table), count in counts.most_common(20)]


def _mdm_sql_tables(text: str) -> list[str]:
    counts: Counter[str] = Counter()
    for match in TDDL_SPAN_ENTITY_RE.finditer(text):
        table = _clean_sql_part(match.group("table") or "")
        if _is_mdm_table(table):
            counts[table] += 1
    for block in SQL_TABLE_SUMMARY_BLOCK_RE.finditer(text):
        for match in SUMMARY_SQL_TABLE_RE.finditer(block.group("body")):
            table = _clean_sql_part(match.group("table"))
            if _is_mdm_table(table):
                counts[table] += int(match.group("count"))
    if not counts:
        for table in MDM_TABLE_NAME_RE.findall(text):
            cleaned = _clean_sql_part(table)
            if _is_mdm_table(cleaned):
                counts[cleaned] += 1
    return [table for table, _count in counts.most_common(10)]


def _is_mdm_table(value: str) -> bool:
    return (
        value.startswith("mdm_") and "__" not in value and not value.endswith(("_host", "_group"))
    )


def _best_table_for_service(
    tables: list[tuple[str, str, int]],
    server: str,
    service: str,
) -> str:
    if not tables:
        return ""
    best: tuple[float, str] | None = None
    context_tokens = set(_name_tokens(f"{server} {service}"))
    context_text = f"{server} {service}".lower()
    for _db, table, count in tables:
        table_tokens = set(_name_tokens(table))
        score = min(count, 50) / 20.0
        shared = table_tokens & context_tokens
        score += 4.0 * len(shared)
        score += 4.0 * sum(1 for token in table_tokens - shared if token in context_text)
        if table == server.replace("-", "_"):
            score += 2.0
        if best is None or score > best[0]:
            best = (score, table)
    return best[1] if best else ""


def _clean_sql_part(value: str) -> str:
    return value.strip("`'\"[](){}<>，,.;:").lower()


def _name_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-z0-9]+", value.lower())
        if len(token) >= 3 and token not in {"com", "org", "net", "client", "service"}
    ]


def _app_publish_data_quality_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if not APP_PUBLISH_TRIGGER_RE.search(text):
        return []
    if not QUALIFICATION_FAILURE_RE.search(text):
        return []
    deploy_id = _first_match(DEPLOY_ID_RE, text)
    deploy_version = _first_match(DEPLOY_VERSION_RE, text)
    app = _app_from_publish_context(text) or _entity_label(text)
    release = (
        f"deploy_id={deploy_id}"
        if deploy_id
        else f"deploy_version={deploy_version}"
        if deploy_version
        else ""
    )
    label = " ".join(part for part in (app, release, "publish_no_qualification") if part)
    return [
        _candidate(
            "pattern_app_publish_data_quality",
            label or "app_publish_no_qualification",
            8.65,
            (
                "visible Aone/Normandy application publish event overlaps a "
                "NO_QUALIFICATION or qualification-check business failure, indicating "
                "a release or service-lifecycle regression rather than isolated bad data"
            ),
            text,
            extra_props={
                "app": app,
                "deploy_id": deploy_id,
                "deploy_version": deploy_version,
                "publish_event": True,
                "business_symptom": "NO_QUALIFICATION",
            },
        )
    ]


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def _app_from_publish_context(text: str) -> str:
    match = APP_FIELD_RE.search(text)
    if not match:
        return ""
    return (match.group("app") or match.group("summary_app") or "").lower()


def _downstream_offline_change_candidates(
    text: str, *, case_type: str = ""
) -> list[dict[str, Any]]:
    if case_type not in {"hsf", "jvm", "自定义监控"}:
        return []
    if not DOWNSTREAM_OFFLINE_CHANGE_RE.search(text):
        return []
    lower = text.lower()
    if not (
        THREADPOOL_TRIGGER_RE.search(text)
        or "hsftimeoutexception" in lower
        or "hsf调用超时" in text
        or "timeout" in lower
    ):
        return []
    app = _app_from_change_context(text) or _entity_label(text)
    if app and not _app_has_failure_context(text, app):
        return []
    change_id = _first_match(CHANGE_ID_RE, text)
    label = " ".join(
        part
        for part in (app, f"change_id={change_id}" if change_id else "", "offline_capacity_change")
        if part
    )
    return [
        _candidate(
            "pattern_downstream_offline_change",
            label or "downstream_offline_capacity_change",
            8.7,
            (
                "visible downstream offline/config change overlaps HSF timeout or "
                "thread-pool saturation evidence, indicating capacity loss in the "
                "called service rather than a caller-side symptom"
            ),
            text,
            extra_props={
                "app": app,
                "change_id": change_id,
                "offline_change": True,
                "capacity_change": True,
            },
        )
    ]


def _instance_count_offline_change_candidates(
    text: str, *, case_type: str = ""
) -> list[dict[str, Any]]:
    if not INSTANCE_COUNT_DROP_RE.search(text):
        return []
    if not NORMANDY_OFFLINE_RE.search(text):
        return []
    app = _app_from_capacity_context(text)
    change_id = _first_normandy_offline_change_id(text) or _first_match(CHANGE_ID_RE, text)
    label = " ".join(
        part
        for part in (
            app,
            f"change_id={change_id}" if change_id else "",
            "normandy_offline_capacity_drop",
        )
        if part
    )
    return [
        _candidate(
            "pattern_instance_count_drop_offline_change",
            label or "normandy_offline_capacity_drop",
            9.25,
            (
                "visible machine or instance-count drop alarm overlaps Normandy "
                "OFFLINE_HOST events, indicating active capacity removal rather "
                "than an application Trace side error"
            ),
            text,
            extra_props={
                "app": app,
                "change_id": change_id,
                "change_system": "normandy-director",
                "change_type": "OFFLINE_HOST",
                "capacity_change": True,
                "instance_count_drop": True,
            },
        )
    ]


def _app_from_capacity_context(text: str) -> str:
    for pattern in (APP_GROUP_RE, BRACKET_APP_GROUP_RE):
        match = pattern.search(text)
        if match:
            app = _normalize_app_group(match.group("group"))
            if app:
                return app
    return _app_from_change_context(text) or _entity_label(text)


def _normalize_app_group(value: str) -> str:
    group = value.strip().lower().strip("'\"[](),.;")
    if not group:
        return ""
    if ":" in group:
        group = group.split(":", 1)[0]
    for marker in (".cn.prodhost", ".prodhost", ".hirerhost", ".host"):
        if marker in group:
            group = group.split(marker, 1)[0]
            break
    group = re.sub(r"(?:[_-]?(?:prod)?host|[_-]?hirerhost)$", "", group)
    return group.strip("._-")


def _first_normandy_offline_change_id(text: str) -> str:
    for segment in _evidence_segments(text):
        if not NORMANDY_OFFLINE_RE.search(segment):
            continue
        change_id = _first_match(CHANGE_ID_RE, segment)
        if change_id:
            return change_id
    return ""


def _app_from_change_context(text: str) -> str:
    app_match = CHANGE_APP_RE.search(text)
    if app_match:
        return app_match.group("app").lower()
    match = CHANGE_SUMMARY_APP_RE.search(text)
    return match.group("app").lower() if match else ""


def _app_has_failure_context(text: str, app: str) -> bool:
    app_lower = app.lower()
    if not app_lower:
        return False
    relation_re = re.compile(
        rf"(?:remote_app_name={re.escape(app_lower)}\b|server={re.escape(app_lower)}:|"
        rf"->\s*{re.escape(app_lower)}:|\bapp_group=[^\s,;\]]*{re.escape(app_lower)}[^\s,;\]]*)",
        re.I,
    )
    failure_re = re.compile(
        r"hsftimeoutexception|hsf调用超时|timeout|rpc_error|"
        r"resulttype=0?[234]|rc=0?[234]|error_qps|success_rate|trend=rising|trend=falling",
        re.I,
    )
    for segment in _evidence_segments(text):
        if app_lower not in segment.lower():
            continue
        if relation_re.search(segment) and (
            THREADPOOL_TRIGGER_RE.search(segment) or failure_re.search(segment)
        ):
            return True
    return False


def _evidence_segments(text: str) -> list[str]:
    segments: list[str] = []
    for line in text.splitlines():
        parts = re.split(
            r";\s+(?=(?:client|server|service|metric|sourceProduct|change_|remote_app_name|app_group)=)",
            line,
        )
        segments.extend(part.strip() for part in parts if part.strip())
    return segments


def _hsf_capacity_change_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type != "hsf":
        return []
    lower = text.lower()
    if not CHANGE_COUNT_RE.search(text):
        return []
    if "_offline_host" not in lower and ".offline" not in lower:
        return []
    if not any(marker in lower for marker in ("timeout", "result=03", "result=3", "success_rate")):
        return []
    service_method = _offline_hsf_service_method(text)
    if not service_method:
        return []
    app = _offline_app(text)
    label = f"{app}:{service_method}" if app else service_method
    return [
        _candidate(
            "pattern_capacity_change",
            f"{label} capacity_change_cpu_saturation",
            8.55,
            (
                "visible offline HSF timeout metrics plus change events indicate "
                "downstream interface failure from scale-down or machine-offline "
                "capacity loss, QPS突增, 缩容变更, and CPU打满"
            ),
            text,
            extra_props={
                "app": app,
                "service_method": service_method,
                "offline_group": True,
                "capacity_change": True,
            },
        )
    ]


def _hsf_cold_start_capacity_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type != "hsf":
        return []
    lower = text.lower()
    candidates = _hsf_grayhost_cold_start_candidates(text)
    if "none_core_host" not in lower:
        return candidates
    if not CHANGE_COUNT_RE.search(text):
        return candidates
    if not any(
        marker in lower for marker in ("timeout", "result=03", "result=3", "success_rate", "_rt")
    ):
        return candidates
    app, group = _none_core_group(text)
    if not group:
        return candidates
    label = f"{app or group} {group} cold_start_high_load expansion_change"
    candidates.append(
        _candidate(
            "pattern_hsf_cold_start_capacity",
            label,
            8.75,
            (
                "visible HSF timeout/RT evidence on none_core_host plus change events "
                "indicates newly expanded downstream machines with cold-start high load, "
                "新扩容机器负载高, and 扩容变更关联"
            ),
            text,
            extra_props={
                "app": app,
                "host_group": group,
                "cold_start": True,
                "capacity_change": True,
            },
        )
    )
    return candidates


def _hsf_grayhost_cold_start_candidates(text: str) -> list[dict[str, Any]]:
    lower = text.lower()
    if "_grayhost" not in lower:
        return []
    has_rt_metric_name = "middleware_hsf_consumer_service_method_rt" in lower
    groups: list[str] = []
    service_method = ""
    max_rt = 0.0
    blocks = [*METRIC_SERIES_BLOCK_RE.findall(text), *_grayhost_context_windows(text)]
    for block in blocks:
        if "_grayhost" not in block.lower():
            continue
        if not _has_rising_trend(block):
            continue
        block_groups = [match.group("group").lower() for match in GRAYHOST_GROUP_RE.finditer(block)]
        if not block_groups:
            continue
        block_max = _metric_max(block)
        block_service_method = _hsf_block_service_method(block)
        if not block_service_method:
            continue
        if not has_rt_metric_name and block_max < 2.0:
            continue
        regional_groups = [group for group in block_groups if _grayhost_region(group)]
        if not regional_groups:
            continue
        groups.extend(regional_groups)
        max_rt = max(max_rt, block_max)
        service_method = service_method or block_service_method
    groups = _unique(groups, 4)
    if not groups:
        return []
    app = _grayhost_app(groups[0])
    region_hint = ",".join(_grayhost_region(group) for group in groups if _grayhost_region(group))
    root = app or groups[0]
    label_parts = [root, ",".join(groups)]
    if region_hint:
        label_parts.append(f"groups={region_hint}")
    if service_method:
        label_parts.append(service_method)
    label_parts.append("grayhost_cold_start_high_rt")
    return [
        _candidate(
            "pattern_hsf_cold_start_capacity",
            " ".join(label_parts),
            8.35 + min(0.35, max_rt / 300.0),
            (
                "visible HSF consumer RT metric rises on remote grayhost provider "
                "groups, indicating newly added or gray traffic machines with "
                "cold-start high RT rather than a generic consumer-side symptom"
            ),
            text,
            extra_props={
                "app": app,
                "host_group": groups[0],
                "host_groups": groups,
                "grayhost": True,
                "cold_start": True,
                "service_method": service_method,
                "max_rt": max_rt,
            },
        )
    ]


def _hsf_block_service_method(text: str) -> str:
    service_match = re.search(r'\bservice(?:=|["\']?\s*:\s*["\'])([^,"\]\s]+)', text, re.I)
    method_match = re.search(r'\bmethod(?:=|["\']?\s*:\s*["\'])([^,"\]\s]+)', text, re.I)
    service = service_match.group(1) if service_match else ""
    method = method_match.group(1) if method_match else ""
    if service and method:
        return f"{service}#{method}"
    return service or method


def _has_rising_trend(text: str) -> bool:
    return bool(
        re.search(r'\btrend(?:=|["\']?\s*:\s*["\'])rising\b', text, re.I)
        or re.search(r"(^|[\s,;])rising($|[\s,;])", text, re.I)
    )


def _grayhost_context_windows(text: str) -> list[str]:
    lines = text.splitlines()
    windows: list[str] = []
    for index, line in enumerate(lines):
        if "_grayhost" not in line.lower():
            continue
        start = max(0, index - 8)
        end = min(len(lines), index + 4)
        windows.append("\n".join(lines[start:end]))
    return windows or ([text] if "_grayhost" in text.lower() else [])


def _grayhost_app(group: str) -> str:
    return re.sub(r"_[a-z]{2}\d+_grayhost$", "", group.lower()).removesuffix("_grayhost")


def _grayhost_region(group: str) -> str:
    match = re.search(r"_([a-z]{2}\d+)_grayhost$", group.lower())
    return match.group(1) if match else ""


def _none_core_group(text: str) -> tuple[str, str]:
    match = NONE_CORE_SERVER_RE.search(text)
    if match:
        return match.group("app").lower(), match.group("group").lower()
    match = NONE_CORE_GROUP_RE.search(text)
    if not match:
        return "", ""
    group = match.group("group").lower()
    app = group.removesuffix("_none_core_host")
    return app, group


def _infra_event_candidates(text: str) -> list[dict[str, Any]]:
    instances = _unique(ECS_INSTANCE_RE.findall(text), 5)
    if not instances:
        return []
    if not INFRA_EVENT_STRONG_RE.search(text):
        return []
    if not _has_hardware_fault(text):
        return []
    lower = text.lower()
    if "healthstatuschange" in lower and not (
        "systemmaintenance" in lower
        or "hardware" in lower
        or "memory error" in lower
        or "hostrisk" in lower
        or "硬件" in text
        or "内存" in text
    ):
        return []
    label = _infra_event_label(instances[0], text)
    return [
        _candidate(
            "pattern_infra_event",
            label,
            8.7,
            (
                "visible ECS event indicates host hardware or memory fault; "
                "this infrastructure root can make containers or TCP probes unreachable"
            ),
            text,
            extra_props={
                "instance_id": instances[0],
                "event_source": "ecs",
                "hardware_fault": _has_hardware_fault(text),
                "weak_runtime_signal": bool(INFRA_EVENT_WEAK_RE.search(text)),
            },
        )
    ]


def _infra_event_label(instance: str, text: str) -> str:
    if _has_hardware_fault(text):
        if "memory error" in text.lower() or "内存" in text:
            return f"{instance} hardware_memory_fault"
        return f"{instance} hardware_fault"
    return f"{instance} ecs_infra_event"


def _has_hardware_fault(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in ("hardware", "memory error", "hostrisk")) or any(
        marker in text for marker in ("硬件", "内存")
    )


def _tddl_repeated_query_fanout_candidates(
    text: str, *, case_type: str = ""
) -> list[dict[str, Any]]:
    if case_type not in {"", "hsf", "tddl", "rds", "sql", "自定义监控"}:
        return []
    lower = text.lower()
    if "sql_tables=" not in lower and "tddl_query@" not in lower:
        return []
    repeated = _dominant_repeated_sql_table(_sql_table_entities(text))
    if repeated is None:
        return []
    db, table, count = repeated
    if count < SQL_REPEATED_QUERY_MIN_COUNT:
        return []
    hsf_segment = _primary_hsf_timeout_segment(text)
    explicit_repetition = bool(
        re.search(
            r"n\+1|repeated[_ -]?query|重复查询|TDDL-\d+|Query execution was interrupted",
            text,
            re.I,
        )
    )
    if hsf_segment is None and not explicit_repetition:
        return []
    service = str(hsf_segment.get("service") or "") if hsf_segment else ""
    app = _server_app(str(hsf_segment.get("server") or "")) if hsf_segment else ""
    if hsf_segment is not None and app and not _table_has_sql_top_client(text, table, app):
        return []
    alias = _service_method_alias(service)
    label_parts = [table, "repeated_sql_fanout"]
    if app:
        label_parts.append(app)
    if alias:
        label_parts.append(alias)
    score = 8.55 + min(0.55, count / 80.0)
    if hsf_segment:
        score += 0.35
        if float(hsf_segment.get("duration_ms") or 0.0) >= 2_000.0:
            score += 0.2
    return [
        _candidate(
            "pattern_tddl_repeated_query_fanout",
            " ".join(label_parts),
            round(score, 3),
            (
                "visible trace evidence shows repeated TDDL queries against the same table "
                "inside one downstream service call, indicating N+1 or fanout SQL work that "
                "can amplify HSF latency or timeout"
            ),
            text,
            extra_props={
                "db": db,
                "sql_table": table,
                "repeat_count": count,
                "service_method": service,
                "method_alias": alias,
                "app": app,
                "failure_mode": "tddl_repeated_query_fanout",
                "repeated_query_fanout": True,
                "hsf_downstream_timeout": bool(hsf_segment),
            },
        )
    ]


def _dominant_repeated_sql_table(tables: list[tuple[str, str, int]]) -> tuple[str, str, int] | None:
    counts: Counter[str] = Counter()
    db_by_table: dict[str, str] = {}
    for db, table, count in tables:
        if not table or table.upper() in NOISY_SQL_TABLES:
            continue
        counts[table] += max(1, count)
        if db and table not in db_by_table:
            db_by_table[table] = db
    if not counts:
        return None
    table, count = counts.most_common(1)[0]
    return db_by_table.get(table, ""), table, count


def _table_has_sql_top_client(text: str, table: str, app: str) -> bool:
    if not table or not app:
        return False
    table_pattern = re.escape(table)
    app_pattern = re.escape(app)
    return bool(
        re.search(
            rf"\bclient={app_pattern}:[^;\s]*[^;]*\bTDDL_[A-Z]+@[^;:]+:{table_pattern}\b",
            text,
            re.I,
        )
    )


def _primary_hsf_timeout_segment(text: str) -> dict[str, Any] | None:
    segments = _hsf_timeout_segments(text)
    return segments[0] if segments else None


def _hsf_threadpool_timeout_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type not in {"hsf", "自定义监控"}:
        return []
    if "topology path" not in text.lower() and "hsf_error" not in text.lower():
        return []
    if _has_security_scan_signal(text):
        return []
    candidates: list[dict[str, Any]] = []
    has_direct_threadpool = _has_direct_hsf_threadpool_signal(text)
    for segment in _hsf_timeout_segments(text):
        server = segment["server"]
        service = segment["service"]
        if _is_special_hsf_host(server, service) and not _server_group(server).endswith(
            "_default_production"
        ):
            continue
        app = _server_app(server)
        alias = _service_method_alias(service)
        if not app:
            continue
        alarm_app = _alarm_app(text)
        client_app = _server_app(str(segment.get("client") or ""))
        single_word_downstream = bool(
            alias
            and alarm_app
            and client_app == alarm_app
            and app != alarm_app
            and re.fullmatch(r"[a-z][a-z0-9]{3,80}", app)
        )
        if "-" not in app and not single_word_downstream:
            continue
        group = _server_group(server)
        group_label = _hsf_group_label(app, group)
        kind = (
            "pattern_hsf_threadpool_timeout"
            if has_direct_threadpool
            else "pattern_hsf_downstream_timeout"
        )
        mechanism_label = "threadpool_busy" if has_direct_threadpool else "downstream_timeout"
        label = " ".join(
            part for part in (app, group_label, alias or service, mechanism_label) if part
        )
        if segment["server_ip"]:
            label = f"{label}@{segment['server_ip']}"
        score = (8.35 if has_direct_threadpool else 8.05) + min(
            0.45,
            float(segment["duration_ms"]) / 30_000.0,
        )
        root_boundary_bonus = _hsf_timeout_root_boundary_bonus(text, segment)
        score += root_boundary_bonus
        if _service_has_metric_error_context(text, service):
            score += 0.25
        if group_label and not has_direct_threadpool:
            score += 0.22
        candidates.append(
            _candidate(
                kind,
                label,
                round(score, 3),
                _hsf_timeout_reason(has_direct_threadpool),
                segment["raw_text"],
                extra_props={
                    "app": app,
                    "app_group": group,
                    "service_method": service,
                    "method_alias": alias,
                    "server_ip": segment["server_ip"],
                    "failure_mode": (
                        "hsf_threadpool_busy_timeout"
                        if has_direct_threadpool
                        else "hsf_downstream_timeout"
                    ),
                    "threadpool_busy": has_direct_threadpool,
                    "failure_count": int(segment.get("failure_count") or 1),
                    "provider_ip_count": len(segment.get("provider_ips") or {}),
                    "consumer_ip_count": len(segment.get("consumer_ips") or {}),
                    "root_boundary_bonus": round(root_boundary_bonus, 3),
                },
            )
        )
    return candidates


def _hsf_provider_subset_rpc_error_candidates(
    text: str, *, case_type: str = ""
) -> list[dict[str, Any]]:
    if case_type not in {"hsf", "自定义监控", "异常日志"}:
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for segment in _hsf_trace_error_segments(text):
        result_text = " ".join(segment.get("result_codes") or {}).lower()
        if "rpc" not in result_text or "timeout" in result_text:
            continue
        failure_count = int(segment.get("failure_count") or 0)
        provider_ips = (
            segment.get("provider_ips") if isinstance(segment.get("provider_ips"), dict) else {}
        )
        consumer_ips = (
            segment.get("consumer_ips") if isinstance(segment.get("consumer_ips"), dict) else {}
        )
        if failure_count < 3 or len(provider_ips) < 1:
            continue
        if len(consumer_ips) < 2 and failure_count < 5:
            continue
        app = _server_app(str(segment["server"]))
        if not app:
            continue
        service = str(segment["service"])
        alias = _service_method_alias(service)
        group = _server_group(str(segment["server"]))
        group_label = _hsf_group_label(app, group)
        key = (app, service)
        if key in seen:
            continue
        seen.add(key)
        label = " ".join(
            part
            for part in (app, group_label, alias or service, "provider_subset_rpc_error")
            if part
        )
        score = 7.75 + min(0.55, failure_count / 40.0) + min(0.25, len(consumer_ips) / 12.0)
        candidates.append(
            _candidate(
                "pattern_hsf_provider_subset_rpc_error",
                label,
                round(score, 3),
                (
                    "visible HSF trace errors are concentrated on a downstream provider "
                    "service across multiple consumer hosts, indicating provider-subset "
                    "RPC_ERROR; do not infer a deeper serialization or code mechanism "
                    "without matching log evidence"
                ),
                str(segment["raw_text"]),
                extra_props={
                    "app": app,
                    "app_group": group,
                    "service_method": service,
                    "method_alias": alias,
                    "failure_mode": "hsf_provider_subset_rpc_error",
                    "failure_count": failure_count,
                    "provider_ips": provider_ips,
                    "consumer_ips": consumer_ips,
                    "provider_subset_rpc_error": True,
                    "soft_mechanism": True,
                },
            )
        )
    return candidates


def _hsf_timeout_reason(has_direct_threadpool: bool) -> str:
    if has_direct_threadpool:
        return (
            "visible HSF topology plus direct thread-pool evidence shows target interface "
            "failure or timeout from provider threadpool_busy, queue saturation, or rejected requests"
        )
    return (
        "visible HSF trace/topology evidence shows target downstream interface TIMEOUT; "
        "preserve the target app/service boundary without claiming provider-pool saturation"
    )


def _hsf_timeout_segments(text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for segment in [*_hsf_topology_segments(text), *_hsf_trace_error_segments(text)]:
        if not _segment_is_timeout(segment):
            continue
        if float(segment["duration_ms"]) < _hsf_timeout_min_duration(segment):
            continue
        key = (
            str(segment["server"]).lower(),
            str(segment["service"]).lower(),
            str(segment.get("server_ip") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(segment)
        if len(output) >= 8:
            break
    return sorted(output, key=lambda item: _hsf_timeout_segment_score(text, item), reverse=True)


def _hsf_topology_segments(text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for match in HSF_TOPOLOGY_SEGMENT_RE.finditer(text):
        try:
            duration = float(match.group("duration"))
        except ValueError:
            duration = 0.0
        output.append(
            {
                "client": match.group("client"),
                "server": match.group("server"),
                "service": match.group("service"),
                "server_ip": match.group("server_ip") or "",
                "duration_ms": duration,
                "result_codes": {match.group("rc"): 1},
                "failure_count": 1,
                "provider_ips": {match.group("server_ip"): 1} if match.group("server_ip") else {},
                "consumer_ips": {},
                "raw_text": match.group(0),
                "source_kind": "topology",
            }
        )
    return output


def _hsf_trace_error_segments(text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for match in HSF_TRACE_ERROR_SEGMENT_RE.finditer(text):
        try:
            duration = float(match.group("duration"))
        except ValueError:
            duration = 0.0
        provider_ips = _count_pairs(match.group("provider_ips") or "")
        output.append(
            {
                "client": match.group("client"),
                "server": match.group("server"),
                "service": match.group("service"),
                "server_ip": next(iter(provider_ips), ""),
                "duration_ms": duration,
                "result_codes": _count_pairs(match.group("result_codes") or ""),
                "failure_count": int(match.group("failures") or 0),
                "provider_ips": provider_ips,
                "consumer_ips": _count_pairs(match.group("consumer_ips") or ""),
                "raw_text": match.group(0),
                "source_kind": "trace_error",
            }
        )
    return output


def _count_pairs(text: str) -> dict[str, int]:
    output: dict[str, int] = {}
    for match in COUNT_PAIR_RE.finditer(text):
        try:
            output[match.group("key")] = int(match.group("count"))
        except ValueError:
            continue
    return output


def _segment_is_timeout(segment: dict[str, Any]) -> bool:
    result_text = " ".join(str(value) for value in (segment.get("result_codes") or {})).lower()
    return "timeout" in result_text or bool(re.search(r"\b0?3\b", result_text))


def _hsf_timeout_min_duration(segment: dict[str, Any]) -> float:
    return 500.0 if segment.get("source_kind") == "trace_error" else 2_000.0


def _hsf_timeout_segment_score(text: str, item: dict[str, Any]) -> float:
    score = float(item["duration_ms"])
    if item["server_ip"]:
        score += 500.0
    if _service_has_metric_error_context(text, str(item["service"])):
        score += 1_000.0
    alarm_app = _alarm_app(text)
    if alarm_app:
        client_app = _server_app(str(item.get("client") or ""))
        server_app = _server_app(str(item["server"]))
        if client_app == alarm_app and server_app != alarm_app:
            score += 2_000.0
        if server_app == alarm_app:
            score -= 2_000.0
    return score


def _hsf_timeout_root_boundary_bonus(text: str, item: dict[str, Any]) -> float:
    alarm_app = _alarm_app(text)
    if not alarm_app:
        return 0.0
    client_app = _server_app(str(item.get("client") or ""))
    server_app = _server_app(str(item["server"]))
    if client_app == alarm_app and server_app != alarm_app:
        return 0.45
    if server_app == alarm_app:
        return -0.45
    return 0.0


def _hsf_provider_error_qps_spike_candidates(
    text: str, *, case_type: str = ""
) -> list[dict[str, Any]]:
    if case_type != "hsf":
        return []
    if not _has_hsf_provider_success_rate_context(text):
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for block in _hsf_provider_error_qps_blocks(text):
        service = _metric_block_value(block, "service")
        method = _metric_block_value(block, "method")
        app_group = _metric_block_value(block, "app_group")
        if not service or not method:
            continue
        maximum = _metric_value(block, METRIC_MAX_RE, "max")
        average = _metric_value(block, METRIC_AVG_RE, "avg")
        trend = _metric_block_value(block, "trend") or _metric_trend(block)
        if trend.lower() != "rising":
            continue
        if maximum < HSF_PROVIDER_ERROR_QPS_MIN_MAX or average < HSF_PROVIDER_ERROR_QPS_MIN_AVG:
            continue
        if (
            maximum >= HSF_PROVIDER_ERROR_QPS_HARD_LIMIT_MAX
            and average >= HSF_PROVIDER_ERROR_QPS_HARD_LIMIT_AVG
        ):
            continue
        key = (app_group.lower(), service.lower(), method.lower())
        if key in seen:
            continue
        seen.add(key)
        alias = _service_method_alias(f"{service}@{method}")
        app = _normalize_app_group(app_group)
        label = f"{app_group or app} {alias or f'{service}#{method}'} provider_error_qps_spike"
        score = 7.35 + min(0.35, maximum / 2000.0) + min(0.15, average / 100.0)
        candidates.append(
            _candidate(
                "pattern_hsf_provider_error_qps_spike",
                label.strip(),
                round(score, 3),
                (
                    "visible HSF provider success-rate alarm plus provider method error_qps "
                    "rising indicates a method-level fast-failure spike; this is a soft "
                    "provider error mechanism, not direct hard-control proof"
                ),
                block,
                extra_props={
                    "app": app,
                    "app_group": app_group,
                    "service_method": f"{service}#{method}",
                    "method_alias": alias,
                    "failure_mode": "hsf_provider_error_qps_spike",
                    "provider_error_qps_spike": True,
                    "qps_max": maximum,
                    "qps_avg": average,
                    "soft_mechanism": True,
                },
            )
        )
    return candidates


def _has_hsf_provider_success_rate_context(text: str) -> bool:
    lower = text.lower()
    return (
        "middleware_hsf_provider_success_rate" in lower
        or "middleware_hsf_provider_service_method_success_rate" in lower
        or "hsf提供者成功率" in text
    )


def _hsf_provider_error_qps_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in METRIC_SERIES_BLOCK_RE.finditer(text):
        prefix = text[max(0, match.start() - 180) : match.start()].lower()
        if "middleware_hsf_provider_service_method_error_qps" not in prefix:
            continue
        blocks.append(match.group(1))
    for match in METRIC_JSON_BLOCK_RE.finditer(text):
        prefix = text[max(0, match.start() - 260) : match.start()].lower()
        if "middleware_hsf_provider_service_method_error_qps" not in prefix:
            continue
        blocks.append(f"{match.group('labels')},{match.group('summary')}")
    return blocks


def _metric_block_value(block: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}=([^,\]\s]+)", block, re.I)
    if match:
        return match.group(1).strip().strip("\"'")
    json_match = re.search(rf'["\']{re.escape(key)}["\']\s*:\s*["\']?([^,"\']+)', block, re.I)
    return json_match.group(1).strip() if json_match else ""


def _metric_trend(block: str) -> str:
    match = METRIC_TREND_RE.search(block)
    return match.group("trend") if match else ""


def _alarm_app(text: str) -> str:
    match = re.search(r"\balarm\s+app=(?P<app>[a-z0-9][a-z0-9-]*)\b", text, re.I)
    return match.group("app").lower() if match else ""


def _service_has_metric_error_context(text: str, service: str) -> bool:
    service_base = service.split("@", 1)[0].split("#", 1)[0]
    service_base = service_base.split(":", 1)[0]
    pattern = re.escape(service_base)
    for match in re.finditer(
        r"metric=middleware_hsf_(?:consumer|provider)_service_method_(?:error_qps|success_rate)[^\[]*top=\[(?P<block>[^\]]+)\]",
        text,
        re.I | re.S,
    ):
        if re.search(pattern, match.group("block"), re.I):
            return True
    return False


def _server_app(server: str) -> str:
    app = server.split(":", 1)[0].strip().lower()
    return "" if app in {"(?)", "unknown-server", "externalapp"} else app


def _server_group(server: str) -> str:
    if ":" not in server:
        return ""
    return server.split(":", 1)[1].strip().lower()


def _hsf_group_label(app: str, group: str) -> str:
    if not app or not group:
        return ""
    default_groups = {
        f"{app}host",
        f"{app}_default_host",
        f"{app}-host",
    }
    return "" if group in default_groups else group


def _has_direct_hsf_threadpool_signal(text: str) -> bool:
    return bool(
        re.search(
            r"THREADPOOL_BUSY|thread pool is full|provider threadpool|hsf[-_ ]?thread|"
            r"HSF线程|线程池(?:打满|满|达到上限|耗尽|饱和)",
            text,
            re.I,
        )
    )


def _is_special_hsf_host(server: str, service: str) -> bool:
    text = f"{server} {service}".lower()
    return any(
        marker in text
        for marker in ("_doomhost", "doomhost", "_offline_host", ".offline", "_none_core_host")
    )


def _service_method_alias(service: str) -> str:
    separator = "@" if "@" in service else "#"
    if separator not in service:
        return ""
    service_name, method = service.split(separator, 1)
    service_name = service_name.split(":", 1)[0]
    class_name = service_name.rsplit(".", 1)[-1]
    method_name = method.split("~", 1)[0].split("/", 1)[0].strip()
    return f"{class_name}.{method_name}" if class_name and method_name else ""


def _offline_hsf_service_method(text: str) -> str:
    best: tuple[int, str] | None = None
    for match in METRIC_SERIES_BLOCK_RE.finditer(text):
        block = match.group(1)
        block_lower = block.lower()
        if ".offline" not in block_lower:
            continue
        prefix = text[max(0, match.start() - 180) : match.start()].lower()
        if "middleware_hsf_consumer_service_method" not in prefix:
            continue
        if "trend=rising" not in block_lower and "trend=falling" not in block_lower:
            continue
        service_match = OFFLINE_SERVICE_METHOD_RE.search(block)
        if not service_match:
            continue
        score = 0
        if "error_qps" in prefix:
            score += 3
        if "_rt" in prefix:
            score += 2
        if "success_rate" in prefix:
            score += 1
        if "trend=rising" in block_lower:
            score += 1
        if _metric_max(block) >= 20.0:
            score += 1
        service = service_match.group("service").strip()
        method = service_match.group("method").strip()
        label = f"{service}#{method}"
        if best is None or score > best[0]:
            best = (score, label)
    if best is not None:
        return best[1]
    service_match = OFFLINE_SERVICE_METHOD_RE.search(text)
    if not service_match:
        return ""
    return f"{service_match.group('service').strip()}#{service_match.group('method').strip()}"


def _offline_app(text: str) -> str:
    match = OFFLINE_SERVER_RE.search(text)
    if match:
        return match.group("app").strip().lower()
    remote_match = re.search(r"\bremote_app_name=(?P<app>[a-z0-9][a-z0-9-]*)", text, re.I)
    return remote_match.group("app").strip().lower() if remote_match else ""


def _metric_max(text: str) -> float:
    return _metric_value(text, METRIC_MAX_RE, "max")


def _metric_value(text: str, pattern: re.Pattern[str], group: str) -> float:
    match = pattern.search(text)
    if not match:
        return 0.0
    try:
        return float(match.group(group))
    except ValueError:
        return 0.0


def _limit_candidates(text: str) -> list[dict[str, Any]]:
    if not LIMIT_TRIGGER_RE.search(text):
        return []
    label = _mechanism_label(text) or "sentinel_limit"
    return [
        _candidate(
            "pattern_limit",
            label,
            7.15,
            "visible log/trace text indicates Sentinel or runtime limiting",
            text,
        )
    ]


def _threadpool_candidates(text: str) -> list[dict[str, Any]]:
    if not THREADPOOL_TRIGGER_RE.search(text):
        return []
    entities = entity_features(text)
    label = (
        entities.get("rds_instances")
        or entities.get("apps")
        or _unique(HOST_RE.findall(text), 3)
        or ["threadpool_busy"]
    )[0]
    return [
        _candidate(
            "pattern_threadpool_busy",
            label,
            7.0,
            "visible log/metric text indicates HSF provider thread pool saturation",
            text,
        )
    ]


def _jvm_memory_candidates(text: str) -> list[dict[str, Any]]:
    if not JVM_MEMORY_TRIGGER_RE.search(text):
        return []
    lower = text.lower()
    if "metric=jvm_gc_fgc" in lower and ZERO_ONLY_METRIC_RE.search(text):
        return []
    label = (
        _unique(HOST_RE.findall(text), 3) or entity_features(text).get("apps") or ["jvm_memory"]
    )[0]
    return [
        _candidate(
            "pattern_jvm_memory",
            label,
            7.25,
            "visible JVM metric/log text indicates memory or Full GC pressure",
            text,
        )
    ]


def _jvm_gc_pressure_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if not JVM_GC_PRESSURE_TRIGGER_RE.search(text):
        return []
    if case_type not in {"cpu", "hsf", "jvm", "自定义监控"} and not JVM_MEMORY_TRIGGER_RE.search(
        text
    ):
        return []
    lower = text.lower()
    metric_text = _primary_metric_series_text(text) if "metric=jvm_gc_" in lower else text
    explicit_label = JVM_GC_ROOT_LABEL_RE.search(text)
    label = (
        explicit_label.group(1) or explicit_label.group(2)
        if explicit_label
        else (
            _unique(HOST_RE.findall(metric_text), 3)
            or entity_features(metric_text).get("apps")
            or ["jvm_gc"]
        )[0]
    )
    score = 6.25
    if "metric=jvm_gc_" in lower or "metric_jvm_gc_" in lower:
        score = 7.35
    if "trend=rising" in lower:
        score += 0.35
    if "g1_old_generation" in lower or "fullgc" in lower or "fgc" in lower:
        score += 0.35
    return [
        _candidate(
            "pattern_jvm_gc_pressure",
            label,
            min(score, 8.0),
            "visible JVM GC metric text indicates GC count or pause-time pressure near the alarm window",
            text,
            extra_props={"runtime_metric": True, "gc_pressure": True},
        )
    ]


def _external_dependency_candidates(text: str) -> list[dict[str, Any]]:
    if not EXTERNAL_DEPENDENCY_TRIGGER_RE.search(text):
        return []
    lower = text.lower()
    if any(marker in lower for marker in CACHE_SYSTEM_MARKERS):
        return []
    if CONNECTION_POOL_TRIGGER_RE.search(text):
        return []
    domains = _domain_candidates(text)
    label = domains[0] if domains else _mechanism_label(text) or "external_dependency"
    return [
        _candidate(
            "pattern_external_dependency",
            label,
            7.7 if domains else 6.4,
            "visible trace/log text indicates downstream external dependency timeout or unreachable connection failure",
            text,
        )
    ]


def _search_dependency_candidates(text: str) -> list[dict[str, Any]]:
    if not SEARCH_DEPENDENCY_TRIGGER_RE.search(text):
        return []
    lower = text.lower()
    if "igraph" not in lower:
        return []
    label = "igraph"
    return [
        _candidate(
            "pattern_search_dependency",
            label,
            8.1,
            "visible IGraph search dependency timeout or search failure",
            text,
        )
    ]


def _connection_pool_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    has_explicit_pool = bool(CONNECTION_POOL_TRIGGER_RE.search(text))
    ips = _unique(HOST_RE.findall(text), 3)
    has_single_host_sql_failure = (
        case_type == "tddl"
        and bool(ips)
        and bool(SINGLE_HOST_SQL_FAILURE_RE.search(text))
        and not any(
            marker in text.lower()
            for marker in ("duplicate entry", "unique key", "慢sql", "慢查询", "slow sql")
        )
    )
    if not has_explicit_pool and not has_single_host_sql_failure:
        return []
    label = (ips or entity_features(text).get("rds_instances") or ["db_connection_pool"])[0]
    return [
        _candidate(
            "pattern_connection_pool",
            label,
            7.6,
            "visible DB/JDBC/TDDL evidence indicates single-host connection pool exhaustion or connection acquisition failure",
            text,
        )
    ]


def _data_quality_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    if case_type == "tair":
        return []
    if not DATA_QUALITY_TRIGGER_RE.search(text):
        return []
    lower = text.lower()
    if "collation_mismatch" in lower or "illegal mix of collations" in lower:
        label = "data_quality:collation_mismatch"
    else:
        label = _mechanism_label(text) or "data_quality_error"
    return [
        _candidate(
            "pattern_data_quality",
            label,
            6.35,
            "visible log text indicates invalid data, duplicate key, collation, or parameter contract failure",
            text,
        )
    ]


def _slow_sql_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    lower = text.lower()
    has_sql = any(marker in lower for marker in SLOW_SQL_MARKERS) or re.search(
        r"\bselect\b.+\bwhere\b", lower, re.S
    )
    if not has_sql and case_type in {"tddl", "rds", "sql"}:
        has_sql = ("tddl_query" in lower or "db@" in lower) and any(
            marker in lower for marker in ("duration", "耗时", "rt", "timeout", "超时")
        )
    if not has_sql:
        return []
    entities = entity_features(text)
    tables = entities.get("sql_tables") or _unique(TABLE_RE.findall(text), 3)
    label = tables[0] if tables else (entities.get("rds_instances") or ["slow_sql"])[0]
    return [
        _candidate(
            "pattern_slow_sql",
            label,
            5.45,
            "visible SQL evidence indicates slow query or table scan",
            text,
        )
    ]


def _mq_spike_candidates(text: str) -> list[dict[str, Any]]:
    lower = text.lower()
    if not any(marker in lower for marker in MQ_SPIKE_MARKERS):
        return []
    is_metric_series = any(marker in lower for marker in MQ_METRIC_MARKERS)
    metric_signal_text = _primary_metric_series_text(text) if is_metric_series else text
    is_metric_signal = is_metric_series and "trend=rising" in metric_signal_text.lower()
    has_text_spike_signal = any(
        marker in lower for marker in ("spike", "激增", "突增", "堆积", "消费")
    )
    if not is_metric_signal and not has_text_spike_signal:
        return []
    topics = _unique(TOPIC_RE.findall(metric_signal_text if is_metric_signal else text), 3)
    has_topic_dimension = bool(topics)
    if not topics:
        for token in re.findall(
            r"\b[A-Za-z][A-Za-z0-9_.:-]*(?:topic|producer|consumer)[A-Za-z0-9_.:-]*\b", text, re.I
        ):
            topics.append(token)
            break
    label = topics[0] if topics else _entity_label(text) or "metaq_message_spike"
    score = 6.9
    if is_metric_signal:
        score = 8.1 if has_topic_dimension else 7.2
    return [
        _candidate(
            "pattern_mq_spike",
            label,
            score,
            (
                "visible MetaQ/RocketMQ metric series indicates message volume spike"
                if is_metric_signal
                else "visible MetaQ/RocketMQ evidence indicates message volume spike"
            ),
            text,
            extra_props={
                "source": "metric",
                "metric_signal": True,
                "topic_dimension": has_topic_dimension,
            }
            if is_metric_signal
            else None,
        )
    ]


def _primary_metric_series_text(text: str) -> str:
    match = METRIC_SERIES_BLOCK_RE.search(text)
    return match.group(1) if match else text


def _cache_timeout_candidates(text: str) -> list[dict[str, Any]]:
    lower = text.lower()
    if not any(marker in lower for marker in CACHE_SYSTEM_MARKERS):
        return []
    if not any(marker in lower for marker in CACHE_TIMEOUT_MARKERS):
        return []
    entities = entity_features(text)
    instances = entities.get("rds_instances") or _unique(RDS_STYLE_RE.findall(text), 3)
    label = instances[0] if instances else "cache_timeout"
    return [
        _candidate(
            "pattern_cache_timeout",
            label,
            5.6,
            "visible Redis/Tair evidence indicates cache timeout",
            text,
        )
    ]


def _host_candidates(text: str, *, case_type: str = "") -> list[dict[str, Any]]:
    lower = text.lower()
    has_explicit_host_anomaly = any(marker in lower for marker in HOST_MARKERS) and any(
        marker in lower for marker in HOST_ABNORMAL_MARKERS
    )
    has_trace_target_anomaly = (
        "topology path" in lower
        and any(marker in lower for marker in TRACE_TARGET_HOST_MARKERS)
        and bool(TRACE_ABNORMAL_RESULT_RE.search(text))
    )
    if case_type == "tair" and has_trace_target_anomaly:
        has_trace_target_anomaly = False
    if DATA_QUALITY_TRIGGER_RE.search(text) and not any(
        marker in lower
        for marker in ("full gc", "threadpool", "线程池", "cpu", "oom", "驱逐", "container", "pod")
    ):
        has_trace_target_anomaly = False
        has_explicit_host_anomaly = False
    if not has_explicit_host_anomaly and not has_trace_target_anomaly:
        return []
    target_hosts = _target_hosts(text)
    ips = [host[1] for host in target_hosts] or _unique(HOST_RE.findall(text), 3)
    if not ips:
        return []
    label = _host_label(target_hosts[0]) if target_hosts else ips[0]
    score = 6.85 if has_trace_target_anomaly else 6.3
    if any(marker in lower for marker in SPECIAL_HOST_GROUP_MARKERS):
        score = max(score, 7.55)
    return [
        _candidate(
            "pattern_host_anomaly",
            label,
            score,
            (
                "visible trace/log evidence indicates single-host target-host timeout "
                "or runtime host anomaly"
            ),
            text,
        )
    ]


def _target_hosts(text: str) -> list[tuple[str, str]]:
    matches = list(
        re.finditer(
            r"\bserver_ip\s*[:=]\s*['\"]?(?P<ip>(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d))",
            text,
            re.I,
        )
    )
    output: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in reversed(matches):
        ip = match.group("ip")
        if ip.lower() in seen:
            continue
        seen.add(ip.lower())
        output.append((_server_before(text[: match.start()]), ip))
        if len(output) >= 5:
            break
    return output


def _server_before(prefix: str) -> str:
    matches = list(re.finditer(r"->\s+(?P<server>[^\s|]+)", prefix))
    if not matches:
        return ""
    server = matches[-1].group("server").strip(" ,;")
    return "" if server in {"(?)", "unknown-server"} else server


def _host_label(host: tuple[str, str]) -> str:
    server, ip = host
    return f"{server}@{ip}" if server else ip


def _candidate(
    kind: str,
    label: str,
    score: float,
    reason: str,
    text: str,
    *,
    extra_props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    props = {"pattern": kind.removeprefix("pattern_")}
    if extra_props:
        props.update(extra_props)
    return {
        "kind": kind,
        "label": label,
        "score": score,
        "reason": f"{reason}: {clip_text(text, 260)}",
        "props": props,
    }


def _entity_label(text: str) -> str:
    entities = entity_features(text)
    for key in ("rds_instances", "apps", "services", "methods", "exceptions", "keywords"):
        values = entities.get(key) or []
        if values:
            return values[0]
    return ""


def _mechanism_label(text: str) -> str:
    domains = _domain_candidates(text)
    if domains:
        return domains[0]
    services = [
        service
        for service in _unique(SERVICEISH_RE.findall(text), 5)
        if not service.lower().endswith(("exception", "error"))
    ]
    if services:
        return services[0].lower()
    entities = entity_features(text)
    for key in ("methods", "exceptions", "apps", "sql_tables", "keywords"):
        values = [
            value for value in entities.get(key) or [] if value not in {"alibaba-inc", "aliyuncs"}
        ]
        if values:
            return values[0]
    return ""


def _domain_candidates(text: str) -> list[str]:
    values = [*_unique(URL_HOST_RE.findall(text), 5), *_unique(DOMAIN_RE.findall(text), 8)]
    output: list[str] = []
    for value in values:
        normalized = value.strip(" .,:;()[]{}<>\"'").lower()
        if not normalized or normalized in PSEUDO_DOMAINS:
            continue
        if normalized in output:
            continue
        output.append(normalized)
    return output


def _strip_local_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_local_refs(item)
            for key, item in value.items()
            if key not in {"raw_path", "raw_ref"}
        }
    if isinstance(value, list):
        return [_strip_local_refs(item) for item in value]
    return value


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = (str(item.get("kind") or ""), str(item.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _unique(values: list[str], limit: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        output.append(normalized)
        if len(output) >= limit:
            break
    return output
