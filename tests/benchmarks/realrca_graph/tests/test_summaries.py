from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.summaries import compact_evidence_summary


def test_compact_metric_summary_keeps_labels_and_drops_points() -> None:
    summary = compact_evidence_summary(
        "metric_middleware_hsf_provider_service_method_error_qps",
        "sf metric query sum by(service,method)(middleware_hsf_provider_service_method_error_qps{}) -f json",
        {
            "series_count": 1,
            "series": [
                {
                    "labels": {
                        "__name__": "",
                        "app_group": "provider-app_default_host",
                        "service": "com.alibaba.demo.ProviderApi:1.0.0",
                        "method": "queryFoo~P",
                    },
                    "summary": {
                        "min": 0.0,
                        "max": 12.0,
                        "avg": 4.0,
                        "last": 1.0,
                        "trend": "rising",
                    },
                    "points": [{"time": "1", "value": 0.0}],
                }
            ],
        },
    )

    assert "provider-app_default_host" in summary
    assert "queryFoo~P" in summary
    assert "points" not in summary


def test_compact_metric_summary_keeps_tddl_table_label() -> None:
    summary = compact_evidence_summary(
        "metric_middleware_tddl_write_table_rt",
        "sf metric query avg by(table)(middleware_tddl_write_table_rt{}) -f json",
        {
            "series_count": 1,
            "series": [
                {
                    "labels": {
                        "__name__": "",
                        "table": "c2m_portrait_sku_map_product_sku_record",
                    },
                    "summary": {
                        "min": 0.3333,
                        "max": 56.9535,
                        "avg": 10.8218,
                        "last": 9.875,
                        "trend": "rising",
                    },
                    "points": [{"time": "1", "value": 56.9535}],
                }
            ],
        },
    )

    assert "metric=middleware_tddl_write_table_rt" in summary
    assert "table=c2m_portrait_sku_map_product_sku_record" in summary
    assert "max=56.95" in summary
    assert "points" not in summary


def test_trace_text_summary_is_preserved() -> None:
    summary = compact_evidence_summary(
        "trace_get",
        "sf trace get abc -f json",
        "provider-app ProviderApi timed out at 10000ms",
    )

    assert summary == "provider-app ProviderApi timed out at 10000ms"


def test_trace_summary_preserves_sql_spans_even_when_not_slowest() -> None:
    summary = compact_evidence_summary(
        "trace_get",
        "sf trace get trace-1 -f json",
        [
            {
                "clientName": "web:webhost",
                "serverName": "app:apphost",
                "service": "com.demo.Service@query~S",
                "duration": 12000,
                "resultModel": {"code": 3, "name": "HSF_TIMEOUT"},
                "resultStr": "03",
            },
            {
                "clientName": "app:apphost",
                "serverName": "(db@intl_bw)",
                "service": "TDDL_QUERY@intl_bw:resource_lock_setting_his\x1a61de4574",
                "duration": 0,
                "resultModel": {"code": 0, "name": "OK"},
                "resultStr": "00",
            },
        ],
    )

    assert "sql_top=" in summary
    assert "TDDL_QUERY@intl_bw:resource_lock_setting_his" in summary
    assert "error_top=" in summary


def test_event_summary_prioritizes_schedulerx_job_fields() -> None:
    summary = compact_evidence_summary(
        "event_query_app",
        "sf event query --app titanium-task -f json",
        [
            {
                "stream": {
                    "sourceProduct": "ECS",
                    "type": "acs.ecs[ecs:CloudMonitor:Instance[InstanceFailure.Alert:Executed]]",
                    "eventLevel": "warning",
                },
                "values": [[1782750840, {"reason": "vm_virtio_nobuf_drop"}]],
            },
            {
                "stream": {
                    "sourceProduct": "SchedulerX",
                    "type": "com.alibaba.schedulerx.job.start",
                    "subject": "1025599413_78335451819",
                    "eventLevel": "info",
                },
                "values": [
                    [
                        1782751037,
                        {
                            "range_instance_id": "1025599413_78335451819",
                            "status": "success",
                            "ip": ["33.7.216.246"],
                        },
                    ]
                ],
            },
        ],
    )

    assert "sourceProduct=SchedulerX" in summary
    assert "subject=1025599413_78335451819" in summary
    assert "privateIp=33.7.216.246" in summary


