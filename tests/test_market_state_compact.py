from scripts.simulate_compact_market_state_package import (
    OPEN_AUCTION_MARKET_TRAJECTORY_SCHEMA,
    compact_auction_market,
    compact_core_stocks,
    comparable_new_high_ratio,
)


def test_open_transition_new_high_ratio_is_not_comparable() -> None:
    assert comparable_new_high_ratio("2026-09-03T09:30:15+08:00", 0.63) is None
    assert comparable_new_high_ratio("2026-09-03T09:45:15+08:00", 0.02) == 0.02


def test_opening_auction_core_stocks_keep_the_dedicated_shape() -> None:
    source = {
        "data_context": "OPEN_AUCTION",
        "current_schema": ["stock_code"],
        "current_rows": [["000001.SZ"]],
        "trajectory_schema": ["stock_code", "time"],
        "trajectory_rows": [["000001.SZ", "09:15"]],
    }

    assert compact_core_stocks(source) == source


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
