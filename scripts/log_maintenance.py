from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
GIB = 1024 * MIB
NORMAL_TARGET_BYTES = 20 * GIB
EARLY_CLEANUP_BYTES = 25 * GIB
HARD_LIMIT_BYTES = 30 * GIB
REPORT_PATH = ROOT / "logs" / "log_maintenance.jsonl"
STATE_PATH = ROOT / ".runtime" / "log_maintenance_state.json"


@dataclass(frozen=True)
class LogFamily:
    active_path: Path
    retention_days: int
    max_bytes: int
    backup_count: int
    externally_rotatable: bool = False


LOG_FAMILIES = (
    LogFamily(ROOT / "data/logs/collector.log", 30, 20 * MIB, 50),
    LogFamily(ROOT / "data/logs/daily_pipeline.log", 30, 20 * MIB, 50),
    LogFamily(ROOT / "data/logs/sector_mapping.log", 30, 20 * MIB, 50),
    LogFamily(
        ROOT / "logs/ai_gateway_audit.jsonl",
        30,
        20 * MIB,
        25,
        externally_rotatable=True,
    ),
    LogFamily(ROOT / "feishu_amarket_bot/logs/listener.log", 30, 5 * MIB, 5),
)

BOUNDED_RUNTIME_LOGS = tuple((ROOT / "logs").glob("*.stdout.log")) + tuple(
    (ROOT / "logs").glob("*.stderr.log")
)


def _run(command: list[str], *, timeout: int = 120) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip()[-2000:],
        "stderr": completed.stderr.strip()[-2000:],
    }


def _rotate_append_file(family: LogFamily) -> bool:
    path = family.active_path
    if not family.externally_rotatable or not path.exists():
        return False
    if path.stat().st_size <= family.max_bytes:
        return False
    oldest = path.with_name(f"{path.name}.{family.backup_count}")
    oldest.unlink(missing_ok=True)
    for index in range(family.backup_count - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))
    return True


def _family_files(family: LogFamily) -> list[Path]:
    parent = family.active_path.parent
    if not parent.exists():
        return []
    return [
        path
        for path in parent.glob(f"{family.active_path.name}.*")
        if path.is_file()
    ]


def _prune_expired(now: datetime) -> list[str]:
    deleted: list[str] = []
    for family in LOG_FAMILIES:
        cutoff = now - timedelta(days=family.retention_days)
        for path in _family_files(family):
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified < cutoff:
                path.unlink(missing_ok=True)
                deleted.append(str(path.relative_to(ROOT)))

    runtime_cutoff = now - timedelta(days=30)
    for active in BOUNDED_RUNTIME_LOGS:
        for path in active.parent.glob(f"{active.name}.*"):
            if not path.is_file():
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified < runtime_cutoff:
                path.unlink(missing_ok=True)
                deleted.append(str(path.relative_to(ROOT)))
    return deleted


def _project_log_bytes() -> int:
    paths: set[Path] = set()
    for family in LOG_FAMILIES:
        if family.active_path.exists():
            paths.add(family.active_path)
        paths.update(_family_files(family))
    for path in BOUNDED_RUNTIME_LOGS:
        if path.exists():
            paths.add(path)
        paths.update(item for item in path.parent.glob(f"{path.name}.*") if item.is_file())
    return sum(path.stat().st_size for path in paths if path.exists())


def _parse_bytes(value: str) -> int:
    match = re.search(r"(\d+)", value)
    return int(match.group(1)) if match else 0


def _linux_metrics() -> dict[str, Any]:
    if sys.platform != "linux":
        return {"available": False, "reason": "not running inside Linux"}

    query = (
        "SELECT coalesce(sum(total_bytes), 0) FROM system.tables "
        "WHERE database='system' AND name LIKE '%log%' FORMAT TSVRaw"
    )
    clickhouse = _run(
        [
            "docker",
            "exec",
            "amarket-clickhouse",
            "clickhouse-client",
            "--query",
            query,
        ]
    )
    clickhouse_bytes = _parse_bytes(clickhouse.get("stdout", "")) if clickhouse["ok"] else 0

    server_logs = _run(
        [
            "docker",
            "exec",
            "amarket-clickhouse",
            "du",
            "-sb",
            "/var/log/clickhouse-server",
        ]
    )
    server_log_bytes = _parse_bytes(server_logs.get("stdout", "")) if server_logs["ok"] else 0

    journal = _run(["journalctl", "--disk-usage"])
    journal_match = re.search(
        r"([0-9.]+)([KMG])", journal.get("stdout", ""), flags=re.IGNORECASE
    )
    journal_bytes = 0
    if journal_match:
        multiplier = {"K": 1024, "M": MIB, "G": GIB}[journal_match.group(2).upper()]
        journal_bytes = int(float(journal_match.group(1)) * multiplier)

    return {
        "available": True,
        "clickhouse_system_log_bytes": clickhouse_bytes,
        "clickhouse_server_log_bytes": server_log_bytes,
        "journal_bytes": journal_bytes,
        "query_ok": clickhouse["ok"],
    }


