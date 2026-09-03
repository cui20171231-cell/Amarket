from scripts.simulate_compact_market_state_package import (
    comparable_new_high_ratio,
)


def test_open_transition_new_high_ratio_is_not_comparable() -> None:
    assert comparable_new_high_ratio("2026-09-03T09:30:15+08:00", 0.63) is None
    assert comparable_new_high_ratio("2026-09-03T09:45:15+08:00", 0.02) == 0.02

