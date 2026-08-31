from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.hithink.schedule import build_daily_schedule
from app.hithink.writer import ClickHouseWriter, _split_sql_statements


def test_split_sql_ignores_semicolons_inside_strings_and_comments() -> None:
    sql = """
    CREATE TABLE example
    (
        note String COMMENT 'first; second'
    );
    -- a comment; is not a statement boundary
    ALTER TABLE example ADD COLUMN label String DEFAULT 'it''s; valid';
    /* another; comment */
    SELECT 1;
    """

    statements = _split_sql_statements(sql)

    assert len(statements) == 3
    assert "'first; second'" in statements[0]
    assert "'it''s; valid'" in statements[1]
    assert statements[2].endswith("SELECT 1")


def test_initialize_schema_sends_complete_quoted_comment(tmp_path: Path) -> None:
    schema = tmp_path / "schema.sql"
    schema.write_text(
        "CREATE TABLE example (value UInt16 COMMENT 'compatibility; fixed at 0');",
        encoding="utf-8",
    )
    commands: list[str] = []
    writer = object.__new__(ClickHouseWriter)
    writer.client = type("Client", (), {"command": staticmethod(commands.append)})()

    writer.initialize_schema(schema)

    assert commands == [
        "CREATE TABLE example (value UInt16 COMMENT 'compatibility; fixed at 0')"
    ]


class ScheduleQueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class ScheduleClient:
    def __init__(self, rows):
        self.rows = list(rows)
        self.inserted = []

    def query(self, sql, parameters=None):
        return ScheduleQueryResult(self.rows)

    def insert(self, table, rows, column_names):
        self.inserted.extend(rows)
        self.rows.extend(
            (collection_id, scheduled_time, session, sequence_no)
            for _, collection_id, scheduled_time, session, sequence_no, _ in rows
        )


def _schedule_row(node):
    return node.collection_id, node.scheduled_time, node.session, node.sequence_no


def test_initialize_schedule_fills_only_missing_nodes() -> None:
    nodes = build_daily_schedule(date(2026, 9, 1))
    client = ScheduleClient([_schedule_row(node) for node in nodes[:-1]])
    writer = object.__new__(ClickHouseWriter)
    writer.client = client

    writer.initialize_schedule(nodes)

    assert len(client.inserted) == 1
    assert client.inserted[0][1] == nodes[-1].collection_id
    assert len(client.rows) == 254


def test_initialize_schedule_accepts_fixed_string_bytes_from_database() -> None:
    nodes = build_daily_schedule(date(2026, 9, 1))
    rows = [
        (
            node.collection_id.encode("ascii"),
            node.scheduled_time,
            node.session,
            node.sequence_no,
        )
        for node in nodes
    ]
    client = ScheduleClient(rows)
    writer = object.__new__(ClickHouseWriter)
    writer.client = client

    writer.initialize_schedule(nodes)

    assert client.inserted == []


def test_initialize_schedule_rejects_wrong_existing_identity() -> None:
    nodes = build_daily_schedule(date(2026, 9, 1))
    rows = [_schedule_row(node) for node in nodes]
    collection_id, scheduled_time, session, _ = rows[10]
    rows[10] = (collection_id, scheduled_time, session, 99)
    writer = object.__new__(ClickHouseWriter)
    writer.client = ScheduleClient(rows)

    with pytest.raises(RuntimeError, match="编号或时段不匹配"):
        writer.initialize_schedule(nodes)
