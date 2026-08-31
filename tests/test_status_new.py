from __future__ import annotations

from datetime import date, datetime

from app.hithink import status as status_module
from app.hithink.aggregation import TARGET_NODE_SEQUENCES
from app.hithink.schedule import SHANGHAI, build_daily_schedule
from app.hithink.status import build_status_payload, render_combined_status


def _scheduler(*, active_collection_id: str | None = None) -> dict:
    pid = 15308
    return {
        "collector_processes": [{"pid": pid}],
        "active_node": (
            {"pid": pid, "collection_id": active_collection_id}
            if active_collection_id
            else None
        ),
        "tasks": [
            {
                "name": "HithinkSnapshotCollector",
                "state": "Running",
                "last_result": 0,
                "last_run_time": "2026/08/28 08:50:00",
                "next_run_time": "N/A",
            },
        ],
    }


def _row(node, raw="SUCCESS", derivation="SUCCESS", **values) -> dict:
    emotion = (
        "SUCCESS"
        if derivation == "SUCCESS"
        else "PENDING"
        if derivation == "RUNNING"
        else "BLOCKED"
    )
    fixed_status = (
        "SUCCESS"
        if derivation == "SUCCESS"
        else "PENDING"
        if derivation == "RUNNING"
        else "BLOCKED"
    )
    if node.sequence_no not in TARGET_NODE_SEQUENCES:
        fixed_status = "SKIPPED"
    base = {
        "collection_id": node.collection_id,
        "scheduled_time": node.scheduled_time,
        "sequence_no": node.sequence_no,
        "status": "SUCCESS" if raw == derivation == "SUCCESS" else raw,
        "raw_status": raw,
        "derivation_status": derivation,
        "all_a_snapshot_status": "SUCCESS" if raw == "SUCCESS" else raw,
        "limit_up_pool_status": "SUCCESS" if raw == "SUCCESS" else raw,
        "limit_down_pool_status": "SUCCESS" if raw == "SUCCESS" else raw,
        "limit_break_pool_status": "SUCCESS" if raw == "SUCCESS" else raw,
        "sector_index_status": "SUCCESS" if raw == "SUCCESS" else raw,
        "sector_state_status": "SUCCESS" if derivation == "SUCCESS" else derivation,
        "emotion_state_status": emotion,
        "market_delta_15m_status": fixed_status,
        "capital_migration_status": fixed_status,
        "core_sector_candidate_status": fixed_status,
        "core_stock_candidate_status": fixed_status,
        "market_package_status": fixed_status,
        "raw_error_code": None,
        "raw_error_message": None,
    }
    base.update(values)
    return base


def _payload(as_of: datetime, rows: list[dict], *, trading_day: bool = True, active=None):
    return build_status_payload(
        as_of=as_of,
        trading_day=trading_day,
        nodes=rows,
        daily=[],
        sector=[],
        scheduler=_scheduler(active_collection_id=active),
        resources={"memory_free_bytes": 1, "memory_total_bytes": 2},
        docker={"running": True, "health": "healthy"},
        query_ok=True,
        query_elapsed_ms=20,
    )


def test_all_success_is_healthy() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[0]
    report = _payload(node.scheduled_time, [_row(node)])
    assert report["overall_status"] == "HEALTHY"
    assert report["intraday"]["success"] == 1


def test_raw_failure_without_derivation_is_blocked() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[0]
    report = _payload(node.scheduled_time, [_row(node, raw="FAILED", derivation="PENDING")])
    assert report["derivation"]["blocked"] == 1
    assert report["overall_status"] == "DEGRADED"


def test_partial_data_is_partial_node() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[0]
    report = _payload(node.scheduled_time, [_row(node, raw="PARTIAL", derivation="SUCCESS")])
    assert report["intraday"]["partial"] == 1


def test_abnormal_exit_does_not_leave_permanent_running() -> None:
    schedule = build_daily_schedule(date(2026, 8, 28))
    as_of = schedule[4].scheduled_time
    rows = [_row(node) for node in schedule[:5]]
    rows[0] = _row(schedule[0], raw="RUNNING", derivation="RUNNING")
    report = _payload(as_of, rows)
    assert report["intraday"]["running"] == 0
    assert report["intraday"]["timeout"] == 1


