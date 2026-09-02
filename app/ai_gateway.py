from __future__ import annotations

import atexit
import ctypes
import json
import os
import time
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from app.clickhouse_readonly import execute_clickhouse_readonly_sql
from app.hithink.status import collection_status_response
from app.logging_utils import append_jsonl_bounded
from app.market_context_state import get_market_context_state as read_market_context_state
from app.market_state_package_reader import compact_package_for_ai
from app.market_state_package_reader import (
    get_market_state_package as read_market_state_package,
)

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
    from mcp.types import ToolAnnotations
except ImportError as exc:
    raise RuntimeError("AI 网关依赖尚未安装。请运行 D:\\Amarket\\INSTALL_AI_GATEWAY.cmd。") from exc


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = int(os.environ.get("AMARKET_AI_GATEWAY_PORT", "2091"))
LOG_DIR = ROOT / "logs"
AUDIT_PATH = LOG_DIR / "ai_gateway_audit.jsonl"
PID_PATH = LOG_DIR / "ai_gateway.pid"
_GATEWAY_MUTEX_HANDLE: int | None = None

ArgModelBase.model_config["extra"] = "forbid"

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

mcp = FastMCP(
    "我的行情数据库",
    instructions=(
        "这是 D:\\Amarket 的 ClickHouse 查询入口。"
        "数据库为 127.0.0.1:8123 / market。"
        "仅提供采集状态、聚合包读取和只读查询，不修改数据、不启动或重启任务。"
        "当用户输入/ai 时间、/AI 时间或要求恢复盘中市场状态时，优先调用"
        "get_market_state_package，不要先执行自由SQL。"
    ),
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    log_level="INFO",
)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _audit(tool: str, arguments: dict[str, Any], ok: bool, elapsed: float, error: str = "") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "tool": tool,
        "arguments": _json_safe(arguments),
        "ok": ok,
        "elapsed_seconds": round(elapsed, 4),
        "error": error[:500],
    }
    try:
        append_jsonl_bounded(AUDIT_PATH, record)
    except OSError:
        pass


def audited(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        started = time.monotonic()
        arguments = {key: value for key, value in kwargs.items() if value is not None}
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            _audit(function.__name__, arguments, False, time.monotonic() - started, str(exc))
            raise
        _audit(function.__name__, arguments, True, time.monotonic() - started)
        return result

    return wrapper


@mcp.tool(
    title="采集状态查询",
    description="返回 D:\\Amarket 的 ClickHouse、采集和派生任务状态。仅查询，不修改任何数据或任务。",
    annotations=READ_ONLY,
)
@audited
def collection_status_query() -> dict[str, Any]:
    return collection_status_response()


@mcp.tool(
    title="读取市场状态聚合包",
    description=(
        "读取本地已经生成的A股市场状态聚合包。"
        "当用户输入“/ai 时间”、“/AI 时间”、指定盘中时间、节点号，或要求恢复某个盘中市场状态时，"
        "优先调用本工具。工具按交易日和目标时间/节点定位JSON聚合包并返回复盘核心数据，"
        "仅省略体积过大的全概念历史轨迹矩阵，核心板块自身轨迹仍完整保留；"
        "可在一次调用内等待目标包就绪；不重新查询数据库、不触发聚合任务。"
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
@audited
def get_market_state_package(
    target_time: str | None = None,
    node_seq: int | None = None,
    trade_date: str | None = None,
    wait_for_ready: bool = False,
    retry_interval_seconds: int = 10,
    max_wait_seconds: int = 120,
) -> str:
    result = compact_package_for_ai(
        read_market_state_package(
            target_time=target_time,
            node_seq=node_seq,
            trade_date=trade_date,
            wait_for_ready=wait_for_ready,
            retry_interval_seconds=retry_interval_seconds,
            max_wait_seconds=max_wait_seconds,
        )
    )
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)


@mcp.tool(
    title="读取个股或板块上下文",
    description=(
        "按需读取指定时间的单只股票、最多5只股票或单个板块上下文。"
        "stock模式返回个股轨迹、自动筛选的主要方向及板块内地位；"
        "stocks模式分别返回各股票自己的方向上下文；"
        "sector模式返回板块状态、压缩轨迹和少量关键成员。"
        "查询在数据库端完成聚合、排名和筛选，不返回完整成员分钟明细。"
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
@audited
def get_market_context_state(
    target_type: str,
    target_time: str,
    ticker: str | None = None,
    tickers: list[str] | None = None,
    sector_id: str | None = None,
    sector_name: str | None = None,
    trade_date: str | None = None,
) -> str:
    result = read_market_context_state(
        target_type=target_type,
        target_time=target_time,
        ticker=ticker,
        tickers=tickers,
        sector_id=sector_id,
        sector_name=sector_name,
        trade_date=trade_date,
    )
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)


@mcp.tool(
    title="ClickHouse 只读查询",
    description=(
        "对 D:\\Amarket 的 ClickHouse（127.0.0.1:8123 / market）执行只读查询。"
        "只允许 SELECT、WITH、SHOW、DESCRIBE、DESC、EXPLAIN、EXISTS。"
    ),
    annotations=READ_ONLY,
)
@audited
def query_market_data(
    sql: str,
    parameters: dict[str, Any] | None = None,
    max_rows: int = 2_000,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    return execute_clickhouse_readonly_sql(
        sql=sql,
        parameters=parameters,
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
    )


def _claim_pid() -> None:
    global _GATEWAY_MUTEX_HANDLE

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        create_mutex.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_bool

        ctypes.set_last_error(0)
        handle = create_mutex(None, False, "Global\\AmarketAiGateway")
        if not handle:
            last_error = ctypes.get_last_error()
            if last_error != 5:  # ERROR_ACCESS_DENIED means SYSTEM owns the mutex.
                raise ctypes.WinError(last_error)
        if not handle or ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            if handle:
                close_handle(handle)
            try:
                old_pid = int(PID_PATH.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                old_pid = 0
            suffix = f"（进程号 {old_pid}）" if old_pid else ""
            raise RuntimeError(f"Amarket AI 网关已在运行{suffix}。")
        _GATEWAY_MUTEX_HANDLE = handle
        PID_PATH.write_text(str(os.getpid()), encoding="ascii")
        return

    if PID_PATH.exists():
        try:
            old_pid = int(PID_PATH.read_text(encoding="ascii").strip())
            os.kill(old_pid, 0)
        except (OSError, ValueError):
            PID_PATH.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"Amarket AI 网关已在运行（进程号 {old_pid}）。")
    PID_PATH.write_text(str(os.getpid()), encoding="ascii")


def _release_pid() -> None:
    global _GATEWAY_MUTEX_HANDLE

    try:
        if PID_PATH.exists() and PID_PATH.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_PATH.unlink()
    except OSError:
        pass
    if os.name == "nt" and _GATEWAY_MUTEX_HANDLE is not None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_bool
        close_handle(_GATEWAY_MUTEX_HANDLE)
        _GATEWAY_MUTEX_HANDLE = None


def main() -> int:
    _claim_pid()
    atexit.register(_release_pid)
    print("Amarket AI 网关")
    print("数据库：127.0.0.1:8123 / market")
    print(f"本机地址：http://{HOST}:{PORT}/mcp")
    print("权限：仅查询")
    try:
        mcp.run(transport="streamable-http")
    finally:
        _release_pid()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
