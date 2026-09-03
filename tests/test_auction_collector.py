from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import app.hithink.auction_collector as auction_module
from app.hithink.api import AuctionApiSnapshot, HithinkClient
from app.hithink.auction_collector import (
    AuctionCollector,
    AuctionCollectorRunner,
    build_opening_auction_schedule,
)
from app.hithink.schedule import SHANGHAI


def test_opening_auction_schedule_has_11_exact_integer_minutes() -> None:
    nodes = build_opening_auction_schedule(date(2026, 9, 4))

    assert len(nodes) == 11
    assert [node.sequence_no for node in nodes] == list(range(1, 12))
    assert [
        node.scheduled_time.timetz().replace(tzinfo=None) for node in nodes
    ] == [time(9, minute) for minute in range(15, 26)]
    assert nodes[0].collection_id == "20260904001"
    assert nodes[-1].collection_id == "20260904011"


def test_api_auction_snapshot_preserves_the_requested_batch(monkeypatch) -> None:
    api = object.__new__(HithinkClient)
    captured: dict[str, object] = {}
    codes = ["600519.SH", "000001.SZ"]
    payload = {
        "code": 0,
        "message": "success",
        "request_id": "request-1",
        "data": {
            "timestamp": 1788484500000,
            "auction_phase": "order_entry",
            "data_status": "live",
            "total": 2,
            "item": [
                {"thscode": code, "ticker": code[:6], "name": code}
                for code in codes
            ],
        },
    }

    def request_json(endpoint, url, **kwargs):
        captured.update(endpoint=endpoint, url=url, **kwargs)
        return SimpleNamespace(status_code=200, text=""), payload, 0

    monkeypatch.setattr(api, "_request_json", request_json)
    result = api.fetch_auction_snapshot(codes, "live", allow_retries=False)

    assert result.total == 2
    assert result.request_id == "request-1"
    assert captured["params"] == {"thscodes": "600519.SH,000001.SZ", "stage": "live"}


def test_full_market_auction_collection_uses_100_stock_concurrent_batches() -> None:
    trade_date = date(2026, 9, 4)
    calls: list[tuple[list[str], str]] = []

    class Api:
        def fetch_auction_snapshot(self, codes, stage, **_kwargs):
            calls.append((codes, stage))
            now = datetime(2026, 9, 4, 9, 15, 1, tzinfo=SHANGHAI)
            return AuctionApiSnapshot(
                code=0,
                message="success",
                request_id=f"request-{codes[0]}",
                source_timestamp=1788484501000,
                source_time=now,
                auction_phase="order_entry",
                data_status="live",
                total=len(codes),
                items=[
                    {"thscode": code, "ticker": code[:6], "name": code}
                    for code in codes
                ],
                request_started_at=now,
                request_ended_at=now,
                duration_ms=10,
                retry_count=0,
            )

    class Writer:
        def __init__(self):
            self.snapshots = []
            self.values = {}

        def insert_auction_snapshot_once(self, node, snapshots, **values):
            self.snapshots = snapshots
            self.values = values
            return sum(snapshot.total for snapshot in snapshots), 2

    writer = Writer()
    codes = [f"{index:06d}.SZ" for index in range(205)]
    node = build_opening_auction_schedule(trade_date)[0]
    result = AuctionCollector(Api(), writer).collect(node, codes)

    assert sorted(len(batch) for batch, _stage in calls) == [5, 100, 100]
    assert {stage for _batch, stage in calls} == {"live"}
    assert result.status == "SUCCESS"
    assert result.received_count == 205
    assert result.inserted_count == 205
    assert writer.values["batch_id"] == "20260904001-auction"


def test_0925_collection_uses_final_stage() -> None:
    calls: list[str] = []

    class Api:
        def fetch_auction_snapshot(self, codes, stage, **_kwargs):
            calls.append(stage)
            now = datetime(2026, 9, 4, 9, 25, 1, tzinfo=SHANGHAI)
            return AuctionApiSnapshot(
                0,
                "success",
                "request",
                1788485101000,
                now,
                "matched",
                "final",
                1,
                [{"thscode": codes[0], "ticker": codes[0][:6], "name": "stock"}],
                now,
                now,
                10,
                0,
            )

    class Writer:
        def insert_auction_snapshot_once(self, _node, snapshots, **_values):
            return sum(snapshot.total for snapshot in snapshots), 1

    node = build_opening_auction_schedule(date(2026, 9, 4))[-1]
    AuctionCollector(Api(), Writer()).collect(node, ["600519.SH"])

    assert calls == ["final"]


def test_0850_universe_is_refreshed_at_091350(monkeypatch) -> None:
    trade_date = date(2026, 9, 4)
    current = {"value": datetime(2026, 9, 4, 8, 50, tzinfo=SHANGHAI)}

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current["value"]

    class Api:
        def __init__(self):
            self.calls = 0

        def fetch(self, **_kwargs):
            self.calls += 1
            code = "600000.SH" if self.calls == 1 else "001234.SZ"
            return SimpleNamespace(items=[{"thscode": code}])

    def advance(seconds: float) -> None:
        current["value"] += timedelta(seconds=seconds)

    monkeypatch.setattr(auction_module, "datetime", FixedDateTime)
    monkeypatch.setattr(auction_module, "sleep", advance)
    api = Api()
    runner = AuctionCollectorRunner(api, SimpleNamespace())

    assert runner._prepare_universe(trade_date) == ["001234.SZ"]
    assert api.calls == 2
    assert current["value"] == datetime(2026, 9, 4, 9, 13, 50, tzinfo=SHANGHAI)


def test_091350_failure_falls_back_to_previous_close(monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 4, 9, 13, 50, tzinfo=SHANGHAI)

    class Api:
        @staticmethod
        def fetch(**_kwargs):
            raise RuntimeError("temporary API failure")

    class Writer:
        @staticmethod
        def latest_all_a_codes_before(_trade_date):
            return ["600000.SH"]

    monkeypatch.setattr(auction_module, "datetime", FixedDateTime)
    runner = AuctionCollectorRunner(Api(), Writer())

    assert runner._prepare_universe(date(2026, 9, 4)) == ["600000.SH"]


def test_schema_includes_auction_table() -> None:
    ddl = Path("sql/hithink_snapshot.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS market.hithink_auction_snapshot" in ddl
