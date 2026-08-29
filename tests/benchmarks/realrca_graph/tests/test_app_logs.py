from __future__ import annotations

from tests.benchmarks.realrca_graph.app_logs import (
    app_log_search_queries,
    app_log_signals,
    rank_app_log_store,
    should_query_app_logs,
    summarize_app_logs,
)
from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.features import infer_modality, infer_root_layer


def _threadpool_rows() -> list[dict[str, object]]:
    content = (
        'respMap={"errorCode":"500","cause":{"@type":"com.taobao.hsf.exception.HSFException",'
        '"exceptionCode":"THREADPOOL_BUSY","message":"THREADPOOL_BUSY\\nerror message : '
        "[HSF-Provider-/33.1.203.42] Error log: Provider's HSF thread pool is full.\"}} "
        "com.alibaba.trade.sao.ext.HsfExtensions.hsfMono"
    )
    return [
        {
            "logItem": {
                "content": content,
                "level": "WARN",
                "logger": "c.a.i.s.s.l.b.u.s.StatusActionUtils",
            },
            "sourceMeta": {"__source__": "33.1.218.149"},
        },
        {
            "logItem": {
                "content": content.replace("33.1.203.42", "33.1.203.42"),
                "level": "WARN",
            },
            "sourceMeta": {"__source__": "33.3.29.95"},
        },
    ]


def _sentinel_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "queryItemSkuPrice error, itemId:712940870068, errMsg:response:{"
                    '"canRetry":true,"errorCode":"UMP_SENTINEL_BLOCK",'
                    '"errorMessage":"ump sentinel block","showErrorMessage":"ump Sentinel限流",'
                    '"success":false}'
                ),
                "level": "ERROR",
                "logger": "c.a.s.i.f.price.impl.PriceFacadeImpl",
                "trace": "214782d917846200305946136e10b9",
            },
            "sourceMeta": {"__source__": "33.5.46.135"},
        }
    ]


def _sql_failure_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "record_type": "SQL",
                "sql_success": "false",
                "sql_id": "com.alibaba.tmi.repository.mybatisplus.account.mapper.AccountBalanceDateMapper.selectList",
                "trace_id": "213dd8f217871035517475239d0d4d",
            },
            "sourceMeta": {"__source__": "33.44.96.71"},
        }
    ]


def _heavy_export_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    'BigBagWideDetailMgrProcessor search, request: {"pagination":{"pageNo":3,'
                    '"pageSize":500},"query":{"inboundBatchCode":{"$in":["D000790",'
                    '"D001514","D000843","D000780","D000841","D000842","D000843-W",'
                    '"D000844","D000845"]}},"userContext":{"requestUri":'
                    '"/api/method/main/bigBagWideDetailMgr/export",'
                    '"warehouseCode":"TRAN_STORE_31116027"}}'
                ),
                "trace": "213e018d17849636567341910e0b6e",
            },
            "sourceMeta": {"__source__": "11.183.88.5"},
        }
    ]


def _metaq_business_failure_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "2026-08-09 00:45:41 [ConsumeMessageThread_12] ERROR "
                    "MQRecv@alscEloanCouponPollingMetaqProducer:"
                    "CID-ALSC-ELOAN-COUPON-POLLING-CONSUMER result=BIZ_ERROR "
                    "msgId=0BCA60CC904672C927F114B167E80023 "
                    "couponCode=8988944833367990735 "
                    "com.alibaba.common.lang.BizException: 未查询到优惠券信息"
                ),
                "trace": "212c4c8e17862075418048755d0f3a",
            },
            "sourceMeta": {"__source__": "33.3.197.56"},
        }
    ]


def _metaq_external_org_failure_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "2026-07-28 11:16:33 [ConsumeMessageThread_7] ERROR "
                    "MQRecv@GL_CREDIT-INNER-NOTIFY-TOPIC_AIPAY_PH002:"
                    "CID_GL_CREDIT_INNER_NOTIFY_LISTENER_AIPAY_PH002:LOAN_DISCOUNT "
                    "msgId=2167E54708ED6A933BE28D76A7701E95 "
                    "CreditRuntimeException: channel gateway call failed, "
                    "external org response is not success, "
                    "apiName=inner.lazcredit.paylater.inhouse.loan.discount.notify, "
                    "lenderChannelCode=pera, responseContext.resultCode=FAILED"
                ),
                "trace": "214132dc17852085624024197ef43e",
            },
            "sourceMeta": {"__source__": "33.103.229.90"},
        }
    ]


