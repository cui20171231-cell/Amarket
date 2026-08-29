from __future__ import annotations

import csv
import ctypes
import io
import json
import shutil
import subprocess
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from app.hithink.config import Settings
from app.hithink.schedule import SHANGHAI, build_daily_schedule
from app.hithink.writer import ClickHouseWriter

TASK_NAMES = (
    "HithinkSnapshotCollector",
    "HithinkDailyPipeline",
    "HithinkSectorMappingSync",
)
TASK_LABELS = {
    "HithinkSnapshotCollector": "盘中快照",
    "HithinkDailyPipeline": "每日数据",
    "HithinkSectorMappingSync": "板块映射",
}
RAW_ITEMS = {
    "all_a_snapshot_status": "行情快照",
    "limit_up_pool_status": "涨停池",
    "limit_down_pool_status": "跌停池",
    "limit_break_pool_status": "炸板池",
    "sector_index_status": "板块指数",
}
DERIVED_ITEMS = (
    "个股派生数据",
    "全市场状态",
    "概念板块状态",
    "行业板块状态",
    "风格板块状态",
)
CLICKHOUSE_CONTAINER = "hithink-snapshot-clickhouse-1"
ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_PID_PATH = ROOT / "data" / "hithink_snapshot_collector.pid"
ACTIVE_NODE_PATH = ROOT / ".runtime" / "hithink_snapshot_active_node.json"

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, Any]] = {}
_STATUS_WRITER_LOCK = threading.Lock()
_STATUS_WRITER: ClickHouseWriter | None = None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace").rstrip("\x00")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _timed(name: str, check: Callable[[], Any]) -> dict[str, Any]:
    started = perf_counter()
    try:
        data = check()
        return {
            "name": name,
            "status": "OK",
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "data": data,
        }
    except Exception as exc:  # noqa: BLE001 - retain partial status results
        return {
            "name": name,
            "status": "ERROR",
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _cached(key: str, loader: Callable[[], Any], ttl_seconds: float = 5.0) -> Any:
    now = perf_counter()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < ttl_seconds:
            return cached[1]
    value = loader()
    with _CACHE_LOCK:
        _CACHE[key] = (perf_counter(), value)
    return value


def _task_state_schtasks(name: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", name, "/FO", "CSV", "/V"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=1.0,
    )
    rows = list(csv.reader(io.StringIO(completed.stdout)))
    if len(rows) < 2 or len(rows[1]) < 12:
        raise RuntimeError(f"任务计划程序没有返回完整数据：{name}")
    row = rows[1]
    return {
        "name": name,
        "state": row[3],
        "last_run_time": row[5],
        "last_result": int(row[6]),
        "next_run_time": row[2],
        "enabled": row[11],
    }


def _task_states_com() -> list[dict[str, Any]]:
    import pythoncom
    import win32com.client

    state_names = {0: "Unknown", 1: "Disabled", 2: "Queued", 3: "Ready", 4: "Running"}
    pythoncom.CoInitialize()
    service = root = task = None
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        root = service.GetFolder("\\")
        tasks: list[dict[str, Any]] = []
        for name in TASK_NAMES:
            task = root.GetTask(name)
            next_run = task.NextRunTime
            tasks.append(
                {
                    "name": name,
                    "state": state_names.get(int(task.State), "Unknown"),
                    "last_run_time": task.LastRunTime.strftime("%Y/%m/%d %H:%M:%S"),
                    "last_result": int(task.LastTaskResult),
                    "next_run_time": (
                        "N/A" if next_run.year < 2000 else next_run.strftime("%Y-%m-%dT%H:%M:%S")
                    ),
                    "enabled": "Enabled" if task.Enabled else "Disabled",
                }
            )
            task = None
        return tasks
    finally:
        task = root = service = None
        pythoncom.CoUninitialize()


def _memory_state() -> tuple[int, int]:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise ctypes.WinError(ctypes.get_last_error())
    return memory.available_physical, memory.total_physical


def _running_process_ids() -> set[int]:
    process_ids = (ctypes.c_ulong * 4096)()
    bytes_returned = ctypes.c_ulong()
    if not ctypes.windll.psapi.EnumProcesses(
        ctypes.byref(process_ids), ctypes.sizeof(process_ids), ctypes.byref(bytes_returned)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    count = bytes_returned.value // ctypes.sizeof(ctypes.c_ulong)
    return set(process_ids[:count])


def _collector_processes() -> list[dict[str, int]]:
    try:
        pid = int(COLLECTOR_PID_PATH.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return []
    try:
        running_process_ids = _running_process_ids()
    except OSError:
        return []
    return [{"pid": pid}] if pid in running_process_ids else []


def _active_node(processes: list[dict[str, int]]) -> dict[str, Any] | None:
    try:
        record = json.loads(ACTIVE_NODE_PATH.read_text(encoding="utf-8"))
        pids = {process["pid"] for process in processes}
        return record if int(record["pid"]) in pids else None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _scheduler_state() -> dict[str, Any]:
    def load_tasks() -> list[dict[str, Any]]:
        try:
            return _task_states_com()
        except ImportError:
            return [_task_state_schtasks(name) for name in TASK_NAMES]

    tasks = _cached("scheduled_tasks", load_tasks)
    processes = _collector_processes()
    return {"tasks": tasks, "collector_processes": processes, "active_node": _active_node(processes)}


def _resource_state_uncached() -> dict[str, Any]:
    memory_free, memory_total = _memory_state()
    disk = shutil.disk_usage("D:\\")
    return {
        "memory_free_bytes": memory_free,
        "memory_total_bytes": memory_total,
        "disk_d_free_bytes": disk.free,
        "disk_d_total_bytes": disk.total,
        "uptime_ms": ctypes.windll.kernel32.GetTickCount64(),
    }


def _resource_state() -> dict[str, Any]:
    return _cached("n100_resources", _resource_state_uncached)


def _docker_state_uncached() -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{json .State}}", CLICKHOUSE_CONTAINER],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=1.5,
    )
    state = json.loads(completed.stdout)
    return {
        "running": bool(state.get("Running")),
        "health": state.get("Health", {}).get("Status"),
        "started_at": state.get("StartedAt"),
    }


def _docker_state() -> dict[str, Any]:
    return _cached("clickhouse_container", _docker_state_uncached)


NODE_COLUMNS = (
    "collection_id",
    "scheduled_time",
    "sequence_no",
    "status",
    "raw_status",
    "derivation_status",
    "all_a_snapshot_status",
    "limit_up_pool_status",
    "limit_down_pool_status",
    "limit_break_pool_status",
    "sector_index_status",
    "sector_state_status",
    "raw_error_code",
    "raw_error_message",
    "derivation_error_code",
    "derivation_error_message",
    "raw_insert_count",
    "request_start_time",
    "request_end_time",
    "source_time",
    "updated_at",
)


def _query_progress(as_of: datetime) -> dict[str, Any]:
    global _STATUS_WRITER

    started = perf_counter()
    server_elapsed_values: list[int] = []
    with _STATUS_WRITER_LOCK:
        if _STATUS_WRITER is None:
            settings = Settings.load()
            _STATUS_WRITER = ClickHouseWriter(
                settings.clickhouse_host,
                settings.clickhouse_port,
                settings.clickhouse_database,
                settings.clickhouse_username,
                settings.clickhouse_password,
            )
        writer = _STATUS_WRITER
        tuple_expression = ", ".join(NODE_COLUMNS)
        latest_columns = ", ".join(NODE_COLUMNS)
        snapshot = writer.client.query(
            f"""
            SELECT
                ifNull(
                    (SELECT any(is_trading_day)
                     FROM market.trading_calendar FINAL
                     WHERE trade_date = {{trade_date:Date}}),
                    toUInt8(1)
                ) AS is_trading_day,
                count() AS due_node_count,
                groupArray(tuple({tuple_expression})) AS nodes
            FROM
            (
                SELECT {latest_columns}
                FROM market.hithink_snapshot_schedule
                WHERE trade_date = {{trade_date:Date}}
                ORDER BY updated_at DESC
                LIMIT 1 BY collection_id
            )
            WHERE scheduled_time <= {{as_of:DateTime64(3, 'Asia/Shanghai')}}
            """,
            parameters={"trade_date": as_of.date(), "as_of": as_of},
            settings={"max_execution_time": 1},
        )
        server_elapsed_values.append(int(snapshot.summary.get("elapsed_ns", 0)))
        first = snapshot.result_rows[0]
        nodes = [dict(zip(NODE_COLUMNS, row, strict=True)) for row in first[2]]
        daily = writer.client.query(
            """
            SELECT task_name, status, request_start_time, request_end_time,
                   duration_ms, error_code, error_message, updated_at
            FROM market.hithink_daily_sync_status FINAL
            WHERE trade_date = {trade_date:Date}
            ORDER BY task_name
            """,
            parameters={"trade_date": as_of.date()},
            settings={"max_execution_time": 1},
        )
        server_elapsed_values.append(int(daily.summary.get("elapsed_ns", 0)))
        sector = writer.client.query(
            """
            SELECT sector_type, status, sector_count, sector_success_count,
                   sector_failed_count, membership_count, start_time, end_time,
                   error_code, error_message
            FROM market.sector_mapping_sync_status FINAL
            WHERE sync_date = {trade_date:Date}
              AND sync_run_id = (
                  SELECT argMax(sync_run_id, start_time)
                  FROM market.sector_mapping_sync_status FINAL
                  WHERE sync_date = {trade_date:Date}
              )
            ORDER BY sector_type
            """,
            parameters={"trade_date": as_of.date()},
            settings={"max_execution_time": 1},
        )
        server_elapsed_values.append(int(sector.summary.get("elapsed_ns", 0)))
        return {
            "is_trading_day": bool(first[0]),
            "nodes": nodes,
            "daily": [dict(zip(daily.column_names, row, strict=True)) for row in daily.result_rows],
            "sector": [dict(zip(sector.column_names, row, strict=True)) for row in sector.result_rows],
            "query_elapsed_ms": round(max(server_elapsed_values, default=0) / 1_000_000),
            "database_total_elapsed_ms": round(sum(server_elapsed_values) / 1_000_000),
            "query_roundtrip_ms": round((perf_counter() - started) * 1000),
        }


def _canonical_state(value: Any) -> str:
    state = str(value or "PENDING").upper()
    aliases = {
        "RAW_RUNNING": "RUNNING",
        "DERIVING": "RUNNING",
        "RAW_SUCCESS": "SUCCESS",
        "PARTIAL_SUCCESS": "PARTIAL",
        "RAW_PARTIAL": "PARTIAL",
        "RAW_PARTIAL_SUCCESS": "PARTIAL",
        "DERIVATION_FAILED": "FAILED",
        "RAW_FAILED": "FAILED",
        "MISSED": "SKIPPED",
        "NOT_APPLICABLE": "SKIPPED",
        "": "PENDING",
    }
    return aliases.get(state, state)


def _state_bucket(
    value: Any,
    row: dict[str, Any],
    as_of: datetime,
    active_collection_id: str | None,
    *,
    derivation: bool = False,
) -> str | None:
    state = _canonical_state(value)
    raw_state = _canonical_state(row.get("raw_status"))
    collection_id = str(_json_safe(row.get("collection_id")))
    scheduled_time = row.get("scheduled_time")
    age_seconds = (
        (as_of - scheduled_time).total_seconds()
        if isinstance(scheduled_time, datetime)
        else 999999.0
    )
    if derivation and state == "PENDING":
        derivation_state = _canonical_state(row.get("derivation_status"))
        if collection_id == active_collection_id and derivation_state == "RUNNING":
            return "running"
        if raw_state != "SUCCESS":
            return "blocked"
    if derivation and state == "SKIPPED" and raw_state != "SUCCESS":
        return "blocked"
    if state == "RUNNING":
        if collection_id == active_collection_id:
            return "running"
        return "timeout" if age_seconds > 180 else None
    if state == "FAILED_TIMEOUT":
        return "timeout"
    if state == "SUCCESS":
        return "success"
    if state == "PARTIAL":
        return "partial"
    if state == "FAILED":
        return "failed"
    if state == "BLOCKED":
        return "blocked"
    if state == "SKIPPED":
        return "failed"
    return None


def _empty_stats(expected: int = 0) -> dict[str, Any]:
    return {
        "expected": expected,
        "success": 0,
        "partial": 0,
        "failed": 0,
        "blocked": 0,
        "timeout": 0,
        "running": 0,
        "closed": expected == 0,
    }


def _count_states(
    rows: list[dict[str, Any]],
    field: str,
    as_of: datetime,
    active_collection_id: str | None,
    *,
    derivation: bool = False,
) -> dict[str, Any]:
    stats = _empty_stats(len(rows))
    for row in rows:
        bucket = _state_bucket(
            row.get(field), row, as_of, active_collection_id, derivation=derivation
        )
        if bucket:
            stats[bucket] += 1
    accounted = sum(stats[key] for key in ("success", "partial", "failed", "blocked", "timeout", "running"))
    stats["closed"] = accounted == stats["expected"]
    stats["unclassified"] = stats["expected"] - accounted
    return stats


def _pool_applicable(row: dict[str, Any]) -> bool:
    sequence = int(row.get("sequence_no") or 0)
    return not (1 <= sequence <= 10 or 251 <= sequence <= 253)


def _next_schedule(as_of: datetime) -> datetime | None:
    return next(
        (node.scheduled_time for node in build_daily_schedule(as_of.date()) if node.scheduled_time > as_of),
        None,
    )


def _next_pool_schedule(as_of: datetime) -> datetime | None:
    return next(
        (
            node.scheduled_time
            for node in build_daily_schedule(as_of.date())
            if node.scheduled_time > as_of and node.limit_pools_applicable
        ),
        None,
    )


def _error_category(code: str, message: str) -> str:
    text = f"{code} {message}".lower()
    if "missing required pagination" in text:
        return "接口返回结构异常"
    if "429" in text or "request limit exceeded" in text:
        return "接口限流"
    if "ssl" in text and ("handshake" in text or "timed out" in text):
        return "网络/SSL握手超时"
    if "failed_timeout" in text or "timeout" in text or "timed out" in text:
        return "采集超时"
    return "未分类采集异常"


def _task_from_error(text: str) -> str:
    prefix = text.split(":", 1)[0].strip()
    return RAW_ITEMS.get(f"{prefix}_status", RAW_ITEMS.get(prefix, "行情快照"))


def _error_events(row: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    code_text = str(row.get("raw_error_code") or row.get("error_code") or "")
    message_text = str(row.get("raw_error_message") or row.get("error_message") or "")
    codes = [part.strip() for part in code_text.split(",") if part.strip()] or [""]
    messages = [part.strip() for part in message_text.split(";") if part.strip()] or [message_text]
    events: list[tuple[str, str, str, str]] = []
    for index in range(max(len(codes), len(messages))):
        code_part = codes[min(index, len(codes) - 1)]
        message_part = messages[min(index, len(messages) - 1)]
        raw_code = code_part.split(":", 1)[-1].strip()
        task_source = code_part if ":" in code_part else message_part
        events.append(
            (
                _error_category(raw_code, message_part),
                raw_code,
                _task_from_error(task_source),
                message_part,
            )
        )
    return events


def _aggregate_errors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not (row.get("raw_error_code") or row.get("raw_error_message")):
            continue
        collection_id = str(_json_safe(row.get("collection_id")))
        occurred_at = row.get("scheduled_time")
        for category, code, item, message in _error_events(row):
            group = grouped.setdefault(
                category,
                {
                    "category": category,
                    "codes": Counter(),
                    "node_ids": set(),
                    "occurrence_count": 0,
                    "affected_items": set(),
                    "times": [],
                    "latest_message": "",
                    "latest_time": None,
                    "needs_repair": False,
                },
            )
            group["codes"][code] += 1
            group["node_ids"].add(collection_id)
            group["occurrence_count"] += 1
            group["affected_items"].add(item)
            group["needs_repair"] = bool(
                group["needs_repair"]
                or _canonical_state(row.get("raw_status")) != "SUCCESS"
            )
            if isinstance(occurred_at, datetime):
                group["times"].append(occurred_at)
                if group["latest_time"] is None or occurred_at >= group["latest_time"]:
                    group["latest_time"] = occurred_at
                    group["latest_message"] = message

    result: list[dict[str, Any]] = []
    for group in grouped.values():
        latest_time = group["latest_time"]
        affected_items = sorted(group["affected_items"])
        recovered_items: list[str] = []
        for item in affected_items:
            field = next((field for field, label in RAW_ITEMS.items() if label == item), "raw_status")
            if any(
                isinstance(row.get("scheduled_time"), datetime)
                and latest_time is not None
                and row["scheduled_time"] > latest_time
                and _canonical_state(row.get(field)) == "SUCCESS"
                for row in rows
            ):
                recovered_items.append(item)
        recovered = bool(affected_items) and len(recovered_items) == len(affected_items)
        result.append(
            {
                "category": group["category"],
                "code": group["codes"].most_common(1)[0][0],
                "affected_nodes": len(group["node_ids"]),
                "occurrence_count": group["occurrence_count"],
                "first_time": min(group["times"]) if group["times"] else None,
                "latest_time": max(group["times"]) if group["times"] else None,
                "affected_items": affected_items,
                "latest_message": group["latest_message"],
                "recovered": recovered,
                "waiting_repair": bool(group["needs_repair"]),
            }
        )
    return sorted(result, key=lambda item: (-item["affected_nodes"], item["category"]))


def _parse_task_time(value: str | None) -> datetime | None:
    if not value or value == "N/A":
        return None
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=SHANGHAI)
        except ValueError:
            pass
    return None


def _daily_trigger_counts(as_of: datetime, last_run_time: str | None) -> tuple[int, int]:
    triggers = (time(8, 40), time(10), time(16))
    expected = sum(as_of >= datetime.combine(as_of.date(), trigger, SHANGHAI) for trigger in triggers)
    last_run = _parse_task_time(last_run_time)
    success = (
        sum(last_run >= datetime.combine(as_of.date(), trigger, SHANGHAI) for trigger in triggers)
        if last_run and last_run.date() == as_of.date()
        else 0
    )
    return expected, min(success, expected)


def _simple_stats(expected: int, success: int, running: int = 0) -> dict[str, Any]:
    stats = _empty_stats(expected)
    stats["success"] = min(success, expected)
    stats["running"] = min(running, max(0, expected - stats["success"]))
    stats["failed"] = max(0, expected - stats["success"] - stats["running"])
    stats["closed"] = True
    stats["unclassified"] = 0
    return stats


def _detail_rows(
    rows: list[dict[str, Any]],
    as_of: datetime,
    active_collection_id: str | None,
    daily: list[dict[str, Any]],
    sector: list[dict[str, Any]],
    scheduler: dict[str, Any],
    trading_day: bool,
) -> list[dict[str, Any]]:
    next_snapshot = _next_schedule(as_of)
    next_pool = _next_pool_schedule(as_of)
    details: list[dict[str, Any]] = []
    pool_fields = {"limit_up_pool_status", "limit_down_pool_status", "limit_break_pool_status"}
    for field, item in RAW_ITEMS.items():
        applicable = [row for row in rows if field not in pool_fields or _pool_applicable(row)]
        details.append(
            {
                "group": "盘中快照",
                "item": item,
                **_count_states(applicable, field, as_of, active_collection_id),
                "next_scheduled_time": next_pool if field in pool_fields else next_snapshot,
            }
        )

    for item in DERIVED_ITEMS:
        field = "derivation_status" if item in {"个股派生数据", "全市场状态"} else "sector_state_status"
        details.append(
            {
                "group": "派生",
                "item": item,
                **_count_states(rows, field, as_of, active_collection_id, derivation=True),
                "next_scheduled_time": next_snapshot,
            }
        )

    scheduled = {task["name"]: task for task in scheduler.get("tasks", [])}
    daily_task = scheduled.get("HithinkDailyPipeline", {})
    daily_expected, daily_success = _daily_trigger_counts(as_of, daily_task.get("last_run_time"))
    daily_steps = {row["task_name"]: row for row in daily}
    daily_running = int(daily_task.get("state") == "Running")
    daily_next = _parse_task_time(daily_task.get("next_run_time"))
    monday_delta = (7 - as_of.weekday()) % 7
    adjustment_next = datetime.combine(as_of.date() + timedelta(days=monday_delta), time(16), SHANGHAI)
    if adjustment_next <= as_of:
        adjustment_next += timedelta(days=7)
    daily_specs = [
        ("交易日判断", daily_expected, daily_success, daily_next),
        (
            "日K数据",
            int(trading_day and as_of.time() >= time(16)),
            int((daily_steps.get("hithink_daily_k_raw_sync") or {}).get("status") == "SUCCESS"),
            datetime.combine(as_of.date(), time(16), SHANGHAI),
        ),
        (
            "复权、除权事件",
            int(trading_day and as_of.weekday() == 0 and as_of.time() >= time(16)),
            int((daily_steps.get("hithink_adjustment_events_sync") or {}).get("status") == "SUCCESS"),
            adjustment_next,
        ),
        ("补查", daily_expected, daily_success, daily_next),
    ]
    for item, expected, success, next_time in daily_specs:
        details.append(
            {
                "group": "每日数据",
                "item": item,
                **_simple_stats(expected, success, daily_running if expected > success else 0),
                "next_scheduled_time": next_time,
            }
        )

    sector_task = scheduled.get("HithinkSectorMappingSync", {})
    sector_next = _parse_task_time(sector_task.get("next_run_time"))
    sector_types = {row["sector_type"]: row for row in sector}
    for sector_type, label in (("concept", "概念"), ("industry", "行业"), ("style", "风格")):
        expected = int(as_of.time() >= time(8, 20))
        success = int((sector_types.get(sector_type) or {}).get("status") == "SUCCESS")
        stats = _simple_stats(expected, success, int(sector_task.get("state") == "Running"))
        for suffix in ("板块目录", "板块股票成员关系"):
            details.append(
                {
                    "group": "板块映射",
                    "item": f"{label}{suffix}",
                    **stats,
                    "next_scheduled_time": sector_next,
                }
            )
    return details


def _automated_tasks(scheduler: dict[str, Any]) -> list[dict[str, Any]]:
    scheduled = {task["name"]: task for task in scheduler.get("tasks", [])}
    collector_processes = scheduler.get("collector_processes", [])
    result: list[dict[str, Any]] = []
    for name in TASK_NAMES:
        task = scheduled.get(name, {})
        if name == "HithinkSnapshotCollector":
            status = "RUNNING" if collector_processes else "STOPPED"
            pid = collector_processes[0]["pid"] if collector_processes else None
        elif task.get("state") == "Running":
            status = "RUNNING"
            pid = None
        elif task.get("state") == "Ready" and task.get("last_result") == 0:
            status = "READY_LAST_SUCCESS"
            pid = None
        else:
            status = "ERROR"
            pid = None
        result.append(
            {
                "name": name,
                "label": TASK_LABELS[name],
                "status": status,
                "pid": pid,
                "last_run_time": task.get("last_run_time"),
                "next_run_time": task.get("next_run_time"),
                "last_result": task.get("last_result"),
            }
        )
    return result


def build_status_payload(
    *,
    as_of: datetime,
    trading_day: bool,
    nodes: list[dict[str, Any]],
    daily: list[dict[str, Any]],
    sector: list[dict[str, Any]],
    scheduler: dict[str, Any],
    resources: dict[str, Any] | None,
    docker: dict[str, Any] | None,
    query_ok: bool,
    query_elapsed_ms: int,
) -> dict[str, Any]:
    active = scheduler.get("active_node") or {}
    active_collection_id = str(active.get("collection_id")) if active else None
    node_by_id = {str(_json_safe(row.get("collection_id"))): row for row in nodes}
    due_nodes: list[dict[str, Any]] = []
    if trading_day:
        for node in build_daily_schedule(as_of.date()):
            if node.scheduled_time > as_of:
                continue
            row = node_by_id.get(node.collection_id)
            if row is None:
                row = {
                    "collection_id": node.collection_id,
                    "scheduled_time": node.scheduled_time,
                    "sequence_no": node.sequence_no,
                    "status": "PENDING",
                    "raw_status": "PENDING",
                    "derivation_status": "PENDING",
                    **{field: "PENDING" for field in RAW_ITEMS},
                    "sector_state_status": "PENDING",
                    "raw_error_code": "MISSING_NODE_STATE",
                    "raw_error_message": "scheduled node has no status record",
                }
            due_nodes.append(row)

    intraday = _count_states(due_nodes, "raw_status", as_of, active_collection_id)
    derivation = _count_states(
        due_nodes, "derivation_status", as_of, active_collection_id, derivation=True
    )
    errors = _aggregate_errors(due_nodes)
    details = _detail_rows(
        due_nodes, as_of, active_collection_id, daily, sector, scheduler, trading_day
    )
    all_closed = intraday["closed"] and derivation["closed"] and all(row["closed"] for row in details)
    integrity = {
        "closed": all_closed,
        "message": "状态统计闭合" if all_closed else "状态统计不闭合",
    }

    processes = scheduler.get("collector_processes", [])
    collector_running = bool(processes)
    clickhouse_healthy = bool(query_ok and docker and docker.get("running"))
    n100_online = resources is not None
    schedule = build_daily_schedule(as_of.date())
    collection_window = schedule[0].scheduled_time <= as_of <= schedule[-1].scheduled_time
    recent = sorted(due_nodes, key=lambda row: int(row.get("sequence_no") or 0))[-3:]
    continuous_failure = len(recent) == 3 and all(
        _state_bucket(row.get("raw_status"), row, as_of, active_collection_id) in {"failed", "timeout"}
        for row in recent
    )
    has_anomaly = any(
        intraday[key] or derivation[key]
        for key in ("partial", "failed", "blocked", "timeout")
    )
    if not trading_day:
        overall = "IDLE"
    elif (
        not all_closed
        or not clickhouse_healthy
        or not n100_online
        or collection_window and not collector_running
        or continuous_failure
    ):
        overall = "UNHEALTHY"
    elif has_anomaly:
        overall = "DEGRADED"
    else:
        overall = "HEALTHY"

    successful = [row for row in due_nodes if _canonical_state(row.get("raw_status")) == "SUCCESS"]
    last_success = max((row.get("scheduled_time") for row in successful), default=None)
    next_scheduled = _next_schedule(as_of) if trading_day else None
    tasks = _automated_tasks(scheduler)
    collector_task = next((task for task in tasks if task["name"] == "HithinkSnapshotCollector"), {})
    payload = {
        "overall_status": overall,
        "as_of": as_of,
        "collector": {
            "status": collector_task.get("status", "UNKNOWN"),
            "pid": collector_task.get("pid"),
            "active_collection_id": active_collection_id,
            "last_success_time": last_success,
            "next_scheduled_time": next_scheduled,
        },
        "intraday": intraday,
        "derivation": derivation,
        "anomaly_nodes": {
            "total": intraday["partial"] + intraday["failed"] + intraday["timeout"],
            "complete_failure": intraday["failed"] + intraday["timeout"],
            "partial_failure": intraday["partial"],
        },
        "errors": errors,
        "integrity": integrity,
        "services": {
            "clickhouse": "HEALTHY" if clickhouse_healthy else "UNHEALTHY",
            "n100": "ONLINE" if n100_online else "OFFLINE",
        },
        "automated_tasks": tasks,
        "details": details,
        "clickhouse": docker,
        "n100": resources,
        "query_elapsed_ms": query_elapsed_ms,
    }
    return _json_safe(payload)


def check_status() -> dict[str, Any]:
    """Run one bounded local read-only check and return structured facts."""
    started = perf_counter()
    as_of = datetime.now(SHANGHAI)
    checks: dict[str, Callable[[], Any]] = {
        "progress": lambda: _query_progress(as_of),
        "scheduler": _scheduler_state,
        "resources": _resource_state,
        "docker": _docker_state,
    }
    executor = ThreadPoolExecutor(max_workers=4)
    futures = {executor.submit(_timed, name, check): name for name, check in checks.items()}
    done, pending = wait(futures, timeout=3.5)
    results: dict[str, dict[str, Any]] = {}
    for future in done:
        result = future.result()
        results[result["name"]] = result
    for future in pending:
        name = futures[future]
        future.cancel()
        results[name] = {"name": name, "status": "TIMEOUT", "elapsed_ms": 3500}
    executor.shutdown(wait=False, cancel_futures=True)

    progress_result = results.get("progress", {})
    progress = progress_result.get("data", {})
    payload = build_status_payload(
        as_of=as_of,
        trading_day=bool(progress.get("is_trading_day", True)),
        nodes=progress.get("nodes", []),
        daily=progress.get("daily", []),
        sector=progress.get("sector", []),
        scheduler=results.get("scheduler", {}).get("data", {}),
        resources=results.get("resources", {}).get("data"),
        docker=results.get("docker", {}).get("data"),
        query_ok=progress_result.get("status") == "OK",
        query_elapsed_ms=int(progress.get("query_elapsed_ms", progress_result.get("elapsed_ms", 0))),
    )
    payload["query_roundtrip_ms"] = int(progress.get("query_roundtrip_ms", 0))
    payload["database_total_elapsed_ms"] = int(progress.get("database_total_elapsed_ms", 0))
    payload["tool_elapsed_ms"] = round((perf_counter() - started) * 1000)
    payload["steps"] = [results[name] for name in checks]
    return _json_safe(payload)


def _display_time(value: Any, current_date: date | None = None) -> str:
    if not value:
        return "未设置"
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        parsed = _parse_task_time(str(value))
        if parsed is None:
            return str(value)
    reference_date = current_date or datetime.now(SHANGHAI).date()
    return parsed.strftime("%H:%M:%S") if parsed.date() == reference_date else parsed.strftime("%m月%d日 %H:%M:%S")


def _gb(value: int | None) -> str:
    return "未知" if value is None else f"{value / 1024**3:.2f} GB"


def render_combined_status(report: dict[str, Any]) -> str:
    labels = {
        "HEALTHY": "正常",
        "DEGRADED": "异常降级",
        "UNHEALTHY": "异常",
        "IDLE": "非交易日空闲",
    }
    report_date = datetime.fromisoformat(str(report["as_of"])).date()
    intraday = report["intraday"]
    derivation = report["derivation"]
    lines = [
        f"总体状态：{labels.get(report['overall_status'], report['overall_status'])}",
        f"截至：{str(report['as_of'])[11:19]}",
        "",
        (
            f"盘中节点：应执行{intraday['expected']}，成功{intraday['success']}，"
            f"部分成功{intraday['partial']}，失败{intraday['failed']}，"
            f"超时{intraday['timeout']}，运行中{intraday['running']}"
        ),
        (
            f"派生节点：成功{derivation['success']}，上游阻塞{derivation['blocked']}，"
            f"自身失败{derivation['failed']}，超时{derivation['timeout']}，"
            f"运行中{derivation['running']}"
        ),
        f"最近成功：{_display_time(report['collector']['last_success_time'], report_date)}",
        f"下次执行：{_display_time(report['collector']['next_scheduled_time'], report_date)}",
        "",
    ]
    if report["errors"]:
        summaries = []
        for error in report["errors"]:
            unit = "处" if error["category"] == "接口限流" else "个节点"
            count = error["occurrence_count"] if unit == "处" else error["affected_nodes"]
            summaries.append(f"{error['category']}{count}{unit}")
        lines.append("主要异常：" + "；".join(summaries) + "。")
    else:
        lines.append("主要异常：无。")
    if not report["integrity"]["closed"]:
        lines.append(f"统计校验：{report['integrity']['message']}，不生成正常结论。")
    anomaly = report["anomaly_nodes"]
    if anomaly["total"]:
        lines.append(
            f"异常节点{anomaly['total']}个：完全失败{anomaly['complete_failure']}个，"
            f"部分失败{anomaly['partial_failure']}个。"
        )

    lines.extend(
        [
            "",
            "| 任务组 | 具体项目 | 当前应进行 | 成功 | 部分成功 | 失败 | 阻塞 | 超时 | 运行中 | 下次采集时间 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["details"]:
        lines.append(
            f"| {row['group']} | {row['item']} | {row['expected']} | {row['success']} | "
            f"{row['partial']} | {row['failed']} | {row['blocked']} | {row['timeout']} | "
            f"{row['running']} | {_display_time(row['next_scheduled_time'], report_date)} |"
        )

    if report["errors"]:
        lines.extend(
            [
                "",
                "| 异常原因 | 受影响节点 | 首次发生 | 最近发生 | 受影响数据项 | 已恢复 | 等待补采 |",
                "|---|---:|---|---|---|---|---|",
            ]
        )
        for error in report["errors"]:
            lines.append(
                f"| {error['category']} | {error['affected_nodes']} | "
                f"{_display_time(error['first_time'], report_date)} | "
                f"{_display_time(error['latest_time'], report_date)} | "
                f"{('、'.join(error['affected_items']))} | "
                f"{'是' if error['recovered'] else '否'} | "
                f"{'是' if error['waiting_repair'] else '否'} |"
            )

    lines.extend(["", "| 类别 | 项目 | 状态 | 详细信息 |", "|---|---|---|---|"])
    for task in report["automated_tasks"]:
        detail = f"PID {task['pid']}" if task.get("pid") else f"下次 {_display_time(task.get('next_run_time'), report_date)}"
        lines.append(f"| 自动任务 | {task['label']} | {task['status']} | {detail} |")
    clickhouse = report.get("clickhouse") or {}
    lines.append(
        f"| 依赖服务 | ClickHouse | {report['services']['clickhouse']} | "
        f"容器运行={clickhouse.get('running')}，健康={clickhouse.get('health')} |"
    )
    resources = report.get("n100") or {}
    lines.extend(
        [
            f"| N100服务器 | 内存 | {report['services']['n100']} | 可用{_gb(resources.get('memory_free_bytes'))} / 总计{_gb(resources.get('memory_total_bytes'))} |",
            f"| N100服务器 | D盘 | {report['services']['n100']} | 可用{_gb(resources.get('disk_d_free_bytes'))} / 总计{_gb(resources.get('disk_d_total_bytes'))} |",
            f"| N100服务器 | 运行时间 | {report['services']['n100']} | {round(resources.get('uptime_ms', 0) / 3600000, 2)}小时 |",
            "",
            f"单条数据库查询最长：{report['query_elapsed_ms']}毫秒；数据库执行合计：{report.get('database_total_elapsed_ms', 0)}毫秒；数据库往返：{report.get('query_roundtrip_ms', 0)}毫秒；工具内部总耗时：{report.get('tool_elapsed_ms', 0)}毫秒。",
        ]
    )
    return "\n".join(lines)


def collection_status_response() -> dict[str, Any]:
    payload = check_status()
    payload["markdown"] = render_combined_status(payload)
    return payload
