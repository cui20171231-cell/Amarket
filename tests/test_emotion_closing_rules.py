from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

import app.hithink.raw_collector as raw_module
import app.hithink.runner as runner_module
from app.hithink.api import ApiSnapshot, LimitPoolSnapshot, SectorIndexSnapshot
from app.hithink.raw_collector import RawCollectionResult, RawCollector
from app.hithink.runner import CollectorRunner
from app.hithink.schedule import build_daily_schedule
from app.hithink.writer import ClickHouseWriter


def closing_node(trade_date: date = date(2026, 8, 28)):
    return build_daily_schedule(trade_date)[-1]


def pool_snapshot(source_time: datetime) -> LimitPoolSnapshot:
    return LimitPoolSnapshot(
        code=0,
        source_timestamp=1_788_000_000_000,
        source_time=source_time,
        total=0,
        items=[],
        duration_ms=10,
        retry_count=0,
    )


def writer_with_pool_source(source):
    writer = object.__new__(ClickHouseWriter)
    writer.latest_complete_pool_source = lambda node: source
    return writer


def test_254_all_three_pools_success_is_current() -> None:
    node = closing_node()
    source = (node.collection_id, node.trade_date, node.scheduled_time)

    resolved, status = writer_with_pool_source(source).emotion_pool_context(node)

    assert resolved == source
    assert status == "CURRENT"


def test_254_one_pool_failure_never_becomes_fallback() -> None:
    node = closing_node()
    prior = build_daily_schedule(node.trade_date)[-5]
    writer = writer_with_pool_source(
        (prior.collection_id, prior.trade_date, prior.scheduled_time)
    )

    with pytest.raises(RuntimeError, match="requires its own three successful pools"):
        writer.emotion_pool_context(node)


def test_254_retry_failure_then_success_finishes_with_all_three_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = closing_node()
    pauses: list[float] = []
    monkeypatch.setattr(raw_module, "sleep", pauses.append)

    class Api:
        calls = 0

        def fetch_limit_pool(self, endpoint, trade_date):
            assert endpoint == "limit-up-pool"
            assert trade_date == node.trade_date
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary closing-pool failure")
            return pool_snapshot(node.scheduled_time)

    class Writer:
        def __init__(self) -> None:
            self.statuses: list[dict] = []

        def set_schedule_status(self, current_node, status, **values):
            assert current_node == node
            self.statuses.append({"status": status, **values})

        def insert_limit_up_pool(self, *args):
            return None

        def insert_limit_down_pool(self, *args):
            return None

        def insert_limit_break_pool(self, *args):
            return None

    api = Api()
    writer = Writer()
    existing = pool_snapshot(node.scheduled_time)

    limit_up, limit_down, limit_break = RawCollector(
        api, writer
    ).collect_closing_pools_until_success(
        node,
        node.collection_id,
        limit_down=existing,
        limit_break=existing,
    )

    assert api.calls == 2
    assert pauses == [raw_module.CLOSING_POOL_RETRY_SECONDS]
    assert limit_up.source_time == node.scheduled_time
    assert limit_down is existing
    assert limit_break is existing
    assert writer.statuses[-1]["limit_pool_collected"] == 1
    assert writer.statuses[-1]["emotion_state_status"] == "PENDING"
    source = (node.collection_id, node.trade_date, node.scheduled_time)
    _, final_status = writer_with_pool_source(source).emotion_pool_context(node)
    assert final_status == "CURRENT"
    assert all(status.get("pool_data_status") != "FALLBACK" for status in writer.statuses)


