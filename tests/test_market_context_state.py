from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path

from app.market_context_compact import compact_market_context_response
from app.market_context_state import (
    AUCTION_TRAJECTORY_SCHEMA,
    MAX_STOCKS,
    _compress_sector_trajectory,
    _compress_stock_trajectory,
    _normalize_sector_name,
    _normalize_stock_code,
    _requested_time_quality,
    _row_offsets,
    _sector_metrics,
    _sector_structure_blocks,
    _select_stock_sectors,
    _stock_auction_context,
    _stock_current_block,
    get_market_context_state,
)


def test_stock_code_normalization_accepts_common_forms() -> None:
    assert _normalize_stock_code("000977") == ("000977", "000977.SZ")
    assert _normalize_stock_code(977) == ("000977", "000977.SZ")
    assert _normalize_stock_code("600000.SH") == ("600000", "600000.SH")
    assert _normalize_stock_code("BJ430047") == ("430047", "430047.BJ")


def test_invalid_mode_inputs_are_rejected_before_database_connection() -> None:
    assert (
        get_market_context_state(target_type="bad", target_time="10:30")["status"]
        == "INVALID_REQUEST"
    )
    assert (
        get_market_context_state(target_type="stock", target_time="10:30")["status"]
        == "INVALID_REQUEST"
    )
    assert (
        get_market_context_state(target_type="stocks", tickers=["000977"], target_time="10:30")[
            "status"
        ]
        == "INVALID_REQUEST"
    )
    assert MAX_STOCKS == 50
    assert (
        get_market_context_state(
            target_type="stocks",
            tickers=[f"{index:06d}" for index in range(MAX_STOCKS + 1)],
            target_time="10:30",
        )["status"]
        == "INVALID_REQUEST"
    )
    assert (
        get_market_context_state(
            target_type="sector",
            sector_id="885887.TI",
            sector_name="数据中心(AIDC)",
            target_time="10:30",
        )["status"]
        == "INVALID_REQUEST"
    )


def test_sector_name_normalization_ignores_parentheses_and_spaces() -> None:
    assert _normalize_sector_name("数据中心 (AIDC)") == _normalize_sector_name("数据中心AIDC")


def test_stock_sector_selection_reserves_one_industry_slot() -> None:
    rows = [
        {
            "sector_type": "concept",
            "sector_code": f"C{index}",
            "relative_row": 1,
            "index_change_ratio_pct": index,
            "index_change_1m_pct": 0,
            "up_ratio": 0.5,
            "valid_member_count": 100,
            "limit_up_count": 0,
            "limit_break_count": 0,
        }
        for index in range(1, 7)
    ]
    rows.append(
        {
            "sector_type": "industry",
            "sector_code": "I1",
            "relative_row": 1,
            "index_change_ratio_pct": 99,
            "index_change_1m_pct": 0,
            "up_ratio": 0.5,
            "valid_member_count": 10,
            "limit_up_count": 0,
            "limit_break_count": 0,
        }
    )

    selected = _select_stock_sectors(rows, 5)

    assert len(selected) == 4
    assert sum(row["sector_type"] == "concept" for row in selected) == 3
    assert sum(row["sector_type"] == "industry" for row in selected) == 1


def test_strategic_core_direction_has_auditable_priority_component() -> None:
    rows = [
        {
            "sector_type": "concept",
            "sector_code": code,
            "relative_row": 1,
            "index_change_ratio_pct": 0,
            "index_change_1m_pct": 0,
            "up_ratio": 0.5,
            "valid_member_count": 100,
            "limit_up_count": 0,
            "limit_break_count": 0,
        }
        for code in ("CORE", "OTHER")
    ]
    profiles = [
        {
            "sector_type": "concept",
            "sector_code": "CORE",
            "watch_level": "CORE",
            "valid_members": 100,
            "corr_all": 0.5,
            "corr_recent": 0.5,
            "recent_observations": 30,
        },
        {
            "sector_type": "concept",
            "sector_code": "OTHER",
            "watch_level": "",
            "valid_members": 100,
        },
    ]

    selected = _select_stock_sectors(rows, 1, 0, profiles)

    assert selected[0]["sector_code"] == "CORE"
    breakdown = selected[0]["selection_score_breakdown"]
    assert breakdown["raw_components"]["strategic_direction_priority"] == 1
    assert set(breakdown) == {"weights", "raw_components", "weighted_components", "profile_facts"}


