from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path

from app.market_context_state import (
    _compress_sector_trajectory,
    _compress_stock_trajectory,
    _normalize_sector_name,
    _normalize_stock_code,
    _requested_time_quality,
    _row_offsets,
    _sector_metrics,
    _select_stock_sectors,
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

    assert len(selected) == 5
    assert sum(row["sector_type"] == "concept" for row in selected) == 4
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
    assert current["prev_turnover_15m"] == 1500
    assert current["turnover_1m_change_pct"] is not None


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
    assert "def get_market_state_package(" in gateway
    assert '"name": "get_market_state_package"' in plugin