def test_event_summary_decodes_changefree_string_payload() -> None:
    summary = compact_evidence_summary(
        "event_changefree_query",
        'sf event query --query \'{} | appName = "mp-fund" and source = "changefree"\' -f json',
        {
            "result": [
                {
                    "stream": {
                        "sourceProduct": "CHANGEFREE_EXE",
                        "source": "changefree",
                        "type": "EXE[cf:normandy]",
                    },
                    "values": [
                        [
                            1785206100,
                            json.dumps(
                                {
                                    "change_summary": "应用mp-fund部署production环境",
                                    "change_system": "normandy",
                                    "change_type_name": "APP_PUBLISH",
                                    "change_result": "变更成功",
                                    "ext_info": '{"deploy_id":"157962710"}',
                                    "change_object": '{"name":"mp-fund","deploy_version":"234485125","app_groups":"mp-fund_default_host"}',
                                    "gray_strategy": '{"batchSize":3,"currentBatch":2}',
                                    "id": "2992969898",
                                },
                                ensure_ascii=False,
                            ),
                        ]
                    ],
                }
            ]
        },
    )

    assert "sourceProduct=CHANGEFREE_EXE" in summary
    assert "change_system=normandy" in summary
    assert "change_type=APP_PUBLISH" in summary
    assert "deploy_id=157962710" in summary
    assert "deploy_version=234485125" in summary
    assert "change_app=mp-fund" in summary
    assert "change_groups=mp-fund_default_host" in summary
    assert "batch=2/3" in summary


def test_event_summary_preserves_offline_change_detail_url() -> None:
    summary = compact_evidence_summary(
        "event_changefree_query_freight_template",
        'sf event query --query \'{} | appName = "freight-template" and source = "changefree"\' -f json',
        {
            "result": [
                {
                    "stream": {"sourceProduct": "CHANGEFREE_EXE", "source": "changefree"},
                    "values": [
                        [
                            1786169934,
                            json.dumps(
                                {
                                    "change_summary": "freight-template 应用变更",
                                    "change_system": "normandy-director",
                                    "change_type_name": "CONFIG_PUSH",
                                    "change_result": "变更成功",
                                    "change_object": json.dumps(
                                        {
                                            "appName": "freight-template",
                                            "extraInfo": {
                                                "detailUrl": "https://n.alibaba-inc.com/micro/ops/app/freight-template/action/res/offline/detail"
                                            },
                                        }
                                    ),
                                    "id": "3033872029",
                                },
                                ensure_ascii=False,
                            ),
                        ]
                    ],
                }
            ]
        },
    )

    assert "change_app=freight-template" in summary
    assert "change_type=CONFIG_PUSH" in summary
    assert (
        "detail_url=https://n.alibaba-inc.com/micro/ops/app/freight-template/action/res/offline/detail"
        in summary
    )


def test_change_list_summary_prioritizes_normandy_offline_host() -> None:
    summary = compact_evidence_summary(
        "event_change_list",
        "sf event change list --app mtee3 --infra -f json",
        {
            "business_changes": [
                {
                    "id": "100",
                    "change_type": "CONFIG_PUSH",
                    "title": "mtee3-普通配置恢复",
                    "result": "变更成功",
                    "system": "preplan2",
                    "end_time": "2026-06-11 20:07:52",
                },
                {
                    "id": "101",
                    "change_type": "CONFIG_PUSH",
                    "title": "mtee3-普通配置恢复",
                    "result": "变更成功",
                    "system": "preplan2",
                    "end_time": "2026-06-11 20:09:52",
                },
                {
                    "id": "2843585453",
                    "change_type": "OFFLINE_HOST",
                    "title": "正式-机器下线",
                    "result": "变更成功",
                    "system": "normandy-director",
                    "end_time": "2026-06-11 22:20:36",
                },
            ]
        },
    )

    assert "changes=3" in summary
    assert "id=2843585453 system=normandy-director type=OFFLINE_HOST" in summary
    assert summary.index("id=2843585453") < summary.index("id=101")


