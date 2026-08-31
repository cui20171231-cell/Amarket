from datetime import date, datetime, timedelta

import pytest

import app.hithink.runner as runner_module
from app.hithink.runner import (
    CollectorRunner,
    next_daily_collection_at,
    should_run_mapping_after_confirmation,
)
from app.hithink.schedule import SHANGHAI
from app.hithink.sector_mapping import (
    SectorMappingDeadlineExceeded,
    SectorMappingSync,
)


def test_sector_mapping_runs_after_0850_confirmation_for_resident_collector() -> None:
    startup = datetime(2026, 8, 31, 8, 0, tzinfo=SHANGHAI)
    assert should_run_mapping_after_confirmation(startup, date(2026, 8, 31)) is True


def test_collector_start_after_0850_does_not_catch_up_mapping() -> None:
    startup = datetime(2026, 8, 31, 15, 34, 50, tzinfo=SHANGHAI)
    assert should_run_mapping_after_confirmation(startup, date(2026, 8, 31)) is False


def test_resident_collector_runs_mapping_on_the_next_trading_day() -> None:
    startup = datetime(2026, 8, 31, 15, 34, 50, tzinfo=SHANGHAI)
    assert should_run_mapping_after_confirmation(startup, date(2026, 9, 1)) is True


def test_mapping_failure_does_not_stop_intraday_collector() -> None:
    class FailedMapping:
        @staticmethod
        def sync_all(*, deadline=None) -> str:
            raise RuntimeError("temporary mapping failure")

    runner = object.__new__(CollectorRunner)
    runner.sector_mapping = FailedMapping()

    runner._run_sector_mapping()


def test_sector_mapping_deadline_is_enforced() -> None:
    with pytest.raises(SectorMappingDeadlineExceeded):
        SectorMappingSync._remaining_seconds(
            datetime.now(SHANGHAI) - timedelta(seconds=1)
        )


def test_daily_collection_runs_at_1600_when_collector_is_already_resident() -> None:
    before = datetime(2026, 8, 31, 15, 59, 59, tzinfo=SHANGHAI)
    assert next_daily_collection_at(before) == datetime(
        2026, 8, 31, 16, 0, tzinfo=SHANGHAI
    )


def test_collector_start_after_1600_catches_up_daily_collection() -> None:
    after = datetime(2026, 8, 31, 16, 21, 35, tzinfo=SHANGHAI)
    assert next_daily_collection_at(after) == after


def test_daily_failure_does_not_stop_intraday_collector() -> None:
    class Writer:
        initialized = False

        def initialize_daily_task_plan(self, trade_date: date) -> None:
            self.initialized = True

        @staticmethod
        def daily_seed_complete() -> bool:
            return True

        @staticmethod
        def daily_status(trade_date: date, task_name: str) -> str | None:
            return None

    class FailedDailyPipeline:
        @staticmethod
        def run_pipeline(trade_date: date, *, initialize: bool) -> None:
            raise RuntimeError("temporary daily failure")

    runner = object.__new__(CollectorRunner)
    runner.writer = Writer()
    runner.daily_pipeline = FailedDailyPipeline()

    assert runner._run_daily_collection(date(2026, 8, 31)) is False
    assert runner.writer.initialized is True


def test_daily_worker_can_run_while_closing_node_is_still_retrying(
    monkeypatch,
) -> None:
    trade_date = date(2026, 8, 31)
    calls: list[date] = []

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 16, 1, tzinfo=SHANGHAI)

    monkeypatch.setattr(runner_module, "datetime", FixedDateTime)
    runner = object.__new__(CollectorRunner)
    runner.daily_collection_job = lambda value: calls.append(value) or True

    runner._daily_collection_retry_loop(trade_date, run_immediately=True)

    assert calls == [trade_date]