def _vacuum_journal() -> dict[str, Any]:
    if sys.platform != "linux":
        return {"ok": False, "skipped": "not running inside Linux"}
    time_result = _run(["journalctl", "--vacuum-time=14d"])
    size_result = _run(["journalctl", "--vacuum-size=300M"])
    return {"ok": time_result["ok"] and size_result["ok"]}


def _clickhouse_emergency_cleanup(total_bytes: int) -> dict[str, Any]:
    if sys.platform != "linux" or total_bytes < EARLY_CLEANUP_BYTES:
        return {"activated": False}

    statements: list[str] = []
    if total_bytes >= HARD_LIMIT_BYTES:
        for table in (
            "trace_log",
            "trace_log_0",
            "processors_profile_log",
            "metric_log",
            "asynchronous_metric_log",
            "query_metric_log",
        ):
            statements.append(f"TRUNCATE TABLE IF EXISTS system.{table}")
    else:
        for table in (
            "trace_log",
            "trace_log_0",
            "processors_profile_log",
            "metric_log",
            "asynchronous_metric_log",
            "query_metric_log",
        ):
            statements.append(
                f"ALTER TABLE IF EXISTS system.{table} DELETE "
                "WHERE event_date < today() - INTERVAL 3 DAY"
            )

    outcomes = []
    for statement in statements:
        outcome = _run(
            [
                "docker",
                "exec",
                "amarket-clickhouse",
                "clickhouse-client",
                "--query",
                statement,
            ],
            timeout=300,
        )
        outcomes.append({"statement": statement, "ok": outcome["ok"]})
    return {
        "activated": True,
        "level": "hard" if total_bytes >= HARD_LIMIT_BYTES else "early",
        "outcomes": outcomes,
    }


def _append_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if REPORT_PATH.exists() and REPORT_PATH.stat().st_size + len(encoded) > 20 * MIB:
        backup = REPORT_PATH.with_name(f"{REPORT_PATH.name}.1")
        backup.unlink(missing_ok=True)
        os.replace(REPORT_PATH, backup)
    with REPORT_PATH.open("ab") as file:
        file.write(encoded)


def _write_state(report: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_PATH)


def run(mode: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    rotated: list[str] = []
    errors: list[str] = []
    for family in LOG_FAMILIES:
        try:
            if _rotate_append_file(family):
                rotated.append(str(family.active_path.relative_to(ROOT)))
        except OSError as exc:
            errors.append(f"rotate {family.active_path.name}: {exc}")

    try:
        deleted = _prune_expired(now)
    except OSError as exc:
        deleted = []
        errors.append(f"prune: {exc}")

    journal_result = _vacuum_journal() if mode == "daily" else {"skipped": True}
    linux = _linux_metrics()
    project_bytes = _project_log_bytes()
    total_bytes = project_bytes
    if linux.get("available"):
        total_bytes += int(linux.get("clickhouse_system_log_bytes", 0))
        total_bytes += int(linux.get("clickhouse_server_log_bytes", 0))
        total_bytes += int(linux.get("journal_bytes", 0))

    emergency = _clickhouse_emergency_cleanup(total_bytes)
    report = {
        "checked_at_utc": now.isoformat(),
        "mode": mode,
        "status": "OK" if not errors else "WARNING",
        "project_log_bytes": project_bytes,
        "estimated_total_log_bytes": total_bytes,
        "normal_target_bytes": NORMAL_TARGET_BYTES,
        "hard_limit_bytes": HARD_LIMIT_BYTES,
        "rotated": rotated,
        "deleted": deleted,
        "journal_maintenance": journal_result,
        "linux": linux,
        "emergency_cleanup": emergency,
        "errors": errors,
    }
    _append_report(report)
    _write_state(report)
    return report


def main() -> None:
    command = argparse.ArgumentParser()
    command.add_argument("--mode", choices=("daily", "guard"), default="daily")
    args = command.parse_args()
    print(json.dumps(run(args.mode), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
