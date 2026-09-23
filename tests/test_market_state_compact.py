from app.market_state_package import MARKET_INTRADAY_FIELDS
from scripts.simulate_compact_market_state_package import (
    MARKET_TRAJECTORY_SCHEMA,
    OPEN_AUCTION_MARKET_TRAJECTORY_SCHEMA,
    compact_auction_market,
    compact_core_stocks,
    compact_market,
    comparable_new_high_ratio,
)


def test_open_transition_new_high_ratio_is_not_comparable() -> None:
    assert comparable_new_high_ratio("2026-09-03T09:30:15+08:00", 0.63) is None
    assert comparable_new_high_ratio("2026-09-03T09:45:15+08:00", 0.02) == 0.02


def test_market_trajectory_keeps_six_band_distribution_and_nulls() -> None:
    source_fields = {
        "up_5_to_limit_count",
        "up_1_to_5_count",
        "up_0_to_1_count",
        "down_0_to_1_count",
        "down_1_to_5_count",
        "down_5_to_limit_count",
    }
    source = {
        "intraday_trajectory": [
            {
                "scheduled_time": "2026-09-15T09:45:15+08:00",
                "limit_up_count": 16,
                "up_5_to_limit_count": 51,
                "up_1_to_5_count": 444,
                "up_0_to_1_count": 848,
                "down_0_to_1_count": 2327,
                "down_1_to_5_count": 1679,
                "down_5_to_limit_count": None,
                "limit_down_count": 9,
            }
        ]
    }

    trajectory = compact_market(source)["trajectory"]
    row = dict(zip(trajectory["schema"], trajectory["rows"][0], strict=True))

    assert source_fields <= set(MARKET_INTRADAY_FIELDS)
    assert trajectory["schema"] == MARKET_TRAJECTORY_SCHEMA
    assert row["up_5_to_limit"] == 51
    assert row["up_1_to_5"] == 444
    assert row["up_0_to_1"] == 848
    assert row["down_0_to_1"] == 2327
    assert row["down_1_to_5"] == 1679
    assert row["down_5_to_limit"] is None


def test_opening_auction_core_stocks_keep_the_dedicated_shape() -> None:
    source = {
        "data_context": "OPEN_AUCTION",
        "current_schema": ["stock_code"],
        "current_rows": [["000001.SZ"]],
        "trajectory_schema": ["stock_code", "time"],
        "trajectory_rows": [["000001.SZ", "09:15"]],
    }

    assert compact_core_stocks(source) == source


def test_intraday_core_stocks_rank_turnover_relative_to_float_cap() -> None:
    source = [
        {"thscode": "A", "turnover_to_float_cap_pct": 2.0},
        {"thscode": "B", "turnover_to_float_cap_pct": None},
        {"thscode": "C", "turnover_to_float_cap_pct": 5.0},
    ]

    compact = compact_core_stocks(source)
    rank_at = compact["schema"].index("turnover_to_float_cap_rank")

    assert [row[rank_at] for row in compact["rows"]] == [2, None, 1]


def test_opening_auction_market_compaction_keeps_only_business_facts() -> None:
    source = {
        "data_context": "OPEN_AUCTION",
        "trajectory": {
            "schema": OPEN_AUCTION_MARKET_TRAJECTORY_SCHEMA,
            "rows": [["09:15", "order_entry"]],
        },
        "current": {"valid_count": 5000, "request_id": "must-not-leak"},
        "change": {
            "significant_change_threshold_pct_points": 1.0,
            "from_09_15_to_09_20": {"stronger_stock_count": 100},
        },
        "batch_id": "must-not-leak",
    }

    compact = compact_auction_market(source)

    assert compact["current"]["valid_count"] == 5000
    assert compact["change"]["from_09_15_to_09_20"]["stronger_stock_count"] == 100
    assert "request_id" not in str(compact)
    assert "batch_id" not in str(compact)
