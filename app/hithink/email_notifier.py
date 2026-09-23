from __future__ import annotations

import argparse
import base64
import csv
import ctypes
import io
import json
import logging
import os
import smtplib
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app.hithink.schedule import SCHEDULE_V3_EFFECTIVE_DATE, SHANGHAI
from app.hithink.status import check_status
from app.logging_utils import configure_bounded_root_logging

ROOT = Path(__file__).resolve().parents[2]
PROGRAM_DATA = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
CONFIG_PATH = Path(
    os.environ.get(
        "AMARKET_EMAIL_CONFIG_PATH",
        str(PROGRAM_DATA / "Amarket" / "secrets" / "email_notification.json"),
    )
)
STATE_PATH = Path(
    os.environ.get(
        "AMARKET_EMAIL_STATE_PATH",
        str(PROGRAM_DATA / "Amarket" / "state" / "email_notification_state.json"),
    )
)
LOG_PATH = ROOT / "data" / "logs" / "email_notifier.log"
COLLECTOR_PID_PATH = ROOT / "data" / "hithink_snapshot_collector.pid"
CLOSE_SUMMARY_TIME = time(16, 20)
CLOSING_NODE_RETRY_CUTOFF_TIME = time(16, 0)
CLOSING_NODE_V3_RETRY_CUTOFF_TIME = time(15, 50, 8)
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailConfig:
    sender: str
    recipients: tuple[str, ...]
    smtp_host: str
    smtp_port: int
    encrypted_app_password: str

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> EmailConfig:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            sender=str(payload["sender"]),
            recipients=tuple(str(item) for item in payload["recipients"]),
            smtp_host=str(payload.get("smtp_host", "smtp.gmail.com")),
            smtp_port=int(payload.get("smtp_port", 465)),
            encrypted_app_password=str(payload["app_password_dpapi"]),
        )


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _decrypt_dpapi_machine(encoded: str) -> str:
    if os.name != "nt":
        raise RuntimeError("DPAPI email credentials can only be read on Windows")
    encrypted = base64.b64decode(encoded)
    source = ctypes.create_string_buffer(encrypted)
    source_blob = _DataBlob(
        len(encrypted), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte))
    )
    target_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(target_blob),
    ):
        raise ctypes.WinError()
    try:
        clear = ctypes.string_at(target_blob.pbData, target_blob.cbData)
        return clear.decode("utf-8")
    finally:
        if target_blob.pbData:
            kernel32.LocalFree(target_blob.pbData)


