from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.hithink import email_notifier
from app.hithink.email_notifier import _issues, _summary_body, monitor_once
from app.hithink.schedule import SHANGHAI


def _report(*, collector: str = "RUNNING", clickhouse: str = "HEALTHY") -> dict:
    nodes = [
        {
            "sequence_no": number,
            "status": "SUCCESS",
            "market_package_status": "SUCCESS" if number <= 19 else "SKIPPED",
        }
        for number in range(1, 255)
    ]
    return {
        "as_of": "2026-09-01T16:35:00+08:00",
        "overall_status": "HEALTHY",
        "collector": {"status": collector},
        "services": {"clickhouse": clickhouse, "n100": "ONLINE"},
        "n100": {"disk_d_free_bytes": 700 * 1024**3},
        "intraday": {
            "success": 254,
            "partial": 0,
            "failed": 0,
            "timeout": 0,
        },
        "derivation": {"success": 254, "expected": 254},
        "anomaly_nodes": {
            "total": 0,
            "complete_failure": 0,
            "partial_failure": 0,
        },
        "steps": [
            {
                "name": "progress",
                "status": "OK",
                "data": {
                    "is_trading_day": True,
                    "nodes": nodes,
                    "daily": [
                        {"task_name": "sector_catalog_sync", "status": "SUCCESS"},
                        {"task_name": "sector_membership_sync", "status": "SUCCESS"},
                        {"task_name": "hithink_daily_k_raw_sync", "status": "SUCCESS"},
                        {"task_name": "daily_collection", "status": "SUCCESS"},
                    ],
                },
            }
        ],
    }


def test_close_summary_contains_main_pipeline_results() -> None:
    body = _summary_body(_report(), morning=False)
    assert "254节点计划：254/254" in body
    assert "盘中采集：成功254" in body
    assert "基础派生：成功254/254" in body
    assert "日K：成功" in body


def test_infrastructure_issues_are_plain_and_bounded(monkeypatch) -> None:
    monkeypatch.setattr(email_notifier, "_pid_is_running", lambda _path: False)
    now = datetime(2026, 9, 1, 10, 0, tzinfo=SHANGHAI)
    issues = _issues(_report(collector="STOPPED", clickhouse="UNHEALTHY"), now)
    assert "COLLECTOR_DOWN" in issues
    assert "CLICKHOUSE_DOWN" in issues


def test_first_monitor_run_seeds_state_without_historical_email(tmp_path: Path) -> None:
    sent: list[tuple[str, str]] = []
    state_path = tmp_path / "state.json"
    now = datetime(2026, 9, 1, 20, 0, tzinfo=SHANGHAI)
    result = monitor_once(
        now=now,
        report=_report(collector="STOPPED"),
        state_path=state_path,
        send=lambda subject, body: sent.append((subject, body)),
    )
    assert result == []
    assert sent == []
    assert state_path.exists()


def test_new_issue_alerts_after_two_checks_and_then_recovers(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(email_notifier, "_pid_is_running", lambda _path: False)
    sent: list[tuple[str, str]] = []
    state_path = tmp_path / "state.json"
    send = lambda subject, body: sent.append((subject, body))
    first = datetime(2026, 9, 1, 7, 0, tzinfo=SHANGHAI)
    monitor_once(now=first, report=_report(), state_path=state_path, send=send)
    monitor_once(
        now=first.replace(minute=5),
        report=_report(collector="STOPPED"),
        state_path=state_path,
        send=send,
    )
    monitor_once(
        now=first.replace(minute=10),
        report=_report(collector="STOPPED"),
        state_path=state_path,
        send=send,
    )
    assert any("[异常]" in subject for subject, _ in sent)
    monitor_once(
        now=first.replace(minute=15), report=_report(), state_path=state_path, send=send
    )
    assert any("[已恢复]" in subject for subject, _ in sent)
