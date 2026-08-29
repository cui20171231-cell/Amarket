from __future__ import annotations

from app.hithink.models import DerivedSnapshot, RawSnapshot


def _empty(
    raw: RawSnapshot, previous_trade_day_turnover: int | None = None
) -> DerivedSnapshot:
    previous_day_delta = (
        raw.turnover - previous_trade_day_turnover
        if raw.turnover is not None and previous_trade_day_turnover is not None
        else None
    )
    previous_day_pct = (
        previous_day_delta / previous_trade_day_turnover
        if previous_day_delta is not None and previous_trade_day_turnover > 0
        else None
    )
    return DerivedSnapshot(
        raw,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        previous_trade_day_turnover,
        previous_day_delta,
        previous_day_pct,
    )


def calculate(
    raw: RawSnapshot,
    previous: DerivedSnapshot | None,
    allowed: bool,
    previous_trade_day_turnover: int | None = None,
) -> DerivedSnapshot:
    if not allowed or previous is None:
        return _empty(raw, previous_trade_day_turnover)
    prior = previous.raw
    turnover_delta = (
        raw.turnover - prior.turnover
        if raw.turnover is not None
        and prior.turnover is not None
        and raw.turnover >= prior.turnover
        else None
    )
    volume_delta = (
        raw.volume - prior.volume
        if raw.volume is not None
        and prior.volume is not None
        and raw.volume >= prior.volume
        else None
    )
    growth = None
    if (
        turnover_delta is not None
        and previous.turnover_delta_1m is not None
        and previous.turnover_delta_1m > 0
    ):
        growth = turnover_delta / previous.turnover_delta_1m - 1
    volume_ratio = None
    if (
        volume_delta is not None
        and previous.volume_delta_1m is not None
        and previous.volume_delta_1m > 0
    ):
        volume_ratio = volume_delta / previous.volume_delta_1m
    previous_day_delta = (
        raw.turnover - previous_trade_day_turnover
        if raw.turnover is not None and previous_trade_day_turnover is not None
        else None
    )
    previous_day_pct = (
        previous_day_delta / previous_trade_day_turnover
        if previous_day_delta is not None and previous_trade_day_turnover > 0
        else None
    )
    return DerivedSnapshot(
        raw=raw,
        turnover_delta_1m=turnover_delta,
        volume_delta_1m=volume_delta,
        turnover_growth_1m=growth,
        volume_ratio_1m=volume_ratio,
        new_high_flag=(
            int(raw.high_price > prior.high_price)
            if raw.high_price is not None and prior.high_price is not None
            else None
        ),
        new_low_flag=(
            int(raw.low_price < prior.low_price)
            if raw.low_price is not None and prior.low_price is not None
            else None
        ),
        price_delta_1m=(
            raw.last_price - prior.last_price
            if raw.last_price is not None and prior.last_price is not None
            else None
        ),
        price_change_1m_pct=(raw.last_price / prior.last_price - 1) * 100
        if raw.last_price is not None and prior.last_price is not None and prior.last_price > 0
        else None,
        prev_trade_day_same_time_turnover=previous_trade_day_turnover,
        turnover_prev_trade_day_delta=previous_day_delta,
        turnover_prev_trade_day_pct=previous_day_pct,
    )
