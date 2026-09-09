from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from time import perf_counter, sleep
from typing import TYPE_CHECKING

import httpx

from app.hithink.schedule import SHANGHAI

if TYPE_CHECKING:
    from app.hithink.writer import ClickHouseWriter


LOG = logging.getLogger(__name__)
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_BATCH_SIZE = 100
TENCENT_MAX_ATTEMPTS = 4
TENCENT_BATCH_PAUSE_SECONDS = 0.12
TENCENT_RATE_LIMIT_PAUSES_SECONDS = (2.0, 4.0, 8.0)
TRADING_DAY_POLL_SECONDS = 10
TENCENT_DAILY_TASK = "tencent_stock_basic_sync"
TENCENT_FINISH_TIME = time(9, 0)
_QUOTE_PATTERN = re.compile(r'v_((?:sh|sz|bj)\d{6})="([^"]*)"')


class TencentCollectionDeadlineExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class TencentStockBasic:
    snapshot_date: date
    scheduled_time: datetime
    source_time: datetime | None
    batch_id: str
    thscode: str
    ticker: str
    stock_name: str
    total_shares: int | None
    float_shares: int | None
    source_tag: str = "tencent"


def tencent_stock_candidates() -> list[str]:
    """Return the full code space used to discover currently listed A shares."""
    ranges = (
        ("sh", 600000, 606000),
        ("sh", 688000, 690000),
        ("sz", 0, 4000),
        ("sz", 300000, 302000),
        ("bj", 430000, 440000),
        ("bj", 830000, 840000),
        ("bj", 870000, 880000),
        ("bj", 920000, 930000),
    )
    return [
        f"{prefix}{code:06d}"
        for prefix, start, stop in ranges
        for code in range(start, stop)
    ]


def parse_tencent_stock_basic(
    payload: bytes,
    *,
    snapshot_date: date,
    scheduled_time: datetime,
    batch_id: str,
) -> list[TencentStockBasic]:
    text = payload.decode("gbk", errors="ignore")
    rows: list[TencentStockBasic] = []
    for symbol, raw_fields in _QUOTE_PATTERN.findall(text):
        fields = raw_fields.split("~")
        if len(fields) <= 73:
            continue
        ticker = symbol[-6:]
        stock_name = fields[1].strip()
        if not stock_name or fields[2].strip() != ticker:
            continue
        total_shares = _parse_uint(fields[73])
        float_shares = _parse_uint(fields[72])
        if total_shares is None and float_shares is None:
            continue
        rows.append(
            TencentStockBasic(
                snapshot_date=snapshot_date,
                scheduled_time=scheduled_time,
                source_time=_parse_source_time(fields[30]),
                batch_id=batch_id,
                thscode=f"{ticker}.{_exchange_suffix(symbol[:2])}",
                ticker=ticker,
                stock_name=stock_name,
                total_shares=total_shares,
                float_shares=float_shares,
            )
        )
    return rows


