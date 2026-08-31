import re
from pathlib import Path

DDL = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")
SEED = Path("sql/strategic_sector_watchlist_ai_v1.sql").read_text(encoding="utf-8")
QUERY = Path("sql/strategic_sector_watchlist_status_example.sql").read_text(encoding="utf-8")
CLASSIFICATION = Path("docs/market_table_classification.md").read_text(encoding="utf-8")


def _ddl_section() -> str:
    return DDL.split("CREATE TABLE IF NOT EXISTS market.strategic_sector_watchlist", 1)[1].split(
        "CREATE TABLE IF NOT EXISTS market.sector_membership_history", 1
    )[0]


def test_watchlist_is_configuration_without_market_state_columns() -> None:
    section = _ddl_section()
    for field in (
        "sector_type",
        "sector_code",
        "strategic_theme",
        "watch_level",
        "effective_from",
        "effective_to",
        "definition_source",
    ):
        assert field in section
    for forbidden in ("current_rank", "current_score", "main_line_today", "net_inflow"):
        assert forbidden not in section


def test_watchlist_has_fixed_levels_and_historical_window_guards() -> None:
    section = _ddl_section()
    assert "('CORE', 'IMPORTANT', 'WATCH')" in section
    assert "effective_to >= effective_from" in section
    assert "is_active = 1 AND effective_to IS NULL" in section
    assert "ORDER BY (sector_type, sector_code, strategic_theme)" in section


def test_ai_v1_seed_has_controlled_level_counts() -> None:
    rows = re.findall(r"^\s*\('(concept|industry|style)'", SEED, flags=re.MULTILINE)
    assert len(rows) == 57
    assert len(re.findall(r"'CORE','", SEED)) == 24
    assert len(re.findall(r"'IMPORTANT','", SEED)) == 30
    assert len(re.findall(r"'WATCH','", SEED)) == 3


def test_seed_only_inserts_catalog_matches_and_is_logically_idempotent() -> None:
    assert "INNER JOIN catalog c USING (sector_type,sector_code)" in SEED
    assert "LEFT ANTI JOIN existing e USING (sector_type,sector_code,strategic_theme)" in SEED
    assert "AI战略主线V1" in SEED


def test_status_query_forces_all_active_watchlist_rows_and_preserves_missing_candidate() -> None:
    assert "SELECT * FROM market.strategic_sector_watchlist FINAL WHERE is_active=1" in QUERY
    assert "LEFT JOIN current_migration" in QUERY
    assert "LEFT JOIN candidates" in QUERY
    assert "turnover_rank_last_5" in QUERY
    assert "SETTINGS join_use_nulls=1" in QUERY


def test_watchlist_is_documented_outside_a_b_table_classes() -> None:
    config_section = CLASSIFICATION.split("## 配置/知识层", 1)[1].split("## 1. 四类业务表", 1)[0]
    assert "market.strategic_sector_watchlist" in config_section