def _metaq_broker_failure_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "2026-08-12 09:20:45 ERROR RocketMQClient - "
                    "updateConsumeOffsetToBroker exception, "
                    "com.aliyun.openservices.shade.com.alibaba.rocketmq.remoting.exception.RemotingConnectException: "
                    "connect to <33.9.126.179:10909> failed; "
                    "MQClientException: The broker[trade_sub_notify_metaq-zoneB-11] not exist"
                ),
                "trace": "213e07cd17864976509897160e1238",
            },
            "sourceMeta": {"__source__": "33.63.65.196"},
        }
    ]


def _metaq_duplicate_update_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "2026-06-05 17:22:51,469 [2150466d17806513712328452e0c86] "
                    "[ConsumeMessageThread_6] INFO BizLog - "
                    "BLV1||com.alibaba.tt.logistics.cs.daemon.service.message.listener."
                    "logisticdetail.LogisticsDetailProcessListener||handleMessage||false||20||"
                    "UPDATE_ERROR||更新失败.||null||null||null||null||"
                    "0B5D8035082A3FFCD140185433A1AD19||GOT||"
                    "LOGISTICS_ON_DEMAND_TRACE_TOPIC||YT1134183699405||null|| "
                    "com.wdk.infra.permission.foundation.exception.BadRequestException: 更新失败. "
                    "at ServiceOrderTunnelImpl.updateWithVersion(ServiceOrderTunnelImpl.java:87)"
                ),
                "trace": "2150466d17806513712328452e0c86",
            },
            "sourceMeta": {"__source__": "33.61.32.49"},
        },
        {
            "logItem": {
                "content": (
                    "2026-06-05 17:23:01,573 [ConsumeMessageThread_1] INFO BizLog - "
                    "BLV1||com.alibaba.tt.logistics.cs.daemon.service.message.listener."
                    "logisticdetail.LogisticsDetailProcessListener||handleMessage||true||12||"
                    "null||null||null||null||null||null||"
                    "0B5D8035082A3FFCD140185433A1AD19||GOT||"
                    "LOGISTICS_ON_DEMAND_TRACE_TOPIC||YT1134183699405||null||"
                ),
                "trace": "2150466d17806513712328452e0c86",
            },
            "sourceMeta": {"__source__": "33.54.125.111"},
        },
    ]


def _auth_failure_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "2026-06-16 12:40:05 ERROR BucRefreshSsoTokenError: "
                    "token could not be hit, tenant key error, statusCode: 401, "
                    "originalUrl: /gocFaultDef/innerApi/v2/incident/scenarios/level/defs"
                ),
                "trace": "8ccd75d217815846928741544e77e6",
            },
            "sourceMeta": {"__source__": "33.102.22.35"},
        }
    ]


def _business_system_error_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "serviceId": (
                    "com.alibaba.shared.carriage.delivery.service."
                    "OfficialDeliveryOrderService#consignByOfficialDeliveryOrder"
                ),
                "success": "false",
                "output": "\x1eex:SYSTEM_ERROR::电子面单账户余额不足\x1e",
                "traceId": "215046ea17805496969974010e0e0e",
                "clientAppName": "logisticsmarket-center",
            },
            "sourceMeta": {"__source__": "11.10.170.126"},
        }
    ]


def _successful_business_system_error_text_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "serviceId": (
                    "com.alibaba.shared.carriage.delivery.service."
                    "OfficialDeliveryOrderService#consignByOfficialDeliveryOrder"
                ),
                "success": "true",
                "output": "ex:SYSTEM_ERROR::电子面单账户余额不足 has been documented in faq",
            },
            "sourceMeta": {"__source__": "11.10.170.126"},
        }
    ]


def _normal_login_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "2026-06-16 12:30:03 INFO login_for_sunfire|false|"
                    "https://tr.alibaba-inc.com/gocFaultDef/innerApi/v1/common/product/update/notice/config|"
                )
            },
            "sourceMeta": {"__source__": "33.62.148.181"},
        }
    ]


def _successful_coupon_gateway_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "GatewayAop url=https://openapi.example.com/api/coupon/query "
                    'returnObj:{"success":true,"result":{"couponList":[{"couponCode":"8988944833367990735"}]}}'
                ),
            },
            "sourceMeta": {"__source__": "33.3.197.56"},
        }
    ]


def _external_dependency_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "I/O exception (java.net.NoRouteToHostException) caught when processing "
                    "request to {s}->https://developer.ehuandian.net:443: 没有到主机的路由 "
                    "(Host unreachable)"
                ),
                "trace": "214f63c217841292866250187e1623",
            },
            "sourceMeta": {"__source__": "33.90.138.108"},
        }
    ]