def test_429_is_rate_limit() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[0]
    row = _row(
        node,
        raw="FAILED",
        derivation="PENDING",
        raw_error_code="all_a_snapshot:429",
        raw_error_message="all_a_snapshot: request limit exceeded",
    )
    report = _payload(node.scheduled_time, [row])
    assert report["errors"][0]["category"] == "接口限流"


def test_ssl_timeout_is_network_error() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[0]
    row = _row(
        node,
        raw="FAILED",
        derivation="PENDING",
        raw_error_code="all_a_snapshot:None",
        raw_error_message="all_a_snapshot: _ssl handshake timed out",
    )
    report = _payload(node.scheduled_time, [row])
    assert report["errors"][0]["category"] == "网络/SSL握手超时"


def test_status_counts_close() -> None:
    schedule = build_daily_schedule(date(2026, 8, 28))
    as_of = schedule[2].scheduled_time
    rows = [
        _row(schedule[0]),
        _row(schedule[1], raw="PARTIAL", derivation="SUCCESS"),
        _row(schedule[2], raw="FAILED", derivation="PENDING"),
    ]
    report = _payload(as_of, rows)
    stats = report["intraday"]
    assert stats["expected"] == sum(
        stats[key] for key in ("success", "partial", "failed", "blocked", "timeout", "running")
    )
    assert report["integrity"]["closed"] is True


def test_active_derivation_pending_children_count_as_running() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[0]
    row = _row(
        node,
        raw="SUCCESS",
        derivation="RUNNING",
        sector_state_status="PENDING",
    )
    report = _payload(
        node.scheduled_time,
        [row],
        active=node.collection_id,
    )
    sector_details = [item for item in report["details"] if item["group"] == "派生"][2:]
    assert all(item["running"] == 1 for item in sector_details)
    assert report["integrity"]["closed"] is True


def test_non_trading_day_is_idle() -> None:
    as_of = datetime(2026, 8, 29, 10, 0, tzinfo=SHANGHAI)
    report = _payload(as_of, [], trading_day=False)
    assert report["overall_status"] == "IDLE"
    assert report["intraday"]["expected"] == 0
    daily_k = next(item for item in report["details"] if item["item"] == "日K数据")
    assert daily_k["expected"] == 0
    assert daily_k["failed"] == 0


def test_non_trading_day_skips_mapping_and_1600_daily_collection() -> None:
    as_of = datetime(2026, 8, 29, 17, 0, tzinfo=SHANGHAI)
    report = _payload(as_of, [], trading_day=False)

    mapping = [
        item for item in report["details"] if item["group"] == "板块映射"
    ]
    daily = [item for item in report["details"] if item["group"] == "每日数据"]

    assert mapping and all(item["expected"] == 0 for item in mapping)
    assert next(item for item in daily if item["item"] == "日K数据")["expected"] == 0
    assert next(
        item for item in daily if item["item"] == "复权、除权事件"
    )["expected"] == 0


def test_lunch_next_time_points_to_afternoon() -> None:
    as_of = datetime(2026, 8, 28, 11, 45, tzinfo=SHANGHAI)
    due = [node for node in build_daily_schedule(as_of.date()) if node.scheduled_time <= as_of]
    report = _payload(as_of, [_row(node) for node in due])
    assert report["collector"]["next_scheduled_time"].endswith("13:00:15+08:00")


def test_markdown_uses_same_json_numbers() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[0]
    report = _payload(node.scheduled_time, [_row(node, raw="PARTIAL", derivation="SUCCESS")])
    markdown = render_combined_status(report)
    stats = report["intraday"]
    assert (
        f"盘中节点：应执行{stats['expected']}，成功{stats['success']}，"
        f"部分成功{stats['partial']}，失败{stats['failed']}"
    ) in markdown


def test_collector_pid_detection_works_across_windows_accounts(
    monkeypatch, tmp_path
) -> None:
    pid_path = tmp_path / "collector.pid"
    pid_path.write_text("4321", encoding="ascii")
    monkeypatch.setattr(status_module, "COLLECTOR_PID_PATH", pid_path)
    monkeypatch.setattr(status_module, "_running_process_ids", lambda: {4321})

    assert status_module._collector_processes() == [{"pid": 4321}]