class TencentStockBasicCollector:
    def __init__(
        self,
        writer: ClickHouseWriter,
        *,
        client: httpx.Client | None = None,
        batch_size: int = TENCENT_BATCH_SIZE,
        pause_seconds: float = TENCENT_BATCH_PAUSE_SECONDS,
    ):
        self.writer = writer
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20.0),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://stockapp.finance.qq.com/",
            },
        )
        self._owns_client = client is None
        self.batch_size = batch_size
        self.pause_seconds = pause_seconds

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def run(
        self,
        now: datetime | None = None,
        *,
        deadline: datetime | None = None,
    ) -> tuple[int, int]:
        actual_now = now or datetime.now(SHANGHAI)
        if actual_now.tzinfo is None:
            actual_now = actual_now.replace(tzinfo=SHANGHAI)
        snapshot_date = actual_now.astimezone(SHANGHAI).date()
        scheduled_time = datetime.combine(snapshot_date, time(8, 50), SHANGHAI)
        batch_id = f"tencent-stock-basic-{snapshot_date:%Y%m%d}"
        candidates = tencent_stock_candidates()
        collected: dict[str, TencentStockBasic] = {}

        try:
            for batch_number, symbols in enumerate(
                _chunks(candidates, self.batch_size), start=1
            ):
                _ensure_before_deadline(deadline)
                payload = self._fetch_batch(symbols, deadline=deadline)
                for row in parse_tencent_stock_basic(
                    payload,
                    snapshot_date=snapshot_date,
                    scheduled_time=scheduled_time,
                    batch_id=batch_id,
                ):
                    collected[row.thscode] = row
                if self.pause_seconds:
                    _sleep_with_deadline(self.pause_seconds, deadline)
                if batch_number % 100 == 0:
                    LOG.info(
                        "TENCENT_STOCK_BASIC_PROGRESS date=%s batches=%s stocks=%s",
                        snapshot_date,
                        batch_number,
                        len(collected),
                    )

            if not collected:
                raise RuntimeError("腾讯全市场代码扫描完成，但没有得到有效个股股本数据")
            _ensure_before_deadline(deadline)
            rows = sorted(collected.values(), key=lambda row: row.thscode)
            inserted, insert_ms = self.writer.insert_tencent_stock_basic_once(
                rows, batch_id=batch_id
            )
            LOG.info(
                "TENCENT_STOCK_BASIC_SUCCESS date=%s rows=%s insert_ms=%s",
                snapshot_date,
                inserted,
                insert_ms,
            )
            return inserted, insert_ms
        finally:
            self.close()

    def _fetch_batch(
        self, symbols: list[str], *, deadline: datetime | None = None
    ) -> bytes:
        last_error: Exception | None = None
        url = TENCENT_QUOTE_URL + ",".join(symbols)
        for attempt in range(1, TENCENT_MAX_ATTEMPTS + 1):
            _ensure_before_deadline(deadline)
            try:
                remaining = _remaining_seconds(deadline)
                request_options = (
                    {"timeout": max(0.1, min(20.0, remaining))}
                    if remaining is not None
                    else {}
                )
                response = self.client.get(url, **request_options)
                if response.status_code == 429 and attempt < TENCENT_MAX_ATTEMPTS:
                    wait_seconds = _rate_limit_wait_seconds(response, attempt)
                    LOG.warning(
                        "TENCENT_STOCK_BASIC_429_RETRY attempt=%s/%s "
                        "wait_seconds=%.1f retry_after=%s",
                        attempt,
                        TENCENT_MAX_ATTEMPTS,
                        wait_seconds,
                        response.headers.get("Retry-After"),
                    )
                    _sleep_with_deadline(wait_seconds, deadline)
                    continue
                response.raise_for_status()
                return response.content
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt == TENCENT_MAX_ATTEMPTS:
                    break
                wait_seconds = 1.5 * attempt
                LOG.warning(
                    "TENCENT_STOCK_BASIC_RETRY attempt=%s/%s wait_seconds=%.1f error=%s",
                    attempt,
                    TENCENT_MAX_ATTEMPTS,
                    wait_seconds,
                    exc,
                )
                _sleep_with_deadline(wait_seconds, deadline)
        raise RuntimeError(
            f"腾讯个股基础信息批次连续失败 {TENCENT_MAX_ATTEMPTS} 次: {last_error}"
        ) from last_error