def _collation_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "java.sql.SQLException: Illegal mix of collations "
                    "(utf8_general_ci,IMPLICIT) and (utf8mb4_general_ci,COERCIBLE) "
                    "for operation '=' update robotx_chat_log"
                ),
                "trace_id": "214bf3d217849630567341910e0b6e",
            },
            "sourceMeta": {"__source__": "33.7.130.110"},
        }
    ]


def _db_connection_pool_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "DruidDataSource get connection timeout from TDDL_CONN, SQL failed on "
                    "host 33.70.176.208"
                )
            },
            "sourceMeta": {"__source__": "33.70.176.208"},
        }
    ]


def _stale_db_connection_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "### Error querying database. Cause: "
                    "com.mysql.jdbc.exceptions.jdbc4.CommunicationsException: "
                    "Communications link failure. The last packet successfully received "
                    "from the server was 176,503 milliseconds ago. "
                    "### SQL: select master_id, act_balance_date from "
                    "s_tmi_account_balance_date where master_id = ?"
                ),
                "traceId": "213dd8f217871035517475239d0d4d",
                "result": "false",
            },
            "sourceMeta": {"__source__": "33.44.96.71"},
        }
    ]


def _http_connection_pool_rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "content": (
                    "org.apache.http.impl.conn.PoolingHttpClientConnectionManager "
                    "connection pool wait timed out for https://api.example.com/resource"
                )
            },
            "sourceMeta": {"__source__": "33.1.1.1"},
        }
    ]


def test_should_query_app_logs_for_custom_and_hsf_alarms() -> None:
    assert should_query_app_logs("自定义监控", {"content": "成功率下降 失败数上升"})
    assert should_query_app_logs("HSF", {"content": "THREADPOOL_BUSY"})


def test_app_log_search_queries_use_visible_alarm_terms_and_generic_runtime_terms() -> None:
    queries = app_log_search_queries(
        {
            "monitor_item_name": "线程池满(包含业务线程池)",
            "content": "* [ THREADPOOL_BUSY] 最近5分钟求平均: 17.400 > 3",
            "alarm_tags": [[{"name": "关键字", "value": " THREADPOOL_BUSY"}]],
        }
    )

    assert queries[0] == "THREADPOOL_BUSY"
    assert "thread pool is full" in queries


def test_metaq_app_log_search_queries_keep_business_terms_before_many_ip_tags() -> None:
    alarm = {
        "metric": "middleware_metaq_receive_success_rate",
        "monitor_item_name": "alsc-eloan-service[metaq消费成功率]",
        "content": "共有10台机器[metaq消费成功率]异常",
        "alarm_tags": [[{"name": "ip", "value": f"33.7.154.{index}"}] for index in range(10)],
    }

    queries = app_log_search_queries(alarm, limit=12)

    assert queries[:2] == [
        "BIZ_ERROR OR BizException OR ConsumeMessageThread",
        "msgId OR MQRecv OR ConsumeMessage",
    ]
    assert "33.7.154.0" in queries


def test_goc_proxy_app_log_search_queries_include_auth_terms_before_generic_terms() -> None:
    queries = app_log_search_queries(
        {
            "title": "goc_pass_后端代理(nginx) - 第1条规则",
            "content": "gocFaultDef 失败数 当前值为 30",
            "alarm_tags": [[{"name": "代理名", "value": "gocFaultDef"}]],
        }
    )

    assert "BucRefreshSsoTokenError OR tenant key error OR token could not be hit" in queries[:5]


def test_hsf_app_log_search_queries_include_business_system_error_before_tags() -> None:
    queries = app_log_search_queries(
        {
            "metric": "middleware_hsf_provider_service_method_error_qps",
            "content": "service=com.alibaba.shared.carriage.delivery.service.OfficialDeliveryOrderService:1.0.0",
            "alarm_tags": [
                [{"name": "method", "value": "consignByOfficialDeliveryOrder~O"}],
                [{"name": "ip", "value": "33.10.170.126"}],
            ],
        },
        limit=8,
    )

    assert "SYSTEM_ERROR" in queries[:4]
    assert "consignByOfficialDeliveryOrder AND SYSTEM_ERROR" in queries[:5]


def test_rank_app_log_store_prefers_runtime_logs() -> None:
    stores = [
        {"logstore": "tmi2-oplog"},
        {"logstore": "tradelist_online"},
        {"logstore": "tmi2-publish"},
    ]

    assert sorted(stores, key=rank_app_log_store)[0]["logstore"] == "tradelist_online"


