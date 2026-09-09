from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.hithink.schedule import SHANGHAI
from app.hithink.tencent_stock_basic import (
    TencentStockBasic,
    TencentStockBasicCollector,
    parse_tencent_stock_basic,
    run_tencent_stock_basic_daily,
    tencent_stock_candidates,
)
from app.hithink.writer import ClickHouseWriter

DDL = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")
STANDALONE_DDL = Path("sql/tencent_stock_basic_info.sql").read_text(encoding="utf-8")
INSTALLER = Path("scripts/install_tencent_stock_basic_task.ps1").read_text(
    encoding="utf-8"
)


def _quote(symbol: str, name: str, float_shares: str, total_shares: str) -> bytes:
    fields = [""] * 74
    fields[1] = name
    fields[2] = symbol[-6:]
    fields[30] = "20260903150000"
    fields[72] = float_shares
    fields[73] = total_shares
    return f'v_{symbol}="{"~".join(fields)}";'.encode("gbk")


def test_parser_keeps_only_direct_share_counts_and_chinese_name() -> None:
    scheduled = datetime(2026, 9, 4, 8, 50, tzinfo=SHANGHAI)
    rows = parse_tencent_stock_basic(
        _quote("sh688981", "中芯国际", "1999562549", "8561151853"),
        snapshot_date=date(2026, 9, 4),
        scheduled_time=scheduled,
        batch_id="tencent-stock-basic-20260904",
    )
    assert len(rows) == 1
    assert rows[0].thscode == "688981.SH"
    assert rows[0].stock_name == "中芯国际"
    assert rows[0].float_shares == 1_999_562_549
    assert rows[0].total_shares == 8_561_151_853
    assert not hasattr(rows[0], "total_market_cap")
    assert not hasattr(rows[0], "float_market_cap")


def test_candidate_space_covers_shenzhen_shanghai_and_beijing() -> None:
    candidates = set(tencent_stock_candidates())
    assert {"sh600000", "sh688981", "sz000001", "sz300750", "bj920000"} <= candidates


def test_table_has_only_two_share_business_fields() -> None:
    section = DDL.split("CREATE TABLE IF NOT EXISTS market.tencent_stock_basic_info", 1)[1]
    assert "`total_shares` Nullable(UInt64)" in section
    assert "`float_shares` Nullable(UInt64)" in section
    assert "total_market_cap" not in section
    assert "float_market_cap" not in section
    assert "ReplacingMergeTree(version_time)" in section
    assert "ORDER BY thscode" in section
    assert "PARTITION BY toYYYYMM(snapshot_date)" not in section
    assert "COMMENT '个股基础信息表'" in section
    assert STANDALONE_DDL.strip() in DDL


def test_installer_creates_an_independent_daily_0850_task_without_starting_it() -> None:
    assert "$taskName = 'TencentStockBasicCollector'" in INSTALLER
    assert "-m app.hithink.cli tencent-stock-basic" in INSTALLER
    assert "New-ScheduledTaskTrigger -Daily -At '08:50'" in INSTALLER
    assert "-ExecutionTimeLimit (New-TimeSpan -Minutes 10)" in INSTALLER
    assert "-RestartCount" not in INSTALLER
    assert "Start-ScheduledTask" not in INSTALLER


class _DailyWriter:
    def __init__(self, confirmations):
        self.confirmations = iter(confirmations)
        self.statuses = []
        self.plan_dates = []

    def initialize_daily_task_plan(self, trade_date):
        self.plan_dates.append(trade_date)

    def cached_trading_day_confirmation(self, _trade_date):
        return next(self.confirmations)

    def set_daily_status(self, trade_date, task_name, status, **values):
        self.statuses.append((trade_date, task_name, status, values))


def test_daily_task_waits_for_current_day_confirmation_then_runs() -> None:
    target = datetime(2026, 9, 4, 8, 50, tzinfo=SHANGHAI)
    writer = _DailyWriter(
        [None, (True, datetime(2026, 9, 4, 8, 50, tzinfo=SHANGHAI))]
    )
    waits = []

    class Collector:
        def __init__(self, _writer):
            pass

        def run(self, _now, *, deadline):
            assert deadline == datetime(2026, 9, 4, 9, 0, tzinfo=SHANGHAI)
            return 5566, 7

    result = run_tencent_stock_basic_daily(
        writer,
        target,
        poll_seconds=300,
        sleep_fn=waits.append,
        collector_factory=Collector,
        clock=lambda: target,
    )

    assert result == (5566, 7)
    assert waits == [300]
    assert [status[2] for status in writer.statuses] == ["RUNNING", "SUCCESS"]