def test_trace_summary_keeps_sql_top_before_long_general_top() -> None:
    summary = compact_evidence_summary(
        "trace_get",
        "sf trace get trace-1 -f json",
        [
            *[
                {
                    "clientName": "very-long-client-name",
                    "serverName": f"very-long-server-name-{index}",
                    "service": "com.alibaba.demo.ExtremelyLongServiceNameThatWouldOtherwiseFillTheSummary@call",
                    "duration": 1000 - index,
                    "resultModel": {"code": 0, "name": "OK"},
                    "resultStr": "00",
                }
                for index in range(20)
            ],
            {
                "clientName": "app:apphost",
                "serverName": "(db@lzd_cfo_mdm)",
                "service": "TDDL_QUERY@lzd_cfo_mdm:mdm_bank\x1a8c6ee4f7",
                "duration": 1,
                "resultModel": {"code": 1, "name": "ERR"},
                "resultStr": "01",
            },
        ],
    )

    assert summary.index("sql_top=") < summary.index(" top=")
    assert "TDDL_QUERY@lzd_cfo_mdm:mdm_bank" in summary


def test_trace_summary_prioritizes_hsf_errors_before_sql_top() -> None:
    summary = compact_evidence_summary(
        "trace_get",
        "sf trace get trace-1 -f json",
        [
            *[
                {
                    "clientName": "provider:providerhost",
                    "serverName": "(db@demo)",
                    "service": f"TDDL_QUERY@demo:side_table_{index}\x1asid",
                    "duration": 50 + index,
                    "resultModel": {"code": 0, "name": "OK", "type": "OK"},
                    "resultStr": "00",
                }
                for index in range(8)
            ],
            {
                "clientName": "hotel-buy:hotel-buyhost",
                "serverName": "tuan-item:tuan-item_default_production",
                "service": "com.alibaba.fliggy.tuan.item.api.hotel.UpRoomQueryApi@getUpRoomInfo~U",
                "duration": 748,
                "resultModel": {"code": 3, "name": "TIMEOUT", "type": "TIMEOUT"},
                "resultStr": "03",
                "rpcTypeName": "HSF",
                "serverIp": "33.5.100.72",
                "hostIp": "33.3.251.222",
            },
        ],
    )

    assert summary.index("hsf_error_top=") < summary.index("sql_top=")
    assert "UpRoomQueryApi@getUpRoomInfo" in summary
    assert "provider_ips={'33.5.100.72': 1}" in summary


def test_trace_summary_keeps_biz_error_code_one_in_error_top() -> None:
    summary = compact_evidence_summary(
        "trace_get",
        "sf trace get trace-1 -f json",
        [
            {
                "clientName": "(notify@TC_REFUND_DISPUTE)",
                "serverName": "wdk-crowd-center:wdk-crowd-centerhost",
                "service": "Notify@recv~BytesMessage:TC_REFUND_DISPUTE:TAG:GROUP",
                "duration": 83,
                "resultModel": {"code": 1, "name": "ERR", "type": "BIZ_ERROR"},
                "resultStr": "01",
            }
        ],
    )

    assert "error_top=" in summary
    assert "Notify@recv~BytesMessage:TC_REFUND_DISPUTE" in summary
    assert "result=01/ERR/BIZ_ERROR" in summary


def test_trace_summary_keeps_sql_table_frequency_for_short_spans() -> None:
    summary = compact_evidence_summary(
        "trace_get",
        "sf trace get trace-1 -f json",
        [
            {
                "clientName": "wdk-suppliercore:wdk-suppliercorehost",
                "serverName": "(db@wdk_supplierprod)",
                "service": "TDDL_QUERY@wdk_supplierprod:wdk_supplier\x1a65ee5e9a",
                "duration": 0,
                "resultModel": {"code": 0, "name": "OK"},
                "resultStr": "00",
            },
            {
                "clientName": "web:webhost",
                "serverName": "app:apphost",
                "service": "com.demo.SlowApi@call",
                "duration": 10000,
                "resultModel": {"code": 0, "name": "OK"},
                "resultStr": "00",
            },
        ],
    )

    assert "sql_tables={'wdk_supplier': 1}" in summary


