from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

import pyarrow.parquet as pq

from app.hithink.api import HithinkApiError, HithinkClient
from app.hithink.schedule import SHANGHAI
from app.hithink.writer import ClickHouseWriter

LOG = logging.getLogger(__name__)

RAW = "market.hithink_daily_k_raw"
EVENTS = "market.hithink_adjustment_events"


class DataNotReady(RuntimeError):
    pass


class ForwardFormulaUnverified(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncResult:
    download_rows: int
    insert_rows: int
    source_latest_trade_date: date
    stock_count: int


def _trade_date(milliseconds: int) -> date:
    return datetime.fromtimestamp(milliseconds / 1000, tz=SHANGHAI).date()


def _ticker(thscode: str) -> str:
    return thscode.split(".", 1)[0]


def _event_fingerprint(row: dict[str, Any]) -> str:
    fields = (
        row["thscode"],
        row["ex_date_ms"],
        row["dividend_per_share"],
        row["per_share_bonus"],
        row["allotment_ratio"],
        row["allotment_price"],
        row.get("currency", ""),
    )
    canonical = "|".join(str(value) for value in fields)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DailyPipeline:
    def __init__(self, api: HithinkClient, writer: ClickHouseWriter):
        self.api = api
        self.writer = writer

    def calendar_gate(self, trade_date: date) -> bool | None:
        started = datetime.now(SHANGHAI)
        self.writer.set_daily_status(trade_date, "calendar_gate", "RUNNING", request_start_time=started)
        try:
            calendar = self.api.fetch_trading_days()
            self.writer.cache_trading_calendar(calendar.trade_dates, trade_date)
            is_trading_day = trade_date in calendar.trade_dates
            self.writer.set_daily_status(
                trade_date,
                "calendar_gate",
                "SUCCESS",
                request_start_time=started,
                request_end_time=datetime.now(SHANGHAI),
                retry_count=calendar.retry_count,
            )
            return is_trading_day
        except HithinkApiError as exc:
            cached = self.writer.cached_trading_day(trade_date)
            if cached is not None:
                self.writer.set_daily_status(
                    trade_date,
                    "calendar_gate",
                    "SUCCESS",
                    request_start_time=started,
                    request_end_time=datetime.now(SHANGHAI),
                    error_code="CALENDAR_CACHE",
                    error_message=str(exc)[:1000],
                )
                return cached
            self.writer.set_daily_status(
                trade_date,
                "calendar_gate",
                "FAILED",
                request_start_time=started,
                request_end_time=datetime.now(SHANGHAI),
                error_code="CALENDAR_UNKNOWN",
                error_message=str(exc)[:1000],
            )
            return None

    def _download(self, dump_name: str) -> Path:
        dump = self.api.fetch_dump_url(dump_name)
        descriptor, path = tempfile.mkstemp(prefix=f"hithink-{dump_name}-", suffix=".parquet")
        os.close(descriptor)
        target = Path(path)
        try:
            with self.api.client.stream("GET", dump.url, timeout=180) as response:
                response.raise_for_status()
                with target.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return target

    def sync_raw(self, trade_date: date, *, initialize: bool) -> SyncResult:
        task_name = "hithink_daily_k_raw_sync"
        started = datetime.now(SHANGHAI)
        self.writer.set_daily_status(trade_date, task_name, "RUNNING", request_start_time=started)
        dump_path = self._download("daily_k" if initialize else "daily_k_10d")
        try:
            parquet = pq.ParquetFile(dump_path)
            version_time = datetime.now(SHANGHAI)
            inserted = 0
            latest: date | None = None
            stocks: set[str] = set()
            for batch in parquet.iter_batches(batch_size=100_000):
                rows = []
                for item in batch.to_pylist():
                    thscode = str(item["thscode"])
                    item_date = _trade_date(int(item["date_ms"]))
                    latest = max(latest, item_date) if latest else item_date
                    stocks.add(thscode)
                    rows.append(
                        (
                            item_date, int(item["date_ms"]), thscode, _ticker(thscode),
                            str(item["currency"]), str(item["interval"]), str(item["adjusted"]),
                            float(item["open_price"]), float(item["high_price"]), float(item["low_price"]),
                            float(item["close_price"]), float(item["volume"]), float(item["turnover"]),
                            "hithink", version_time,
                        )
                    )
                self.writer.client.insert(
                    RAW,
                    rows,
                    column_names=[
                        "trade_date", "date_ms", "thscode", "ticker", "currency", "interval", "adjusted",
                        "open_price", "high_price", "low_price", "close_price", "volume", "turnover",
                        "source", "version_time",
                    ],
                )
                inserted += len(rows)
            if latest is None:
                raise RuntimeError("daily-k dump was empty")
            result = SyncResult(parquet.metadata.num_rows, inserted, latest, len(stocks))
            if not initialize and result.source_latest_trade_date < trade_date:
                raise DataNotReady(
                    f"source_latest_trade_date={result.source_latest_trade_date} < today={trade_date}"
                )
            self.writer.set_daily_status(
                trade_date, task_name, "SUCCESS", request_start_time=started,
                request_end_time=datetime.now(SHANGHAI), source_file_date=result.source_latest_trade_date,
                source_latest_trade_date=result.source_latest_trade_date, download_rows=result.download_rows,
                insert_rows=result.insert_rows, updated_rows=result.insert_rows,
            )
            return result
        except DataNotReady as exc:
            self.writer.set_daily_status(
                trade_date, task_name, "FAILED", request_start_time=started,
                request_end_time=datetime.now(SHANGHAI), error_code="DATA_NOT_READY",
                error_message=str(exc),
            )
            raise
        except Exception as exc:
            self.writer.set_daily_status(
                trade_date, task_name, "FAILED", request_start_time=started,
                request_end_time=datetime.now(SHANGHAI), error_code="RAW_SYNC_ERROR",
                error_message=str(exc)[:1000],
            )
            raise
        finally:
            dump_path.unlink(missing_ok=True)

    def sync_events(self, trade_date: date) -> set[str]:
        task_name = "hithink_adjustment_events_sync"
        started = datetime.now(SHANGHAI)
        self.writer.set_daily_status(trade_date, task_name, "RUNNING", request_start_time=started)
        previous = self._event_sets()
        dump_path = self._download("adjustment_factors")
        try:
            parquet = pq.ParquetFile(dump_path)
            version_time = datetime.now(SHANGHAI)
            current: dict[str, set[str]] = {}
            inserted = 0
            for batch in parquet.iter_batches(batch_size=100_000):
                rows = []
                for item in batch.to_pylist():
                    thscode = str(item["thscode"])
                    fingerprint = _event_fingerprint(item)
                    current.setdefault(thscode, set()).add(fingerprint)
                    rows.append(
                        (
                            thscode, str(item["ticker"]), _trade_date(int(item["ex_date_ms"])),
                            int(item["ex_date_ms"]), float(item["dividend_per_share"]),
                            float(item["per_share_bonus"]), float(item["allotment_ratio"]),
                            float(item["allotment_price"]), str(item["currency"]), fingerprint,
                            "hithink", version_time,
                        )
                    )
                self.writer.client.insert(
                    EVENTS, rows,
                    column_names=[
                        "thscode", "ticker", "ex_date", "ex_date_ms", "dividend_per_share",
                        "per_share_bonus", "allotment_ratio", "allotment_price", "currency",
                        "event_fingerprint", "source", "version_time",
                    ],
                )
                inserted += len(rows)
            affected = {code for code in set(previous) | set(current) if previous.get(code, set()) != current.get(code, set())}
            self.writer.set_daily_status(
                trade_date, task_name, "SUCCESS", request_start_time=started,
                request_end_time=datetime.now(SHANGHAI), download_rows=parquet.metadata.num_rows,
                insert_rows=inserted, updated_rows=inserted, affected_stock_count=len(affected),
            )
            return affected
        except Exception as exc:
            self.writer.set_daily_status(
                trade_date, task_name, "FAILED", request_start_time=started,
                request_end_time=datetime.now(SHANGHAI), error_code="EVENT_SYNC_ERROR",
                error_message=str(exc)[:1000],
            )
            raise
        finally:
            dump_path.unlink(missing_ok=True)

    def _event_sets(self) -> dict[str, set[str]]:
        result = self.writer.client.query(f"SELECT thscode, event_fingerprint FROM {EVENTS} FINAL")
        sets: dict[str, set[str]] = {}
        for thscode, fingerprint in result.result_rows:
            sets.setdefault(thscode, set()).add(fingerprint)
        return sets

    def forward_build(self, trade_date: date, affected: set[str]) -> None:
        message = (
            "Official adjustment-event documentation does not define a factor formula for "
            "cash dividends, bonuses and rights issues; forward prices will not be guessed."
        )
        self.writer.set_daily_status(
            trade_date,
            "hithink_daily_k_forward_build",
            "FAILED",
            request_start_time=datetime.now(SHANGHAI),
            request_end_time=datetime.now(SHANGHAI),
            affected_stock_count=len(affected),
            error_code="FORWARD_FORMULA_UNVERIFIED",
            error_message=message,
        )
        raise ForwardFormulaUnverified(message)

    def run_pipeline(self, trade_date: date, *, initialize: bool) -> None:
        started = perf_counter()
        task_name = "hithink_daily_initialize" if initialize else "daily_pipeline"
        self.writer.set_daily_status(trade_date, task_name, "RUNNING", request_start_time=datetime.now(SHANGHAI))
        decision = self.calendar_gate(trade_date)
        if decision is None:
            self.writer.set_daily_status(trade_date, task_name, "FAILED", error_code="CALENDAR_UNKNOWN")
            return
        if not decision:
            self.writer.set_daily_status(
                trade_date, task_name, "SKIPPED", error_code="NON_TRADING_DAY",
                request_end_time=datetime.now(SHANGHAI), duration_ms=round((perf_counter() - started) * 1000),
            )
            return
        try:
            self.sync_raw(trade_date, initialize=initialize)
            affected = self.sync_events(trade_date)
            self.forward_build(trade_date, affected)
        except DataNotReady as exc:
            self.writer.set_daily_status(
                trade_date, task_name, "FAILED", request_end_time=datetime.now(SHANGHAI),
                duration_ms=round((perf_counter() - started) * 1000), error_code="DATA_NOT_READY",
                error_message=str(exc),
            )
            raise
        except Exception as exc:
            self.writer.set_daily_status(
                trade_date, task_name, "FAILED", request_end_time=datetime.now(SHANGHAI),
                duration_ms=round((perf_counter() - started) * 1000), error_code=type(exc).__name__,
                error_message=str(exc)[:1000],
            )
            raise

    def repair_scan(self, trade_date: date) -> None:
        status = self.daily_status(trade_date, "hithink_daily_initialize")
        if status != "SUCCESS":
            self.writer.set_daily_status(
                trade_date, "repair_scan", "SKIPPED", error_code="INITIALIZATION_PENDING"
            )
            return
        self.writer.set_daily_status(trade_date, "repair_scan", "SUCCESS")

    def daily_status(self, trade_date: date, task_name: str) -> str | None:
        return self.writer.daily_status(trade_date, task_name)

    def run_auto(self) -> None:
        now = datetime.now(SHANGHAI)
        trade_date = now.date()
        self.calendar_gate(trade_date)
        self.repair_scan(trade_date)
        if now.time() < time(15, 45):
            return
        initialize = not self.writer.daily_seed_complete()
        if initialize and now.time() < time(16, 5):
            delay = (datetime.combine(trade_date, time(16, 5), tzinfo=SHANGHAI) - now).total_seconds()
            sleep(max(1, delay))
        deadline = datetime.combine(trade_date, time(18, 45), tzinfo=SHANGHAI)
        while datetime.now(SHANGHAI) <= deadline:
            try:
                self.run_pipeline(trade_date, initialize=initialize)
                return
            except DataNotReady:
                sleep(600)
                initialize = not self.writer.daily_seed_complete()
            except ForwardFormulaUnverified:
                return
