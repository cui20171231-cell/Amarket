from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from app.hithink.schedule import SHANGHAI
from app.hithink.trading_day_gate import SharedTradingDayGate


class FakeApi:
    def __init__(self, trade_date: date) -> None:
        self.trade_date = trade_date
        self.calls = 0

    def fetch_trading_days(self):
        self.calls += 1
        return SimpleNamespace(trade_dates={self.trade_date}, retry_count=0)


class FakeWriter:
    def __init__(self) -> None:
        self.confirmations: dict[date, tuple[bool, datetime]] = {}

    def cached_trading_day_confirmation(self, trade_date: date):
        return self.confirmations.get(trade_date)

    def cache_trading_calendar(self, trade_dates: set[date], trade_date: date) -> None:
        self.confirmations[trade_date] = (
            trade_date in trade_dates,
            datetime.combine(trade_date, datetime.min.time(), tzinfo=SHANGHAI),
        )


def test_two_pipelines_share_one_current_day_confirmation(tmp_path: Path) -> None:
    trade_date = date(2026, 9, 1)
    api = FakeApi(trade_date)
    writer = FakeWriter()
    lock_path = tmp_path / "calendar.lock"

    first = SharedTradingDayGate(api, writer, lock_path).confirm(trade_date)
    second = SharedTradingDayGate(api, writer, lock_path).confirm(trade_date)

    assert first.source == "API"
    assert second.source == "CACHE_TODAY"
    assert first.is_trading_day is True
    assert second.is_trading_day is True
    assert api.calls == 1
