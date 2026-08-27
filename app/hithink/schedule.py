from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ScheduleNode:
    trade_date: date
    scheduled_time: datetime
    session: str
    sequence_no: int

    @property
    def collection_id(self) -> str:
        return f"{self.trade_date:%Y%m%d}{self.sequence_no:03d}"


def _at(trade_date: date, value: time) -> datetime:
    return datetime.combine(trade_date, value, SHANGHAI)


def _every_minute(
    trade_date: date, start: time, end: time, session: str
) -> list[tuple[datetime, str]]:
    current = _at(trade_date, start)
    finish = _at(trade_date, end)
    result: list[tuple[datetime, str]] = []
    while current <= finish:
        result.append((current, session))
        current += timedelta(minutes=1)
    return result


def build_daily_schedule(trade_date: date) -> list[ScheduleNode]:
    slots: list[tuple[datetime, str]] = []
    slots.extend(_every_minute(trade_date, time(9, 15), time(9, 25), "auction_open"))
    slots.extend(_every_minute(trade_date, time(9, 30, 15), time(11, 30, 15), "continuous_am"))
    slots.extend(_every_minute(trade_date, time(13, 0, 15), time(14, 56, 15), "continuous_pm"))
    slots.extend(
        (_at(trade_date, value), "auction_close")
        for value in (time(14, 56, 55), time(14, 57), time(14, 58), time(14, 59), time(15))
    )
    nodes = [
        ScheduleNode(trade_date, scheduled, session, index)
        for index, (scheduled, session) in enumerate(slots, 1)
    ]
    if len(nodes) != 254:
        raise RuntimeError(f"schedule invariant failed: expected 254 nodes, got {len(nodes)}")
    return nodes


def allows_one_minute_derivation(current: ScheduleNode, previous: ScheduleNode | None) -> bool:
    return bool(
        previous
        and current.trade_date == previous.trade_date
        and current.session == previous.session
        and current.scheduled_time - previous.scheduled_time == timedelta(minutes=1)
    )
