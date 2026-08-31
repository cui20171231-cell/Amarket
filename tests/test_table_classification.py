from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.hithink.schedule import SHANGHAI, build_daily_schedule
from app.hithink.writer import ClickHouseWriter

COLLECTION_ID_TABLES = {
    "hithink_adjustment_events",
    "hithink_concept_state",
    "hithink_core_sector_candidate",
    "hithink_core_stock_candidate",
    "hithink_daily_k_forward",
    "hithink_daily_k_raw",
    "hithink_emotion_state",
    "hithink_industry_state",
    "hithink_limit_break_pool",
    "hithink_limit_down_pool",
    "hithink_limit_up_pool",
    "hithink_market_delta_15m",
    "hithink_market_state",
    "hithink_sector_index_snapshot",
    "hithink_sector_capital_migration",
    "hithink_snapshot_derived",
    "hithink_snapshot_raw",
    "hithink_snapshot_schedule",
    "hithink_style_state",
}

A_TABLES = {
    "hithink_snapshot_raw",
    "hithink_sector_index_snapshot",
    "hithink_limit_up_pool",
    "hithink_limit_down_pool",
    "hithink_limit_break_pool",
}

A2_TABLES = {
    "hithink_daily_k_raw",
    "hithink_adjustment_events",
    "trading_calendar",
    "sector_catalog",
    "sector_membership_history",
}

B_TABLES = {
    "hithink_snapshot_derived",
    "hithink_market_delta_15m",
    "hithink_market_state",
    "hithink_emotion_state",
    "hithink_concept_state",
    "hithink_core_sector_candidate",
    "hithink_core_stock_candidate",
    "hithink_industry_state",
    "hithink_style_state",
    "hithink_sector_capital_migration",
}

B2_TABLES = {"hithink_daily_k_forward"}

CLOSING_254_CONSTRAINTS = {
    "hithink_snapshot_raw": "ck_snapshot_raw_closing_254",
    "hithink_sector_index_snapshot": "ck_sector_index_closing_254",
    "hithink_limit_up_pool": "ck_limit_up_closing_254",
    "hithink_limit_down_pool": "ck_limit_down_closing_254",
    "hithink_limit_break_pool": "ck_limit_break_closing_254",
    "hithink_snapshot_derived": "ck_snapshot_derived_closing_254",
    "hithink_market_delta_15m": "ck_market_delta_closing_254",
    "hithink_market_state": "ck_market_state_closing_254",
    "hithink_emotion_state": "ck_emotion_closing_254_freshness",
    "hithink_concept_state": "ck_concept_state_closing_254",
    "hithink_core_sector_candidate": "ck_core_sector_closing_254",
    "hithink_core_stock_candidate": "ck_core_stock_closing_254",
    "hithink_industry_state": "ck_industry_state_closing_254",
    "hithink_style_state": "ck_style_state_closing_254",
    "hithink_sector_capital_migration": "ck_capital_migration_closing_254",
}


def test_every_collection_id_table_has_materialized_node_seq_migration() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")
    expression = (
        "node_seq UInt16 MATERIALIZED "
        "toUInt16(substring(toString(collection_id), 9, 3))"
    )

    for table in COLLECTION_ID_TABLES:
        assert (
            f"ALTER TABLE market.{table} ADD COLUMN IF NOT EXISTS {expression}"
            in ddl
        )


def test_all_business_tables_have_one_fixed_classification() -> None:
    document = Path("docs/market_table_classification.md").read_text(encoding="utf-8")
    classes = (A_TABLES, A2_TABLES, B_TABLES, B2_TABLES)

    assert not any(left & right for index, left in enumerate(classes) for right in classes[index + 1 :])
    for table in set().union(*classes):
        assert document.count(f"`market.{table}`") == 1


def test_a_class_is_exactly_the_five_intraday_raw_tables() -> None:
    assert A_TABLES == {
        "hithink_snapshot_raw",
        "hithink_sector_index_snapshot",
        "hithink_limit_up_pool",
        "hithink_limit_down_pool",
        "hithink_limit_break_pool",
    }


def test_schedule_status_rewrite_never_writes_materialized_node_seq() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[-1]

    class QueryResult:
        def __init__(self) -> None:
            self.column_names = [
                "trade_date",
                "collection_id",
                "node_seq",
                "scheduled_time",
                "session",
                "sequence_no",
                "status",
                "updated_at",
            ]
            self.result_rows = [
                (
                    node.trade_date,
                    node.collection_id,
                    254,
                    node.scheduled_time,
                    node.session,
                    254,
                    "PENDING",
                    datetime(2026, 8, 28, 15, 0, tzinfo=SHANGHAI),
                )
            ]

    class Client:
        def __init__(self) -> None:
            self.column_names: list[str] = []

        def query(self, sql, parameters=None):
            return QueryResult()

        def insert(self, table, rows, column_names):
            self.column_names = column_names

    writer = object.__new__(ClickHouseWriter)
    writer.client = Client()

    writer.set_schedule_status(node, "RUNNING")

    assert "node_seq" not in writer.client.column_names
    assert "updated_at" not in writer.client.column_names
    assert "collection_id" in writer.client.column_names


def test_all_a_and_b_tables_have_a_closing_254_constraint() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")

    assert set(CLOSING_254_CONSTRAINTS) == A_TABLES | B_TABLES
    for table, constraint in CLOSING_254_CONSTRAINTS.items():
        if table == "hithink_market_delta_15m":
            assert f"CONSTRAINT {constraint} CHECK node_seq != 254 OR" in ddl
            continue
        statement = (
            f"ALTER TABLE market.{table} ADD CONSTRAINT IF NOT EXISTS {constraint} "
            "CHECK node_seq != 254 OR"
        )
        assert statement in ddl
