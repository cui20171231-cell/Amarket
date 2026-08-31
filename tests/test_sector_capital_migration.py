from __future__ import annotations

from pathlib import Path

DDL_PATH = Path("sql/hithink_snapshot.sql")
BACKFILL_PATH = Path("sql/hithink_sector_capital_migration_backfill.sql")
DOC_PATH = Path("docs/hithink_sector_capital_migration_derivation.md")

DELTA_FIELDS = {
    "turnover_market_share_delta_15m",
    "turnover_1m_market_share_delta_15m",
    "turnover_share_rank_delta",
    "turnover_1m_share_rank_delta",
    "turnover_increment_15m",
    "turnover_increment_market_share_pct",
    "up_ratio_delta_15m",
    "down_ratio_delta_15m",
    "limit_up_count_delta_15m",
    "limit_break_count_delta_15m",
    "new_high_ratio_delta_15m",
    "new_low_ratio_delta_15m",
    "top1_share_delta_15m",
    "top3_share_delta_15m",
    "top5_share_delta_15m",
}


def table_ddl() -> str:
    ddl = DDL_PATH.read_text(encoding="utf-8")
    return ddl.split(
        "CREATE TABLE IF NOT EXISTS market.hithink_sector_capital_migration", 1
    )[1].split("CREATE TABLE IF NOT EXISTS market.hithink_emotion_state", 1)[0]


def test_table_has_one_row_per_target_and_sector_identity() -> None:
    ddl = table_ddl()

    for field in (
        "trade_date Date",
        "collection_id FixedString(11)",
        "node_seq UInt16 MATERIALIZED",
        "sector_type LowCardinality(String)",
        "sector_code String",
        "sector_name String",
    ):
        assert field in ddl
    assert "ORDER BY (trade_date, collection_id, sector_type, sector_code)" in ddl


def test_all_change_fields_are_nullable() -> None:
    ddl = table_ddl()

    for field in DELTA_FIELDS:
        line = next(line for line in ddl.splitlines() if line.strip().startswith(field))
        assert "Nullable(" in line


def test_backfill_uses_only_the_three_existing_sector_state_sources() -> None:
    sql = BACKFILL_PATH.read_text(encoding="utf-8")

    assert "market.hithink_concept_state FINAL" in sql
    assert "market.hithink_industry_state FINAL" in sql
    assert "market.hithink_style_state FINAL" in sql
    assert "market.hithink_market_state" not in sql
    assert "market.hithink_market_delta_15m" not in sql


def test_backfill_uses_the_same_19_targets_and_three_delta_types() -> None:
    sql = BACKFILL_PATH.read_text(encoding="utf-8")

    assert "11, 12, 27, 42, 57, 72, 87, 102, 117, 132" in sql
    assert "133, 148, 163, 178, 193, 208, 223, 238, 254" in sql
    assert "target_node_seq IN (11, 133), 'SESSION_BASE'" in sql
    assert "target_node_seq = 12, 'AUCTION_TO_OPEN'" in sql
    assert "'NORMAL_15M'" in sql


def test_source_fallback_is_same_day_same_business_period_and_254_is_current() -> None:
    sql = BACKFILL_PATH.read_text(encoding="utf-8")
    ddl = table_ddl()

    assert "target.business_period = source.business_period" in sql
    assert "target.scheduled_time >= source.scheduled_time" in sql
    assert "HAVING uniqExact(state.sector_code) = max(expected.expected_sector_count)" in sql
    assert "target_node_seq = 254 AND selected_source_collection_id != collection_id" in sql
    assert "CONSTRAINT ck_capital_migration_closing_254" in ddl


def test_ranks_are_independent_by_sector_type_and_positive_means_rising() -> None:
    sql = BACKFILL_PATH.read_text(encoding="utf-8")

    assert "PARTITION BY trade_date, collection_id, sector_type" in sql
    assert "toInt16(base_turnover_share_rank) - toInt16(current_turnover_share_rank)" in sql
    assert (
        "toInt16(base_turnover_1m_share_rank) - "
        "toInt16(current_turnover_1m_share_rank)"
    ) in sql


def test_schedule_has_all_five_capital_migration_task_fields() -> None:
    ddl = DDL_PATH.read_text(encoding="utf-8")

    for field in (
        "capital_migration_status",
        "capital_migration_row_count",
        "capital_migration_duration_ms",
        "capital_migration_error_code",
        "capital_migration_error_message",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {field}" in ddl


def test_schema_has_no_fake_net_flow_and_document_warns_about_overlap() -> None:
    ddl_and_sql = table_ddl() + BACKFILL_PATH.read_text(encoding="utf-8")
    document = DOC_PATH.read_text(encoding="utf-8")

    assert "net_inflow" not in ddl_and_sql
    assert "net_outflow" not in ddl_and_sql
    assert "不能相加解释为全市场资金分配" in document
