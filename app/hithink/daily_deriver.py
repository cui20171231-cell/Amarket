from __future__ import annotations

import bisect
import hashlib
import math
from collections import defaultdict
from datetime import date, datetime

from app.hithink.schedule import SHANGHAI
from app.hithink.writer import ClickHouseWriter

RAW = "market.hithink_daily_k_raw"
EVENTS = "market.hithink_adjustment_events"
FORWARD = "market.hithink_daily_k_forward"


class ForwardFormulaUnverified(RuntimeError):
    pass


class DailyForwardDeriver:
    """Daily derivation module: it reads persisted facts and owns only forward-K output.

    It runs only after raw daily-K and adjustment-event collection has succeeded.
    The local formula is used: raw K plus the current effective corporate-action event.
    """

    def __init__(self, writer: ClickHouseWriter):
        self.writer = writer

    def derive(self, trade_date: date, affected: set[str]) -> None:
        started = datetime.now(SHANGHAI)
        self.writer.set_daily_status(
            trade_date, "daily_derivation", "RUNNING", request_start_time=started
        )
        try:
            rows = self._forward_rows(trade_date, affected)
            if rows:
                self.writer.client.insert(
                    FORWARD,
                    rows,
                    column_names=[
                        "trade_date", "collection_id", "thscode", "ticker", "open_price",
                        "high_price", "low_price", "close_price", "volume", "turnover",
                        "adjust_factor", "build_mode", "source_raw_date", "raw_version_time",
                        "event_set_hash", "calculated_at", "version_time",
                    ],
                )
            completed = datetime.now(SHANGHAI)
            self.writer.set_daily_status(
                trade_date, "daily_derivation", "SUCCESS", request_start_time=started,
                request_end_time=completed, insert_rows=len(rows), updated_rows=len(rows),
                affected_stock_count=len(affected),
                duration_ms=round((completed - started).total_seconds() * 1000),
            )
        except Exception as exc:
            completed = datetime.now(SHANGHAI)
            self.writer.set_daily_status(
                trade_date, "daily_derivation", "FAILED", request_start_time=started,
                request_end_time=completed, affected_stock_count=len(affected),
                duration_ms=round((completed - started).total_seconds() * 1000),
                error_code="FORWARD_DERIVATION_ERROR", error_message=str(exc)[:1000],
            )
            raise

    def _forward_rows(self, trade_date: date, affected: set[str]) -> list[tuple[object, ...]]:
        raw_result = self.writer.client.query(
            f"""
            SELECT trade_date, collection_id, thscode, ticker, open_price, high_price, low_price,
                   close_price, volume, turnover, version_time
            FROM {RAW} FINAL
            WHERE trade_date <= {{trade_date:Date}}
            ORDER BY thscode, trade_date
            """,
            parameters={"trade_date": trade_date},
        )
        raw_by_code: dict[str, list[tuple[object, ...]]] = defaultdict(list)
        for row in raw_result.result_rows:
            raw_by_code[str(row[2])].append(row)
        if not raw_by_code:
            return []

        latest_codes = {
            str(row[0])
            for row in self.writer.client.query(
                f"SELECT DISTINCT thscode FROM {RAW} FINAL WHERE trade_date = {{trade_date:Date}}",
                parameters={"trade_date": trade_date},
            ).result_rows
        }
        has_forward = self.writer.client.query(
            f"SELECT count() FROM {FORWARD} FINAL"
        ).result_rows[0][0] > 0
        target_codes = set(raw_by_code) if not has_forward else (set(affected) | latest_codes)

        events_result = self.writer.client.query(
            f"""
            SELECT thscode, ex_date,
                   argMax(dividend_per_share, version_time),
                   argMax(per_share_bonus, version_time),
                   argMax(allotment_ratio, version_time),
                   argMax(allotment_price, version_time),
                   argMax(event_fingerprint, version_time)
            FROM {EVENTS} FINAL
            WHERE ex_date <= {{trade_date:Date}}
            GROUP BY thscode, ex_date
            """,
            parameters={"trade_date": trade_date},
        )
        events_by_code: dict[str, list[tuple[object, ...]]] = defaultdict(list)
        for row in events_result.result_rows:
            events_by_code[str(row[0])].append(row)

        now = datetime.now(SHANGHAI)
        output: list[tuple[object, ...]] = []
        for thscode in target_codes:
            stock_rows = raw_by_code.get(thscode)
            if not stock_rows:
                continue
            output.extend(self._derive_stock(stock_rows, events_by_code.get(thscode, []), now))
        return output

    @staticmethod
    def _derive_stock(
        stock_rows: list[tuple[object, ...]],
        events: list[tuple[object, ...]],
        calculated_at: datetime,
    ) -> list[tuple[object, ...]]:
        dates = [row[0] for row in stock_rows]
        close_by_date = [float(row[7]) for row in stock_rows]
        active_events: list[tuple[date, float, str]] = []
        fingerprints: list[str] = []
        for _, ex_date, dividend, bonus, rights_ratio, rights_price, fingerprint in events:
            previous_index = bisect.bisect_left(dates, ex_date) - 1
            if previous_index < 0:
                continue
            previous_close = close_by_date[previous_index]
            denominator = previous_close * (1.0 + float(bonus) + float(rights_ratio))
            numerator = previous_close - float(dividend) + float(rights_ratio) * float(rights_price)
            factor = numerator / denominator if denominator else 0.0
            if not math.isfinite(factor) or factor <= 0:
                raise ValueError(f"invalid forward-adjustment factor at {ex_date}")
            active_events.append((ex_date, factor, str(fingerprint)))
            fingerprints.append(str(fingerprint))
        active_events.sort(key=lambda row: row[0], reverse=True)
        event_hash = hashlib.sha256("|".join(sorted(fingerprints)).encode("utf-8")).hexdigest()

        factor = 1.0
        event_index = 0
        factors_by_date: dict[date, float] = {}
        for row in reversed(stock_rows):
            row_date = row[0]
            while event_index < len(active_events) and row_date < active_events[event_index][0]:
                factor *= active_events[event_index][1]
                event_index += 1
            factors_by_date[row_date] = factor

        output: list[tuple[object, ...]] = []
        for row in stock_rows:
            (
                row_date, collection_id, thscode, ticker, open_price, high_price, low_price,
                close_price, volume, turnover, raw_version_time,
            ) = row
            factor = factors_by_date[row_date]
            output.append((
                row_date, collection_id, thscode, ticker,
                float(open_price) * factor, float(high_price) * factor,
                float(low_price) * factor, float(close_price) * factor,
                float(volume), float(turnover), factor, "local_formula", row_date,
                raw_version_time, event_hash, calculated_at, calculated_at,
            ))
        return output
