from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from app.hithink.schedule import ScheduleNode, build_daily_schedule

MISSING_MARKET_STATE_NODES = {
    *range(1, 11),
    17,
    72,
    80,
    120,
    126,
    186,
    219,
    232,
}

TARGET_NODE_SEQUENCES = {
    11,
    12,
    27,
    42,
    57,
    72,
    87,
    102,
    117,
    132,
    133,
    148,
    163,
    178,
    193,
    208,
    223,
    238,
    254,
}

DELTA_FIELDS = {
    "up_count_delta_15m",
    "down_count_delta_15m",
    "flat_count_delta_15m",
    "up_ratio_delta_15m",
    "down_ratio_delta_15m",
    "limit_up_count_delta_15m",
    "up_5_to_limit_count_delta_15m",
    "up_1_to_5_count_delta_15m",
    "up_0_to_1_count_delta_15m",
    "down_0_to_1_count_delta_15m",
    "down_1_to_5_count_delta_15m",
    "down_5_to_limit_count_delta_15m",
    "limit_down_count_delta_15m",
    "limit_break_count_delta_15m",
    "turnover_increment_15m",
    "turnover_speed_current",
    "turnover_speed_base",
    "turnover_speed_delta_15m",
    "turnover_speed_change_pct",
    "turnover_accel_count_delta_15m",
    "turnover_decel_count_delta_15m",
    "turnover_accel_50_count_delta_15m",
    "turnover_accel_100_count_delta_15m",
    "volume_expand_count_delta_15m",
    "volume_contract_count_delta_15m",
    "volume_ratio_1_5_count_delta_15m",
    "volume_ratio_2_count_delta_15m",
    "volume_ratio_3_count_delta_15m",
    "new_high_count_delta_15m",
    "new_low_count_delta_15m",
    "price_up_1m_count_delta_15m",
    "price_down_1m_count_delta_15m",
    "price_flat_1m_count_delta_15m",
    "volume_price_up_count_delta_15m",
    "volume_price_down_count_delta_15m",
    "contract_price_up_count_delta_15m",
    "contract_price_down_count_delta_15m",
}


def business_period(value: datetime) -> str | None:
    clock = value.timetz().replace(tzinfo=None)
    if time(9, 15) <= clock <= time(11, 30, 59, 999999):
        return "AM"
    if time(13, 0) <= clock <= time(15, 0):
        return "PM"
    return None


def select_base(current: ScheduleNode, states: list[ScheduleNode]) -> ScheduleNode | None:
    period = business_period(current.scheduled_time)
    if period is None:
        return None
    candidates = [
        node
        for node in states
        if node.trade_date == current.trade_date
        and business_period(node.scheduled_time) == period
        and node.scheduled_time < current.scheduled_time
    ]
    return max(candidates, key=lambda node: node.scheduled_time, default=None)


def real_market_state_nodes() -> list[ScheduleNode]:
    return [
        node
        for node in build_daily_schedule(date(2026, 8, 28))
        if node.sequence_no not in MISSING_MARKET_STATE_NODES
    ]


def target_nodes() -> list[ScheduleNode]:
    return [
        node
        for node in build_daily_schedule(date(2026, 8, 28))
        if node.sequence_no in TARGET_NODE_SEQUENCES
    ]


def select_source(target: ScheduleNode, states: list[ScheduleNode]) -> ScheduleNode | None:
    period = business_period(target.scheduled_time)
    candidates = [
        node
        for node in states
        if node.trade_date == target.trade_date
        and business_period(node.scheduled_time) == period
        and node.scheduled_time <= target.scheduled_time
    ]
    source = max(candidates, key=lambda node: node.scheduled_time, default=None)
    if target.sequence_no == 254 and source != target:
        return None
    return source


def test_business_period_uses_clock_time_not_session_label() -> None:
    nodes = build_daily_schedule(date(2026, 8, 28))

    assert business_period(nodes[10].scheduled_time) == "AM"
    assert business_period(nodes[11].scheduled_time) == "AM"
    assert business_period(nodes[132].scheduled_time) == "PM"
    assert nodes[249].session == "auction_close"
    assert business_period(nodes[249].scheduled_time) == "PM"
    assert business_period(nodes[253].scheduled_time) == "PM"


def test_first_valid_nodes_restart_after_open_and_lunch() -> None:
    targets = target_nodes()
    valid = [node for node in targets if select_base(node, targets) is not None]

    first_am = min(
        (node for node in valid if business_period(node.scheduled_time) == "AM"),
        key=lambda node: node.scheduled_time,
    )
    first_pm = min(
        (node for node in valid if business_period(node.scheduled_time) == "PM"),
        key=lambda node: node.scheduled_time,
    )
    assert first_am.sequence_no == 12
    assert first_pm.sequence_no == 148