def send_email(config: EmailConfig, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = f"Amarket 行情系统 <{config.sender}>"
    message["To"] = ", ".join(config.recipients)
    message["Subject"] = subject
    message.set_content(body)
    password = _decrypt_dpapi_machine(config.encrypted_app_password).replace(" ", "")
    try:
        with smtplib.SMTP_SSL(
            config.smtp_host, config.smtp_port, timeout=20
        ) as client:
            client.login(config.sender, password)
            client.send_message(message)
    finally:
        password = ""


def _progress(report: dict[str, Any]) -> dict[str, Any]:
    for step in report.get("steps", []):
        if step.get("name") == "progress" and step.get("status") == "OK":
            return dict(step.get("data") or {})
    return {}


def _daily_statuses(report: dict[str, Any]) -> dict[str, str]:
    return {
        str(row.get("task_name")): str(row.get("status"))
        for row in _progress(report).get("daily", [])
    }


def _node(report: dict[str, Any], sequence_no: int) -> dict[str, Any]:
    return next(
        (
            dict(row)
            for row in _progress(report).get("nodes", [])
            if int(row.get("sequence_no") or 0) == sequence_no
        ),
        {},
    )


def _trading_day(report: dict[str, Any]) -> bool | None:
    progress = _progress(report)
    if not progress:
        return None
    return bool(progress.get("is_trading_day"))


def _schedule_count(report: dict[str, Any]) -> int | None:
    value = _progress(report).get("schedule_count")
    return int(value) if value is not None else None


def _status_text(status: object) -> str:
    return {
        "HEALTHY": "正常",
        "DEGRADED": "存在数据缺口",
        "UNHEALTHY": "异常",
        "IDLE": "非交易日空闲",
        "SUCCESS": "成功",
        "PARTIAL": "部分成功",
        "FAILED": "失败",
        "RUNNING": "运行中",
        "PENDING": "等待执行",
        "SKIPPED": "今日不执行",
        None: "尚未生成",
    }.get(status, str(status))


def _pid_is_running(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        completed = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(
        len(row) >= 2 and row[1] == str(pid)
        for row in csv.reader(io.StringIO(completed.stdout))
    )


def _summary_body(report: dict[str, Any], *, morning: bool) -> str:
    now = datetime.fromisoformat(str(report["as_of"]))
    progress = _progress(report)
    nodes = progress.get("nodes", [])
    daily = _daily_statuses(report)
    trading_day = _trading_day(report)
    intraday = report.get("intraday", {})
    derivation = report.get("derivation", {})
    package_success = sum(
        str(row.get("market_package_status")) == "SUCCESS" for row in nodes
    )
    package_expected = sum(
        str(row.get("market_package_status")) not in {"", "None", "SKIPPED"}
        for row in nodes
    )
    if trading_day is True:
        day_text = "是"
    elif trading_day is False:
        day_text = "否"
    else:
        day_text = "尚未确认"
    lines = [
        f"检查时间：{now:%Y-%m-%d %H:%M:%S}",
        f"是否交易日：{day_text}",
        f"总体状态：{_status_text(report.get('overall_status'))}",
        f"主采集服务：{_status_text(report.get('collector', {}).get('status'))}",
        f"数据库：{_status_text(report.get('services', {}).get('clickhouse'))}",
    ]
    if trading_day is False:
        lines.append("今日不执行盘中采集、板块映射和日K采集。")
        return "\n".join(lines)
    lines.extend(
        [
            f"254节点计划：{_schedule_count(report) or 0}/254",
            (
                "板块映射：目录"
                f"{_status_text(daily.get('sector_catalog_sync'))}，成员关系"
                f"{_status_text(daily.get('sector_membership_sync'))}"
            ),
        ]
    )
    if not morning:
        lines.extend(
            [
                (
                    "盘中采集："
                    f"成功{intraday.get('success', 0)}，部分成功{intraday.get('partial', 0)}，"
                    f"失败{intraday.get('failed', 0)}，超时{intraday.get('timeout', 0)}"
                ),
                (
                    "基础派生："
                    f"成功{derivation.get('success', 0)}/{derivation.get('expected', 0)}"
                ),
                f"市场状态聚合包：成功{package_success}/{package_expected}",
                f"日K：{_status_text(daily.get('hithink_daily_k_raw_sync'))}",
                f"每日数据任务：{_status_text(daily.get('daily_collection'))}",
            ]
        )
    free_bytes = (report.get("n100") or {}).get("disk_d_free_bytes")
    if free_bytes is not None:
        lines.append(f"D盘剩余：{int(free_bytes) / 1024**3:.1f} GB")
    anomalies = report.get("anomaly_nodes", {})
    if int(anomalies.get("total") or 0):
        lines.append(
            "需要关注：存在"
            f"{anomalies.get('total', 0)}个不完整节点，其中完全失败"
            f"{anomalies.get('complete_failure', 0)}个、部分失败"
            f"{anomalies.get('partial_failure', 0)}个。"
        )
    else:
        lines.append("需要关注：无。")
    return "\n".join(lines)


def _issues(report: dict[str, Any], now: datetime) -> dict[str, str]:
    issues: dict[str, str] = {}
    report_date = now.date().isoformat()
    collector = report.get("collector", {})
    services = report.get("services", {})
    if collector.get("status") != "RUNNING" and not _pid_is_running(
        COLLECTOR_PID_PATH
    ):
        issues["COLLECTOR_DOWN"] = "主采集服务没有运行，采集节点可能被漏过。"
    if services.get("clickhouse") != "HEALTHY":
        issues["CLICKHOUSE_DOWN"] = "数据库不可用，采集结果暂时无法入库。"
    free_bytes = (report.get("n100") or {}).get("disk_d_free_bytes")
    if free_bytes is not None and int(free_bytes) < 50 * 1024**3:
        issues["DISK_LOW"] = f"D盘只剩{int(free_bytes) / 1024**3:.1f}GB，需要释放空间。"

    trading_day = _trading_day(report)
    if now.time() >= time(8, 55) and trading_day is None:
        issues[f"CALENDAR_UNKNOWN:{report_date}"] = "交易日尚未确认，今日任务无法可靠编排。"
        return issues
    if trading_day is not True:
        return issues

    schedule_count = _schedule_count(report)
    if now.time() >= time(9, 10) and schedule_count != 254:
        issues[f"PLAN_INCOMPLETE:{report_date}"] = (
            f"今日254节点计划只有{schedule_count or 0}条，计划不完整。"
        )
    first = _node(report, 1)
    if now.time() >= time(9, 20) and first.get("status") not in {
        "SUCCESS",
        "PARTIAL",
        "FAILED",
        "TIMEOUT",
    }:
        issues[f"FIRST_NODE_LATE:{report_date}"] = "第1号采集节点尚未结束。"
    closing = _node(report, 254)
    closing_retry_cutoff = (
        CLOSING_NODE_V3_RETRY_CUTOFF_TIME
        if now.date() >= SCHEDULE_V3_EFFECTIVE_DATE
        else CLOSING_NODE_RETRY_CUTOFF_TIME
    )
    if now.time() >= closing_retry_cutoff and closing.get("status") != "SUCCESS":
        issues[f"CLOSING_NODE_FAILED:{report_date}"] = (
            f"第254号收盘节点状态为{_status_text(closing.get('status'))}。"
        )
    daily = _daily_statuses(report)
    if now.time() >= CLOSE_SUMMARY_TIME and daily.get("daily_collection") != "SUCCESS":
        issues[f"DAILY_K_FAILED:{report_date}"] = (
            "16点日K任务在16:15结束重试后仍未成功。"
        )
    intraday = report.get("intraday", {})
    if now.time() >= time(15, 5) and any(
        int(intraday.get(key) or 0) for key in ("partial", "failed", "timeout")
    ):
        issues[f"DATA_INCOMPLETE:{report_date}"] = (
            "今日盘中数据存在"
            f"{intraday.get('partial', 0)}个部分成功、{intraday.get('failed', 0)}个失败、"
            f"{intraday.get('timeout', 0)}个超时节点。"
        )
    return issues


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "initialized": False, "incidents": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "initialized": False, "incidents": {}}


def _save_state(state: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def monitor_once(
    *,
    now: datetime | None = None,
    report: dict[str, Any] | None = None,
    state_path: Path = STATE_PATH,
    send: Callable[[str, str], None] | None = None,
) -> list[str]:
    now = now or datetime.now(SHANGHAI)
    report = report or check_status()
    config = EmailConfig.load() if send is None else None
    send = send or (lambda subject, body: send_email(config, subject, body))  # type: ignore[arg-type]
    state = _load_state(state_path)
    current_issues = _issues(report, now)
    sent: list[str] = []
    today = now.date().isoformat()

    if not state.get("initialized"):
        state["initialized"] = True
        state["initialized_at"] = now.isoformat()
        state["incidents"] = {
            code: {
                "message": message,
                "first_seen": now.isoformat(),
                "consecutive": 1,
                "baseline": True,
                "alerted": False,
            }
            for code, message in current_issues.items()
        }
        if now.time() >= time(8, 55):
            state.setdefault("morning_dates", []).append(today)
        if now.time() >= CLOSE_SUMMARY_TIME:
            state.setdefault("close_dates", []).append(today)
        _save_state(state, state_path)
        return sent

    incidents = state.setdefault("incidents", {})
    new_alerts: list[str] = []
    recoveries: list[str] = []
    for code, message in current_issues.items():
        incident = incidents.setdefault(
            code,
            {
                "message": message,
                "first_seen": now.isoformat(),
                "consecutive": 0,
                "baseline": False,
                "alerted": False,
            },
        )
        incident["message"] = message
        incident["consecutive"] = int(incident.get("consecutive") or 0) + 1
        if (
            incident["consecutive"] >= 2
            and not incident.get("alerted")
            and not incident.get("baseline")
        ):
            new_alerts.append(message)

    for code in list(incidents):
        if code in current_issues:
            continue
        incident = incidents[code]
        if incident.get("alerted"):
            recoveries.append(str(incident.get("message") or code))
        del incidents[code]

    if new_alerts:
        subject = f"[Amarket][异常] {len(new_alerts)}项问题需要关注"
        body = (
            f"发生时间：{now:%Y-%m-%d %H:%M:%S}\n\n"
            + "\n".join(f"- {item}" for item in new_alerts)
            + "\n\n系统将继续定时检查；如状态恢复，将另发恢复邮件。"
        )
        send(subject, body)
        sent.append(subject)
        for incident in incidents.values():
            if incident.get("message") in new_alerts:
                incident["alerted"] = True

    if recoveries:
        subject = f"[Amarket][已恢复] {len(recoveries)}项问题已经解除"
        body = (
            f"恢复确认时间：{now:%Y-%m-%d %H:%M:%S}\n\n"
            + "\n".join(f"- {item}" for item in recoveries)
            + "\n\n系统当前检查已经不再发现上述问题。"
        )
        send(subject, body)
        sent.append(subject)

    morning_dates = state.setdefault("morning_dates", [])
    if now.time() >= time(8, 55) and today not in morning_dates:
        subject = f"[Amarket][早间确认] {today} 系统状态"
        send(subject, _summary_body(report, morning=True))
        sent.append(subject)
        morning_dates.append(today)

    close_dates = state.setdefault("close_dates", [])
    if (
        now.time() >= CLOSE_SUMMARY_TIME
        and _trading_day(report) is True
        and today not in close_dates
    ):
        subject = f"[Amarket][收盘汇总] {today} 采集结果"
        send(subject, _summary_body(report, morning=False))
        sent.append(subject)
        close_dates.append(today)

    state["last_checked_at"] = now.isoformat()
    state["morning_dates"] = morning_dates[-40:]
    state["close_dates"] = close_dates[-40:]
    _save_state(state, state_path)
    return sent


def send_test() -> None:
    config = EmailConfig.load()
    now = datetime.now(SHANGHAI)
    body = (
        "Amarket邮件通知已经配置成功。\n\n"
        f"测试时间：{now:%Y-%m-%d %H:%M:%S}\n"
        f"运行机器：{socket.gethostname()}\n"
        "这封邮件只验证发信账号、授权码和收件地址，没有修改任何采集数据。"
    )
    send_email(config, "[Amarket] 邮件通知配置成功", body)


def _configure_logging() -> None:
    handlers = configure_bounded_root_logging(LOG_PATH)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="amarket-email-notifier")
    parser.add_argument("command", choices=("monitor", "send-test"))
    args = parser.parse_args()
    _configure_logging()
    try:
        if args.command == "send-test":
            send_test()
            LOG.info("EMAIL_TEST_SUCCESS recipients=%s", EmailConfig.load().recipients)
        else:
            sent = monitor_once()
            LOG.info("EMAIL_MONITOR_SUCCESS sent=%s", len(sent))
    except Exception:
        LOG.exception("EMAIL_NOTIFIER_FAILED command=%s", args.command)
        raise


if __name__ == "__main__":
    main()
