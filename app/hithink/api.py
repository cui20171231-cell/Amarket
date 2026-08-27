from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from time import perf_counter, sleep
from typing import Any

import httpx

from app.hithink.schedule import SHANGHAI

URL = "https://fuyao.aicubes.cn/api/a-share/prices/snapshot?limit=10000&offset=0"
TRADING_DAYS_URL = "https://fuyao.aicubes.cn/api/a-share/calendar/trading-days"
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