def test_strategic_label_alone_does_not_enter_primary_directions() -> None:
    rows = [
        {
            "sector_type": "concept",
            "sector_code": "LABEL_ONLY",
            "relative_row": 1,
            "index_change_ratio_pct": 0,
            "index_change_1m_pct": 0,
            "up_ratio": 0.5,
            "valid_member_count": 1000,
            "limit_up_count": 0,
            "limit_break_count": 0,
        }
    ]
    profiles = [
        {
            "sector_type": "concept",
            "sector_code": "LABEL_ONLY",
            "watch_level": "CORE",
            "valid_members": 1000,
        }
    ]

    assert _select_stock_sectors(rows, 4, 0, profiles) == []


def test_sector_structure_blocks_keep_market_cap_and_turnover_order_independent() -> None:
    rows = [
        {
            "thscode": "A",
            "stock_name": "A",
            "float_market_cap": 60,
            "total_market_cap": 100,
            "turnover": 10,
            "turnover_to_float_cap_pct": 16.67,
            "sector_float_market_cap_sum": 100,
            "sector_total_market_cap_sum": 200,
            "sector_turnover_sum": 100,
            "float_market_cap_valid_member_count": 3,
            "total_market_cap_valid_member_count": 3,
        },
        {"thscode": "B", "float_market_cap": 30, "total_market_cap": 20, "turnover": 70},
        {"thscode": "C", "float_market_cap": 10, "total_market_cap": 80, "turnover": 20},
        {"thscode": "D", "float_market_cap": None, "total_market_cap": 0, "turnover": 0},
    ]
    current = {
        "valid_member_count": 4,
        "turnover_1m_top1_share_pct": 50,
        "turnover_1m_top3_share_pct": 90,
        "turnover_1m_top5_share_pct": 100,
    }

    caps, largest, turnover = _sector_structure_blocks(rows, current)

    assert caps["float_market_cap_valid_member_count"] == 3
    assert caps["float_cap_top1_share_pct"] == 60
    assert caps["total_cap_top1_share_pct"] == 50
    assert [row["ticker"] for row in largest] == ["A", "B", "C"]
    assert turnover["turnover_top1_share_pct"] == 70
    assert turnover["turnover_1m_top3_share_pct"] == 90


def test_minute_offsets_use_1_5_15_minutes_across_session_labels() -> None:
    rows = [
        {
            "scheduled_time": datetime(2026, 9, 1, 14, 30, 15, tzinfo=UTC),
            "session": "continuous_pm",
        },
        {
            "scheduled_time": datetime(2026, 9, 1, 14, 45, 15, tzinfo=UTC),
            "session": "continuous_pm",
        },
        {
            "scheduled_time": datetime(2026, 9, 1, 15, 0, 0, tzinfo=UTC),
            "session": "auction_close",
        },
    ]

    current, prev_1m, prev_5m, prev_15m = _row_offsets(rows)

    assert current["scheduled_time"].strftime("%H:%M:%S") == "15:00:00"
    assert prev_1m is None
    assert prev_5m is None
    assert prev_15m and prev_15m["scheduled_time"].strftime("%H:%M:%S") == "14:45:15"


def test_stock_trajectory_keeps_30_continuous_recent_minutes_and_earlier_events() -> None:
    rows = []
    start = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)
    for minute in range(40):
        rows.append(
            {
                "scheduled_time": start + timedelta(minutes=minute),
                "last_price": 10 + minute / 100,
                "price_change_ratio_pct": minute / 10,
                "price_change_1m_pct": 0.1,
                "turnover": minute * 100,
                "turnover_delta_1m": minute,
                "new_high_flag": int(minute == 0),
                "new_low_flag": 0,
                "is_limit_up": 0,
                "is_limit_break": 0,
            }
        )

    trajectory = _compress_stock_trajectory(rows)
    recent = [row for row in trajectory if row["time"] >= start + timedelta(minutes=10)]

    assert len(recent) == 30
    assert all(
        later["time"] - earlier["time"] == timedelta(minutes=1)
        for earlier, later in pairwise(recent)
    )
    assert any(row["time"] == start for row in trajectory)
    assert "当前节点" in trajectory[-1]["event"]
    assert "price_change_1m_pct" in trajectory[-1]


