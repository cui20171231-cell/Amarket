from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic, perf_counter, sleep
from typing import Any

import httpx

from app.hithink.schedule import SHANGHAI

URL = "https://fuyao.aicubes.cn/api/a-share/prices/snapshot?limit=10000&offset=0"
TRADING_DAYS_URL = "https://fuyao.aicubes.cn/api/a-share/calendar/trading-days"
LIMIT_POOL_URL = "https://fuyao.aicubes.cn/api/a-share/special-data/{pool_name}"
SECTOR_INDEX_SNAPSHOT_URL = "https://fuyao.aicubes.cn/api/a-share-index/prices/snapshot"
DUMP_URLS = {
    "daily_k": "https://fuyao.aicubes.cn/api/dump/market-dumps/daily-k/download-url",
    "daily_k_10d": "https://fuyao.aicubes.cn/api/dump/market-dumps/daily-k-10d/download-url",
    "adjustment_factors": "https://fuyao.aicubes.cn/api/dump/market-dumps/adjustment-factors/download-url",
}
RETRYABLE_CODES = {4001, 5001, 5002, 5003}
REQUEST_TIMEOUT_SECONDS = 10.0
RETRY_PAUSES_SECONDS = (0.0, 1.0, 2.0)
LOG = logging.getLogger(__name__)


