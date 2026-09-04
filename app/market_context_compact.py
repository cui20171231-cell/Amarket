"""Compact minute-level market context into the AI-facing contract."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

VERSION = "COMPACT_CONTEXT_1M_V2"

STOCK_TRAJECTORY_SCHEMA = [
    "time",
    "price",
    "change_pct",
    "turnover",
    "turnover_1m",
    "price_change_1m_pct",
    "new_high",
    "new_low",
    "limit_up",
    "limit_break",
    "event",
]

REFERENCE_STOCK_SCHEMA = [
    "ticker",
    "name",
    "change_pct",
    "turnover",
    "turnover_1m",
    "turnover_rank",
    "gain_rank",
    "limit_up",
    "limit_break",
]

SECTOR_TRAJECTORY_SCHEMA = [
    "time",
    "change_pct",
    "up_ratio",
    "turnover_1m",
    "instant_share",
    "cum_share",
    "limit_up",
    "limit_break",
    "new_high_ratio",
]

KEY_STOCK_SCHEMA = [
    "ticker",
    "name",
    "change_pct",
    "change_1m_pct",
    "turnover",
    "turnover_1m",
    "sector_turnover_rank",
    "sector_gain_rank",
    "limit_up",
    "limit_break",
    "new_high",
]


def _round_two(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, (float, Decimal)):
        return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _round_two(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_two(item) for item in value]
    return value


def _short_time(value: Any, *, seconds: bool = False) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S" if seconds else "%H:%M")
    if isinstance(value, str) and "T" in value:
        length = 8 if seconds else 5
        return value.split("T", 1)[1][:length]
    return value


def _drop_nulls(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _quality(response: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = response.get("resolved") or {}
    status = (context or {}).get("data_status") or resolved.get("data_status") or "CURRENT"
    fallback = bool((context or {}).get("is_fallback", resolved.get("is_fallback", False)))
    age = (context or {}).get("source_age_seconds")
    if age is None:
        age = resolved.get("source_age_seconds", 0)
    return {"status": status, "fallback": fallback, "source_age_seconds": age}


def _stock_current(source: dict[str, Any]) -> dict[str, Any]:
    return _drop_nulls(
        {
            "price": source.get("price"),
            "change_pct": source.get("change_pct"),
            "change_1m_pct": source.get("change_1m_pct"),
            "change_5m_pct": source.get("change_5m_pct"),
            "change_15m_pct": source.get("change_15m_pct"),
            "turnover": source.get("turnover"),
            "turnover_1m": source.get("turnover_1m"),
            "turnover_5m": source.get("turnover_5m"),
            "turnover_15m": source.get("turnover_15m"),
            "prev_turnover_1m": source.get("prev_turnover_1m"),
            "prev_turnover_5m": source.get("prev_turnover_5m"),
            "prev_turnover_15m": source.get("prev_turnover_15m"),
            "turnover_1m_change_pct": source.get("turnover_1m_change_pct"),
            "turnover_5m_change_pct": source.get("turnover_5m_change_pct"),
            "turnover_15m_change_pct": source.get("turnover_15m_change_pct"),
            "new_high": source.get("new_high"),
            "new_low": source.get("new_low"),
            "limit_up": source.get("limit_up"),
            "limit_down": source.get("limit_down"),
            "limit_break": source.get("limit_break"),
        }
    )


def _comparison_window(source: dict[str, Any]) -> dict[str, Any]:
    window = source.get("comparison_window") or {}
    return _drop_nulls(
        {
            "current_time": _short_time(window.get("current_time")),
            "prev_1m_time": _short_time(window.get("prev_1m_time")),
            "prev_5m_time": _short_time(window.get("prev_5m_time")),
            "prev_15m_time": _short_time(window.get("prev_15m_time")),
        }
    )


def _stock_trajectory(source: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for item in source:
        time_text = _short_time(item.get("time"))
        opening_transition = time_text == "09:30"
        rows.append(
            [
                time_text,
                item.get("price"),
                item.get("change_pct"),
                item.get("turnover"),
                item.get("turnover_1m"),
                item.get("price_change_1m_pct"),
                None if opening_transition else item.get("new_high"),
                None if opening_transition else item.get("new_low"),
                item.get("limit_up"),
                item.get("limit_break"),
                item.get("event") or [],
            ]
        )
    return {"schema": STOCK_TRAJECTORY_SCHEMA, "rows": rows}


def _selection_reasons(direction: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    facts = direction.get("selection_facts") or {}
    breakdown = facts.get("score_breakdown") or {}
    profile = breakdown.get("profile_facts") or {}
    position = direction.get("stock_position") or {}
    reasons: list[str] = []
    if profile.get("strategic_theme"):
        reasons.append("战略方向")
    if position.get("top5_turnover_carrier"):
        reasons.append("个股成交承载")
    correlation = profile.get("recent_60m_correlation")
    if correlation is None:
        correlation = profile.get("intraday_correlation")
    if isinstance(correlation, (int, float)) and correlation >= 0.3:
        reasons.append("盘中共同运动")
    excess = position.get("excess_change_pct")
    if isinstance(excess, (int, float)) and abs(excess) <= 1:
        reasons.append("当前相对贴合")
    if not reasons:
        reasons.append("方向筛选得分")
    return reasons, profile


def _direction_current(source: dict[str, Any]) -> dict[str, Any]:
    return _drop_nulls(
        {
            "change_pct": source.get("change_pct"),
            "change_1m_pct": source.get("change_1m_pct"),
            "change_5m_pct": source.get("change_5m_pct"),
            "change_15m_pct": source.get("change_15m_pct"),
            "up_ratio": source.get("up_ratio"),
            "up_ratio_change_1m": source.get("up_ratio_change_1m"),
            "up_ratio_change_5m": source.get("up_ratio_change_5m"),
            "up_ratio_change_15m": source.get("up_ratio_change_15m"),
            "turnover": source.get("turnover"),
            "turnover_1m": source.get("turnover_1m"),
            "turnover_5m": source.get("turnover_5m"),
            "turnover_15m": source.get("turnover_15m"),
            "turnover_1m_change_pct": source.get("turnover_1m_change_pct"),
            "turnover_5m_change_pct": source.get("turnover_5m_change_pct"),
            "turnover_15m_change_pct": source.get("turnover_15m_change_pct"),
            "instant_share": source.get("instant_market_share"),
            "instant_share_change_1m": source.get("instant_share_change_1m"),
            "instant_share_change_5m": source.get("instant_share_change_5m"),
            "instant_share_change_15m": source.get("instant_share_change_15m"),
            "cum_share": source.get("cum_market_share"),
            "limit_up": source.get("limit_up_count"),
            "limit_down": source.get("limit_down_count"),
            "limit_break": source.get("limit_break_count"),
            "new_high": source.get("new_high_count"),
            "new_low": source.get("new_low_count"),
        }
    )


def _stock_position(source: dict[str, Any]) -> dict[str, Any]:
    return _drop_nulls(
        {
            "stock_turnover_rank": source.get("turnover_rank"),
            "stock_turnover_1m_rank": source.get("turnover_1m_rank"),
            "stock_gain_rank": source.get("gain_rank"),
            "stock_turnover_share_pct": source.get("turnover_share_pct"),
            "stock_turnover_1m_share_pct": source.get("turnover_1m_share_pct"),
            "excess_change_pct": source.get("excess_change_pct"),
            "relative_strength": source.get("relative_strength"),
            "top5_turnover_carrier": source.get("top5_turnover_carrier"),
            "top5_price_expression": source.get("top5_price_expression"),
        }
    )


def _reference_row(item: dict[str, Any]) -> list[Any]:
    return [
        item.get("thscode") or item.get("ticker"),
        item.get("name"),
        item.get("change_pct"),
        item.get("turnover"),
        item.get("turnover_1m"),
        item.get("turnover_rank"),
        item.get("gain_rank"),
        item.get("limit_up"),
        item.get("limit_break"),
    ]


def _references(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": REFERENCE_STOCK_SCHEMA,
        "turnover_carriers": [
            _reference_row(item) for item in (source.get("turnover_carriers") or [])[:3]
        ],
        "strong_price": [
            _reference_row(item) for item in (source.get("strong_price_expression") or [])[:3]
        ],
        "important_weak": [
            _reference_row(item) for item in (source.get("important_weak") or [])[:3]
        ],
    }


def _compact_direction(source: dict[str, Any]) -> dict[str, Any]:
    reasons, profile = _selection_reasons(source)
    facts = source.get("selection_facts") or {}
    current = source.get("current_state") or {}
    return {
        "sector_name": source.get("sector_name"),
        "sector_type": source.get("sector_type"),
        "sector_id": source.get("sector_id"),
        "selection_score": facts.get("selection_score"),
        "strategic_theme": profile.get("strategic_theme"),
        "strategic_subtheme": profile.get("strategic_subtheme"),
        "selection_reasons": reasons,
        "comparison_window": _comparison_window(current),
        "sector_current": _direction_current(current),
        "stock_position": _stock_position(source.get("stock_position") or {}),
        "reference_stocks": _references(source.get("reference_stocks") or {}),
    }


def _roles(context: dict[str, Any]) -> list[str]:
    current = context.get("current_state") or {}
    directions = context.get("primary_sectors") or []
    roles: list[str] = []
    if any((item.get("stock_position") or {}).get("top5_price_expression") for item in directions):
        roles.append("价格核心")
    if any((item.get("stock_position") or {}).get("top5_turnover_carrier") for item in directions):
        roles.append("成交承载核心")
    if current.get("limit_up"):
        roles.append("涨停核心")
    if current.get("new_high"):
        roles.append("新高核心")
    if any(
        isinstance((item.get("stock_position") or {}).get("excess_change_pct"), (int, float))
        and (item.get("stock_position") or {}).get("excess_change_pct") >= 1
        for item in directions
    ):
        roles.append("板块相对强势")
    if not roles:
        roles.append("跟随")
    return roles


def _alternatives(context: dict[str, Any]) -> list[list[Any]]:
    result = []
    for item in (context.get("direction_selection") or {}).get("next_alternatives") or []:
        breakdown = item.get("score_breakdown") or {}
        profile = breakdown.get("profile_facts") or {}
        result.append(
            [
                item.get("sector_name"),
                item.get("selection_score"),
                profile.get("strategic_theme"),
            ]
        )
    return result[:3]


def _compact_stock(context: dict[str, Any], resolved: dict[str, Any]) -> dict[str, Any]:
    current = context.get("current_state") or {}
    current_compact = _stock_current(current)
    if _short_time(resolved.get("actual_time")) == "09:30":
        current_compact["new_high"] = None
        current_compact["new_low"] = None
    result = {
        "target": {
            "trade_date": str(resolved.get("trade_date")),
            "time": _short_time(resolved.get("actual_time")),
            "requested_time": _short_time(resolved.get("requested_time")),
            "actual_time": _short_time(resolved.get("actual_time"), seconds=True),
            "ticker": context.get("thscode") or context.get("ticker"),
            "name": context.get("name"),
        },
        "quality": _quality({"resolved": resolved}, context),
        "comparison_window": _comparison_window(current),
        "stock_current": current_compact,
        "stock_trajectory": _stock_trajectory(context.get("key_trajectory") or []),
        "directions": [
            _compact_direction(item) for item in (context.get("primary_sectors") or [])[:5]
        ],
        "alternatives": _alternatives(context),
        "roles": _roles(context),
    }
    return result


def _key_stock_row(item: dict[str, Any]) -> list[Any]:
    return [
        item.get("thscode") or item.get("ticker"),
        item.get("name"),
        item.get("change_pct"),
        item.get("change_1m_pct"),
        item.get("turnover"),
        item.get("turnover_1m"),
        item.get("turnover_rank"),
        item.get("gain_rank"),
        item.get("limit_up"),
        item.get("limit_break"),
        item.get("new_high"),
    ]


def _key_stocks(source: dict[str, Any]) -> dict[str, Any]:
    combined = []
    seen: set[str] = set()
    for group in (source.get("limit_up") or [], source.get("new_high") or []):
        for item in group:
            code = str(item.get("thscode") or item.get("ticker"))
            if code not in seen:
                combined.append(item)
                seen.add(code)
            if len(combined) >= 5:
                break
    return {
        "schema": KEY_STOCK_SCHEMA,
        "turnover_carriers": [
            _key_stock_row(item) for item in (source.get("turnover_top") or [])[:5]
        ],
        "strong_price": [_key_stock_row(item) for item in (source.get("price_gain_top") or [])[:5]],
        "limit_or_new_high": [_key_stock_row(item) for item in combined[:5]],
        "important_weak": [
            _key_stock_row(item) for item in (source.get("important_weak") or [])[:5]
        ],
    }


def _sector_current(source: dict[str, Any]) -> dict[str, Any]:
    return _drop_nulls(
        {
            "change_pct": source.get("change_pct"),
            "change_1m_pct": source.get("change_1m_pct"),
            "change_5m_pct": source.get("change_5m_pct"),
            "change_15m_pct": source.get("change_15m_pct"),
            "up_count": source.get("up_count"),
            "down_count": source.get("down_count"),
            "up_ratio": source.get("up_ratio"),
            "up_ratio_change_1m": source.get("up_ratio_change_1m"),
            "up_ratio_change_5m": source.get("up_ratio_change_5m"),
            "up_ratio_change_15m": source.get("up_ratio_change_15m"),
            "turnover": source.get("turnover"),
            "turnover_1m": source.get("turnover_1m"),
            "turnover_5m": source.get("turnover_5m"),
            "turnover_15m": source.get("turnover_15m"),
            "prev_turnover_1m": source.get("prev_turnover_1m"),
            "prev_turnover_5m": source.get("prev_turnover_5m"),
            "prev_turnover_15m": source.get("prev_turnover_15m"),
            "turnover_1m_change_pct": source.get("turnover_1m_change_pct"),
            "turnover_5m_change_pct": source.get("turnover_5m_change_pct"),
            "turnover_15m_change_pct": source.get("turnover_15m_change_pct"),
            "cum_share": source.get("cum_market_share"),
            "instant_share": source.get("instant_market_share"),
            "instant_share_change_1m": source.get("instant_share_change_1m"),
            "instant_share_change_5m": source.get("instant_share_change_5m"),
            "instant_share_change_15m": source.get("instant_share_change_15m"),
            "cum_rank": source.get("cum_rank"),
            "instant_rank": source.get("instant_rank"),
            "limit_up": source.get("limit_up_count"),
            "limit_down": source.get("limit_down_count"),
            "limit_break": source.get("limit_break_count"),
            "new_high": source.get("new_high_count"),
            "new_low": source.get("new_low_count"),
        }
    )


def _sector_trajectory(source: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        [
            _short_time(item.get("time")),
            item.get("change_pct"),
            item.get("up_ratio"),
            item.get("turnover_1m"),
            item.get("instant_share"),
            item.get("cum_share"),
            item.get("limit_up_count"),
            item.get("limit_break_count"),
            item.get("new_high_ratio"),
        ]
        for item in source
    ]
    return {"schema": SECTOR_TRAJECTORY_SCHEMA, "rows": rows}


def _core_stock_trajectories(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ticker": item.get("ticker"),
            "name": item.get("name"),
            "trajectory": _stock_trajectory(item.get("trajectory") or []),
        }
        for item in source
    ]


def _compact_sector(response: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    resolved = response.get("resolved") or {}
    resolution = context.get("resolution") or {}
    current = context.get("current_state") or {}
    current_compact = _sector_current(current)
    if _short_time(resolved.get("actual_time")) == "09:30":
        current_compact["new_high"] = None
        current_compact["new_low"] = None
    return {
        "status": response.get("status"),
        "version": VERSION,
        "target": {
            "trade_date": str(resolved.get("trade_date")),
            "time": _short_time(resolved.get("actual_time")),
            "requested_time": _short_time(resolved.get("requested_time")),
            "actual_time": _short_time(resolved.get("actual_time"), seconds=True),
            "sector_id": resolution.get("sector_id"),
            "sector_name": resolution.get("sector_name"),
            "sector_type": resolution.get("sector_type"),
        },
        "quality": _quality(response, current),
        "comparison_window": _comparison_window(current),
        "sector_current": current_compact,
        "sector_trajectory": _sector_trajectory(context.get("key_trajectory") or []),
        "market_position": _drop_nulls(
            {
                "candidate_rank": current.get("candidate_rank"),
                "cum_rank": current.get("cum_rank"),
                "instant_rank": current.get("instant_rank"),
                "cum_market_share": current.get("cum_market_share"),
                "instant_market_share": current.get("instant_market_share"),
            }
        ),
        "key_stocks": _key_stocks(context.get("key_stocks") or {}),
        "core_stock_trajectories": _core_stock_trajectories(
            context.get("core_stock_trajectories") or []
        ),
        "strategic_relation": context.get("strategic_relation") or [],
        "matched_sectors": context.get("matched_sectors") or [],
    }


def compact_market_context_response(response: dict[str, Any]) -> dict[str, Any]:
    """Return only facts required by the model while retaining the status contract."""
    if response.get("status") not in {"OK", "PARTIAL"}:
        return _round_two(response)
    target_type = response.get("target_type")
    data = response.get("data") or {}
    resolved = response.get("resolved") or {}
    if target_type == "sector":
        result = _compact_sector(response, data.get("sector") or {})
    elif target_type == "stock":
        contexts = data.get("stocks") or []
        if not contexts:
            return _round_two(response)
        result = {
            "status": response.get("status"),
            "version": VERSION,
            **_compact_stock(contexts[0], resolved),
        }
    else:
        result = {
            "status": response.get("status"),
            "version": VERSION,
            "target": {
                "trade_date": str(resolved.get("trade_date")),
                "time": _short_time(resolved.get("actual_time")),
                "requested_time": _short_time(resolved.get("requested_time")),
                "actual_time": _short_time(resolved.get("actual_time"), seconds=True),
            },
            "quality": _quality(response),
            "stocks": [_compact_stock(context, resolved) for context in (data.get("stocks") or [])],
        }
    return _round_two(result)