def test_missing_1030_checkpoint_falls_back_and_1045_uses_that_actual_source() -> None:
    states = real_market_state_nodes()
    assert all(node.sequence_no != 72 for node in states)
    targets = target_nodes()
    target_1030 = next(node for node in targets if node.sequence_no == 72)
    target_1045 = next(node for node in targets if node.sequence_no == 87)
    source_1030 = select_source(target_1030, states)
    base_target = select_base(target_1045, targets)

    assert source_1030 is not None
    assert source_1030.sequence_no == 71
    assert (target_1030.scheduled_time - source_1030.scheduled_time).total_seconds() == 60
    assert base_target == target_1030
    assert (target_1045.scheduled_time - source_1030.scheduled_time).total_seconds() == 960


def test_closing_254_uses_source_checkpoint_238() -> None:
    states = real_market_state_nodes()
    targets = target_nodes()
    current = next(node for node in targets if node.sequence_no == 254)
    base_target = select_base(current, targets)
    assert base_target is not None
    base = select_source(base_target, states)

    assert base is not None
    assert base.sequence_no == 238
    assert base.scheduled_time.timetz().replace(tzinfo=None) == time(14, 45, 15)
    assert (current.scheduled_time - base.scheduled_time).total_seconds() == 885


def test_closing_254_current_state_never_falls_back() -> None:
    states = [node for node in real_market_state_nodes() if node.sequence_no != 254]
    current = next(node for node in target_nodes() if node.sequence_no == 254)

    assert select_source(current, states) is None


def test_all_delta_fields_are_nullable_in_the_ddl() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")
    table_ddl = ddl.split(
        "CREATE TABLE IF NOT EXISTS market.hithink_market_delta_15m", 1
    )[1].split("CREATE TABLE IF NOT EXISTS market.hithink_emotion_state", 1)[0]

    for field in DELTA_FIELDS:
        line = next(line for line in table_ddl.splitlines() if line.strip().startswith(field))
        assert "Nullable(" in line


def test_current_state_source_and_age_fields_are_fixed_in_the_ddl() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")

    for field in (
        "state_data_status LowCardinality(String)",
        "state_source_collection_id Nullable(FixedString(11))",
        "state_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai'))",
        "state_source_age_seconds Nullable(UInt32)",
        "state_is_fallback UInt8",
    ):
        assert field in ddl
    assert "state_data_status = 'CURRENT'" in ddl
    assert "state_data_status = 'FALLBACK'" in ddl
    assert "state_source_age_seconds = dateDiff" in ddl


def test_delta_type_separates_session_bases_auction_open_and_normal_rows() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")
    sql = Path("sql/hithink_market_delta_15m_backfill.sql").read_text(encoding="utf-8")

    assert "delta_type LowCardinality(String)" in ddl
    assert "target_node_seq IN (11, 133), 'SESSION_BASE'" in sql
    assert "target_node_seq = 12, 'AUCTION_TO_OPEN'" in sql
    assert "'NORMAL_15M'" in sql


def test_backfill_uses_fixed_source_nodes_and_previous_real_checkpoint() -> None:
    sql = Path("sql/hithink_market_delta_15m_backfill.sql").read_text(encoding="utf-8")

    assert "11, 12, 27, 42, 57, 72, 87, 102, 117, 132" in sql
    assert "133, 148, 163, 178, 193, 208, 223, 238, 254" in sql
    assert "FROM market.hithink_snapshot_schedule FINAL" in sql
    assert "FROM market.hithink_market_state FINAL" in sql
    assert "target.scheduled_time >= state.scheduled_time" in sql
    assert "current.previous_lookup_time >= base.scheduled_time" in sql
    assert "state_source_age_seconds" in sql
    assert "resolved_state_data_status" in sql
    assert "target_node_seq != 254 OR state_source_collection_id = collection_id" in sql
    assert "node_seq - 15" not in sql
    assert "collection_id - 15" not in sql


def test_schedule_has_all_five_delta_task_fields() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")

    for field in (
        "market_delta_15m_status",
        "market_delta_15m_row_count",
        "market_delta_15m_duration_ms",
        "market_delta_15m_error_code",
        "market_delta_15m_error_message",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {field}" in ddl


def test_no_persistent_30_or_60_minute_delta_table_exists() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")

    assert "hithink_market_delta_30m" not in ddl
    assert "hithink_market_delta_60m" not in ddl
