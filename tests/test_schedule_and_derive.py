from datetime import date, datetime, time

import pytest

from app.hithink.api import HithinkClient
from app.hithink.derive import calculate
from app.hithink.models import DerivedSnapshot, RawSnapshot
from app.hithink.schedule import (
    SHANGHAI,
    build_daily_schedule,
)


def raw(**changes):
    values = {
        "trade_date": date(2026, 8, 27),
        "collection_id": "20260827134",
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


def test_schedule_has_fixed_254_nodes_and_sessions():
    nodes = build_daily_schedule(date(2026, 8, 27))
    assert len(nodes) == 254
    assert sum(node.session == "auction_open" for node in nodes) == 11
    assert sum(node.session == "continuous_am" for node in nodes) == 121
    assert sum(node.session == "continuous_pm" for node in nodes) == 117
    assert sum(node.session == "auction_close" for node in nodes) == 5
    assert nodes[-3].scheduled_time.timetz().replace(tzinfo=None) == time(14, 58)
    assert nodes[0].collection_id == "20260827001"
    assert nodes[-1].collection_id == "20260827254"


def test_collection_id_has_a_permanent_time_mapping():
    nodes = build_daily_schedule(date(2026, 8, 27))
    expected = {
        1: time(9, 15, 15),
        11: time(9, 25, 15),
        12: time(9, 30, 15),
        132: time(11, 30, 15),
        133: time(13, 0, 15),
        249: time(14, 56, 15),
        250: time(14, 56, 55),
        254: time(15, 0),
    }
    for sequence_no, planned_time in expected.items():
        node = nodes[sequence_no - 1]
        assert node.collection_id == f"20260827{sequence_no:03d}"
        assert node.scheduled_time.timetz().replace(tzinfo=None) == planned_time


def test_limit_pools_run_only_at_meaningful_nodes():
    nodes = build_daily_schedule(date(2026, 8, 27))
    applicable = [node.sequence_no for node in nodes if node.limit_pools_applicable]
    assert len(applicable) == 241
    assert applicable[0] == 11
    assert 250 in applicable
    assert 251 not in applicable
    assert 252 not in applicable
    assert 253 not in applicable
    assert applicable[-1] == 254
    assert all(not nodes[index - 1].limit_pools_applicable for index in range(1, 11))
    assert all(not nodes[index - 1].limit_pools_applicable for index in range(251, 254))
    assert nodes[10].scheduled_time.timetz().replace(tzinfo=None) == time(9, 25, 15)
    assert nodes[10].limit_pools_applicable
    assert nodes[253].scheduled_time.timetz().replace(tzinfo=None) == time(15, 0)
    assert nodes[253].limit_pools_applicable


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


def test_previous_trade_day_turnover_uses_the_last_available_stock_record():
    derived = calculate(
        raw(turnover=1300),
        previous=None,
        allowed=False,
        previous_trade_day_turnover=1000,
    )
    assert derived.prev_trade_day_same_time_turnover == 1000
    assert derived.turnover_prev_trade_day_delta == 300
    assert derived.turnover_prev_trade_day_pct == pytest.approx(0.3)


def test_derivation_uses_the_last_successful_snapshot_after_a_failed_node():
    last_success = DerivedSnapshot(
        raw(last_price=10, high_price=10, low_price=9, volume=100, turnover=1000),
        50,
        10,
        None,
        None,
        0,
        0,
        0.2,
        2,
    )
    derived = calculate(
        raw(last_price=11, high_price=11, low_price=9, volume=160, turnover=1300),
        last_success,
        allowed=True,
    )
    assert derived.turnover_delta_1m == 300
    assert derived.volume_delta_1m == 60
    assert derived.price_delta_1m == 1


def test_auction_nodes_are_facts_only_and_have_no_minute_derivation():
    auction = calculate(
        raw(session="auction_open", turnover=1300),
        previous=None,
        allowed=False,
    )
    assert auction.turnover_delta_1m is None
    assert auction.volume_delta_1m is None


def test_empty_limit_pool_is_a_valid_same_node_fact():
    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "code": 0,
                "data": {
                    "item": [],
                    "pagination": {"total": 0, "pages": 0},
                    "timestamp": 1787814000000,
                },
            }

    class Client:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    api = object.__new__(HithinkClient)
    api.client = Client()
    result = api.fetch_limit_pool("limit-down-pool", date(2026, 8, 27))
    assert result.total == 0
    assert result.items == []
    assert result.source_timestamp == 1787814000000
