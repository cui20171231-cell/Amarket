from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.hithink.api import HithinkApiError, HithinkClient
from app.hithink.schedule import SHANGHAI
from app.hithink.service_guard import SingleInstanceLock
from app.hithink.writer import ClickHouseWriter

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
GATE_LOCK_PATH = ROOT / ".runtime" / "hithink_trading_day_gate.lock"


@dataclass(frozen=True)
class TradingDayConfirmation:
    is_trading_day: bool | None
    source: str
    confirmed_at: datetime | None = None
    retry_count: int = 0
    error_message: str | None = None


class SharedTradingDayGate:
    """One Beijing-time calendar confirmation shared by all local pipelines."""

    def __init__(
        self,
        api: HithinkClient,
        writer: ClickHouseWriter,
        lock_path: Path = GATE_LOCK_PATH,
    ) -> None:
        self.api = api
        self.writer = writer
        self.lock_path = lock_path

    def confirm(self, trade_date: date) -> TradingDayConfirmation:
        cached = self._current_day_cache(trade_date)
        if cached is not None:
            return cached

        with SingleInstanceLock(self.lock_path):
            cached = self._current_day_cache(trade_date)
            if cached is not None:
                return cached
            try:
                calendar = self.api.fetch_trading_days()
                self.writer.cache_trading_calendar(calendar.trade_dates, trade_date)
                confirmed = self.writer.cached_trading_day_confirmation(trade_date)
                if confirmed is None:
                    raise RuntimeError(f"Trading calendar did not persist {trade_date}")
                is_trading_day, confirmed_at = confirmed
                LOG.info(
                    "SHARED_CALENDAR_CONFIRMED date=%s trading_day=%s timezone=%s",
                    trade_date,
                    is_trading_day,
                    SHANGHAI.key,
                )
                return TradingDayConfirmation(
                    is_trading_day=is_trading_day,
                    source="API",
                    confirmed_at=confirmed_at,
                    retry_count=calendar.retry_count,
                )
            except (HithinkApiError, RuntimeError) as exc:
                stale = self.writer.cached_trading_day_confirmation(trade_date)
                if stale is not None:
                    is_trading_day, confirmed_at = stale
                    LOG.warning(
                        "Shared calendar refresh failed; using cached decision=%s: %s",
                        is_trading_day,
                        exc,
                    )
                    return TradingDayConfirmation(
                        is_trading_day=is_trading_day,
                        source="CACHE_STALE",
                        confirmed_at=confirmed_at,
                        error_message=str(exc)[:1000],
                    )
                return TradingDayConfirmation(
                    is_trading_day=None,
                    source="ERROR",
                    error_message=str(exc)[:1000],
                )

    def _current_day_cache(self, trade_date: date) -> TradingDayConfirmation | None:
        cached = self.writer.cached_trading_day_confirmation(trade_date)
        if cached is None:
            return None
        is_trading_day, confirmed_at = cached
        if confirmed_at.astimezone(SHANGHAI).date() != trade_date:
            return None
        return TradingDayConfirmation(
            is_trading_day=is_trading_day,
            source="CACHE_TODAY",
            confirmed_at=confirmed_at,
        )
