from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Lock
from time import sleep as real_sleep

import pytest

import app.hithink.api as api_module
import app.hithink.raw_collector as raw_module
from app.hithink.api import (
    HithinkApiError,
    HithinkClient,
    LimitPoolSnapshot,
    SectorIndexItem,
    SectorIndexSnapshot,
    _EndpointRateLimiter,
)
from app.hithink.raw_collector import RawCollector
from app.hithink.runner import CollectorRunner
from app.hithink.schedule import SHANGHAI, build_daily_schedule


class Response:
    def __init__(self, payload: dict, *, status_code: int = 200, headers: dict | None = None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = str(payload)

    def json(self) -> dict:
        return self.payload


def success_snapshot() -> Response:
    return Response(
        {
            "code": 0,
            "data": {
                "total": 1,
                "timestamp": 1_787_814_000_000,
                "item": [{"thscode": "000001.SZ"}],
            },
        }
    )


def client_with_responses(responses: list[Response]) -> HithinkClient:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, *args, **kwargs) -> Response:
            response = responses[self.calls]
            self.calls += 1
            return response

    api = object.__new__(HithinkClient)
    api.client = Client()
    api._endpoint_limiters = {}
    return api


def test_429_uses_only_the_dedicated_short_retries_and_retry_after(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(api_module, "sleep", pauses.append)
    api = client_with_responses(
        [
            Response({"code": 429, "message": "request limit exceeded"}),
            Response(
                {"code": 429, "message": "request limit exceeded"},
                headers={"Retry-After": "0.25"},
            ),
            success_snapshot(),
        ]
    )

    with caplog.at_level(logging.WARNING):
        result = api.fetch()

    assert pauses == [1.0, 0.25]
    assert result.retry_count == 2
    assert api.client.calls == 3
    evidence = [record.message for record in caplog.records if "API_429_EVIDENCE" in record.message]
    assert len(evidence) == 2
    assert all("endpoint=all_a_snapshot" in line for line in evidence)
    assert all("http_status=200" in line for line in evidence)
    assert all("limiter_source=UPSTREAM" in line for line in evidence)


def test_third_429_is_classified_as_upstream_and_keeps_failure_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(api_module, "sleep", pauses.append)
    api = client_with_responses(
        [Response({"code": 429, "message": "request limit exceeded"}) for _ in range(3)]
    )

    with pytest.raises(HithinkApiError) as caught:
        api.fetch()

    error = caught.value
    assert pauses == [1.0, 2.0]
    assert error.code == 429
    assert error.error_code == "UPSTREAM_HTTP_429"
    assert error.endpoint == "all_a_snapshot"
    assert error.http_status == 200
    assert error.retry_index == 2
    assert error.limiter_source == "UPSTREAM"
    assert error.duration_ms is not None


def test_non_429_error_does_not_use_the_429_retry_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(api_module, "sleep", pauses.append)
    api = client_with_responses([Response({"code": 4004, "message": "bad request"})])

    with pytest.raises(HithinkApiError) as caught:
        api.fetch()

    assert caught.value.code == 4004
    assert pauses == []
    assert api.client.calls == 1


def test_all_a_limiter_serializes_itself_but_is_independent_from_sector() -> None:
    class Client:
        def __init__(self) -> None:
            self.lock = Lock()
            self.active = 0
            self.maximum = 0

        def get(self, *args, **kwargs) -> Response:
            with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            real_sleep(0.03)
            with self.lock:
                self.active -= 1
            return Response({"code": 0})

    api = object.__new__(HithinkClient)
    api.client = Client()
    api._endpoint_limiters = {
        "all_a_snapshot": _EndpointRateLimiter(0),
        "sector_index": _EndpointRateLimiter(0),
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(api._get, "all_a_snapshot", "https://example.test/all-a")
            for _ in range(2)
        ]
        for future in futures:
            future.result()
    assert api.client.maximum == 1

    api.client.maximum = 0
    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(api._get, "all_a_snapshot", "https://example.test/all-a")
        two = executor.submit(api._get, "sector_index", "https://example.test/sector")
        one.result()
        two.result()
    assert api.client.maximum == 2


class Writer:
    def __init__(self) -> None:
        self.statuses: list[dict] = []

    def active_sector_catalog(self):
        return [("S1", "板块", "concept", "source")]

    def set_schedule_status(self, node, status, **values):
        self.statuses.append({"status": status, **values})

    def insert_raw_once(self, rows):
        return len(rows), 1

    def insert_limit_up_pool(self, *args):
        return 0, 1

    def insert_limit_down_pool(self, *args):
        return 0, 1

    def insert_limit_break_pool(self, *args):
        return 0, 1

    def insert_sector_index_once(self, *args):
        return 1, 1


class PartialApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self):
        self.calls.append("all_a_snapshot")
        raise HithinkApiError(
            429,
            "request limit exceeded",
            error_code="UPSTREAM_HTTP_429",
            retry_index=2,
            duration_ms=3200,
        )

    def fetch_sector_index_snapshot(self, codes):
        self.calls.append("sector_index")
        source_time = datetime(2026, 8, 28, 10, 30, tzinfo=SHANGHAI)
        return SectorIndexSnapshot(
            total=1,
            items=[SectorIndexItem({"thscode": "S1"}, 1_787_814_000_000, source_time)],
            duration_ms=100,
            retry_count=0,
        )

    def fetch_limit_pool(self, name, trade_date):
        self.calls.append(name)
        source_time = datetime(2026, 8, 28, 10, 30, tzinfo=SHANGHAI)
        return LimitPoolSnapshot(
            code=0,
            source_timestamp=1_787_814_000_000,
            source_time=source_time,
            total=0,
            items=[],
            duration_ms=100,
            retry_count=0,
        )


