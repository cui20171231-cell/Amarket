from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.hithink.api import AuctionApiSnapshot
from app.hithink.derive import calculate
from app.hithink.models import RawSnapshot
from app.hithink.schedule import SHANGHAI, build_daily_schedule
from app.hithink.writer import ClickHouseWriter

DDL = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")
WRITER = Path("app/hithink/writer.py").read_text(encoding="utf-8")


def _raw(price: float | None) -> RawSnapshot:
    return RawSnapshot(
        trade_date=date(2026, 9, 4),
        collection_id="20260904072",
        scheduled_time=datetime(2026, 9, 4, 10, 15, tzinfo=SHANGHAI),
        source_timestamp=1,
        source_time=datetime(2026, 9, 4, 10, 15, tzinfo=SHANGHAI),
        session="continuous_am",
        batch_id="batch",
        thscode="000001.SZ",
        ticker="000001",
        last_price=price,
        price_change=None,
        price_change_ratio_pct=None,
        open_price=None,
        high_price=None,
        low_price=None,
        prev_price=None,
        volume=None,
        turnover=None,
    )


def test_stock_market_caps_use_current_node_price_and_share_counts() -> None:
    derived = calculate(
        _raw(10.25),
        previous=None,
        allowed=False,
        total_shares=1_000,
        float_shares=800,
    )
    assert derived.total_market_cap == 10_250
    assert derived.float_market_cap == 8_200


def test_missing_price_or_share_count_stays_null() -> None:
    without_price = calculate(
        _raw(None), previous=None, allowed=False, total_shares=1_000, float_shares=800
    )
    without_float_shares = calculate(
        _raw(10.25), previous=None, allowed=False, total_shares=1_000, float_shares=None
    )
    assert without_price.total_market_cap is None
    assert without_price.float_market_cap is None
    assert without_float_shares.total_market_cap == 10_250
    assert without_float_shares.float_market_cap is None


def test_schema_adds_market_caps_only_to_derived_and_sector_state_tables() -> None:
    assert DDL.count("total_market_cap Nullable(Float64)") >= 6
    assert DDL.count("float_market_cap Nullable(Float64)") >= 6
    assert "ALTER TABLE market.sector_membership_history ADD COLUMN" not in DDL


def test_sector_states_sum_known_member_market_caps() -> None:
    assert "derived.total_market_cap AS total_market_cap" in WRITER
    assert "derived.float_market_cap AS float_market_cap" in WRITER
    assert "toFloat64(sum(total_market_cap))) AS total_market_cap" in WRITER
    assert "toFloat64(sum(float_market_cap))) AS float_market_cap" in WRITER
    assert "WHERE snapshot_date <= {{trade_date:Date}}" in WRITER
    assert WRITER.count("argMaxIf(") >= 2


def test_auction_row_uses_auction_price_times_current_share_counts() -> None:
    node = build_daily_schedule(date(2026, 9, 4))[0]
    now = datetime(2026, 9, 4, 9, 15, tzinfo=SHANGHAI)
    snapshot = AuctionApiSnapshot(
        code=0,
        message="success",
        request_id="request",
        source_timestamp=1,
        source_time=now,
        auction_phase="order_entry",
        data_status="live",
        total=1,
        items=[
            {
                "thscode": "000001.SZ",
                "ticker": "000001",
                "name": "平安银行",
                "auction_price": 10.25,
                "float_market_cap": 999,
            }
        ],
        request_started_at=now,
        request_ended_at=now,
        duration_ms=1,
        retry_count=0,
    )
    captured = {}
    writer = object.__new__(ClickHouseWriter)
    writer.latest_stock_share_counts = lambda _date: {"000001.SZ": (1_000, 800)}

    def insert_once(table, rows, columns, batch_id=None):
        captured.update(table=table, rows=rows, columns=list(columns), batch_id=batch_id)
        return len(rows), 1

    writer._insert_once = insert_once
    writer.insert_auction_snapshot_once(
        node,
        [snapshot],
        batch_id="auction",
        status="SUCCESS",
        raw_completed_at=now,
        total_duration_ms=1,
    )
    total_at = captured["columns"].index("total_market_cap")
    float_at = captured["columns"].index("float_market_cap")
    assert captured["rows"][0][total_at] == 10_250
    assert captured["rows"][0][float_at] == 8_200
