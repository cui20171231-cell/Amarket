from scripts.simulate_compact_market_state_package import (
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