def test_trace_summary_preserves_slow_http_error_spans() -> None:
    summary = compact_evidence_summary(
        "trace_get_0b13be611783",
        "sf trace get 0b13be6117833251722073237d0c28 -f json",
        [
            {
                "clientName": "camel:camel_hz_production",
                "serverName": "rlab-service:rlab-service_hz_host",
                "service": "com.alibaba.icbu.rlabservice.remote.RLabExecuteService@_call~RRRR",
                "duration": 150595,
                "resultModel": {"code": 3, "name": "TIMEOUT"},
                "resultStr": "03",
            },
            {
                "serverName": "aserver-ingress:tengine-ingress-work-alilang-host",
                "service": "https://iai.alibaba-inc.com/azure/chat",
                "duration": 120000,
                "resultModel": {"code": 499, "name": "499"},
                "resultStr": "499",
            },
        ],
    )

    assert "https://iai.alibaba-inc.com/azure/chat" in summary
    assert "duration_ms=120000" in summary
    assert "result=499/499" in summary
    assert "error_top=" in summary


def test_trace_summary_uses_span_client_when_duration_is_zero() -> None:
    summary = compact_evidence_summary(
        "trace_get_213601cd1783",
        "sf trace get 213601cd17839992396178376e2584 -f json",
        [
            {
                "clientName": "ads-sell-service:ads-sell-service_center_cloud-hz-pub-na610_host",
                "serverName": "(db@intl_bw)",
                "service": "TDDL_QUERY@intl_bw:resource_lock_setting_his",
                "duration": 0,
                "spanClient": 2646,
                "resultModel": {"code": 0, "name": "OK", "type": "OK"},
                "resultStr": "00",
            },
            {
                "clientName": "jantar:jantar_hz_host",
                "serverName": "(db@crm_omega)",
                "service": "TDDL_QUERY@crm_omega_01:global_customer_ext\x1a35199f96",
                "duration": 23,
                "resultModel": {"code": 0, "name": "OK", "type": "OK"},
                "resultStr": "00",
            },
        ],
    )

    assert "resource_lock_setting_his" in summary
    assert "duration_ms=2646" in summary
    assert summary.index("resource_lock_setting_his") < summary.index("global_customer_ext")


def test_trace_summary_preserves_target_host_ip() -> None:
    summary = compact_evidence_summary(
        "trace_get",
        "sf trace get trace-1 -f json",
        [
            {
                "clientName": "consumer:consumer_host",
                "serverName": "provider:provider_doomhost",
                "service": "com.demo.ProviderApi@query~P",
                "duration": 10002,
                "resultModel": {"code": 3, "name": "TIMEOUT"},
                "resultStr": "03",
                "server_ip": "33.42.114.145",
                "host_ip": "33.42.114.145",
            },
        ],
    )

    assert "server_ip=33.42.114.145" in summary
    assert "host_ip=33.42.114.145" not in summary


def test_event_summary_preserves_ecs_hardware_reason_from_list_payload() -> None:
    summary = compact_evidence_summary(
        "event_query_app",
        'sf event query -Q {appName="demo"} -f json',
        [
            {
                "stream": {
                    "sourceProduct": "ECS",
                    "eventLevel": "critical",
                    "instanceId": '["i-8vbiyp6wvmcp36j72a5u"]',
                    "type": "acs.ecs[ecs:CloudMonitor:Instance[SystemMaintenance.Redeploy:Avoided]]",
                },
                "values": [
                    [
                        1784755034,
                        {
                            "data": {
                                "alertRuleName": "local_disk_nc_down_hardware_error",
                                "eventStatus": "Avoided",
                                "instanceId": "i-8vbiyp6wvmcp36j72a5u",
                                "privateIpAddress": ["33.33.183.119"],
                                "reason": "The host machine has potential failure risks;Memory error",
                            },
                            "id": "E85057CC3FDC0AEE3F1DB5C4B634AB139BA8BEA7-CMS",
                            "time": "2026-07-22T21:17:14.000Z",
                        },
                    ]
                ],
            }
        ],
    )

    assert "sourceProduct=ECS" in summary
    assert "instanceId=i-8vbiyp6wvmcp36j72a5u" in summary
    assert "local_disk_nc_down_hardware_error" in summary
    assert "Memory error" in summary
    assert "privateIp=33.33.183.119" in summary