def test_one_failed_interface_keeps_the_other_four_and_marks_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(raw_module, "sleep", pauses.append)
    node = build_daily_schedule(date(2026, 8, 28))[20]
    api = PartialApi()

    result = RawCollector(api, Writer()).collect(node, node.collection_id)

    assert api.calls == [
        "all_a_snapshot",
        "sector_index",
        "limit-up-pool",
        "limit-down-pool",
        "limit-break-pool",
    ]
    assert pauses == [0.3, 0.3, 0.3, 0.3]
    assert result.prices is None
    assert result.sector_index is not None
    assert result.limit_up is not None
    assert result.limit_down is not None
    assert result.limit_break is not None
    assert result.raw_status == "PARTIAL"
    assert result.errors["all_a_snapshot"][0] == "UPSTREAM_HTTP_429"
    failure = result.failures["all_a_snapshot"]
    assert isinstance(failure, HithinkApiError)
    assert failure.duration_ms == 3200

    runner = object.__new__(CollectorRunner)
    values = runner._raw_values(
        result,
        datetime(2026, 8, 28, 10, 30, tzinfo=SHANGHAI),
        datetime(2026, 8, 28, 10, 30, 4, tzinfo=SHANGHAI),
    )
    assert values["api_code"] == 429
    assert values["api_duration_ms"] == 3200
    assert values["retry_count"] == 2
    assert "all_a_snapshot:UPSTREAM_HTTP_429" in values["raw_error_code"]


class FailedApi(PartialApi):
    def fetch_sector_index_snapshot(self, codes):
        self.calls.append("sector_index")
        raise HithinkApiError(500, "sector failed", duration_ms=100)

    def fetch_limit_pool(self, name, trade_date):
        self.calls.append(name)
        raise HithinkApiError(500, f"{name} failed", duration_ms=100)


class StaticCollector:
    def __init__(self, result):
        self.result = result

    def collect(self, node, batch_id):
        return self.result


def test_node_is_failed_only_when_every_applicable_interface_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(raw_module, "sleep", lambda seconds: None)
    node = build_daily_schedule(date(2026, 8, 28))[20]
    result = RawCollector(FailedApi(), Writer()).collect(node, node.collection_id)
    assert result.raw_status == "FAILED"

    writer = Writer()
    runner = object.__new__(CollectorRunner)
    runner.writer = writer
    runner.raw_collector = StaticCollector(result)
    runner._run_node(node)

    assert writer.statuses[-1]["status"] == "FAILED"
    assert writer.statuses[-1]["raw_status"] == "FAILED"
    assert writer.statuses[-1]["derivation_status"] == "BLOCKED"


def test_node_is_partial_when_full_a_failed_but_other_interfaces_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(raw_module, "sleep", lambda seconds: None)
    node = build_daily_schedule(date(2026, 8, 28))[20]
    result = RawCollector(PartialApi(), Writer()).collect(node, node.collection_id)
    assert result.raw_status == "PARTIAL"

    writer = Writer()
    runner = object.__new__(CollectorRunner)
    runner.writer = writer
    runner.raw_collector = StaticCollector(result)
    runner._run_node(node)

    assert writer.statuses[-1]["status"] == "PARTIAL"
    assert writer.statuses[-1]["raw_status"] == "PARTIAL"
    assert writer.statuses[-1]["derivation_status"] == "BLOCKED"
