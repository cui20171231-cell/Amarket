"""MCP server exposing Amarket's read-only status, package and SQL tools."""

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
from app.market_context_state import get_market_context_state
from app.market_state_package_reader import compact_package_for_ai, get_market_state_package

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
        "name": "get_market_state_package",
        "title": "读取市场状态聚合包",
        "description": (
            "读取本地已经生成的A股市场状态聚合包。"
            "当用户输入“/ai 时间”、“/AI 时间”、指定盘中时间、节点号，或要求恢复某个盘中市场状态时，"
            "优先调用本工具。工具按交易日和目标时间/节点定位JSON聚合包并返回复盘核心数据，"
            "仅省略体积过大的全概念历史轨迹矩阵，核心板块自身轨迹仍完整保留；"
            "可在一次调用内等待目标包就绪；不重新查询数据库、不触发聚合任务。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_time": {"type": ["string", "null"]},
                "node_seq": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": 254,
                },
                "trade_date": {"type": ["string", "null"]},
                "wait_for_ready": {"type": "boolean", "default": False},
                "retry_interval_seconds": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 30,
                    "default": 10,
                },
                "max_wait_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 300,
                    "default": 120,
                },
            },
            "anyOf": [{"required": ["target_time"]}, {"required": ["node_seq"]}],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_market_context_state",
        "title": "读取个股或板块上下文",
        "description": (
            "按需读取指定时间的单只股票、最多5只股票或单个板块上下文。"
            "stock模式返回个股轨迹、自动筛选的主要方向及板块内地位；"
            "stocks模式分别返回各股票自己的方向上下文；"
            "sector模式返回板块状态、压缩轨迹和少量关键成员。"
            "数据库端完成聚合、排名和筛选，不返回完整成员分钟明细。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_type": {"type": "string", "enum": ["stock", "stocks", "sector"]},
                "ticker": {"type": ["string", "null"]},
                "tickers": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 5,
                },
                "sector_id": {"type": ["string", "null"]},
                "sector_name": {"type": ["string", "null"]},
                "target_time": {"type": "string"},
                "trade_date": {"type": ["string", "null"]},
            },
            "required": ["target_type", "target_time"],
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
    if name == "get_market_state_package":
        result = compact_package_for_ai(get_market_state_package(**arguments))
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
            ],
        }
    if name == "get_market_context_state":
        result = get_market_context_state(**arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
            ],
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
                    "serverInfo": {"name": "我的行情数据库", "version": "1.1.0"},
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
