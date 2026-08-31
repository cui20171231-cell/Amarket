from pathlib import Path

DDL = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")
SECTOR_SQL = Path("sql/hithink_core_sector_candidate_backfill.sql").read_text(encoding="utf-8")
STOCK_SQL = Path("sql/hithink_core_stock_candidate_backfill.sql").read_text(encoding="utf-8")
STOCK_SUMMARY_SQL = Path("sql/hithink_core_stock_candidate_summary.sql").read_text(encoding="utf-8")
DOC = Path("docs/hithink_core_candidates_v1_v2.md").read_text(encoding="utf-8")


def test_v1_tables_have_version_and_no_v2_capacity_columns() -> None:
    section = DDL.split("CREATE TABLE IF NOT EXISTS market.hithink_core_sector_candidate", 1)[1].split("CREATE TABLE IF NOT EXISTS market.hithink_emotion_state", 1)[0]
    for forbidden in ("total_shares", "float_shares", "total_market_cap", "float_market_cap", "turnover_rate_pct"):
        assert forbidden not in section
    assert section.count("candidate_model_version LowCardinality(String)") == 2
    assert section.count("candidate_model_version = 'V1'") == 2


def test_sector_score_is_explainable_and_separately_capped() -> None:
    assert "hit_price_strength*8" in SECTOR_SQL
    assert "price_total_strength" in SECTOR_SQL
    assert "qualification_score>=35" in SECTOR_SQL
    assert "percent_rank() OVER (PARTITION BY collection_id,sector_type" in SECTOR_SQL
    assert "{concept_candidate_limit:UInt16}" in SECTOR_SQL
    assert "{industry_candidate_limit:UInt16}" in SECTOR_SQL
    assert "market.hithink_style_state FINAL" not in SECTOR_SQL
    assert "sector_type IN ('concept','industry')" in SECTOR_SQL
    assert "candidate_reason_mask" in SECTOR_SQL


def test_style_is_a_compatibility_field_not_core_candidate_evidence() -> None:
    assert "sector_type IN ('concept','industry')" in STOCK_SUMMARY_SQL
    assert "toUInt16(0)" in STOCK_SUMMARY_SQL
    assert "core_style_count UInt16 COMMENT 'COMPATIBILITY ONLY:" in DDL
    assert "ck_core_stock_style_compat CHECK core_style_count = 0" in DDL


def test_stock_score_uses_absolute_turnover_only_as_proxy() -> None:
    assert "hit_turnover_absolute*20" in STOCK_SQL
    assert "turnover_rank_market<=ceil(market_count*0.03)" in STOCK_SQL
    assert "candidate_rank<=50" in STOCK_SQL


def test_persistence_is_partitioned_by_business_period() -> None:
    assert "trade_date,business_period,sector_type,sector_code" in SECTOR_SQL
    assert "trade_date,business_period,thscode" in STOCK_SQL


def test_weak_stock_cannot_qualify_from_sector_membership_alone() -> None:
    guard = "hit_price_strength+hit_turnover_absolute+hit_turnover_acceleration+hit_new_high+hit_limit_strength"
    assert guard in STOCK_SQL


def test_v2_upgrade_is_documented_without_dirtying_v1_schema() -> None:
    assert "ALTER TABLE ADD COLUMN" in DOC
    assert "candidate_model_version='V2'" in DOC
    assert "不能解释成\n真实市值容量" in DOC
