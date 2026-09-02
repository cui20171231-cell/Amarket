from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.market_context_state import (
    _compress_stock_trajectory,
    _normalize_sector_name,
    _normalize_stock_code,
    _row_offsets,
    _select_stock_sectors,
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


def test_closing_node_uses_nearest_15_and_30_minute_points_across_session_labels() -> None:
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

    current, base, prebase = _row_offsets(rows)

    assert current["scheduled_time"].strftime("%H:%M:%S") == "15:00:00"
    assert base and base["scheduled_time"].strftime("%H:%M:%S") == "14:45:15"
    assert prebase and prebase["scheduled_time"].strftime("%H:%M:%S") == "14:30:15"


def test_stock_trajectory_is_compressed_and_keeps_current_node() -> None:
    rows = []
    for minute in range(30):
        rows.append(
            {
                "scheduled_time": datetime(2026, 9, 1, 9, 30 + minute, tzinfo=UTC),
                "last_price": 10 + minute / 100,
                "price_change_ratio_pct": minute / 10,
                "turnover": minute * 100,
                "turnover_delta_1m": minute,
                "new_high_flag": 0,
                "new_low_flag": 0,
                "is_limit_up": 0,
                "is_limit_break": 0,
            }
        )

    compressed = _compress_stock_trajectory(rows)

    assert len(compressed) < len(rows)
    assert "当前节点" in compressed[-1]["reasons"]


def test_new_tool_is_wired_into_both_mcp_entries_without_replacing_market_package() -> None:
    gateway = Path("app/ai_gateway.py").read_text(encoding="utf-8")
    plugin = Path("app/clickhouse_plugin_server.py").read_text(encoding="utf-8")
    assert "def get_market_context_state(" in gateway
    assert '"name": "get_market_context_state"' in plugin
    assert "def get_market_state_package(" in gateway
    assert '"name": "get_market_state_package"' in plugin