def test_stock_current_uses_minute_windows_and_turnover_comparisons() -> None:
    start = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)
    rows = [
        {
            "scheduled_time": start + timedelta(minutes=index),
            "last_price": 10 + index / 100,
            "price_change_ratio_pct": index / 10,
            "price_change_1m_pct": 0.1,
            "turnover": index * 100,
            "turnover_delta_1m": 100 + index,
            "new_high_flag": 0,
            "new_low_flag": 0,
            "is_limit_up": 0,
            "is_limit_down": 0,
            "is_limit_break": 0,
        }
        for index in range(31)
    ]

    current = _stock_current_block(rows)

    assert current["comparison_window"]["prev_1m_time"].strftime("%H:%M") == "09:59"
    assert current["comparison_window"]["prev_5m_time"].strftime("%H:%M") == "09:55"
    assert current["comparison_window"]["prev_15m_time"].strftime("%H:%M") == "09:45"
    assert current["turnover_5m"] == 500
    assert current["turnover_15m"] == sum(100 + index for index in range(16, 31))
    assert current["turnover_15m_valid_minutes"] == 15
    assert current["prev_turnover_15m"] == sum(100 + index for index in range(1, 16))


def test_stock_turnover_15m_does_not_cross_lunch_break() -> None:
    rows = [
        {
            "scheduled_time": datetime(2026, 9, 1, 11, 30, tzinfo=UTC),
            "last_price": 10,
            "turnover_delta_1m": 999,
        },
        *[
            {
                "scheduled_time": datetime(2026, 9, 1, 13, minute, tzinfo=UTC),
                "last_price": 10,
                "turnover_delta_1m": 100,
            }
            for minute in range(4)
        ],
    ]

    current = _stock_current_block(rows)

    assert current["turnover_15m"] == 400
    assert current["turnover_15m_valid_minutes"] == 4
    assert current["turnover_1m_change_pct"] is not None


def test_stock_auction_context_keeps_business_facts_and_key_nodes_only() -> None:
    auction_rows = [
        {
            "node_seq": node_seq,
            "time": f"09:{minute:02d}",
            "auction_phase": phase,
            "auction_price": price,
            "auction_pct": change_pct,
            "auction_amount": amount,
            "auction_volume": volume,
            "auction_turnover_pct": turnover_pct,
            "auction_yesterday_ratio_pct": yesterday_ratio,
            "pre_close_price": 86.35,
            "collection_id": f"20260904{node_seq:03d}",
            "request_id": "must-not-leak",
        }
        for node_seq, minute, phase, price, change_pct, amount, volume, turnover_pct,
        yesterday_ratio in (
            (1, 15, "order_entry", 88.0, 1.91, None, None, None, None),
            (6, 20, "no_cancel", 87.8, 1.68, 10_000_000.123, 1200, 0.01, 0.2),
            (10, 24, "no_cancel", 87.5, 1.33, 40_000_000.456, 4600, 0.03, 0.4),
            (11, 25, "matched", 87.25, 1.0423, 50_736_137.0, 5815, 0.0396, 0.52),
        )
    ]
    stock_rows = [
        {
            "scheduled_time": datetime.fromisoformat("2026-09-04T09:30:08+08:00"),
            "last_price": 86.35,
            "price_change_ratio_pct": 0.0,
        },
        {
            "scheduled_time": datetime.fromisoformat("2026-09-04T09:45:08+08:00"),
            "last_price": 80.0,
            "price_change_ratio_pct": -7.35,
        },
    ]

    context = _stock_auction_context(
        auction_rows,
        stock_rows,
        datetime.fromisoformat("2026-09-04T10:00:08+08:00"),
    )

    assert context["final"] == {
        "time": "09:25",
        "price": 87.25,
        "change_pct": 1.0423,
        "amount": 50_736_137.0,
        "volume": 5815,
        "turnover_pct": 0.0396,
        "yesterday_ratio_pct": 0.52,
        "pre_close_price": 86.35,
    }
    assert context["trajectory"]["schema"] == AUCTION_TRAJECTORY_SCHEMA
    assert [row[0] for row in context["trajectory"]["rows"]] == [
        "09:15",
        "09:20",
        "09:24",
        "09:25",
    ]
    assert context["trajectory"]["rows"][0][-1] is None
    comparison = context["auction_to_open"]
    assert comparison["open_price"] == 86.35
    assert comparison["open_pct"] == 0.0
    assert round(comparison["change_from_auction_to_open_pct"], 4) == -1.0423
    assert round(comparison["first_15m_pct"], 2) == -7.35
    assert "collection_id" not in str(context)
    assert "request_id" not in str(context)


