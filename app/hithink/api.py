from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from time import perf_counter, sleep
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


class HithinkApiError(RuntimeError):
    def __init__(self, code: int | None, message: str):
        super().__init__(message)
        self.code = code


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
            timeout=httpx.Timeout(15.0, connect=5.0), headers={"X-api-key": api_key}
        )

    def close(self) -> None:
        self.client.close()

    def fetch(self) -> ApiSnapshot:
        last_error: HithinkApiError | None = None
        for attempt, pause_seconds in enumerate((0, 1, 2, 4)):
            if pause_seconds:
                sleep(pause_seconds)
            started = perf_counter()
            try:
                response = self.client.get(URL)
                payload = response.json()
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
                    retry_count=attempt,
                )
            except (httpx.HTTPError, ValueError, KeyError, HithinkApiError) as exc:
                error = exc if isinstance(exc, HithinkApiError) else HithinkApiError(None, str(exc))
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
                response = self.client.get(TRADING_DAYS_URL)
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

    def fetch_limit_pool(self, pool_name: str, trade_date: date) -> LimitPoolSnapshot:
        if pool_name not in {"limit-up-pool", "limit-down-pool"}:
            raise ValueError(f"Unsupported limit pool: {pool_name}")
        last_error: HithinkApiError | None = None
        for attempt, pause_seconds in enumerate((0, 1, 2, 4)):
            if pause_seconds:
                sleep(pause_seconds)
            started = perf_counter()
            try:
                page = 1
                page_count = 1
                expected_total: int | None = None
                collected: list[LimitPoolItem] = []
                last_code = 0
                latest_timestamp = 0
                latest_source_time: datetime | None = None
                while page <= page_count:
                    response = self.client.get(
                        LIMIT_POOL_URL.format(pool_name=pool_name),
                        params={"date": trade_date.isoformat(), "page": page, "size": 50},
                    )
                    payload = response.json()
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
                    retry_count=attempt,
                )
            except (httpx.HTTPError, ValueError, KeyError, HithinkApiError) as exc:
                error = exc if isinstance(exc, HithinkApiError) else HithinkApiError(None, str(exc))
                last_error = error
                if error.code not in RETRYABLE_CODES and error.code is not None:
                    break
        assert last_error is not None
        raise last_error

    def fetch_sector_index_snapshot(
        self, sector_codes: list[str], batch_size: int = 600
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
            result = self._fetch_sector_index_batch(batch)
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

    def _fetch_sector_index_batch(self, sector_codes: list[str]) -> SectorIndexSnapshot:
        last_error: HithinkApiError | None = None
        for attempt, pause_seconds in enumerate((0, 1, 2, 4)):
            if pause_seconds:
                sleep(pause_seconds)
            started = perf_counter()
            try:
                response = self.client.get(
                    SECTOR_INDEX_SNAPSHOT_URL, params={"thscodes": ",".join(sector_codes)}
                )
                payload = response.json()
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
                    retry_count=attempt,
                )
            except (httpx.HTTPError, ValueError, KeyError, HithinkApiError) as exc:
                error = exc if isinstance(exc, HithinkApiError) else HithinkApiError(None, str(exc))
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
            response = self.client.get(endpoint)
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
