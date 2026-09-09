from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Lock
from time import monotonic
from time import sleep as real_sleep

import pytest

import app.hithink.api as api_module
import app.hithink.raw_collector as raw_module
from app.hithink.aggregation import MarketAggregationPipeline
from app.hithink.api import (
    HithinkApiError,
    HithinkClient,
    LimitPoolSnapshot,
    MarketIndexSnapshot,
    SectorIndexItem,
    SectorIndexSnapshot,
    _EndpointRateLimiter,
)
from app.hithink.post_derivation import PostDerivationPipeline
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


def test_429_uses_dedicated_retries_and_honors_retry_after(
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

    assert pauses == [2.0, 0.25]
    assert result.retry_count == 2
    assert api.client.calls == 3
    evidence = [record.message for record in caplog.records if "API_429_EVIDENCE" in record.message]
    assert len(evidence) == 2
    assert all("endpoint=all_a_snapshot" in line for line in evidence)
    assert all("http_status=200" in line for line in evidence)
    assert all("limiter_source=UPSTREAM" in line for line in evidence)


def test_fourth_429_is_classified_as_upstream_and_keeps_failure_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(api_module, "sleep", pauses.append)
    api = client_with_responses(
        [Response({"code": 429, "message": "request limit exceeded"}) for _ in range(4)]
    )

    with pytest.raises(HithinkApiError) as caught:
        api.fetch()

    error = caught.value
    assert pauses == [2.0, 4.0, 8.0]
    assert error.code == 429
    assert error.error_code == "UPSTREAM_HTTP_429"
    assert error.endpoint == "all_a_snapshot"
    assert error.http_status == 200
    assert error.retry_index == 3
    assert error.limiter_source == "UPSTREAM"
    assert error.duration_ms is not None


def test_429_stops_before_the_30_second_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(api_module, "sleep", pauses.append)
    api = client_with_responses(
        [Response({"code": 429, "message": "request limit exceeded"})]
    )

    with pytest.raises(HithinkApiError) as caught:
        api.fetch(rate_limit_deadline=monotonic() + 0.01)

    assert api.client.calls == 1
    assert pauses == []
    assert caught.value.code == 429
    assert caught.value.error_code == "UPSTREAM_HTTP_429"
    assert "30-second rate-limit deadline reached" in str(caught.value)


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


def test_retryable_5003_stops_after_two_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(api_module, "sleep", pauses.append)
    api = client_with_responses(
        [
            Response(
                {
                    "code": 5003,
                    "message": "hotspot_focus pool response missing required pagination",
                }
            )
            for _ in range(3)
        ]
    )

    with pytest.raises(HithinkApiError) as caught:
        api.fetch()

    assert api.client.calls == 3
    assert pauses == [1.0, 2.0]
    assert caught.value.code == 5003
    assert caught.value.retry_index == 2


def test_expired_node_deadline_starts_no_request() -> None:
    api = client_with_responses([success_snapshot()])

    with pytest.raises(HithinkApiError) as caught:
        api.fetch(deadline=monotonic() - 1)

    assert caught.value.error_code == "NODE_DEADLINE_EXCEEDED"
    assert api.client.calls == 0


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

    def insert_market_index_once(self, *args):
        return 8, 1

    def upsert_emotion_state(self, node):
        return None

    def emotion_state_count(self, node):
        return 1


class PartialApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, *, deadline=None, rate_limit_deadline=None):
        self.calls.append("all_a_snapshot")
        raise HithinkApiError(
            429,
            "request limit exceeded",
            error_code="UPSTREAM_HTTP_429",
            retry_index=2,
            duration_ms=3200,
        )

    def fetch_sector_index_snapshot(
        self, codes, *, deadline=None, rate_limit_deadline=None
    ):
        self.calls.append("sector_index")
        source_time = datetime(2026, 8, 28, 10, 30, tzinfo=SHANGHAI)
        return SectorIndexSnapshot(
            total=1,
            items=[SectorIndexItem({"thscode": "S1"}, 1_787_814_000_000, source_time)],
            duration_ms=100,
            retry_count=0,
        )

    def fetch_market_index_snapshot(
        self, trade_date, *, deadline=None, rate_limit_deadline=None, allow_retries=True
    ):
        self.calls.append("market_index")
        return MarketIndexSnapshot(total=8, items=[], duration_ms=100, retry_count=0)

    def fetch_limit_pool(
        self, name, trade_date, *, deadline=None, rate_limit_deadline=None
    ):
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
    worker_counts: list[int] = []

    def executor(*args, **kwargs):
        worker_counts.append(int(kwargs["max_workers"]))
        return ThreadPoolExecutor(*args, **kwargs)

    monkeypatch.setattr(raw_module, "sleep", pauses.append)
    monkeypatch.setattr(raw_module, "uniform", lambda low, high: 1.0)
    monkeypatch.setattr(raw_module, "ThreadPoolExecutor", executor)
    node = build_daily_schedule(date(2026, 8, 28))[20]
    api = PartialApi()

    result = RawCollector(api, Writer()).collect(node, node.collection_id)

    assert api.calls == [
        "all_a_snapshot",
        "market_index",
        "sector_index",
        "limit-up-pool",
        "limit-down-pool",
        "limit-break-pool",
    ]
    assert pauses == [1.0, 1.0, 1.0, 1.0, 1.0]
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
    assert worker_counts == [6]
    assert raw_module.NODE_COLLECTION_BUDGET_SECONDS < 60


