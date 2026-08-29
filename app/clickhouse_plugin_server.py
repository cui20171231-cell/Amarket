"""MCP server exposing one read-only Amarket collection-status query."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.clickhouse_readonly import execute_clickhouse_readonly_sql
from app.hithink.status import collection_status_response

TOOLS = [
    {
        "name": "collection_status_query",
        "title": "采集状态查询",
        "description": (
            "按需读取N100上的采集状态。无需参数。一次返回盘中快照、派生、每日数据、"
            "板块映射、三个自动任务、ClickHouse依赖和N100服务器状态，并显示查询耗时。"
            "使用方法：直接调用本工具，不要先调用其他接口。工具只读，不修改或重启任务。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "execute_clickhouse_readonly_sql",
        "title": "ClickHouse只读查询",
        "description": (
            "对 D:\\Amarket 的 ClickHouse（127.0.0.1:8123 / market）执行任意只读查询。"
            "支持SELECT、WITH、SHOW、DESCRIBE、DESC、EXPLAIN和EXISTS；支持参数化查询、"
            "结果行数和超时限制。强制readonly=1，不能写表、删表或修改数据。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "parameters": {"type": ["object", "null"]},
                "max_rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10000,
                    "default": 2000,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "default": 30,
                },
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
]


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "collection_status_query":
        if arguments:
            raise ValueError("采集状态查询不需要参数")
        report = collection_status_response()
        return {
            "content": [{"type": "text", "text": report["markdown"]}],
            "structuredContent": report,
        }
    if name == "execute_clickhouse_readonly_sql":
        result = execute_clickhouse_readonly_sql(**arguments)
        return {
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
            ]
        }
    raise ValueError(f"未知工具：{name}")


def respond(message: dict[str, Any]) -> None:
    print(json.dumps(message, ensure_ascii=False, default=str), flush=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    for line in sys.stdin:
        request: dict[str, Any] = {}
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": request.get("params", {}).get(
                        "protocolVersion", "2024-11-05"
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "采集状态查询", "version": "1.0.0"},
                }
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params", {})
                result = call_tool(
                    str(params.get("name", "")),
                    dict(params.get("arguments", {})),
                )
            else:
                raise ValueError(f"不支持的方法：{method}")
            if request_id is not None:
                respond({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:  # noqa: BLE001 - MCP must return a structured error.
            if request.get("id") is not None:
                respond(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32000, "message": str(exc)},
                    }
                )


if __name__ == "__main__":
    main()