def test_log_error_summary_preserves_tddl_sql_details() -> None:
    summary = compact_evidence_summary(
        "log_error_list",
        "sf log error list --app aliyun-customer-servcie -f json",
        {
            "errors": [
                {
                    "exception": "org.springframework.dao.DataAccessResourceFailureException",
                    "trace_id": "0a032a2217849822069777361e9eb2",
                    "stack": (
                        "ERR-CODE: [TDDL-4202][ERR_SQL_QUERY_TIMEOUT] "
                        "Atom:cn-zhangjiakou_i-8vb95unz835pvzktw354_ticket_service_3016, "
                        "Group:TICKET_SERVICE_GROUP, AppName:TICKET_SERVICE_APP, "
                        "file [/home/admin/app/BOOT-INF/classes/mybatis/sqlmapper/ticket/BizTicketMapper.xml] "
                        "SQL: select id from biz_ticket where aliuid = ? order by gmt_create desc"
                    ),
                }
            ]
        },
    )

    assert "TDDL-4202" in summary
    assert "BIZ_TICKET" in summary
    assert "ticket/BizTicketMapper.xml" in summary
    assert "ticket_service_3016" in summary
    assert "TICKET_SERVICE_GROUP" in summary
    assert "0a032a2217849822069777361e9eb2" in summary


def test_log_error_summary_preserves_root_hints_and_domains() -> None:
    summary = compact_evidence_summary(
        "log_error_list",
        "sf log error list --app demo -f json",
        {
            "errors": [
                {
                    "exception": "java.net.SocketException",
                    "message": (
                        "dataservice-api.dw.alibaba-inc.com failed with Connection reset; "
                        "fallback later raised BadRequestException because account 不存在"
                    ),
                    "trace_id": "213e055d17843543573341923ecf19",
                }
            ]
        },
    )

    assert "dataservice-api.dw.alibaba-inc.com" in summary
    assert "Connection reset" in summary
    assert "BadRequestException" in summary
    assert "不存在" in summary


def test_log_error_summary_preserves_igraph_search_hints() -> None:
    summary = compact_evidence_summary(
        "log_error_list",
        "sf log error list --app ae-sellingpoint-s -f json",
        {
            "errors": [
                {
                    "exception": "com.taobao.igraph.client.common.IGraphServerException",
                    "message": "IgraphReadService - igraph search error, timeout=[50]",
                    "trace_id": "2103274e17816404542632185e17db",
                }
            ]
        },
    )

    assert "IGraphServerException" in summary
    assert "igraph search error" in summary
    assert "2103274e17816404542632185e17db" in summary


def test_log_error_summary_preserves_rocketmq_broker_hints() -> None:
    summary = compact_evidence_summary(
        "log_error_list",
        "sf log error list --app idle-cco -f json",
        {
            "errors": [
                {
                    "exception": "java.net.SocketTimeoutException",
                    "message": "RocketmqCommon - fetch name server address exception",
                },
                {
                    "exception": "org.apache.rocketmq.client.exception.MQClientException",
                    "message": (
                        "updateConsumeOffsetToBroker exception; "
                        "RemotingConnectException connect to <33.9.126.179:10909> failed; "
                        "The broker[trade_sub_notify_metaq-zoneB-11] not exist"
                    ),
                    "trace_id": "213e07cd17864976509897160e1238",
                },
            ]
        },
    )

    assert "broker_hints=" in summary
    assert "fetch name server address exception" in summary
    assert "trade_sub_notify_metaq-zoneB-11" in summary
    assert "213e07cd17864976509897160e1238" in summary
