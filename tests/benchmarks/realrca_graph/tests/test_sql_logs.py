from __future__ import annotations

from tests.benchmarks.realrca_graph.features import infer_modality
from tests.benchmarks.realrca_graph.sql_logs import (
    rank_sql_log_store,
    should_query_sql_logs,
    sql_log_search_queries,
    sql_log_signals,
    summarize_sql_logs,
)


def _tddl_rows() -> list[dict[str, object]]:
    content = (
        "[2026-06-03 22:16:53.299] ERROR c.a.m.b.bo.impl.GenerateLockBoImpl "
        "[traceId=45c266be-d82b-4947-8581-7d21f9628737:107938458, "
        "eagleEyeId=211b268417804962111914664d0f17]|加锁异常:"
        "TRADE_RECORD_LOCK_WorldFirst_bm3tgcoupon@service.aliyun.com_USD_\n"
        "org.mybatis.spring.MyBatisSystemException: nested exception is "
        "org.apache.ibatis.exceptions.PersistenceException:\n"
        "### Error updating database. Cause: ERR-CODE: [TDDL-4614][ERR_EXECUTE_ON_MYSQL] "
        "Error occurs when execute on GROUP 'ALIBABA_MANHATTAN_GROUP' ATOM "
        "'cn-zhangjiakou_i-8vbdnifm2kex8c6zzacb_alibaba_manhattan_3023': "
        "Duplicate entry 'TRADE_RECORD_LOCK_WorldFirst_bm3tgcoupon@service.aliyun.com_USD_' "
        "for key 'WS_GENERATE_LOCK_UK'\n"
        "### SQL: insert into WS_GENERATE_LOCK ( ID, NAME ) values ( ?, ? )\n"
        "at com.alibaba.manhattan.biz.bo.impl.GenerateLockBoImpl.setNx(GenerateLockBoImpl.java:130)"
    )
    return [
        {
            "extendMeta": {"__LogStore__": "manhattan-logtail"},
            "logItem": {"content": content},
            "sourceMeta": {"__source__": "33.27.38.132"},
        },
        {
            "extendMeta": {"__LogStore__": "manhattan-logtail"},
            "logItem": {"content": content.replace("22:16:53.299", "22:16:54.100")},
            "sourceMeta": {"__source__": "33.27.38.132"},
        },
    ]


def test_should_query_sql_logs_for_tddl_alarm() -> None:
    assert should_query_sql_logs(
        "TDDL",
        {
            "metric": "middleware_tddl_write_success_rate",
            "content": "33.27.38.132 tddl write success rate low",
        },
    )


def test_sql_log_search_queries_include_alarm_ip_and_database_terms() -> None:
    queries = sql_log_search_queries(
        {
            "content": "* [33.27.38.132] tddl写成功率 当前值为 83.984%",
            "alarm_tags": [[{"name": "ip", "value": "33.27.38.132"}]],
        }
    )

    assert (
        "33.27.38.132 AND (Duplicate OR deadlock OR timeout OR SQL OR connection OR pool)"
        in queries
    )
    assert "33.27.38.132 AND TDDL-4614" in queries


def test_rank_sql_log_store_prefers_logtail() -> None:
    stores = [
        {"logstore": "manhattan-oplog"},
        {"logstore": "manhattan-logtail"},
        {"logstore": "manhattan_monitor"},
    ]

    assert sorted(stores, key=rank_sql_log_store)[0]["logstore"] == "manhattan-logtail"


def test_summarize_sql_logs_compacts_tddl_duplicate_key() -> None:
    summary = summarize_sql_logs(_tddl_rows())

    assert "sql_logs count=2" in summary
    assert "TDDL-4614" in summary
    assert "WS_GENERATE_LOCK" in summary
    assert "WS_GENERATE_LOCK_UK" in summary
    assert "211b268417804962111914664d0f17" in summary


def test_sql_log_signals_extract_tddl_root() -> None:
    signals = sql_log_signals(_tddl_rows())

    assert signals[0].label == "TDDL-4614:WS_GENERATE_LOCK:WS_GENERATE_LOCK_UK"
    assert signals[0].props["error_code"] == "TDDL-4614"
    assert signals[0].props["sql_table"] == "WS_GENERATE_LOCK"
    assert signals[0].props["duplicate_key"] == "WS_GENERATE_LOCK_UK"
    assert signals[0].trace_ids == ["211b268417804962111914664d0f17"]


def test_tddl_sls_summary_counts_as_sql_modality() -> None:
    assert infer_modality("sls_sql_log", summarize_sql_logs(_tddl_rows())) == "sql"