def test_app_log_signals_extract_hsf_threadpool_busy_provider() -> None:
    signals = app_log_signals(_threadpool_rows())

    assert signals[0].kind == "hsf_threadpool_busy"
    assert signals[0].label == "THREADPOOL_BUSY:33.1.203.42"
    assert signals[0].props["provider_ips"] == ["33.1.203.42"]
    assert "THREADPOOL_BUSY" in summarize_app_logs(_threadpool_rows())


def test_app_log_signals_extract_sentinel_limit_method_and_trace() -> None:
    signals = app_log_signals(_sentinel_rows())

    assert signals[0].kind == "app_log_limit"
    assert signals[0].label == "UMP_SENTINEL_BLOCK:queryItemSkuPrice"
    assert signals[0].trace_ids == ["214782d917846200305946136e10b9"]
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "middleware_limit"
    )


def test_app_log_signals_extract_sql_success_false() -> None:
    signals = app_log_signals(_sql_failure_rows())

    assert signals[0].kind == "db_access_failure"
    assert signals[0].label == (
        "sql_failure:com.alibaba.tmi.repository.mybatisplus.account.mapper."
        "AccountBalanceDateMapper.selectList"
    )
    assert signals[0].trace_ids == ["213dd8f217871035517475239d0d4d"]


def test_app_log_signals_extract_heavy_business_export() -> None:
    signals = app_log_signals(_heavy_export_rows())

    assert signals[0].kind == "heavy_business_query"
    assert signals[0].label == (
        "heavy_query:/api/method/main/bigBagWideDetailMgr/export:pageSize=500:in=9"
    )
    assert signals[0].trace_ids == ["213e018d17849636567341910e0b6e"]
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "application"
    )


def test_app_log_signals_extract_metaq_business_failure_without_coupon_success_false_positive() -> (
    None
):
    signals = app_log_signals(_metaq_business_failure_rows())

    assert signals[0].kind == "metaq_business_failure"
    assert signals[0].label == (
        "alscEloanCouponPollingMetaqProducer:business_consume_failure:"
        "CID-ALSC-ELOAN-COUPON-POLLING-CONSUMER:couponCode=8988944833367990735"
    )
    assert signals[0].trace_ids == ["212c4c8e17862075418048755d0f3a"]
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "application"
    )
    assert app_log_signals(_successful_coupon_gateway_rows()) == []


def test_app_log_signals_extract_metaq_external_org_callback_failure() -> None:
    signals = app_log_signals(_metaq_external_org_failure_rows())

    assert signals[0].kind == "metaq_business_failure"
    assert signals[0].label == (
        "GL_CREDIT-INNER-NOTIFY-TOPIC_AIPAY_PH002:business_consume_failure:"
        "LOAN_DISCOUNT:lender=pera"
    )
    assert signals[0].trace_ids == ["214132dc17852085624024197ef43e"]
    assert signals[0].props["business_tags"] == ["LOAN_DISCOUNT"]
    assert signals[0].props["external_orgs"] == ["pera"]
    assert signals[0].props["api_names"] == [
        "inner.lazcredit.paylater.inhouse.loan.discount.notify"
    ]
    assert "external_orgs=['pera']" in summarize_app_logs(_metaq_external_org_failure_rows())


def test_app_log_signals_extract_metaq_broker_failure() -> None:
    signals = app_log_signals(_metaq_broker_failure_rows())

    assert signals[0].kind == "metaq_broker_failure"
    assert signals[0].label == "trade_sub_notify_metaq-zoneB-11:broker_connectivity_failure"
    assert signals[0].trace_ids == ["213e07cd17864976509897160e1238"]
    assert signals[0].props["broker_names"] == ["trade_sub_notify_metaq-zoneB-11"]
    assert signals[0].props["broker_ips"] == ["33.9.126.179", "33.63.65.196"]
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "message_queue"
    )
    assert "broker_names=['trade_sub_notify_metaq-zoneB-11']" in summarize_app_logs(
        _metaq_broker_failure_rows()
    )


def test_app_log_signals_extract_metaq_duplicate_update_conflict() -> None:
    signals = app_log_signals(_metaq_duplicate_update_rows())

    assert signals[0].kind == "metaq_duplicate_update_conflict"
    assert signals[0].label == (
        "LOGISTICS_ON_DEMAND_TRACE_TOPIC:duplicate_update_conflict:GOT:mailNo=YT1134183699405"
    )
    assert "2150466d17806513712328452e0c86" in signals[0].trace_ids
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "application"
    )
    assert "duplicate_update_conflict" in summarize_app_logs(_metaq_duplicate_update_rows())


