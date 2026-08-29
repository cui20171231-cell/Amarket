from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any

from app.hithink.config import Settings
from app.hithink.writer import ClickHouseWriter

READ_ONLY_PREFIXES = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "EXISTS"}
MAX_ROWS_LIMIT = 10_000
MAX_TIMEOUT_SECONDS = 120


def _first_keyword(sql: str) -> str:
    without_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    without_comments = re.sub(r"(?m)^\s*--.*$", " ", without_comments)
    match = re.search(r"[A-Za-z]+", without_comments)
    return match.group(0).upper() if match else ""


def _validate_read_only_sql(sql: str) -> str:
    statement = sql.strip()
    if not statement:
        raise ValueError("sql不能为空")
    if _first_keyword(statement) not in READ_ONLY_PREFIXES:
        raise ValueError("只允许SELECT、WITH、SHOW、DESCRIBE、DESC、EXPLAIN或EXISTS只读语句")
    return statement


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def execute_clickhouse_readonly_sql(
    sql: str,
    parameters: dict[str, Any] | None = None,
    max_rows: int = 2_000,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    statement = _validate_read_only_sql(sql)
    if not 1 <= max_rows <= MAX_ROWS_LIMIT:
        raise ValueError(f"max_rows必须在1到{MAX_ROWS_LIMIT}之间")
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds必须在1到{MAX_TIMEOUT_SECONDS}之间")

    settings = Settings.load()
    writer = ClickHouseWriter(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        settings.clickhouse_username,
        settings.clickhouse_password,
    )
    started = perf_counter()
    try:
        result = writer.client.query(
            statement,
            parameters=parameters or {},
            settings={
                "readonly": 1,
                "max_execution_time": timeout_seconds,
                "max_result_rows": max_rows + 1,
                "result_overflow_mode": "break",
            },
        )
        rows = list(result.result_rows)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return {
            "database": {
                "root": r"D:\Amarket",
                "host": settings.clickhouse_host,
                "port": settings.clickhouse_port,
                "name": settings.clickhouse_database,
            },
            "columns": list(result.column_names),
            "rows": _json_safe(rows),
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "mode": "strictly_read_only",
        }
    finally:
        writer.close()
