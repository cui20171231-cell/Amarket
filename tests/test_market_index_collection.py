from __future__ import annotations

from datetime import date, datetime

import pytest

import app.hithink.api as api_module
from app.hithink.api import (
    HithinkApiError,
    HithinkClient,
    SectorIndexItem,
    SectorIndexSnapshot,
)
from app.hithink.market_indices import MARKET_INDICES
from app.hithink.schedule import SHANGHAI, build_daily_schedule
from app.hithink.writer import ClickHouseWriter


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {}
        self.text = str(payload)

    def json(self):
        return self.payload


def _hithink_snapshot(count: int = 8) -> SectorIndexSnapshot:
    source_time = datetime(2026, 9, 4, 10, 7, 8, tzinfo=SHANGHAI)
    codes = [item.index_code for item in MARKET_INDICES[:count]]
    return SectorIndexSnapshot(
        total=len(codes),
        items=[
            SectorIndexItem(
                {
                    "thscode": code,
                    "last_price": 100.0,
                    "price_change_ratio_pct": 1.0,
                },
                1_788_500_828_000,
                source_time,
            )
            for code in codes
        ],
        duration_ms=50,
        retry_count=0,
    )


def test_fixed_market_index_catalog_is_exactly_eight() -> None:
    assert [item.index_code for item in MARKET_INDICES] == [
        "000001.SH",
        "000300.SH",
        "000852.SH",
        "000905.SH",
        "399006.SZ",
        "000688.SH",
        "000016.SH",
        "883957.TI",
    ]
    assert all(item.index_group == "BROAD_MARKET" for item in MARKET_INDICES)
    assert all(item.source_tag == "HITHINK_INDEX" for item in MARKET_INDICES)


def test_market_index_fetch_returns_one_complete_8_row_batch() -> None:
    api = object.__new__(HithinkClient)
    api.fetch_sector_index_snapshot = lambda *args, **kwargs: _hithink_snapshot()

    result = api.fetch_market_index_snapshot(date(2026, 9, 4), allow_retries=False)

    assert result.total == 8
    assert [item.index_code for item in result.items] == [
        item.index_code for item in MARKET_INDICES
    ]


def test_market_index_fetch_rejects_a_7_of_8_batch() -> None:
    api = object.__new__(HithinkClient)
    api.fetch_sector_index_snapshot = lambda *args, **kwargs: _hithink_snapshot(7)

    with pytest.raises(HithinkApiError, match="missing"):
        api.fetch_market_index_snapshot(date(2026, 9, 4), allow_retries=False)


def test_market_index_fetch_reuses_existing_429_retry_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success = {
        "code": 0,
        "data": {
            "total": 8,
            "timestamp": 1_788_500_828_000,
            "item": [
                {"thscode": definition.index_code}
                for definition in MARKET_INDICES
            ],
        },
    }

    class Client:
        def __init__(self):
            self.responses = [
                Response({"code": 429, "message": "request limit exceeded"}, 429),
                Response(success),
            ]
            self.calls = 0

        def get(self, url, **kwargs):
            response = self.responses[self.calls]
            self.calls += 1
            return response

    pauses = []
    monkeypatch.setattr(api_module, "sleep", pauses.append)
    api = object.__new__(HithinkClient)
    api.client = Client()
    api._endpoint_limiters = {}

    result = api.fetch_market_index_snapshot(date(2026, 9, 4))

    assert result.total == 8
    assert api.client.calls == 2
    assert pauses == [2.0]


def test_writer_persists_fixed_names_groups_sources_and_eight_rows() -> None:
    api = object.__new__(HithinkClient)
    api.fetch_sector_index_snapshot = lambda *args, **kwargs: _hithink_snapshot()
    snapshot = api.fetch_market_index_snapshot(date(2026, 9, 4), allow_retries=False)
    node = build_daily_schedule(date(2026, 9, 4))[20]
    writer = object.__new__(ClickHouseWriter)
    captured = {}

    def insert_once(table, rows, columns, batch_id=None):
        captured.update(table=table, rows=rows, columns=list(columns), batch_id=batch_id)
        return len(rows), 1

    writer._insert_once = insert_once
    inserted, _ = writer.insert_market_index_once(node, snapshot)

    assert inserted == 8
    assert captured["table"] == "market.hithink_market_index_snapshot"
    assert captured["batch_id"] == f"{node.collection_id}-market-index"
    code_at = captured["columns"].index("index_code")
    group_at = captured["columns"].index("index_group")
    source_at = captured["columns"].index("source_tag")
    assert [row[code_at] for row in captured["rows"]] == [
        item.index_code for item in MARKET_INDICES
    ]
    assert all(row[group_at] == "BROAD_MARKET" for row in captured["rows"])
    assert all(row[source_at] == "HITHINK_INDEX" for row in captured["rows"])