def test_app_log_signals_extract_auth_session_failure_without_login_false_positive() -> None:
    signals = app_log_signals(_auth_failure_rows())

    assert signals[0].kind == "auth_session_failure"
    assert signals[0].label == (
        "/gocFaultDef/innerApi/v2/incident/scenarios/level/defs buc_sso_token auth_session_failure"
    )
    assert signals[0].trace_ids == ["8ccd75d217815846928741544e77e6"]
    assert signals[0].props["auth_markers"] == [
        "BucRefreshSsoTokenError",
        "token could not be hit",
        "tenant key error",
        "401/UNAUTHORIZED",
    ]
    assert app_log_signals(_normal_login_rows()) == []
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "service_dependency"
    )


def test_app_log_signals_extract_business_system_error_from_failed_hsf_result() -> None:
    signals = app_log_signals(_business_system_error_rows())

    assert signals[0].kind == "business_system_error"
    assert signals[0].label == (
        "OfficialDeliveryOrderService.consignByOfficialDeliveryOrder "
        "SYSTEM_ERROR 电子面单账户余额不足"
    )
    assert signals[0].trace_ids == ["215046ea17805496969974010e0e0e"]
    assert signals[0].props["error_codes"] == ["SYSTEM_ERROR"]
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "application"
    )
    assert "business_system_error" in summarize_app_logs(_business_system_error_rows())


def test_business_system_error_requires_failed_row() -> None:
    assert app_log_signals(_successful_business_system_error_text_rows()) == []


def test_app_log_signals_extract_external_dependency_domain() -> None:
    signals = app_log_signals(_external_dependency_rows())

    assert signals[0].kind == "external_dependency_failure"
    assert signals[0].label == "developer.ehuandian.net"
    assert signals[0].trace_ids == ["214f63c217841292866250187e1623"]
    assert "label=developer.ehuandian.net" in summarize_app_logs(_external_dependency_rows())
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "service_dependency"
    )


def test_app_log_signals_extract_collation_as_data_quality_sql_error() -> None:
    signals = app_log_signals(_collation_rows())

    assert signals[0].kind == "app_sql_error"
    assert signals[0].label == "data_quality:collation_mismatch"
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "database"
    )


def test_app_log_signals_extract_db_connection_pool_without_http_pool_false_positive() -> None:
    signals = app_log_signals(_db_connection_pool_rows())

    assert signals[0].kind == "connection_pool_exhausted"
    assert signals[0].label == "connection_pool:33.70.176.208"
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "database"
    )
    assert all(
        signal.kind != "connection_pool_exhausted"
        for signal in app_log_signals(_http_connection_pool_rows())
    )


def test_app_log_signals_extract_stale_db_connection() -> None:
    signals = app_log_signals(_stale_db_connection_rows())

    assert signals[0].kind == "stale_db_connection"
    assert signals[0].label == "stale_jdbc_connection:S_TMI_ACCOUNT_BALANCE_DATE"
    assert signals[0].props["stale_packet_ms"] == ["176503"]
    assert signals[0].props["exceptions"] == [
        "com.mysql.jdbc.exceptions.jdbc4.CommunicationsException"
    ]
    assert signals[0].trace_ids == ["213dd8f217871035517475239d0d4d"]
    assert (
        infer_root_layer(signals[0].kind, signals[0].label, signals[0].props, signals[0].reason)
        == "database"
    )


def test_sls_app_summary_counts_as_log_modality() -> None:
    assert (
        infer_modality(
            "sls_app_tradelist_online_THREADPOOL_BUSY", summarize_app_logs(_threadpool_rows())
        )
        == "log"
    )


def test_empty_sls_app_results_do_not_support_app_log_candidate() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "hsf_threadpool_busy",
                    "label": "THREADPOOL_BUSY:33.1.203.42",
                    "score": 5.0,
                    "reason": "HSF provider thread pool busy in application log near alarm window",
                }
            ],
            "evidence": [
                {
                    "name": "sls_app_empty",
                    "command": "sf log sls query --query THREADPOOL_BUSY -f json",
                    "returncode": 0,
                    "summary": "app_logs count=0 top=",
                },
                {
                    "name": "sls_app_hit",
                    "command": "sf log sls query --query THREADPOOL_BUSY -f json",
                    "returncode": 0,
                    "summary": (
                        "app_logs count=20 error_codes={'THREADPOOL_BUSY': 20} "
                        "top_signals=['kind=hsf_threadpool_busy label=THREADPOOL_BUSY:33.1.203.42']"
                    ),
                },
            ],
        }
    )

    support_names = [item.name for item in bundle.hypotheses[0].support]
    assert support_names == ["sls_app_hit"]
