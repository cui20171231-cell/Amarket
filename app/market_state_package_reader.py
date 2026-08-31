"""Read and validate one persisted market-state package without querying ClickHouse."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

from app.hithink.schedule import (
    MARKET_REVIEW_NODE_SEQUENCES,
    SHANGHAI,
    ScheduleNode,
    build_daily_schedule,
)

PACKAGE_ROOT = Path(r"D:\Amarket\data\market_state_packages")
DATE_DIRECTORY_PATTERN = re.compile(r"^\d{8}$")
PACKAGE_FILE_PATTERN = re.compile(r"^(\d{8})(\d{3})\.json$")
TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2}))?$")


class PackageInvalidError(ValueError):
    pass


def compact_package_for_ai(result: dict[str, Any]) -> dict[str, Any]:
    """Keep review facts while omitting the very large all-concept trajectory matrix."""
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    intraday = data.get("core_sector_intraday")
    if not isinstance(intraday, dict):
        return result

    compact_result = dict(result)
    compact_data = dict(data)
    compact_intraday = dict(intraday)
    trajectories = compact_intraday.pop("concept_trajectories", [])
    compact_intraday.update(
        {
            "concept_trajectory_count": len(trajectories)
            if isinstance(trajectories, list)
            else 0,
            "concept_trajectories_omitted": True,
            "omitted_reason": (
                "AI传输只省略全概念历史轨迹；当前核心板块自身轨迹仍保留在core_sectors"
            ),
        }
    )
    compact_data["core_sector_intraday"] = compact_intraday
    compact_result["data"] = compact_data
    compact_result["transport"] = {
        "mode": "AI_COMPACT",
        "omitted": ["data.core_sector_intraday.concept_trajectories"],
    }
    return compact_result


def _parse_trade_date(value: str) -> date:
    if not isinstance(value, str):
        raise TypeError("trade_date必须是YYYY-MM-DD或YYYYMMDD")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parts = value.split("-")
    elif re.fullmatch(r"\d{8}", value):
        parts = (value[:4], value[4:6], value[6:8])
    else:
        raise ValueError("trade_date必须是YYYY-MM-DD或YYYYMMDD")
    try:
        return date(*(int(part) for part in parts))
    except ValueError as exc:
        raise ValueError("trade_date不是有效日期") from exc


def _parse_target_time(value: str) -> tuple[time, str, bool]:
    if not isinstance(value, str):
        raise TypeError("target_time必须是HH:MM或HH:MM:SS")
    match = TIME_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("target_time必须是HH:MM或HH:MM:SS")
    hour, minute = int(match.group(1)), int(match.group(2))
    second_text = match.group(3)
    second = int(second_text or 0)
    try:
        parsed = time(hour, minute, second)
    except ValueError as exc:
        raise ValueError("target_time不是有效时间") from exc
    has_seconds = second_text is not None
    return parsed, parsed.strftime("%H:%M:%S" if has_seconds else "%H:%M"), has_seconds


def _parse_node_seq(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("node_seq必须是1到254之间的整数")
    if not 1 <= value <= 254:
        raise ValueError("node_seq必须是1到254之间的整数")
    return value


def _review_nodes(trade_date: date) -> list[ScheduleNode]:
    return [
        node
        for node in build_daily_schedule(trade_date)
        if node.sequence_no in MARKET_REVIEW_NODE_SEQUENCES
    ]


def _node_for_time(
    nodes: list[ScheduleNode], requested: time, has_seconds: bool
) -> ScheduleNode | None:
    for node in nodes:
        actual = node.scheduled_time.timetz().replace(tzinfo=None)
        if has_seconds and actual == requested:
            return node
        if not has_seconds and (actual.hour, actual.minute) == (requested.hour, requested.minute):
            return node
    return None


def _safe_package_path(root: Path, trade_date: date, node_seq: int) -> Path:
    directory = root / trade_date.strftime("%Y%m%d")
    path = directory / f"{trade_date:%Y%m%d}{node_seq:03d}.json"
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError("数据包路径超出允许目录")
    return path


def _package_identity(data: dict[str, Any]) -> tuple[str, int, datetime]:
    target = data.get("target")
    if not isinstance(target, dict):
        raise PackageInvalidError("JSON缺少target对象")
    collection_id = target.get("resolved_collection_id") or target.get("collection_id")
    node_seq = target.get("resolved_node_seq")
    if node_seq is None:
        node_seq = target.get("node_seq")
    scheduled_time = target.get("resolved_scheduled_time") or target.get("scheduled_time")
    if not isinstance(collection_id, str) or not re.fullmatch(r"\d{11}", collection_id):
        raise PackageInvalidError("JSON内部collection_id无效")
    if isinstance(node_seq, bool) or not isinstance(node_seq, int):
        raise PackageInvalidError("JSON内部node_seq无效")
    if not isinstance(scheduled_time, str):
        raise PackageInvalidError("JSON内部scheduled_time无效")
    try:
        scheduled = datetime.fromisoformat(scheduled_time)
    except ValueError as exc:
        raise PackageInvalidError("JSON内部scheduled_time无效") from exc
    return collection_id, node_seq, scheduled


def _load_and_validate(path: Path, expected_date: date, expected_node: ScheduleNode) -> dict[str, Any]:
    match = PACKAGE_FILE_PATTERN.fullmatch(path.name)
    if not match:
        raise PackageInvalidError("文件名不符合YYYYMMDDNNN.json")
    directory_date = path.parent.name
    expected_date_text = expected_date.strftime("%Y%m%d")
    if directory_date != expected_date_text or match.group(1) != expected_date_text:
        raise PackageInvalidError("目录日期、文件名日期与请求日期不一致")
    file_node_seq = int(match.group(2))
    if file_node_seq != expected_node.sequence_no:
        raise PackageInvalidError("文件名node_seq与请求节点不一致")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PackageInvalidError("JSON文件不是有效UTF-8文本") from exc
    if not text.strip():
        raise PackageInvalidError("JSON文件为空")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PackageInvalidError("JSON文件损坏，无法解析") from exc
    if not isinstance(data, dict) or not data:
        raise PackageInvalidError("JSON顶层必须是非空对象")

    collection_id, node_seq, scheduled = _package_identity(data)
    expected_collection_id = expected_node.collection_id
    package_id = data.get("package_id")
    if package_id != expected_collection_id:
        raise PackageInvalidError("JSON内部package_id与文件名不一致")
    if collection_id != expected_collection_id:
        raise PackageInvalidError("JSON内部collection_id与文件名不一致")
    if node_seq != expected_node.sequence_no:
        raise PackageInvalidError("JSON内部node_seq与文件名不一致")
    target = data["target"]
    if target.get("trade_date") != expected_date.isoformat():
        raise PackageInvalidError("JSON内部trade_date与目录日期不一致")
    if scheduled.date() != expected_date:
        raise PackageInvalidError("JSON内部scheduled_time日期与目录日期不一致")
    expected_time = expected_node.scheduled_time.timetz().replace(tzinfo=None)
    if scheduled.timetz().replace(tzinfo=None) != expected_time:
        raise PackageInvalidError("JSON内部scheduled_time与节点计划时间不一致")
    for optional_name, expected_value in (
        ("collection_id", expected_collection_id),
        ("node_seq", expected_node.sequence_no),
    ):
        if optional_name in data and data[optional_name] != expected_value:
            raise PackageInvalidError(f"JSON顶层{optional_name}与文件名不一致")
    if "scheduled_time" in data:
        try:
            top_time = datetime.fromisoformat(str(data["scheduled_time"]))
        except ValueError as exc:
            raise PackageInvalidError("JSON顶层scheduled_time无效") from exc
        if top_time != scheduled:
            raise PackageInvalidError("JSON顶层scheduled_time与target不一致")
    return data


def _available_nodes(root: Path, trade_date: date) -> list[dict[str, Any]]:
    available: list[dict[str, Any]] = []
    for node in _review_nodes(trade_date):
        path = _safe_package_path(root, trade_date, node.sequence_no)
        if not path.is_file():
            continue
        try:
            _load_and_validate(path, trade_date, node)
        except (OSError, PackageInvalidError):
            continue
        available.append(
            {
                "node_seq": node.sequence_no,
                "scheduled_time": node.scheduled_time.strftime("%H:%M:%S"),
                "collection_id": node.collection_id,
                "file_name": path.name,
            }
        )
    return available


def _directory_has_valid_package(root: Path, trade_date: date) -> bool:
    for node in _review_nodes(trade_date):
        path = _safe_package_path(root, trade_date, node.sequence_no)
        if not path.is_file():
            continue
        try:
            _load_and_validate(path, trade_date, node)
        except (OSError, PackageInvalidError):
            continue
        return True
    return False


def _default_trade_date(root: Path) -> date | None:
    today = datetime.now(SHANGHAI).date()
    candidates: list[date] = []
    if not root.is_dir():
        return None
    for item in root.iterdir():
        if not item.is_dir() or not DATE_DIRECTORY_PATTERN.fullmatch(item.name):
            continue
        try:
            candidate = date(int(item.name[:4]), int(item.name[4:6]), int(item.name[6:8]))
        except ValueError:
            continue
        if candidate <= today:
            candidates.append(candidate)
    for candidate in sorted(candidates, reverse=True):
        if _directory_has_valid_package(root, candidate):
            return candidate
    return None


def _not_found_details(
    root: Path, trade_date: date, requested_time: time | None
) -> dict[str, Any]:
    available = _available_nodes(root, trade_date)
    result: dict[str, Any] = {"available_nodes": available}
    if requested_time is None:
        return result
    requested_seconds = requested_time.hour * 3600 + requested_time.minute * 60 + requested_time.second
    before = None
    after = None
    for item in available:
        hour, minute, second = (int(part) for part in item["scheduled_time"].split(":"))
        actual_seconds = hour * 3600 + minute * 60 + second
        if actual_seconds <= requested_seconds:
            before = item
        elif after is None:
            after = item
    result["nearest_before"] = before
    result["nearest_after"] = after
    return result


def get_market_state_package(
    target_time: str | None = None,
    node_seq: int | None = None,
    trade_date: str | None = None,
    wait_for_ready: bool = False,
    retry_interval_seconds: int = 10,
    max_wait_seconds: int = 120,
    *,
    package_root: Path = PACKAGE_ROOT,
    _clock: Callable[[], float] = perf_counter,
    _sleep: Callable[[float], None] = sleep,
) -> dict[str, Any]:
    """Locate, validate and return one existing package; never query or modify data."""

    started = _clock()

    def response(
        status: str,
        *,
        wait_elapsed_ms: float = 0,
        retry_count: int = 0,
        **values: Any,
    ) -> dict[str, Any]:
        total_elapsed_ms = max(0.0, (_clock() - started) * 1000)
        read_elapsed_ms = max(0.0, total_elapsed_ms - wait_elapsed_ms)
        return {
            "status": status,
            **values,
            "wait_for_ready": wait_for_ready if isinstance(wait_for_ready, bool) else False,
            "wait_elapsed_ms": round(wait_elapsed_ms, 3),
            "retry_count": retry_count,
            "read_elapsed_ms": round(read_elapsed_ms, 3),
        }

    if target_time is None and node_seq is None:
        return response(
            "INVALID_REQUEST",
            error="target_time和node_seq至少提供一个",
        )
    try:
        if not isinstance(wait_for_ready, bool):
            raise TypeError("wait_for_ready必须是布尔值")
        if isinstance(retry_interval_seconds, bool) or not isinstance(
            retry_interval_seconds, int
        ):
            raise TypeError("retry_interval_seconds必须是5到30之间的整数")
        if not 5 <= retry_interval_seconds <= 30:
            raise ValueError("retry_interval_seconds必须在5到30秒之间")
        if isinstance(max_wait_seconds, bool) or not isinstance(max_wait_seconds, int):
            raise TypeError("max_wait_seconds必须是1到300之间的整数")
        if not 1 <= max_wait_seconds <= 300:
            raise ValueError("max_wait_seconds必须在1到300秒之间")
        parsed_time = None
        normalized_time = None
        has_seconds = False
        if target_time is not None:
            parsed_time, normalized_time, has_seconds = _parse_target_time(target_time)
        parsed_node_seq = _parse_node_seq(node_seq) if node_seq is not None else None
        if trade_date is not None:
            selected_date = _parse_trade_date(trade_date)
        elif wait_for_ready:
            # Waiting is a live-node operation. It must never silently select yesterday's package.
            selected_date = datetime.now(SHANGHAI).date()
        else:
            selected_date = _default_trade_date(package_root)
            if selected_date is None:
                return response(
                    "NOT_FOUND",
                    trade_date=None,
                    requested_time=normalized_time,
                    requested_node_seq=parsed_node_seq,
                    error="没有找到包含有效JSON包的交易日目录",
                    available_nodes=[],
                )
    except (TypeError, ValueError) as exc:
        return response("INVALID_REQUEST", error=str(exc))
    except OSError as exc:
        return response("READ_ERROR", error=str(exc))

    nodes = _review_nodes(selected_date)
    time_node = _node_for_time(nodes, parsed_time, has_seconds) if parsed_time else None
    if parsed_time is not None and time_node is None:
        return response(
            "NOT_FOUND",
            trade_date=selected_date.isoformat(),
            requested_time=normalized_time,
            requested_node_seq=parsed_node_seq,
            error="目标时间没有对应的聚合包节点",
            **_not_found_details(package_root, selected_date, parsed_time),
        )
    if (
        time_node is not None
        and parsed_node_seq is not None
        and time_node.sequence_no != parsed_node_seq
    ):
        return response(
            "INVALID_REQUEST",
            trade_date=selected_date.isoformat(),
            requested_time=normalized_time,
            requested_node_seq=parsed_node_seq,
            error="target_time与node_seq指向不同节点",
        )
    selected_node_seq = parsed_node_seq if parsed_node_seq is not None else time_node.sequence_no
    expected_node = next((node for node in nodes if node.sequence_no == selected_node_seq), None)
    if expected_node is None:
        return response(
            "NOT_FOUND",
            trade_date=selected_date.isoformat(),
            requested_time=normalized_time,
            requested_node_seq=parsed_node_seq,
            error="该节点不是19个聚合节点之一",
            **_not_found_details(package_root, selected_date, parsed_time),
        )

    path = _safe_package_path(package_root, selected_date, expected_node.sequence_no)
    actual_time = expected_node.scheduled_time.strftime("%H:%M:%S")
    time_offset_seconds = None
    if parsed_time is not None:
        requested_seconds = parsed_time.hour * 3600 + parsed_time.minute * 60 + parsed_time.second
        actual = expected_node.scheduled_time.timetz().replace(tzinfo=None)
        actual_seconds = actual.hour * 3600 + actual.minute * 60 + actual.second
        time_offset_seconds = actual_seconds - requested_seconds

    wait_started = _clock()
    retry_count = 0
    last_validation_error: str | None = None
    last_read_error: str | None = None
    while True:
        file_exists = path.is_file()
        if file_exists:
            try:
                data = _load_and_validate(path, selected_date, expected_node)
                stat = path.stat()
            except PackageInvalidError as exc:
                last_validation_error = str(exc)
                if not wait_for_ready:
                    return response(
                        "INVALID_PACKAGE",
                        trade_date=selected_date.isoformat(),
                        requested_time=normalized_time,
                        requested_node_seq=parsed_node_seq,
                        package_path=str(path),
                        error=last_validation_error,
                    )
            except OSError as exc:
                last_read_error = str(exc)
                if not wait_for_ready:
                    return response(
                        "READ_ERROR",
                        trade_date=selected_date.isoformat(),
                        requested_time=normalized_time,
                        requested_node_seq=parsed_node_seq,
                        package_path=str(path),
                        error=last_read_error,
                    )
            else:
                wait_elapsed_ms = (
                    max(0.0, (_clock() - wait_started) * 1000) if retry_count else 0.0
                )
                return response(
                    "OK",
                    wait_elapsed_ms=wait_elapsed_ms,
                    retry_count=retry_count,
                    trade_date=selected_date.isoformat(),
                    requested_time=normalized_time,
                    requested_node_seq=parsed_node_seq,
                    actual_time=actual_time,
                    node_seq=expected_node.sequence_no,
                    collection_id=expected_node.collection_id,
                    package_path=str(path),
                    time_offset_seconds=time_offset_seconds,
                    file_size_bytes=stat.st_size,
                    file_modified_time=datetime.fromtimestamp(
                        stat.st_mtime, SHANGHAI
                    ).isoformat(),
                    data=data,
                )
        elif not wait_for_ready:
            return response(
                "NOT_FOUND",
                trade_date=selected_date.isoformat(),
                requested_time=normalized_time,
                requested_node_seq=parsed_node_seq,
                error="对应聚合包文件不存在",
                **_not_found_details(package_root, selected_date, parsed_time),
            )

        wait_elapsed_seconds = max(0.0, _clock() - wait_started)
        if wait_elapsed_seconds >= max_wait_seconds:
            wait_elapsed_ms = wait_elapsed_seconds * 1000
            if path.is_file() and last_validation_error:
                return response(
                    "INVALID_PACKAGE",
                    wait_elapsed_ms=wait_elapsed_ms,
                    retry_count=retry_count,
                    trade_date=selected_date.isoformat(),
                    requested_time=normalized_time,
                    requested_node_seq=parsed_node_seq,
                    package_path=str(path),
                    error="目标文件在等待期内始终未通过完整性校验",
                    last_validation_error=last_validation_error,
                )
            if path.is_file() and last_read_error:
                return response(
                    "READ_ERROR",
                    wait_elapsed_ms=wait_elapsed_ms,
                    retry_count=retry_count,
                    trade_date=selected_date.isoformat(),
                    requested_time=normalized_time,
                    requested_node_seq=parsed_node_seq,
                    package_path=str(path),
                    error="目标文件在等待期内始终无法完整读取",
                    last_read_error=last_read_error,
                )
            return response(
                "TIMEOUT",
                wait_elapsed_ms=wait_elapsed_ms,
                retry_count=retry_count,
                trade_date=selected_date.isoformat(),
                requested_time=normalized_time,
                requested_node_seq=parsed_node_seq,
                node_seq=expected_node.sequence_no,
                collection_id=expected_node.collection_id,
                package_path=str(path),
                message="目标市场状态聚合包在最大等待时间内未生成",
            )

        remaining_seconds = max_wait_seconds - wait_elapsed_seconds
        _sleep(min(float(retry_interval_seconds), remaining_seconds))
        retry_count += 1