class HithinkApiError(RuntimeError):
    def __init__(
        self,
        code: int | None,
        message: str,
        *,
        error_code: str | None = None,
        endpoint: str | None = None,
        url: str | None = None,
        http_status: int | None = None,
        response_summary: str | None = None,
        retry_after: str | None = None,
        request_started_at: datetime | None = None,
        request_ended_at: datetime | None = None,
        retry_index: int = 0,
        limiter_source: str | None = None,
        duration_ms: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.error_code = error_code or (str(code) if code is not None else "API_ERROR")
        self.endpoint = endpoint
        self.url = url
        self.http_status = http_status
        self.response_summary = response_summary
        self.retry_after = retry_after
        self.request_started_at = request_started_at
        self.request_ended_at = request_ended_at
        self.retry_index = retry_index
        self.limiter_source = limiter_source
        self.duration_ms = duration_ms


class _EndpointRateLimiter:
    """Serialize one endpoint and keep at least one second between request starts."""

    def __init__(self, minimum_interval_seconds: float = 1.0):
        self.minimum_interval_seconds = minimum_interval_seconds
        self._lock = Lock()
        self._last_started = 0.0

    @contextmanager
    def slot(self, endpoint: str):
        with self._lock:
            wait_seconds = max(
                0.0,
                self.minimum_interval_seconds - (monotonic() - self._last_started),
            )
            if wait_seconds:
                LOG.info(
                    "API_LOCAL_RATE_LIMIT endpoint=%s code=LOCAL_RATE_LIMIT "
                    "wait_ms=%s limiter_source=LOCAL",
                    endpoint,
                    round(wait_seconds * 1000),
                )
                sleep(wait_seconds)
            self._last_started = monotonic()
            yield


@dataclass(frozen=True)
class ApiSnapshot:
    code: int
    source_timestamp: int
    source_time: datetime
    total: int
    items: list[dict[str, Any]]
    duration_ms: int
    retry_count: int


@dataclass(frozen=True)
class TradingCalendar:
    trade_dates: set[date]
    duration_ms: int
    retry_count: int


@dataclass(frozen=True)
class DumpUrl:
    url: str
    duration_ms: int


@dataclass(frozen=True)
class LimitPoolItem:
    item: dict[str, Any]
    source_timestamp: int
    source_time: datetime


@dataclass(frozen=True)
class LimitPoolSnapshot:
    code: int
    source_timestamp: int
    source_time: datetime
    total: int
    items: list[LimitPoolItem]
    duration_ms: int
    retry_count: int


@dataclass(frozen=True)
class SectorIndexItem:
    item: dict[str, Any]
    source_timestamp: int
    source_time: datetime


@dataclass(frozen=True)
class SectorIndexSnapshot:
    total: int
    items: list[SectorIndexItem]
    duration_ms: int
    retry_count: int


class HithinkClient:
    def __init__(self, api_key: str):
        self.client = httpx.Client(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=5.0),
            headers={"X-api-key": api_key},
        )
        self._active_request_lock = Lock()
        self._active_request_count = 0
        self._endpoint_limiters = {
            "all_a_snapshot": _EndpointRateLimiter(1.0),
            "sector_index": _EndpointRateLimiter(1.0),
        }

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _deadline_error(endpoint: str) -> HithinkApiError:
        return HithinkApiError(
            None,
            f"{endpoint} collection deadline exceeded",
            error_code="NODE_DEADLINE_EXCEEDED",
            endpoint=endpoint,
        )

    @classmethod
    def _sleep_before_retry(
        cls, seconds: float, endpoint: str, deadline: float | None
    ) -> None:
        if not seconds:
            return
        if deadline is not None and deadline - monotonic() <= seconds:
            raise cls._deadline_error(endpoint)
        sleep(seconds)

    @classmethod
    def _request_timeout(
        cls, endpoint: str, deadline: float | None
    ) -> httpx.Timeout | None:
        if deadline is None:
            return None
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise cls._deadline_error(endpoint)
        total = min(REQUEST_TIMEOUT_SECONDS, remaining)
        return httpx.Timeout(total, connect=min(5.0, total))

    def _get(
        self,
        endpoint: str,
        url: str,
        *,
        deadline: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue one HTTP request and log the real in-flight request count."""
        if not hasattr(self, "_active_request_lock"):
            self._active_request_lock = Lock()
            self._active_request_count = 0
        if not hasattr(self, "_endpoint_limiters"):
            self._endpoint_limiters = {
                "all_a_snapshot": _EndpointRateLimiter(1.0),
                "sector_index": _EndpointRateLimiter(1.0),
            }

        def send() -> httpx.Response:
            with self._active_request_lock:
                self._active_request_count += 1
                active = self._active_request_count
            started = perf_counter()
            LOG.info("API_REQUEST_START endpoint=%s active=%s", endpoint, active)
            try:
                timeout = self._request_timeout(endpoint, deadline)
                if timeout is not None:
                    kwargs["timeout"] = timeout
                return self.client.get(url, **kwargs)
            finally:
                duration_ms = round((perf_counter() - started) * 1000)
                with self._active_request_lock:
                    self._active_request_count -= 1
                    active = self._active_request_count
                LOG.info(
                    "API_REQUEST_END endpoint=%s active=%s duration_ms=%s",
                    endpoint,
                    active,
                    duration_ms,
                )

        limiter = self._endpoint_limiters.get(endpoint)
        if limiter is None:
            return send()
        with limiter.slot(endpoint):
            return send()

    @staticmethod
    def _retry_after_seconds(raw: str | None, ended_at: datetime) -> float | None:
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=SHANGHAI)
                return max(0.0, (parsed - ended_at).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    def _request_json(
        self,
        endpoint: str,
        url: str,
        *,
        deadline: float | None = None,
        **kwargs: Any,
    ) -> tuple[httpx.Response, dict[str, Any], int]:
        """Retry only upstream 429 responses with the dedicated 1s/2s policy."""
        rate_limit_started = perf_counter()
        for retry_index in range(3):
            request_started_at = datetime.now(SHANGHAI)
            response = self._get(endpoint, url, deadline=deadline, **kwargs)
            request_ended_at = datetime.now(SHANGHAI)
            payload = response.json()
            code = int(payload.get("code", response.status_code))
            message = str(payload.get("message", ""))
            is_rate_limit = response.status_code == 429 or code == 429
            if not is_rate_limit:
                return response, payload, retry_index

            retry_after_raw = response.headers.get("Retry-After")
            retry_after_seconds = self._retry_after_seconds(
                retry_after_raw, request_ended_at
            )
            response_summary = " ".join(response.text.split())[:300]
            if not response_summary:
                response_summary = json.dumps(payload, ensure_ascii=False)[:300]
            LOG.warning(
                "API_429_EVIDENCE endpoint=%s url=%s http_status=%s "
                "response_summary=%r retry_after=%r request_started_at=%s "
                "request_ended_at=%s retry_index=%s limiter_source=UPSTREAM",
                endpoint,
                url,
                response.status_code,
                response_summary,
                retry_after_raw,
                request_started_at.isoformat(),
                request_ended_at.isoformat(),
                retry_index,
            )
            if retry_index == 2:
                raise HithinkApiError(
                    429,
                    message or response_summary or "upstream rate limit",
                    error_code="UPSTREAM_HTTP_429",
                    endpoint=endpoint,
                    url=url,
                    http_status=response.status_code,
                    response_summary=response_summary,
                    retry_after=retry_after_raw,
                    request_started_at=request_started_at,
                    request_ended_at=request_ended_at,
                    retry_index=retry_index,
                    limiter_source="UPSTREAM",
                    duration_ms=round((perf_counter() - rate_limit_started) * 1000),
                )
            wait_seconds = (
                retry_after_seconds
                if retry_after_seconds is not None
                else (1.0 if retry_index == 0 else 2.0)
            )
            self._sleep_before_retry(wait_seconds, endpoint, deadline)
        raise AssertionError("unreachable")

    def fetch(self, *, deadline: float | None = None) -> ApiSnapshot:
        last_error: HithinkApiError | None = None
        for attempt, pause_seconds in enumerate(RETRY_PAUSES_SECONDS):
            self._sleep_before_retry(
                pause_seconds, "all_a_snapshot", deadline
            )
            started = perf_counter()
            try:
                response, payload, rate_retries = self._request_json(
                    "all_a_snapshot", URL, deadline=deadline
                )
                code = int(payload.get("code", response.status_code))
                if response.status_code >= 400 or code != 0:
                    raise HithinkApiError(code, str(payload.get("message", response.text[:300])))
                data = payload.get("data") or {}
                items = data.get("item") or []
                total = int(data.get("total", -1))
                if total < 0 or len(items) != total:
                    raise HithinkApiError(code, f"API total={total}, item.Count={len(items)}")
                timestamp = int(data["timestamp"])
                return ApiSnapshot(
                    code=code,
                    source_timestamp=timestamp,
                    source_time=datetime.fromtimestamp(timestamp / 1000, tz=SHANGHAI),
                    total=total,
                    items=items,
                    duration_ms=round((perf_counter() - started) * 1000),
                    retry_count=attempt + rate_retries,
                )
            except (httpx.HTTPError, ValueError, KeyError, HithinkApiError) as exc:
                error = exc if isinstance(exc, HithinkApiError) else HithinkApiError(None, str(exc))
                error.retry_index = max(error.retry_index, attempt)
                last_error = error
                if error.code not in RETRYABLE_CODES and error.code is not None:
                    break
        assert last_error is not None
        raise last_error

    def fetch_trading_days(self) -> TradingCalendar:
        last_error: HithinkApiError | None = None
        for attempt, pause_seconds in enumerate((0, 1, 2, 4)):
            if pause_seconds:
                sleep(pause_seconds)
            started = perf_counter()
            try:
                response = self._get("trading_calendar", TRADING_DAYS_URL)
                payload = response.json()
                code = int(payload.get("code", response.status_code))
                if response.status_code >= 400 or code != 0:
                    raise HithinkApiError(code, str(payload.get("message", response.text[:300])))
                items = (payload.get("data") or {}).get("item") or []
                trade_dates = {date.fromisoformat(str(item["date"])) for item in items}
                if not trade_dates:
                    raise HithinkApiError(code, "Trading calendar returned no dates")
                return TradingCalendar(
                    trade_dates=trade_dates,
                    duration_ms=round((perf_counter() - started) * 1000),
                    retry_count=attempt,
                )
            except (httpx.HTTPError, ValueError, KeyError, HithinkApiError) as exc:
                error = exc if isinstance(exc, HithinkApiError) else HithinkApiError(None, str(exc))
                last_error = error
                if error.code not in RETRYABLE_CODES and error.code is not None:
                    break
        assert last_error is not None
        raise last_error

    def fetch_limit_pool(
        self,
        pool_name: str,
        trade_date: date,
        *,
        deadline: float | None = None,
    ) -> LimitPoolSnapshot:
        if pool_name not in {"limit-up-pool", "limit-down-pool", "limit-break-pool"}:
            raise ValueError(f"Unsupported limit pool: {pool_name}")
        last_error: HithinkApiError | None = None
        endpoint_name = f"limit_pool:{pool_name}"
        for attempt, pause_seconds in enumerate(RETRY_PAUSES_SECONDS):
            self._sleep_before_retry(pause_seconds, endpoint_name, deadline)
            started = perf_counter()
            try:
                page = 1
                page_count = 1
                expected_total: int | None = None
                collected: list[LimitPoolItem] = []
                last_code = 0
                latest_timestamp = 0
                latest_source_time: datetime | None = None
                rate_retries = 0
                while page <= page_count:
                    response, payload, page_rate_retries = self._request_json(
                        endpoint_name,
                        LIMIT_POOL_URL.format(pool_name=pool_name),
                        deadline=deadline,
                        params={
                            "date_ms": int(datetime.combine(trade_date, time.min, tzinfo=SHANGHAI).timestamp() * 1000),
                            "page": page,
                            "size": 200,
                        },
                    )
                    rate_retries += page_rate_retries
                    last_code = int(payload.get("code", response.status_code))
                    if response.status_code >= 400 or last_code != 0:
                        raise HithinkApiError(
                            last_code, str(payload.get("message", response.text[:300]))
                        )
                    data = payload.get("data") or {}
                    pagination = data.get("pagination") or {}
                    page_total = int(pagination.get("total", -1))
                    page_count = int(pagination.get("pages", 0))
                    if (
                        page_total < 0
                        or page_count < 0
                        or (page_total > 0 and page_count < 1)
                    ):
                        raise HithinkApiError(last_code, "Limit pool pagination is invalid")
                    if expected_total is None:
                        expected_total = page_total
                    elif expected_total != page_total:
                        raise HithinkApiError(
                            last_code,
                            f"Limit pool total changed during pagination: {expected_total}/{page_total}",
                        )
                    timestamp = int(data["timestamp"])
                    source_time = datetime.fromtimestamp(timestamp / 1000, tz=SHANGHAI)
                    if timestamp >= latest_timestamp:
                        latest_timestamp = timestamp
                        latest_source_time = source_time
                    for item in data.get("item") or []:
                        collected.append(LimitPoolItem(item, timestamp, source_time))
                    page += 1
                expected_total = expected_total or 0
                if len(collected) != expected_total:
                    raise HithinkApiError(
                        last_code,
                        f"Limit pool total={expected_total}, collected={len(collected)}",
                    )
                if latest_source_time is None:
                    raise HithinkApiError(last_code, "Limit pool response has no source timestamp")
                return LimitPoolSnapshot(
                    code=last_code,
                    source_timestamp=latest_timestamp,
                    source_time=latest_source_time,
                    total=expected_total,
                    items=collected,
                    duration_ms=round((perf_counter() - started) * 1000),
                    retry_count=attempt + rate_retries,
                )
            except (httpx.HTTPError, ValueError, KeyError, HithinkApiError) as exc:
                error = exc if isinstance(exc, HithinkApiError) else HithinkApiError(None, str(exc))
                error.retry_index = max(error.retry_index, attempt)
                last_error = error
                if error.code not in RETRYABLE_CODES and error.code is not None:
                    break
        assert last_error is not None
        raise last_error

    def fetch_sector_index_snapshot(
        self,
        sector_codes: list[str],
        batch_size: int = 600,
        *,
        deadline: float | None = None,
    ) -> SectorIndexSnapshot:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        requested = list(dict.fromkeys(sector_codes))
        if not requested:
            return SectorIndexSnapshot(total=0, items=[], duration_ms=0, retry_count=0)

        started = perf_counter()
        collected: list[SectorIndexItem] = []
        retries = 0
        for offset in range(0, len(requested), batch_size):
            batch = requested[offset : offset + batch_size]
            result = self._fetch_sector_index_batch(batch, deadline=deadline)
            collected.extend(result.items)
            retries += result.retry_count

        returned_codes = [str(record.item["thscode"]) for record in collected]
        if len(returned_codes) != len(requested) or set(returned_codes) != set(requested):
            missing = sorted(set(requested) - set(returned_codes))
            unexpected = sorted(set(returned_codes) - set(requested))
            raise HithinkApiError(
                None,
                f"Sector index response mismatch missing={missing[:5]} unexpected={unexpected[:5]}",
            )
        if len(returned_codes) != len(set(returned_codes)):
            raise HithinkApiError(None, "Sector index response contains duplicate codes")
        return SectorIndexSnapshot(
            total=len(collected),
            items=collected,
            duration_ms=round((perf_counter() - started) * 1000),
            retry_count=retries,
        )

    def _fetch_sector_index_batch(
        self, sector_codes: list[str], *, deadline: float | None = None
    ) -> SectorIndexSnapshot:
        last_error: HithinkApiError | None = None
        for attempt, pause_seconds in enumerate(RETRY_PAUSES_SECONDS):
            self._sleep_before_retry(pause_seconds, "sector_index", deadline)
            started = perf_counter()
            try:
                response, payload, rate_retries = self._request_json(
                    "sector_index",
                    SECTOR_INDEX_SNAPSHOT_URL,
                    deadline=deadline,
                    params={"thscodes": ",".join(sector_codes)},
                )
                code = int(payload.get("code", response.status_code))
                if response.status_code >= 400 or code != 0:
                    raise HithinkApiError(code, str(payload.get("message", response.text[:300])))
                data = payload.get("data") or {}
                items = data.get("item") or []
                total = int(data.get("total", -1))
                if total != len(items) or total != len(sector_codes):
                    raise HithinkApiError(
                        code,
                        f"Sector index API total={total}, items={len(items)}, requested={len(sector_codes)}",
                    )
                timestamp = int(data["timestamp"])
                source_time = datetime.fromtimestamp(timestamp / 1000, tz=SHANGHAI)
                return SectorIndexSnapshot(
                    total=total,
                    items=[SectorIndexItem(item, timestamp, source_time) for item in items],
                    duration_ms=round((perf_counter() - started) * 1000),
                    retry_count=attempt + rate_retries,
                )
            except (httpx.HTTPError, ValueError, KeyError, HithinkApiError) as exc:
                error = exc if isinstance(exc, HithinkApiError) else HithinkApiError(None, str(exc))
                error.retry_index = max(error.retry_index, attempt)
                last_error = error
                if error.code not in RETRYABLE_CODES and error.code is not None:
                    break
        assert last_error is not None
        raise last_error

    def fetch_dump_url(self, dump_name: str) -> DumpUrl:
        try:
            endpoint = DUMP_URLS[dump_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported dump name: {dump_name}") from exc
        started = perf_counter()
        try:
            response = self._get(f"dump:{dump_name}", endpoint)
            payload = response.json()
            code = int(payload.get("code", response.status_code))
            if response.status_code >= 400 or code != 0:
                raise HithinkApiError(code, str(payload.get("message", response.text[:300])))
            url = str((payload.get("data") or {})["presigned_url"])
            if not url.startswith("https://"):
                raise HithinkApiError(code, "Dump response did not contain an HTTPS download URL")
            return DumpUrl(url=url, duration_ms=round((perf_counter() - started) * 1000))
        except (httpx.HTTPError, ValueError, KeyError, HithinkApiError) as exc:
            if isinstance(exc, HithinkApiError):
                raise
            raise HithinkApiError(None, str(exc)) from exc
