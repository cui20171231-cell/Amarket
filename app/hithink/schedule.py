from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEDULE_V2_EFFECTIVE_DATE = date(2026, 9, 2)
CLOSING_AUCTION_SECOND_EFFECTIVE_DATE = date(2026, 9, 4)
SCHEDULE_V3_EFFECTIVE_DATE = date(2026, 9, 14)

# Shared fixed axis for 15-minute derivatives and the 19 market review packages.
MARKET_REVIEW_NODE_SEQUENCES = frozenset(
    {11, 12, 27, 42, 57, 72, 87, 102, 117, 132, 133, 148, 163, 178, 193, 208, 223, 238, 254}
)


@dataclass(frozen=True)
class ScheduleNode:
    trade_date: date
    scheduled_time: datetime
    session: str
    sequence_no: int

    @property
    def collection_id(self) -> str:
        return f"{self.trade_date:%Y%m%d}{self.sequence_no:03d}"

    @property
    def limit_pools_applicable(self) -> bool:
        """Three limit pools are meaningful only outside live call-auction samples.

        Sequence 011 is captured after the 09:25 opening match, sequence 249
        is the last continuous sample, and sequence 254 is the real post-close
        baseline.  Those three nodes therefore remain applicable.
        """
        closing_auction_start = 250 if self.trade_date >= SCHEDULE_V3_EFFECTIVE_DATE else 251
        return not (
            1 <= self.sequence_no <= 10
            or closing_auction_start <= self.sequence_no <= 253
        )


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
    shifted = trade_date >= SCHEDULE_V2_EFFECTIVE_DATE
    regular_second = 8 if shifted else 15
    closing_auction_second = (
        8 if trade_date >= CLOSING_AUCTION_SECOND_EFFECTIVE_DATE else 0
    )
    slots: list[tuple[datetime, str]] = []
    slots.extend(
        _every_minute(
            trade_date,
            time(9, 15, regular_second),
            time(9, 25, regular_second),
            "auction_open",
        )
    )
    slots.extend(
        _every_minute(
            trade_date,
            time(9, 30, regular_second),
            time(11, 30, regular_second),
            "continuous_am",
        )
    )
    slots.extend(
        _every_minute(
            trade_date,
            time(13, 0, regular_second),
            time(14, 56, regular_second),
            "continuous_pm",
        )
    )
    if trade_date >= SCHEDULE_V3_EFFECTIVE_DATE:
        closing_times = (
            time(14, 57, 8),
            time(14, 58, 8),
            time(14, 59, 8),
            time(15, 0, 8),
            time(15, 30, 8),
        )
    else:
        pre_close_second = 53 if shifted else 55
        closing_times = (
            time(14, 56, pre_close_second),
            time(14, 57, closing_auction_second),
            time(14, 58, closing_auction_second),
            time(14, 59, closing_auction_second),
            time(15, 0, closing_auction_second),
        )
    slots.extend(
        (_at(trade_date, value), "auction_close") for value in closing_times
    )
    nodes = [
        ScheduleNode(trade_date, scheduled, session, index)
        for index, (scheduled, session) in enumerate(slots, 1)
    ]
    if len(nodes) != 254:
        raise RuntimeError(f"schedule invariant failed: expected 254 nodes, got {len(nodes)}")
    return nodes