def test_daily_task_skips_after_non_trading_day_confirmation() -> None:
    target = datetime(2026, 9, 5, 8, 50, tzinfo=SHANGHAI)
    writer = _DailyWriter(
        [(False, datetime(2026, 9, 5, 8, 50, tzinfo=SHANGHAI))]
    )

    class Collector:
        def __init__(self, _writer):
            raise AssertionError("collector must not start on a non-trading day")

    result = run_tencent_stock_basic_daily(
        writer, target, collector_factory=Collector, clock=lambda: target
    )

    assert result is None
    assert [status[2] for status in writer.statuses] == ["SKIPPED"]


def test_tencent_429_honors_retry_after_before_succeeding(monkeypatch) -> None:
    calls = 0
    waits = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"})
        return httpx.Response(200, content=b'ok')

    collector = TencentStockBasicCollector(
        _Writer(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr("app.hithink.tencent_stock_basic.sleep", waits.append)

    assert collector._fetch_batch(["sh600000"]) == b"ok"
    assert waits == [0.25]


def test_daily_task_stops_at_0900_when_calendar_is_still_unknown() -> None:
    target = datetime(2026, 9, 4, 8, 50, tzinfo=SHANGHAI)
    deadline = datetime(2026, 9, 4, 9, 0, tzinfo=SHANGHAI)
    writer = _DailyWriter([None])

    result = run_tencent_stock_basic_daily(
        writer,
        target,
        sleep_fn=lambda _seconds: None,
        clock=lambda: deadline,
    )

    assert result is None
    assert writer.statuses[-1][2] == "FAILED"
    assert writer.statuses[-1][3]["error_code"] == "CALENDAR_CONFIRMATION_DEADLINE"


def test_daily_collection_failure_keeps_previous_snapshot_and_returns_normally() -> None:
    target = datetime(2026, 9, 4, 8, 50, tzinfo=SHANGHAI)
    writer = _DailyWriter(
        [(True, datetime(2026, 9, 4, 8, 50, tzinfo=SHANGHAI))]
    )

    class Collector:
        def __init__(self, _writer):
            pass

        def run(self, _now, *, deadline):
            raise RuntimeError("upstream unavailable")

    result = run_tencent_stock_basic_daily(
        writer, target, collector_factory=Collector, clock=lambda: target
    )

    assert result is None
    assert [status[2] for status in writer.statuses] == ["RUNNING", "FAILED"]


class _Writer:
    def __init__(self) -> None:
        self.rows = []

    def insert_tencent_stock_basic_once(self, rows, *, batch_id):
        self.rows = rows
        return len(rows), 1


def test_collector_does_not_insert_if_any_request_batch_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.hithink.tencent_stock_basic.tencent_stock_candidates",
        lambda: ["sh600000", "sz000001"],
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, content=_quote("sh600000", "浦发银行", "1", "2"))
        return httpx.Response(500)

    writer = _Writer()
    collector = TencentStockBasicCollector(
        writer,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        batch_size=1,
        pause_seconds=0,
    )
    monkeypatch.setattr("app.hithink.tencent_stock_basic.sleep", lambda _seconds: None)

    try:
        collector.run(datetime(2026, 9, 4, 8, 50, tzinfo=SHANGHAI))
    except RuntimeError:
        pass
    else:
        raise AssertionError("collector should fail the complete run")
    assert writer.rows == []


class _BasicInfoClient:
    def __init__(self, existing):
        self.existing = existing
        self.inserted = []

    def query(self, _query):
        return SimpleNamespace(result_rows=self.existing)

    def insert(self, _table, values, *, column_names):
        self.inserted.append((values, column_names))


def _basic_row(code: str, total: int, floating: int) -> TencentStockBasic:
    ticker = code[:6]
    return TencentStockBasic(
        snapshot_date=date(2026, 9, 5),
        scheduled_time=datetime(2026, 9, 5, 8, 50, tzinfo=SHANGHAI),
        source_time=datetime(2026, 9, 4, 15, 0, tzinfo=SHANGHAI),
        batch_id="tencent-stock-basic-20260905",
        thscode=code,
        ticker=ticker,
        stock_name=ticker,
        total_shares=total,
        float_shares=floating,
    )


def test_daily_refresh_inserts_only_new_or_changed_stocks() -> None:
    client = _BasicInfoClient(
        [
            ("000001.SZ", b"000001", "000001", 100, 80),
            ("600000.SH", b"600000", "600000", 200, 180),
        ]
    )
    writer = object.__new__(ClickHouseWriter)
    writer.client = client
    rows = [
        _basic_row("000001.SZ", 100, 80),
        _basic_row("600000.SH", 201, 180),
        _basic_row("688999.SH", 300, 100),
    ]

    inserted, _ = writer.insert_tencent_stock_basic_once(
        rows, batch_id="tencent-stock-basic-20260905"
    )

    assert inserted == 2
    inserted_values = client.inserted[0][0]
    code_at = client.inserted[0][1].index("thscode")
    assert {row[code_at] for row in inserted_values} == {"600000.SH", "688999.SH"}