def test_stock_api_compacts_and_rounds_auction_context() -> None:
    auction_context = {
        "final": {
            "time": "09:25",
            "price": 87.251,
            "change_pct": 1.0423,
            "amount": 50_736_137.0,
            "volume": 5815,
            "turnover_pct": 0.0396,
            "yesterday_ratio_pct": 0.519,
            "pre_close_price": 86.35,
            "request_id": "must-not-leak",
        },
        "trajectory": {
            "schema": AUCTION_TRAJECTORY_SCHEMA,
            "rows": [["09:15", None, None, None], ["09:25", 87.251, 1.0423, 50_736_137.0]],
        },
        "auction_to_open": {
            "auction_final_pct": 1.0423,
            "open_price": 86.35,
            "open_pct": 0.0,
            "change_from_auction_to_open_pct": -1.0423,
            "first_15m_pct": -5.394,
        },
        "collection_id": "20260904011",
    }
    response = {
        "status": "OK",
        "target_type": "stock",
        "resolved": {
            "trade_date": date(2026, 9, 4),
            "requested_time": "10:00",
            "actual_time": datetime.fromisoformat("2026-09-04T10:00:08+08:00"),
        },
        "data": {
            "stocks": [
                {
                    "status": "OK",
                    "ticker": "000977",
                    "thscode": "000977.SZ",
                    "name": "浪潮信息",
                    "auction_context": auction_context,
                }
            ]
        },
    }

    compact = compact_market_context_response(response)["auction_context"]

    assert compact["final"]["price"] == 87.25
    assert compact["final"]["change_pct"] == 1.04
    assert compact["final"]["turnover_pct"] == 0.04
    assert compact["auction_to_open"]["change_from_auction_to_open_pct"] == -1.04
    assert compact["auction_to_open"]["first_15m_pct"] == -5.39
    assert compact["trajectory"]["rows"][0] == ["09:15", None, None, None]
    assert "collection_id" not in str(compact)
    assert "request_id" not in str(compact)


def test_sector_current_and_trajectory_are_minute_level() -> None:
    start = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)
    rows = [
        {
            "scheduled_time": start + timedelta(minutes=index),
            "index_last_price": 100 + index,
            "index_change_ratio_pct": index / 10,
            "index_change_1m_pct": 0.1,
            "up_ratio": 0.5 + index / 1000,
            "turnover_total": index * 1000,
            "turnover_delta_1m_total": 1000 + index,
            "turnover_market_share_pct": 3 + index / 100,
            "turnover_1m_market_share_pct": 4 + index / 100,
            "limit_up_count": 1,
            "limit_break_count": 0,
            "new_high_ratio": 0.1,
        }
        for index in range(35)
    ]
    current, prev_1m, prev_5m, prev_15m = _row_offsets(rows)
    metrics = _sector_metrics(current, prev_1m, prev_5m, prev_15m, rows[-11], rows[-31])
    trajectory = _compress_sector_trajectory(rows)

    assert metrics["change_5m_pct"] is not None
    assert round(metrics["instant_share_change_1m"], 6) == 0.01
    assert metrics["turnover_5m"] == 5000
    assert len(trajectory) == 30
    assert trajectory[-1]["turnover_1m"] == 1034


def test_requested_minute_fallback_age_ignores_snapshot_seconds() -> None:
    trade_day = date(2026, 9, 1)
    actual = datetime.fromisoformat("2026-09-01T10:06:08+08:00")

    requested, age = _requested_time_quality(
        trade_day, time(10, 7), False, "10:07", actual
    )
    latest, latest_age = _requested_time_quality(trade_day, None, False, None, actual)

    assert (requested, age) == ("10:07", 60)
    assert (latest, latest_age) == ("10:06", 0)


def test_new_tool_is_wired_into_both_mcp_entries_without_replacing_market_package() -> None:
    gateway = Path("app/ai_gateway.py").read_text(encoding="utf-8")
    plugin = Path("app/clickhouse_plugin_server.py").read_text(encoding="utf-8")
    assert "def get_market_context_state(" in gateway
    assert '"name": "get_market_context_state"' in plugin
    assert '"required": ["target_type"]' in plugin
    assert '"maxItems": 50' in plugin
    assert "最多50只股票" in gateway
    assert "最多50只股票" in plugin
    assert "def get_market_state_package(" in gateway
    assert '"name": "get_market_state_package"' in plugin