def run_tencent_stock_basic_daily(
    writer: ClickHouseWriter,
    now: datetime | None = None,
    *,
    poll_seconds: float = TRADING_DAY_POLL_SECONDS,
    sleep_fn=sleep,
    collector_factory=TencentStockBasicCollector,
    clock: Callable[[], datetime] | None = None,
) -> tuple[int, int] | None:
    """Finish by 09:00; failure leaves the previous share-count snapshot active."""
    now_fn = clock or _shanghai_now
    actual_now = now or now_fn()
    if actual_now.tzinfo is None:
        actual_now = actual_now.replace(tzinfo=SHANGHAI)
    trade_date = actual_now.astimezone(SHANGHAI).date()
    deadline = datetime.combine(trade_date, TENCENT_FINISH_TIME, SHANGHAI)
    writer.initialize_daily_task_plan(trade_date)
    started_at = actual_now

    while True:
        confirmation = writer.cached_trading_day_confirmation(trade_date)
        if confirmation is not None:
            is_trading_day, confirmed_at = confirmation
            if _confirmation_date(confirmed_at) == trade_date:
                break
        current = now_fn()
        if current >= deadline:
            writer.set_daily_status(
                trade_date,
                TENCENT_DAILY_TASK,
                "FAILED",
                request_start_time=started_at,
                request_end_time=current,
                error_code="CALENDAR_CONFIRMATION_DEADLINE",
                error_message="09:00前未取得交易日确认，保留上一版股本数据",
            )
            LOG.error("TENCENT_STOCK_BASIC_DEADLINE date=%s stage=calendar", trade_date)
            return None
        LOG.error(
            "TENCENT_STOCK_BASIC_WAIT_CALENDAR date=%s retry_seconds=%s",
            trade_date,
            poll_seconds,
        )
        sleep_fn(min(poll_seconds, max(0.0, (deadline - current).total_seconds())))

    if not is_trading_day:
        writer.set_daily_status(
            trade_date,
            TENCENT_DAILY_TASK,
            "SKIPPED",
            request_end_time=now_fn(),
            error_code="NON_TRADING_DAY",
        )
        LOG.info("TENCENT_STOCK_BASIC_SKIPPED date=%s non_trading_day", trade_date)
        return None

    started = perf_counter()
    writer.set_daily_status(
        trade_date,
        TENCENT_DAILY_TASK,
        "RUNNING",
        request_start_time=started_at,
    )
    try:
        result = collector_factory(writer).run(actual_now, deadline=deadline)
    except Exception as exc:  # noqa: BLE001 - failure must preserve yesterday's snapshot
        completed_at = now_fn()
        writer.set_daily_status(
            trade_date,
            TENCENT_DAILY_TASK,
            "FAILED",
            request_start_time=started_at,
            request_end_time=completed_at,
            duration_ms=round((perf_counter() - started) * 1000),
            error_code=type(exc).__name__,
            error_message=str(exc)[:1000],
        )
        LOG.error(
            "TENCENT_STOCK_BASIC_FAILED_USE_PREVIOUS date=%s error=%s",
            trade_date,
            exc,
        )
        return None
    inserted, _ = result
    writer.set_daily_status(
        trade_date,
        TENCENT_DAILY_TASK,
        "SUCCESS",
        request_start_time=started_at,
        request_end_time=now_fn(),
        insert_rows=inserted,
        duration_ms=round((perf_counter() - started) * 1000),
    )
    return result


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    if size <= 0:
        raise ValueError("batch_size must be greater than zero")
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


def _remaining_seconds(deadline: datetime | None) -> float | None:
    if deadline is None:
        return None
    return (deadline - _shanghai_now()).total_seconds()


def _ensure_before_deadline(deadline: datetime | None) -> None:
    remaining = _remaining_seconds(deadline)
    if remaining is not None and remaining <= 0:
        raise TencentCollectionDeadlineExceeded(
            "腾讯股本采集已到09:00截止时间，保留上一版股本数据"
        )


def _sleep_with_deadline(seconds: float, deadline: datetime | None) -> None:
    remaining = _remaining_seconds(deadline)
    if remaining is None:
        sleep(seconds)
        return
    if remaining <= 0:
        _ensure_before_deadline(deadline)
    sleep(min(seconds, remaining))
    _ensure_before_deadline(deadline)


def _confirmation_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI).date()


def _rate_limit_wait_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 30.0))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(retry_after)
                now = datetime.now(parsed.tzinfo or SHANGHAI)
                return max(0.0, min((parsed - now).total_seconds(), 30.0))
            except (TypeError, ValueError, OverflowError):
                pass
    return TENCENT_RATE_LIMIT_PAUSES_SECONDS[attempt - 1]


def _parse_uint(value: str) -> int | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        parsed = Decimal(stripped)
    except InvalidOperation:
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return int(parsed)


def _parse_source_time(value: str) -> datetime | None:
    stripped = value.strip()
    if len(stripped) != 14 or not stripped.isdigit():
        return None
    try:
        return datetime.strptime(stripped, "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
    except ValueError:
        return None


def _exchange_suffix(prefix: str) -> str:
    return {"sh": "SH", "sz": "SZ", "bj": "BJ"}[prefix]