def test_market_index_failure_does_not_block_existing_raw_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MarketIndexFailedApi(PartialApi):
        def fetch(self, *, deadline=None, rate_limit_deadline=None):
            self.calls.append("all_a_snapshot")
            source_time = datetime(2026, 8, 28, 10, 30, tzinfo=SHANGHAI)
            return api_module.ApiSnapshot(0, 1_787_814_000_000, source_time, 0, [], 1, 0)

        def fetch_market_index_snapshot(
            self,
            trade_date,
            *,
            deadline=None,
            rate_limit_deadline=None,
            allow_retries=True,
        ):
            self.calls.append("market_index")
            raise HithinkApiError(
                None,
                "market indices unavailable",
                error_code="MARKET_INDEX_BATCH_INCOMPLETE",
            )

    monkeypatch.setattr(raw_module, "sleep", lambda seconds: None)
    node = build_daily_schedule(date(2026, 8, 28))[20]
    writer = Writer()

    result = RawCollector(MarketIndexFailedApi(), writer).collect(
        node, node.collection_id
    )

    assert result.market_index is None
    assert result.raw_status == "SUCCESS"
    assert result.errors["market_index"][0] == "MARKET_INDEX_BATCH_INCOMPLETE"
    assert any(status.get("market_index_status") == "FAILED" for status in writer.statuses)


def test_145653_node_disables_all_api_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingApi(PartialApi):
        def __init__(self) -> None:
            super().__init__()
            self.options: list[bool] = []

        def fetch(self, *, deadline=None, rate_limit_deadline=None, allow_retries=True):
            self.options.append(allow_retries)
            return super().fetch(deadline=deadline, rate_limit_deadline=rate_limit_deadline)

        def fetch_sector_index_snapshot(
            self, codes, *, deadline=None, rate_limit_deadline=None, allow_retries=True
        ):
            self.options.append(allow_retries)
            return super().fetch_sector_index_snapshot(
                codes, deadline=deadline, rate_limit_deadline=rate_limit_deadline
            )

        def fetch_market_index_snapshot(
            self,
            trade_date,
            *,
            deadline=None,
            rate_limit_deadline=None,
            allow_retries=True,
        ):
            self.options.append(allow_retries)
            return super().fetch_market_index_snapshot(
                trade_date,
                deadline=deadline,
                rate_limit_deadline=rate_limit_deadline,
                allow_retries=allow_retries,
            )

        def fetch_limit_pool(
            self,
            name,
            trade_date,
            *,
            deadline=None,
            rate_limit_deadline=None,
            allow_retries=True,
        ):
            self.options.append(allow_retries)
            return super().fetch_limit_pool(
                name,
                trade_date,
                deadline=deadline,
                rate_limit_deadline=rate_limit_deadline,
            )

    monkeypatch.setattr(raw_module, "sleep", lambda seconds: None)
    node = build_daily_schedule(date(2026, 9, 2))[249]
    api = RecordingApi()

    RawCollector(api, Writer()).collect(node, node.collection_id)

    assert node.scheduled_time.strftime("%H:%M:%S") == "14:56:53"
    assert api.options == [False, False, False, False, False, False]


def test_api_no_retry_mode_stops_after_first_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(api_module, "sleep", pauses.append)
    api = client_with_responses(
        [Response({"code": 429, "message": "request limit exceeded"})]
    )

    with pytest.raises(HithinkApiError):
        api.fetch(allow_retries=False)

    assert api.client.calls == 1
    assert pauses == []


class FailedApi(PartialApi):
    def fetch_sector_index_snapshot(
        self, codes, *, deadline=None, rate_limit_deadline=None
    ):
        self.calls.append("sector_index")
        raise HithinkApiError(500, "sector failed", duration_ms=100)

    def fetch_limit_pool(
        self, name, trade_date, *, deadline=None, rate_limit_deadline=None
    ):
        self.calls.append(name)
        raise HithinkApiError(500, f"{name} failed", duration_ms=100)


class StaticCollector:
    def __init__(self, result):
        self.result = result

    def collect(self, node, batch_id):
        return self.result


class RaisingCollector:
    def collect(self, node, batch_id):
        raise HithinkApiError(5003, "temporary upstream structure error")


def test_unhandled_raw_failure_closes_every_child_status() -> None:
    node = build_daily_schedule(date(2026, 8, 28))[0]
    writer = Writer()
    runner = object.__new__(CollectorRunner)
    runner.writer = writer
    runner.raw_collector = RaisingCollector()
    runner.post_deriver = PostDerivationPipeline(writer)
    runner.aggregator = MarketAggregationPipeline(writer)

    runner._run_node(node)

    final = writer.statuses[-1]
    assert final["status"] == "FAILED"
    assert final["raw_status"] == "FAILED"
    assert final["all_a_snapshot_status"] == "FAILED"
    assert final["limit_up_pool_status"] == "SKIPPED"
    assert final["limit_down_pool_status"] == "SKIPPED"
    assert final["limit_break_pool_status"] == "SKIPPED"
    assert final["market_index_status"] == "FAILED"
    assert final["sector_index_status"] == "FAILED"
    assert final["sector_state_status"] == "BLOCKED"
    assert all(value != "RUNNING" for key, value in final.items() if key.endswith("_status"))


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
    runner.post_deriver = PostDerivationPipeline(writer)
    runner.aggregator = MarketAggregationPipeline(writer)
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
    runner.post_deriver = PostDerivationPipeline(writer)
    runner.aggregator = MarketAggregationPipeline(writer)
    runner._run_node(node)

    assert writer.statuses[-1]["status"] == "PARTIAL"
    assert writer.statuses[-1]["raw_status"] == "PARTIAL"
    assert writer.statuses[-1]["derivation_status"] == "BLOCKED"
