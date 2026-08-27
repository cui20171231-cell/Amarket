from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class RawSnapshot:
    trade_date: date
    scheduled_time: datetime
    source_timestamp: int
    source_time: datetime
    session: str
    batch_id: str
    thscode: str
    ticker: str
    last_price: float | None
    price_change: float | None
    price_change_ratio_pct: float | None
    open_price: float | None
    high_price: float | None
    low_price: float | None
    prev_price: float | None
    volume: int | None
    turnover: int | None

    @classmethod
    def from_api(cls, item: dict[str, Any], **context: Any) -> RawSnapshot:
        return cls(
            **context,
            thscode=str(item["thscode"]),
            ticker=str(item["ticker"]),
            last_price=_optional_float(item.get("last_price")),
            price_change=_optional_float(item.get("price_change")),
            price_change_ratio_pct=_optional_float(item.get("price_change_ratio_pct")),
            open_price=_optional_float(item.get("open_price")),
            high_price=_optional_float(item.get("high_price")),
            low_price=_optional_float(item.get("low_price")),
            prev_price=_optional_float(item.get("prev_price")),
            volume=_optional_int(item.get("volume")),
            turnover=_optional_int(item.get("turnover")),
        )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True)
class DerivedSnapshot:
    raw: RawSnapshot
    turnover_delta_1m: int | None
    volume_delta_1m: int | None
    turnover_growth_1m: float | None
    volume_ratio_1m: float | None
    new_high_flag: int | None
    new_low_flag: int | None
    price_delta_1m: float | None
    price_change_1m_pct: float | None
