from __future__ import annotations

import atexit
import json
import os
import time
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from app.clickhouse_readonly import execute_clickhouse_readonly_sql
from app.hithink.status import collection_status_response

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
    from mcp.types import ToolAnnotations
except ImportError as exc:
    raise RuntimeError(
        "AI 网关依赖尚未安装。请运行 D:\\Amarket\\INSTALL_AI_GATEWAY.cmd。"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = int(os.environ.get("AMARKET_AI_GATEWAY_PORT", "2091"))
LOG_DIR = ROOT / "logs"
AUDIT_PATH = LOG_DIR / "ai_gateway_audit.jsonl"
PID_PATH = LOG_DIR / "ai_gateway.pid"

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
        "仅提供采集状态和只读查询，不修改数据、不启动或重启任务。"
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
        with AUDIT_PATH.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
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
    LOG_DIR.mkdir(parents=True, exist_ok=True)
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
    try:
        if PID_PATH.exists() and PID_PATH.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_PATH.unlink()
    except OSError:
        pass


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