def test_254_retries_all_a_and_sector_index_until_both_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = closing_node()
    pauses: list[float] = []
    monkeypatch.setattr(raw_module, "sleep", pauses.append)

    class Api:
        price_calls = 0
        sector_calls = 0

        def fetch(self):
            self.price_calls += 1
            if self.price_calls == 1:
                raise RuntimeError("temporary all-A failure")
            return ApiSnapshot(0, 1, node.scheduled_time, 0, [], 10, 0)

        def fetch_sector_index_snapshot(self, codes):
            assert codes == ["S1"]
            self.sector_calls += 1
            if self.sector_calls == 1:
                raise RuntimeError("temporary sector failure")
            return SectorIndexSnapshot(0, [], 10, 0)

    class Writer:
        def __init__(self) -> None:
            self.statuses: list[dict] = []

        def set_schedule_status(self, current_node, status, **values):
            self.statuses.append({"status": status, **values})

        def active_sector_catalog(self):
            return [("S1", "板块", "concept", "source")]

        def insert_raw_once(self, rows):
            return len(rows), 1

        def insert_sector_index_once(self, *args):
            return 0, 1

        def insert_limit_up_pool(self, *args):
            return None

        def insert_limit_down_pool(self, *args):
            return None

        def insert_limit_break_pool(self, *args):
            return None

    pool = pool_snapshot(node.scheduled_time)
    initial = RawCollectionResult(
        node=node,
        batch_id=node.collection_id,
        prices=None,
        limit_up=pool,
        limit_down=pool,
        limit_break=pool,
        sector_index=None,
        sectors=[],
        raw_rows=[],
        raw_insert_count=0,
        raw_insert_ms=0,
        sector_index_insert_count=0,
        sector_index_insert_ms=0,
        errors={
            "all_a_snapshot": ("TEMP", "failed"),
            "sector_index": ("TEMP", "failed"),
        },
        failures={},
        not_applicable=frozenset(),
    )
    api = Api()
    writer = Writer()

    completed = RawCollector(api, writer).complete_closing_node_until_success(
        node, node.collection_id, initial
    )

    assert api.price_calls == 2
    assert api.sector_calls == 2
    assert pauses == [raw_module.CLOSING_POOL_RETRY_SECONDS]
    assert completed.prices is not None
    assert completed.sector_index is not None
    assert completed.raw_status == "SUCCESS"
    assert completed.errors == {}
    assert any(status.get("all_a_snapshot_status") == "SUCCESS" for status in writer.statuses)
    assert any(status.get("sector_index_status") == "SUCCESS" for status in writer.statuses)


def test_254_whole_node_retries_until_final_status_is_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = closing_node()
    pauses: list[float] = []
    calls: list[str] = []

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 28, 15, 1, tzinfo=tz)

    monkeypatch.setattr(runner_module, "datetime", FixedDateTime)
    monkeypatch.setattr(runner_module, "sleep", pauses.append)

    class Writer:
        def statuses(self, trade_date):
            final = "SUCCESS" if len(calls) >= 2 else "PARTIAL"
            return {node.scheduled_time: final}

    runner = object.__new__(CollectorRunner)
    runner.writer = Writer()
    runner.run_node = lambda current_node: calls.append(current_node.collection_id)

    runner._run_closing_node_until_success(node)

    assert calls == [node.collection_id, node.collection_id]
    assert pauses == [runner_module.CLOSING_NODE_RETRY_SECONDS]


def test_254_retry_stops_at_1600_after_one_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = closing_node()
    calls: list[str] = []

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 28, 16, 0, tzinfo=tz)

    monkeypatch.setattr(runner_module, "datetime", FixedDateTime)
    runner = object.__new__(CollectorRunner)
    runner.run_node = lambda current_node: calls.append(current_node.collection_id)

    runner._run_closing_node_until_success(node)

    assert calls == []


def test_254_database_constraint_requires_own_collection_id() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")

    assert "CONSTRAINT ck_emotion_closing_current" in ddl
    assert "pool_source_collection_id = collection_id" in ddl
    assert "limit_up_source_collection_id = collection_id" in ddl
    assert "limit_down_source_collection_id = collection_id" in ddl
    assert "limit_break_source_collection_id = collection_id" in ddl


def test_254_database_constraint_requires_zero_age_and_no_fallback() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")

    assert "pool_source_age_seconds = 0" in ddl
    assert "pool_is_fallback = 0" in ddl
    assert "limit_up_is_fallback = 0" in ddl
    assert "limit_down_is_fallback = 0" in ddl
    assert "limit_break_is_fallback = 0" in ddl


class QueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class CapturingClient:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.parameters = {}

    def query(self, sql, parameters=None):
        self.sql = sql
        self.parameters = parameters or {}
        return QueryResult(self.rows)


def test_next_day_baseline_reads_previous_254_current() -> None:
    previous_node = closing_node(date(2026, 8, 27))
    client = CapturingClient(
        [
            (
                previous_node.collection_id,
                previous_node.trade_date,
                previous_node.scheduled_time,
            )
        ]
    )
    writer = object.__new__(ClickHouseWriter)
    writer.client = client
    writer.previous_trading_day = lambda trade_date: previous_node.trade_date

    source = writer.previous_closing_pool_source(date(2026, 8, 28))

    assert source == (
        previous_node.collection_id,
        previous_node.trade_date,
        previous_node.scheduled_time,
    )
    assert "sequence_no = 254" in client.sql
    assert "node_seq = 254" in client.sql
    assert "pool_data_status = 'CURRENT'" in client.sql


def test_missing_previous_254_never_silently_uses_1456() -> None:
    client = CapturingClient([])
    writer = object.__new__(ClickHouseWriter)
    writer.client = client
    writer.previous_trading_day = lambda trade_date: date(2026, 8, 27)

    source = writer.previous_closing_pool_source(date(2026, 8, 28))

    assert source is None
    assert "sequence_no = 254" in client.sql
    assert "scheduled_time <" not in client.sql
    assert "ORDER BY scheduled_time DESC" not in client.sql
