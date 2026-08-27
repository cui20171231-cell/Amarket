from datetime import date, datetime, time

import pytest

from app.hithink.derive import calculate
from app.hithink.models import DerivedSnapshot, RawSnapshot
from app.hithink.schedule import (
    SHANGHAI,
    allows_one_minute_derivation,
    build_daily_schedule,
)


def raw(**changes):
    values = {
        "trade_date": date(2026, 8, 27),
        "scheduled_time": datetime(2026, 8, 27, 13, 1, 15, tzinfo=SHANGHAI),
        "source_timestamp": 1,
        "source_time": datetime(2026, 8, 27, 13, 1, 15, tzinfo=SHANGHAI),
        "session": "continuous_pm",
        "batch_id": "batch",
        "thscode": "600519.SH",
        "ticker": "600519",
        "last_price": 15.5,
        "price_change": 0.5,
        "price_change_ratio_pct": 3.33,
        "open_price": 15,
        "high_price": 15.6,
        "low_price": 14.8,
        "prev_price": 15,
        "volume": 150,
        "turnover": 1500,
    }
    values.update(changes)
    return RawSnapshot(**values)


def test_schedule_has_fixed_314_nodes_and_sessions():
    nodes = build_daily_schedule(date(2026, 8, 27))
    assert len(nodes) == 314
    assert sum(node.session == "auction_open" for node in nodes) == 11
    assert sum(node.session == "continuous_am" for node in nodes) == 121
    assert sum(node.session == "continuous_pm" for node in nodes) == 177
    assert sum(node.session == "auction_close" for node in nodes) == 5
    assert nodes[-3].scheduled_time.timetz().replace(tzinfo=None) == time(15, 58)


def test_only_exact_session_continuity_allows_derivation():
    nodes = build_daily_schedule(date(2026, 8, 27))
    by_time = {node.scheduled_time.timetz().replace(tzinfo=None): node for node in nodes}
    assert not allows_one_minute_derivation(by_time[time(9, 30, 15)], by_time[time(9, 25)])
    assert not allows_one_minute_derivation(by_time[time(15, 56, 55)], by_time[time(15, 56, 15)])
    assert not allows_one_minute_derivation(by_time[time(15, 57)], by_time[time(15, 56, 55)])
    assert allows_one_minute_derivation(by_time[time(15, 58)], by_time[time(15, 57)])


def test_derivation_obeys_zero_and_counter_rollback_rules():
    previous = DerivedSnapshot(
        raw(last_price=10, high_price=10, low_price=9, volume=100, turnover=1000),
        100,
        20,
        None,
        None,
        0,
        0,
        0.5,
        1,
    )
    derived = calculate(
        raw(last_price=11, high_price=11, low_price=8, volume=150, turnover=1300), previous, True
    )
    assert (derived.turnover_delta_1m, derived.volume_delta_1m) == (300, 50)
    assert derived.turnover_growth_1m == 2
    assert derived.volume_ratio_1m == 2.5
    assert derived.new_high_flag == derived.new_low_flag == 1
    assert derived.price_change_1m_pct == pytest.approx(10)
    rollback = calculate(raw(volume=99, turnover=999), previous, True)
    assert rollback.turnover_delta_1m is None and rollback.turnover_growth_1m is None
    assert rollback.volume_delta_1m is None and rollback.volume_ratio_1m is None
