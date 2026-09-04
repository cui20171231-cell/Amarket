"""Build one read-only, deterministic market-state package from persisted tables."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from app.hithink.candidate_config import CAPITAL_SECTOR_TYPES, CORE_SECTOR_TYPES
from app.hithink.config import Settings
from app.hithink.schedule import MARKET_REVIEW_NODE_SEQUENCES
from app.hithink.writer import ClickHouseWriter

SHANGHAI = ZoneInfo("Asia/Shanghai")
INTERVAL_MINUTES = (30, 45, 60)
SECTOR_REASON_FIELDS = {
    "hit_price_strength": "价格强度",
    "hit_breadth": "板块广度",
    "hit_capital_share": "成交关注度",
    "hit_capital_acceleration": "成交关注加速",
    "hit_limit_strength": "涨停结构",
    "hit_new_high": "新高结构",
    "hit_persistence": "持续性",
}
STOCK_REASON_FIELDS = {
    "hit_price_strength": "价格强度",
    "hit_turnover_absolute": "绝对成交承载代理",
    "hit_turnover_acceleration": "成交加速",
    "hit_new_high": "新高",
    "hit_limit_strength": "涨停或炸板表达",
    "hit_core_sector": "核心板块关联",
    "hit_sector_leader": "板块内部地位",
    "hit_persistence": "持续性",
}

MARKET_DIFF_FIELDS = (
    "up_count",
    "down_count",
    "flat_count",
    "up_ratio",
    "down_ratio",
    "limit_up_count",
    "limit_down_count",
    "limit_break_count",
    "turnover_total",
    "turnover_delta_1m_total",
    "new_high_count",
    "new_low_count",
    "turnover_accel_count",
    "turnover_decel_count",
    "volume_expand_count",
    "volume_contract_count",
    "price_up_1m_count",
    "price_down_1m_count",
    "volume_price_up_count",
    "volume_price_down_count",
    "contract_price_up_count",
    "contract_price_down_count",
)

MARKET_INTRADAY_FIELDS = (
    "source_time",
    "up_count",
    "down_count",
    "flat_count",
    "up_ratio",
    "limit_up_count",
    "up_5_to_limit_count",
    "up_1_to_5_count",
    "down_1_to_5_count",
    "down_5_to_limit_count",
    "limit_down_count",
    "limit_break_count",
    "turnover_total",
    "turnover_delta_1m_total",
    "turnover_prev_day_pct",
    "new_high_count",
    "new_low_count",
    "price_up_1m_count",
    "price_down_1m_count",
)

EMOTION_BUSINESS_FIELDS = (
    "limit_up_count",
    "limit_down_count",
    "limit_break_count",
    "limit_attempt_count",
    "limit_success_rate",
    "limit_break_rate",
    "first_board_count",
    "second_board_count",
    "third_board_count",
    "fourth_board_count",
    "fifth_plus_board_count",
    "max_board_height",
    "promotion_base_count",
    "promotion_success_count",
    "promotion_fail_count",
    "promotion_rate",
    "high_board_count",
    "high_board_break_count",
    "high_board_fail_count",
)

EMOTION_POOL_SOURCE_FIELDS = (
    "pool_data_status",
    "pool_source_scheduled_time",
    "pool_source_age_seconds",
)

CAPITAL_FIELDS = (
    "sector_type",
    "sector_code",
    "sector_name",
    "state_data_status",
    "state_source_scheduled_time",
    "state_source_age_seconds",
    "turnover_market_share_pct",
    "turnover_1m_market_share_pct",
    "turnover_market_share_delta_15m",
    "turnover_1m_market_share_delta_15m",
    "turnover_increment_15m",
    "turnover_increment_market_share_pct",
    "turnover_share_rank",
    "turnover_1m_share_rank",
    "turnover_share_rank_delta",
    "turnover_1m_share_rank_delta",
    "up_ratio",
    "up_ratio_delta_15m",
    "limit_up_count",
    "limit_break_count",
    "new_high_ratio",
    "new_high_ratio_delta_15m",
    "turnover_1m_top1_share_pct",
    "turnover_1m_top3_share_pct",
    "turnover_1m_top5_share_pct",
)

CORE_TRAJECTORY_STATE_FIELDS = (
    "index_change_ratio_pct",
    "index_change_1m_pct",
    "up_ratio",
    "down_ratio",
    "turnover_market_share_pct",
    "turnover_1m_market_share_pct",
    "limit_up_count",
    "limit_break_count",
    "new_high_ratio",
    "new_low_ratio",
)

CORE_STATE_TABLES = {
    "concept": "market.hithink_concept_state",
    "industry": "market.hithink_industry_state",
}

OPEN_AUCTION_NODE_SEQUENCES = tuple(range(1, 12))
OPEN_AUCTION_CURRENT_SCHEMA = [
    "stock_code",
    "stock_name",
    "auction_price",
    "auction_pct",
    "auction_amount",
    "auction_amount_rank",
    "auction_turnover_pct",
    "auction_yesterday_ratio_pct",
    "auction_unmatched",
]
OPEN_AUCTION_TRAJECTORY_SCHEMA = [
    "stock_code",
    "time",
    "auction_phase",
    "auction_pct",
    "auction_amount",
    "auction_turnover_pct",
    "auction_yesterday_ratio_pct",
    "auction_unmatched",
]
OPEN_AUCTION_MARKET_TRAJECTORY_SCHEMA = [
    "time",
    "auction_phase",
    "valid_count",
    "up_count",
    "down_count",
    "flat_count",
    "up_ratio",
    "down_ratio",
    "up_5_count",
    "up_2_to_5_count",
    "up_0_to_2_count",
    "down_0_to_2_count",
    "down_2_to_5_count",
    "down_5_count",
    "limit_up_or_near_count",
    "limit_down_or_near_count",
    "auction_amount_total",
    "auction_amount_valid_count",
    "yesterday_ratio_valid_count",
    "yesterday_ratio_gt_100_count",
    "yesterday_ratio_gt_200_count",
]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").rstrip("\x00")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").rstrip("\x00")
    return str(value)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _auction_number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if isfinite(number) else None


def _round_auction_number(value: Any) -> float | None:
    number = _auction_number(value)
    if number is None:
        return None
    return float(
        Decimal(str(number)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def _subtract(current: Any, base: Any) -> Any:
    if current is None or base is None:
        return None
    return current - base


def _pick(row: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if row is None:
        return None
    return {field: row.get(field) for field in fields}


def _strip_internal(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        key: value
        for key, value in row.items()
        if key not in {"version_time", "calculated_at", "created_at", "updated_at"}
    }


def _parse_target_time(value: str) -> time:
    if len(value) not in {5, 8}:
        raise ValueError("target_time必须是HH:MM或HH:MM:SS，例如10:30")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("target_time必须是HH:MM或HH:MM:SS，例如10:30") from exc
    if parsed.tzinfo is not None:
        raise ValueError("target_time不能携带时区")
    return parsed


def _business_period(target: datetime) -> tuple[str, datetime, datetime]:
    value = target.timetz().replace(tzinfo=None)
    if time(9, 25) <= value <= time(11, 30, 59):
        return (
            "AM",
            datetime.combine(target.date(), time(9, 25), SHANGHAI),
            datetime.combine(target.date(), time(11, 30, 59), SHANGHAI),
        )
    if time(13, 0) <= value <= time(15, 0):
        return (
            "PM",
            datetime.combine(target.date(), time(13, 0), SHANGHAI),
            datetime.combine(target.date(), time(15, 0), SHANGHAI),
        )
    return (
        "OUTSIDE",
        datetime.combine(target.date(), time.min, SHANGHAI),
        datetime.combine(target.date(), time.max, SHANGHAI),
    )


class MarketStatePackageBuilder:
    """Compose the six persisted layers without writing a seventh table."""

    def __init__(self, client: Any):
        self.client = client

    def _rows(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        result = self.client.query(
            sql,
            parameters=parameters or {},
            settings={"readonly": 1, "max_execution_time": 60},
        )
        columns = list(result.column_names)
        return [dict(zip(columns, row, strict=True)) for row in result.result_rows]

    def _one(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        rows = self._rows(sql, parameters)
        return rows[0] if rows else None

    def _resolve_trade_date(self, requested: str | None) -> date:
        if requested:
            try:
                return date.fromisoformat(requested)
            except ValueError as exc:
                raise ValueError("trade_date必须是YYYY-MM-DD") from exc
        row = self._one(
            "SELECT max(trade_date) trade_date FROM market.hithink_market_delta_15m FINAL"
        )
        if row is None or row.get("trade_date") is None:
            raise ValueError("市场15分钟状态表中没有可恢复的交易日")
        return row["trade_date"]

    def _resolve_target(self, trade_date_value: date, requested: datetime) -> dict[str, Any]:
        row = self._one(
            """
            SELECT toString(trade_date) trade_date,toString(collection_id) collection_id,
                node_seq,scheduled_time,session
            FROM
            (
                SELECT *,node_seq FROM market.hithink_market_delta_15m FINAL
                WHERE trade_date={trade_date:Date}
            )
            ORDER BY abs(dateDiff('millisecond',scheduled_time,
                {requested:DateTime64(3,'Asia/Shanghai')})),scheduled_time
            LIMIT 1
            """,
            {"trade_date": trade_date_value, "requested": requested},
        )
        if row is None:
            raise ValueError(f"{trade_date_value}没有市场状态检查点")
        row["requested_time"] = requested
        row["resolution_distance_seconds"] = abs(
            (row["scheduled_time"] - requested).total_seconds()
        )
        return row

    def _table_row(
        self, table: str, trade_date_value: date, collection_id: str
    ) -> dict[str, Any] | None:
        return self._one(
            f"""
            SELECT *,node_seq FROM {table} FINAL
            WHERE trade_date={{trade_date:Date}}
              AND toString(collection_id)={{collection_id:String}}
            LIMIT 1
            """,
            {"trade_date": trade_date_value, "collection_id": collection_id},
        )

    def _last_target_nodes(
        self, trade_date_value: date, target: datetime
    ) -> list[dict[str, Any]]:
        _, period_start, period_end = _business_period(target)
        rows = self._rows(
            """
            SELECT toString(collection_id) collection_id,node_seq,scheduled_time
            FROM
            (
                SELECT *,node_seq FROM market.hithink_market_delta_15m FINAL
                WHERE trade_date={trade_date:Date}
                  AND scheduled_time>={period_start:DateTime64(3,'Asia/Shanghai')}
                  AND scheduled_time<={period_end:DateTime64(3,'Asia/Shanghai')}
                  AND scheduled_time<={target:DateTime64(3,'Asia/Shanghai')}
            )
            ORDER BY scheduled_time DESC
            LIMIT 5
            """,
            {
                "trade_date": trade_date_value,
                "period_start": period_start,
                "period_end": period_end,
                "target": target,
            },
        )
        return list(reversed(rows))

    def _intraday_target_nodes(
        self, trade_date_value: date, target: datetime
    ) -> list[dict[str, Any]]:
        """Return the existing fixed review axis from 09:25 through the target node."""
        return self._rows(
            """
            SELECT toString(collection_id) collection_id,node_seq,scheduled_time,
                delta_type,state_data_status,
                toString(state_source_collection_id) state_source_collection_id,
                state_source_scheduled_time,state_source_age_seconds,state_is_fallback
            FROM market.hithink_market_delta_15m FINAL
            WHERE trade_date={trade_date:Date}
              AND scheduled_time<={target:DateTime64(3,'Asia/Shanghai')}
            ORDER BY scheduled_time
            """,
            {"trade_date": trade_date_value, "target": target},
        )

    def _market_intraday_trajectory(
        self,
        trade_date_value: date,
        nodes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_ids = sorted(
            {
                row["state_source_collection_id"]
                for row in nodes
                if row.get("state_source_collection_id")
            }
        )
        state_rows = (
            self._rows(
                f"""
                SELECT toString(collection_id) collection_id,
                    {', '.join(MARKET_INTRADAY_FIELDS)}
                FROM market.hithink_market_state FINAL
                WHERE trade_date={{trade_date:Date}}
                  AND toString(collection_id) IN {{collection_ids:Array(String)}}
                """,
                {"trade_date": trade_date_value, "collection_ids": source_ids},
            )
            if source_ids
            else []
        )
        state_map = {row["collection_id"]: row for row in state_rows}
        result: list[dict[str, Any]] = []
        for node in nodes:
            source_id = node.get("state_source_collection_id")
            state = state_map.get(source_id) if source_id else None
            result.append(
                {
                    "node_seq": node["node_seq"],
                    "scheduled_time": node["scheduled_time"],
                    "delta_type": _text(node.get("delta_type")),
                    "state_data_status": _text(node.get("state_data_status")),
                    "state_source_scheduled_time": node.get(
                        "state_source_scheduled_time"
                    ),
                    "state_source_age_seconds": node.get("state_source_age_seconds"),
                    "state_is_fallback": node.get("state_is_fallback"),
                    **{
                        field: state.get(field) if state else None
                        for field in MARKET_INTRADAY_FIELDS
                    },
                }
            )
        return result

    def _emotion_intraday_trajectory(
        self,
        trade_date_value: date,
        target: datetime,
    ) -> list[dict[str, Any]]:
        """Return fixed review-node emotion facts through the package target."""
        rows = self._rows(
            f"""
            SELECT scheduled_time,{', '.join(EMOTION_POOL_SOURCE_FIELDS)},
                {', '.join(EMOTION_BUSINESS_FIELDS)}
            FROM market.hithink_emotion_state FINAL
            WHERE trade_date={{trade_date:Date}}
              AND node_seq IN {{node_sequences:Array(UInt16)}}
              AND scheduled_time<={{target:DateTime64(3,'Asia/Shanghai')}}
            ORDER BY scheduled_time
            """,
            {
                "trade_date": trade_date_value,
                "node_sequences": sorted(MARKET_REVIEW_NODE_SEQUENCES),
                "target": target,
            },
        )
        return [
            {
                "scheduled_time": row["scheduled_time"],
                **{field: row.get(field) for field in EMOTION_POOL_SOURCE_FIELDS},
                **{field: row.get(field) for field in EMOTION_BUSINESS_FIELDS},
            }
            for row in rows
        ]

    def _market_intervals(
        self,
        trade_date_value: date,
        target_time: datetime,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        period, _, _ = _business_period(target_time)
        if current is None or period == "OUTSIDE" or target_time.time() < time(9, 30):
            return {
                f"{minutes}m": {
                    "status": "NO_BASE",
                    "requested_interval_minutes": minutes,
                    "reason": "当前节点没有连续交易时段基准",
                }
                for minutes in INTERVAL_MINUTES
            }
        continuous_start = datetime.combine(
            trade_date_value, time(9, 30) if period == "AM" else time(13, 0), SHANGHAI
        )
        states = self._rows(
            """
            SELECT *,node_seq FROM market.hithink_market_state FINAL
            WHERE trade_date={trade_date:Date}
              AND scheduled_time>={period_start:DateTime64(3,'Asia/Shanghai')}
              AND scheduled_time<={target:DateTime64(3,'Asia/Shanghai')}
            ORDER BY scheduled_time
            """,
            {
                "trade_date": trade_date_value,
                "period_start": continuous_start,
                "target": target_time,
            },
        )
        result: dict[str, Any] = {}
        for minutes in INTERVAL_MINUTES:
            cutoff = target_time - timedelta(minutes=minutes)
            bases = [row for row in states if row["scheduled_time"] <= cutoff]
            if not bases:
                result[f"{minutes}m"] = {
                    "status": "NO_BASE",
                    "requested_interval_minutes": minutes,
                    "target_cutoff_time": cutoff,
                }
                continue
            base = bases[-1]
            deltas = {
                f"{field}_delta": _subtract(current.get(field), base.get(field))
                for field in MARKET_DIFF_FIELDS
            }
            result[f"{minutes}m"] = {
                "status": "VALID",
                "requested_interval_minutes": minutes,
                "current_source_collection_id": _text(current.get("collection_id")),
                "current_source_scheduled_time": current.get("scheduled_time"),
                "base_collection_id": _text(base.get("collection_id")),
                "base_scheduled_time": base.get("scheduled_time"),
                "actual_span_seconds": int(
                    (current["scheduled_time"] - base["scheduled_time"]).total_seconds()
                ),
                "deltas": deltas,
            }
        return result

    def _capital_block(
        self, rows: list[dict[str, Any]], top_n: int
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        result: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for sector_type in CAPITAL_SECTOR_TYPES:
            typed = [row for row in rows if _text(row.get("sector_type")) == sector_type]
            cumulative_rising = [
                row
                for row in typed
                if (_number(row.get("turnover_market_share_delta_15m")) or 0) > 0
            ]
            cumulative_falling = [
                row
                for row in typed
                if (_number(row.get("turnover_market_share_delta_15m")) or 0) < 0
            ]
            instant_1m_rising = [
                row
                for row in typed
                if (_number(row.get("turnover_1m_market_share_delta_15m")) or 0) > 0
            ]
            instant_1m_falling = [
                row
                for row in typed
                if (_number(row.get("turnover_1m_market_share_delta_15m")) or 0) < 0
            ]
            cumulative_rising.sort(
                key=lambda row: _number(row.get("turnover_market_share_delta_15m"))
                or float("-inf"),
                reverse=True,
            )
            cumulative_falling.sort(
                key=lambda row: _number(row.get("turnover_market_share_delta_15m"))
                or float("inf")
            )
            instant_1m_rising.sort(
                key=lambda row: _number(
                    row.get("turnover_1m_market_share_delta_15m")
                )
                or float("-inf"),
                reverse=True,
            )
            instant_1m_falling.sort(
                key=lambda row: _number(
                    row.get("turnover_1m_market_share_delta_15m")
                )
                or float("inf")
            )
            result[sector_type] = {
                "cumulative_share_rising_top": [
                    _pick(row, CAPITAL_FIELDS) for row in cumulative_rising[:top_n]
                ],
                "cumulative_share_falling_top": [
                    _pick(row, CAPITAL_FIELDS) for row in cumulative_falling[:top_n]
                ],
                "instant_1m_share_rising_top": [
                    _pick(row, CAPITAL_FIELDS) for row in instant_1m_rising[:top_n]
                ],
                "instant_1m_share_falling_top": [
                    _pick(row, CAPITAL_FIELDS) for row in instant_1m_falling[:top_n]
                ],
            }
        return result

    def _candidate_histories(
        self,
        trade_date_value: date,
        nodes: list[dict[str, Any]],
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        node_ids = [row["collection_id"] for row in nodes]
        rows = self._rows(
            """
            SELECT toString(collection_id) collection_id,scheduled_time,sector_type,
                sector_code,candidate_rank
            FROM market.hithink_core_sector_candidate FINAL
            WHERE trade_date={trade_date:Date}
              AND toString(collection_id) IN {collection_ids:Array(String)}
              AND sector_type IN {core_sector_types:Array(String)}
            """,
            {
                "trade_date": trade_date_value,
                "collection_ids": node_ids,
                "core_sector_types": list(CORE_SECTOR_TYPES),
            },
        )
        rank_map = {
            (row["collection_id"], _text(row["sector_type"]), row["sector_code"]): row[
                "candidate_rank"
            ]
            for row in rows
        }
        keys = {(_text(row["sector_type"]), row["sector_code"]) for row in rows}
        result: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for key in keys:
            history = []
            for node in nodes:
                rank = rank_map.get((node["collection_id"], key[0], key[1]))
                history.append(
                    {
                        "scheduled_time": node["scheduled_time"],
                        "node_seq": node["node_seq"],
                        "candidate_rank": rank,
                        "candidate_rank_status": (
                            "RANKED" if rank is not None else "OUTSIDE_TOP_N"
                        ),
                    }
                )
            result[key] = history
        return result

    def _core_sector_trajectories(
        self,
        trade_date_value: date,
        target_time: datetime,
        target_nodes: list[dict[str, Any]],
        selected_rows: list[dict[str, Any]],
    ) -> dict[tuple[str, str], dict[str, list[dict[str, Any]]]]:
        """Build short and key-node trajectories without persisting another fact table."""
        selected_codes: dict[str, list[str]] = {
            sector_type: sorted(
                {
                    row["sector_code"]
                    for row in selected_rows
                    if _text(row.get("sector_type")) == sector_type
                }
            )
            for sector_type in CORE_SECTOR_TYPES
        }
        result = {
            (_text(row["sector_type"]) or "", row["sector_code"]): {
                "recent": [],
                "key_nodes": [],
            }
            for row in selected_rows
        }
        if not result:
            return result

        _, period_start, period_end = _business_period(target_time)

        # Raw state nodes are factual minute history. Candidate evaluation only runs
        # at the 19 fixed target nodes, so raw-node rank and score stay NULL.
        for sector_type, table in CORE_STATE_TABLES.items():
            codes = selected_codes.get(sector_type) or []
            if not codes:
                continue
            rows = self._rows(
                f"""
                SELECT toString(collection_id) collection_id,node_seq,scheduled_time,
                    sector_code,index_change_ratio_pct,index_change_1m_pct,
                    up_ratio,down_ratio,turnover_market_share_pct,
                    turnover_1m_market_share_pct,limit_up_count,limit_break_count,
                    new_high_ratio,new_low_ratio
                FROM {table} FINAL
                WHERE trade_date={{trade_date:Date}}
                  AND sector_code IN {{sector_codes:Array(String)}}
                  AND scheduled_time>={{period_start:DateTime64(3,'Asia/Shanghai')}}
                  AND scheduled_time<={{period_end:DateTime64(3,'Asia/Shanghai')}}
                  AND scheduled_time<={{target:DateTime64(3,'Asia/Shanghai')}}
                ORDER BY sector_code,scheduled_time DESC
                """,
                {
                    "trade_date": trade_date_value,
                    "sector_codes": codes,
                    "period_start": period_start,
                    "period_end": period_end,
                    "target": target_time,
                },
            )
            grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                if len(grouped[row["sector_code"]]) < 5:
                    grouped[row["sector_code"]].append(row)
            for sector_code, sector_rows in grouped.items():
                key = (sector_type, sector_code)
                result[key]["recent"] = [
                    {
                        "scheduled_time": row["scheduled_time"],
                        "node_seq": row["node_seq"],
                        "candidate_rank": None,
                        "candidate_rank_status": "NOT_EVALUATED",
                        "candidate_score_v1": None,
                        **{field: row.get(field) for field in CORE_TRAJECTORY_STATE_FIELDS},
                        "state_data_status": "CURRENT",
                        "state_source_scheduled_time": row["scheduled_time"],
                        "state_source_age_seconds": 0,
                    }
                    for row in reversed(sector_rows)
                ]

        node_ids = [node["collection_id"] for node in target_nodes]
        node_map = {node["collection_id"]: node for node in target_nodes}
        candidate_rows = self._rows(
            """
            SELECT toString(collection_id) collection_id,sector_type,sector_code,
                candidate_rank,candidate_score_v1
            FROM market.hithink_core_sector_candidate FINAL
            WHERE trade_date={trade_date:Date}
              AND toString(collection_id) IN {collection_ids:Array(String)}
              AND sector_type IN {core_sector_types:Array(String)}
            """,
            {
                "trade_date": trade_date_value,
                "collection_ids": node_ids,
                "core_sector_types": list(CORE_SECTOR_TYPES),
            },
        )
        candidate_map = {
            (row["collection_id"], _text(row["sector_type"]), row["sector_code"]): row
            for row in candidate_rows
        }

        for sector_type, table in CORE_STATE_TABLES.items():
            codes = selected_codes.get(sector_type) or []
            if not codes:
                continue
            migration_rows = self._rows(
                """
                SELECT toString(collection_id) collection_id,node_seq,scheduled_time,
                    sector_code,state_data_status,
                    toString(state_source_collection_id) state_source_collection_id,
                    state_source_scheduled_time,state_source_age_seconds,
                    up_ratio,down_ratio,turnover_market_share_pct,
                    turnover_1m_market_share_pct,limit_up_count,limit_break_count,
                    new_high_ratio,new_low_ratio
                FROM market.hithink_sector_capital_migration FINAL
                WHERE trade_date={trade_date:Date}
                  AND sector_type={sector_type:String}
                  AND sector_code IN {sector_codes:Array(String)}
                  AND toString(collection_id) IN {collection_ids:Array(String)}
                """,
                {
                    "trade_date": trade_date_value,
                    "sector_type": sector_type,
                    "sector_codes": codes,
                    "collection_ids": node_ids,
                },
            )
            migration_map = {
                (row["collection_id"], row["sector_code"]): row
                for row in migration_rows
            }
            source_ids = sorted(
                {
                    row["state_source_collection_id"]
                    for row in migration_rows
                    if row.get("state_source_collection_id")
                }
            )
            state_rows = (
                self._rows(
                    f"""
                    SELECT toString(collection_id) collection_id,sector_code,
                        index_change_ratio_pct,index_change_1m_pct
                    FROM {table} FINAL
                    WHERE trade_date={{trade_date:Date}}
                      AND sector_code IN {{sector_codes:Array(String)}}
                      AND toString(collection_id) IN {{collection_ids:Array(String)}}
                    """,
                    {
                        "trade_date": trade_date_value,
                        "sector_codes": codes,
                        "collection_ids": source_ids,
                    },
                )
                if source_ids
                else []
            )
            state_map = {
                (row["collection_id"], row["sector_code"]): row for row in state_rows
            }
            for sector_code in codes:
                key = (sector_type, sector_code)
                key_rows: list[dict[str, Any]] = []
                for collection_id in node_ids:
                    node = node_map[collection_id]
                    migration = migration_map.get((collection_id, sector_code))
                    candidate = candidate_map.get(
                        (collection_id, sector_type, sector_code)
                    )
                    source_id = (
                        migration.get("state_source_collection_id") if migration else None
                    )
                    state = state_map.get((source_id, sector_code)) if source_id else None
                    key_rows.append(
                        {
                            "scheduled_time": node["scheduled_time"],
                            "node_seq": node["node_seq"],
                            "candidate_rank": candidate.get("candidate_rank")
                            if candidate
                            else None,
                            "candidate_rank_status": (
                                "RANKED"
                                if candidate
                                and candidate.get("candidate_rank") is not None
                                else "OUTSIDE_TOP_N"
                            ),
                            "candidate_score_v1": candidate.get("candidate_score_v1")
                            if candidate
                            else None,
                            "index_change_ratio_pct": state.get(
                                "index_change_ratio_pct"
                            )
                            if state
                            else None,
                            "index_change_1m_pct": state.get("index_change_1m_pct")
                            if state
                            else None,
                            **{
                                field: migration.get(field) if migration else None
                                for field in CORE_TRAJECTORY_STATE_FIELDS
                                if field
                                not in {
                                    "index_change_ratio_pct",
                                    "index_change_1m_pct",
                                }
                            },
                            "state_data_status": migration.get("state_data_status")
                            if migration
                            else None,
                            "state_source_scheduled_time": migration.get(
                                "state_source_scheduled_time"
                            )
                            if migration
                            else None,
                            "state_source_age_seconds": migration.get(
                                "state_source_age_seconds"
                            )
                            if migration
                            else None,
                        }
                    )
                result[key]["key_nodes"] = key_rows
        return result

    def _opening_auction_rows(self, trade_date_value: date) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT node_seq,scheduled_time,auction_phase,
                toString(thscode) stock_code,name stock_name,
                auction_price,auction_pct,auction_amount,auction_unmatched,
                auction_turnover_pct,auction_yesterday_ratio_pct
            FROM market.hithink_auction_snapshot FINAL
            WHERE trade_date={trade_date:Date}
              AND node_seq BETWEEN 1 AND 11
            ORDER BY stock_code,node_seq
            """,
            {"trade_date": trade_date_value},
        )

    def _opening_auction_market(
        self, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Aggregate all opening-auction stocks into eleven market-level rows."""
        rows_by_node: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            node_seq = int(row.get("node_seq") or 0)
            if node_seq in OPEN_AUCTION_NODE_SEQUENCES:
                rows_by_node[node_seq].append(row)

        row_counts = [len(rows_by_node[node_seq]) for node_seq in OPEN_AUCTION_NODE_SEQUENCES]
        if not row_counts or min(row_counts) == 0 or len(set(row_counts)) != 1:
            raise RuntimeError(
                "opening auction does not contain the same full-market stock set "
                f"for all 11 nodes: {row_counts}"
            )

        node_stats: dict[int, dict[str, Any]] = {}
        pct_by_node: dict[int, dict[str, float]] = {}
        for node_seq in OPEN_AUCTION_NODE_SEQUENCES:
            node_rows = rows_by_node[node_seq]
            valid_rows = [
                row for row in node_rows if _auction_number(row.get("auction_pct")) is not None
            ]
            pct_by_node[node_seq] = {
                str(row["stock_code"]): value
                for row in valid_rows
                for value in [_auction_number(row.get("auction_pct"))]
                if value is not None
            }
            pct_values = list(pct_by_node[node_seq].values())
            amount_values = [
                value
                for row in node_rows
                for value in [_auction_number(row.get("auction_amount"))]
                if value is not None
            ]
            yesterday_ratio_values = [
                value
                for row in node_rows
                for value in [_auction_number(row.get("auction_yesterday_ratio_pct"))]
                if value is not None
            ]
            valid_count = len(pct_values)
            up_count = sum(value > 0 for value in pct_values)
            down_count = sum(value < 0 for value in pct_values)
            node_stats[node_seq] = {
                "time": f"09:{14 + node_seq:02d}",
                "auction_phase": (
                    "order_entry"
                    if node_seq <= 5
                    else "no_cancel"
                    if node_seq <= 10
                    else "matched"
                ),
                "valid_count": valid_count,
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": sum(value == 0 for value in pct_values),
                "up_ratio": _round_auction_number(
                    up_count / valid_count * 100 if valid_count else None
                ),
                "down_ratio": _round_auction_number(
                    down_count / valid_count * 100 if valid_count else None
                ),
                "up_5_count": sum(value >= 5 for value in pct_values),
                "up_2_to_5_count": sum(2 <= value < 5 for value in pct_values),
                "up_0_to_2_count": sum(0 < value < 2 for value in pct_values),
                "down_0_to_2_count": sum(-2 < value < 0 for value in pct_values),
                "down_2_to_5_count": sum(-5 < value <= -2 for value in pct_values),
                "down_5_count": sum(value <= -5 for value in pct_values),
                "limit_up_or_near_count": sum(value >= 9.5 for value in pct_values),
                "limit_down_or_near_count": sum(value <= -9.5 for value in pct_values),
                "auction_amount_total": _round_auction_number(sum(amount_values)),
                "auction_amount_valid_count": len(amount_values),
                "yesterday_ratio_valid_count": len(yesterday_ratio_values),
                "yesterday_ratio_gt_100_count": sum(
                    value > 100 for value in yesterday_ratio_values
                ),
                "yesterday_ratio_gt_200_count": sum(
                    value > 200 for value in yesterday_ratio_values
                ),
            }

        final_stats = node_stats[11]
        final_amounts = sorted(
            (
                value
                for row in rows_by_node[11]
                for value in [_auction_number(row.get("auction_amount"))]
                if value is not None
            ),
            reverse=True,
        )
        final_amount_total = _auction_number(final_stats["auction_amount_total"])

        def top_share(limit: int) -> float | None:
            if final_amount_total in (None, 0):
                return None
            return _round_auction_number(
                sum(final_amounts[:limit]) / final_amount_total * 100
            )

        current_fields = (
            "valid_count",
            "up_count",
            "down_count",
            "flat_count",
            "up_ratio",
            "down_ratio",
            "up_5_count",
            "down_5_count",
            "limit_up_or_near_count",
            "limit_down_or_near_count",
            "auction_amount_total",
            "auction_amount_valid_count",
            "yesterday_ratio_valid_count",
            "yesterday_ratio_gt_100_count",
            "yesterday_ratio_gt_200_count",
        )
        current = {field: final_stats[field] for field in current_fields}
        current.update(
            auction_amount_top10_share=top_share(10),
            auction_amount_top20_share=top_share(20),
            auction_amount_top50_share=top_share(50),
        )

        def change(start_node: int, end_node: int) -> dict[str, Any]:
            start, end = node_stats[start_node], node_stats[end_node]
            start_pct, end_pct = pct_by_node[start_node], pct_by_node[end_node]
            comparable = sorted(set(start_pct) & set(end_pct))
            changes = [end_pct[stock_code] - start_pct[stock_code] for stock_code in comparable]
            return {
                "up_count_change": end["up_count"] - start["up_count"],
                "down_count_change": end["down_count"] - start["down_count"],
                "up_5_count_change": end["up_5_count"] - start["up_5_count"],
                "down_5_count_change": end["down_5_count"] - start["down_5_count"],
                "auction_amount_change": _round_auction_number(
                    _number(end["auction_amount_total"])
                    - _number(start["auction_amount_total"])
                ),
                "comparable_stock_count": len(comparable),
                "stronger_stock_count": sum(value >= 1 for value in changes),
                "weaker_stock_count": sum(value <= -1 for value in changes),
            }

        trajectory_rows = [
            [node_stats[node_seq][field] for field in OPEN_AUCTION_MARKET_TRAJECTORY_SCHEMA]
            for node_seq in OPEN_AUCTION_NODE_SEQUENCES
        ]
        return {
            "data_context": "OPEN_AUCTION",
            "trajectory": {
                "schema": OPEN_AUCTION_MARKET_TRAJECTORY_SCHEMA,
                "rows": trajectory_rows,
            },
            "current": current,
            "change": {
                "significant_change_threshold_pct_points": 1.0,
                "from_09_15_to_09_20": change(1, 6),
                "from_09_20_to_09_25": change(6, 11),
                "from_09_15_to_09_25": change(1, 11),
            },
        }

    def _opening_auction_core_stocks(
        self,
        trade_date_value: date,
        stock_top_n: int,
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build the 09:25 stock layer from all eleven opening-auction minutes."""
        if rows is None:
            rows = self._opening_auction_rows(trade_date_value)
        grouped: defaultdict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
        for row in rows:
            stock_code = _text(row.get("stock_code")) or ""
            node_seq = int(row.get("node_seq") or 0)
            if stock_code and node_seq in OPEN_AUCTION_NODE_SEQUENCES:
                grouped[stock_code][node_seq] = row

        complete = {
            stock_code: nodes
            for stock_code, nodes in grouped.items()
            if tuple(sorted(nodes)) == OPEN_AUCTION_NODE_SEQUENCES
            and _text(nodes[11].get("auction_phase")) == "matched"
        }
        final_rows = {
            stock_code: nodes[11] for stock_code, nodes in complete.items()
        }

        amount_order = sorted(
            (
                (_auction_number(row.get("auction_amount")), stock_code)
                for stock_code, row in final_rows.items()
                if _auction_number(row.get("auction_amount")) is not None
            ),
            key=lambda item: (-item[0], item[1]),
        )
        amount_rank = {
            stock_code: rank
            for rank, (_, stock_code) in enumerate(amount_order, start=1)
        }

        def ranked_codes(metric, *, positive: bool = False) -> list[str]:
            values: list[tuple[float, str]] = []
            for stock_code, nodes in complete.items():
                value = metric(nodes)
                if value is None or (positive and value <= 0):
                    continue
                values.append((value, stock_code))
            values.sort(key=lambda item: (-item[0], item[1]))
            return [stock_code for _, stock_code in values]

        def pct(nodes: dict[int, dict[str, Any]], node_seq: int) -> float | None:
            return _auction_number(nodes[node_seq].get("auction_pct"))

        def amount(nodes: dict[int, dict[str, Any]], node_seq: int) -> float | None:
            return _auction_number(nodes[node_seq].get("auction_amount"))

        def delta(
            nodes: dict[int, dict[str, Any]],
            field,
            start_node: int,
            end_node: int,
        ) -> float | None:
            start = field(nodes, start_node)
            end = field(nodes, end_node)
            return None if start is None or end is None else end - start

        def early_extreme(nodes: dict[int, dict[str, Any]]) -> float | None:
            values = [
                abs(value)
                for node_seq in range(1, 6)
                for value in [pct(nodes, node_seq)]
                if value is not None
            ]
            return max(values) if values else None

        def early_final_contrast(nodes: dict[int, dict[str, Any]]) -> float | None:
            final = pct(nodes, 11)
            if final is None:
                return None
            values = [
                abs(final - value)
                for node_seq in range(1, 6)
                for value in [pct(nodes, node_seq)]
                if value is not None
            ]
            return max(values) if values else None

        per_category = max(4, min(8, stock_top_n // 2))
        candidate_lists = [
            [stock_code for _, stock_code in amount_order[: max(10, stock_top_n)]],
            ranked_codes(
                lambda nodes: pct(nodes, 11)
                if (amount(nodes, 11) or 0) > 0
                else None,
                positive=True,
            )[:per_category],
            ranked_codes(
                lambda nodes: -pct(nodes, 11)
                if pct(nodes, 11) is not None and (amount(nodes, 11) or 0) > 0
                else None,
                positive=True,
            )[:per_category],
            ranked_codes(
                lambda nodes: _auction_number(
                    nodes[11].get("auction_yesterday_ratio_pct")
                ),
                positive=True,
            )[:per_category],
            ranked_codes(early_extreme, positive=True)[:per_category],
            ranked_codes(early_final_contrast, positive=True)[:per_category],
            ranked_codes(
                lambda nodes: delta(nodes, pct, 6, 11), positive=True
            )[:per_category],
            ranked_codes(
                lambda nodes: (
                    -value if (value := delta(nodes, pct, 6, 11)) is not None else None
                ),
                positive=True,
            )[:per_category],
            ranked_codes(
                lambda nodes: (
                    abs(value)
                    if (value := delta(nodes, pct, 10, 11)) is not None
                    else None
                ),
                positive=True,
            )[:per_category],
            ranked_codes(
                lambda nodes: delta(nodes, amount, 6, 11), positive=True
            )[:per_category],
            ranked_codes(
                lambda nodes: delta(nodes, amount, 10, 11), positive=True
            )[:per_category],
        ]

        limit = min(stock_top_n, 30)
        selected: list[str] = []
        seen: set[str] = set()
        for index in range(max((len(items) for items in candidate_lists), default=0)):
            for items in candidate_lists:
                if index >= len(items):
                    continue
                stock_code = items[index]
                if stock_code in seen:
                    continue
                selected.append(stock_code)
                seen.add(stock_code)
                if len(selected) == limit:
                    break
            if len(selected) == limit:
                break

        selected.sort(
            key=lambda stock_code: (
                amount_rank.get(stock_code) is None,
                amount_rank.get(stock_code) or 0,
                stock_code,
            )
        )
        current_rows: list[list[Any]] = []
        trajectory_rows: list[list[Any]] = []
        for stock_code in selected:
            current = final_rows[stock_code]
            current_rows.append(
                [
                    stock_code,
                    current.get("stock_name"),
                    _round_auction_number(current.get("auction_price")),
                    _round_auction_number(current.get("auction_pct")),
                    _round_auction_number(current.get("auction_amount")),
                    amount_rank.get(stock_code),
                    _round_auction_number(current.get("auction_turnover_pct")),
                    _round_auction_number(
                        current.get("auction_yesterday_ratio_pct")
                    ),
                    current.get("auction_unmatched"),
                ]
            )
            for node_seq in OPEN_AUCTION_NODE_SEQUENCES:
                row = complete[stock_code][node_seq]
                scheduled_time = row.get("scheduled_time")
                trajectory_rows.append(
                    [
                        stock_code,
                        scheduled_time.strftime("%H:%M")
                        if isinstance(scheduled_time, datetime)
                        else str(scheduled_time)[11:16],
                        _text(row.get("auction_phase")),
                        _round_auction_number(row.get("auction_pct")),
                        _round_auction_number(row.get("auction_amount")),
                        _round_auction_number(row.get("auction_turnover_pct")),
                        _round_auction_number(
                            row.get("auction_yesterday_ratio_pct")
                        ),
                        row.get("auction_unmatched"),
                    ]
                )

        return {
            "data_context": "OPEN_AUCTION",
            "current_schema": OPEN_AUCTION_CURRENT_SCHEMA,
            "current_rows": current_rows,
            "trajectory_schema": OPEN_AUCTION_TRAJECTORY_SCHEMA,
            "trajectory_rows": trajectory_rows,
        }

    def _stock_memberships(
        self,
        trade_date_value: date,
        collection_id: str,
        thscodes: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        if not thscodes:
            return {}
        rows = self._rows(
            """
            SELECT h.thscode,c.sector_type,c.sector_code,c.sector_name,
                c.candidate_rank,c.candidate_score_v1
            FROM
            (
                SELECT * FROM market.hithink_core_sector_candidate FINAL
                WHERE trade_date={trade_date:Date}
                  AND toString(collection_id)={collection_id:String}
                  AND sector_type IN {core_sector_types:Array(String)}
            ) c
            INNER JOIN
            (
                SELECT * FROM market.sector_membership_history FINAL
                WHERE observed_from<={trade_date:Date}
                  AND (observed_to IS NULL OR observed_to>={trade_date:Date})
                  AND thscode IN {thscodes:Array(String)}
            ) h
            ON h.sector_type=c.sector_type AND h.sector_code=c.sector_code
            ORDER BY h.thscode,c.candidate_rank,c.sector_type,c.sector_code
            """,
            {
                "trade_date": trade_date_value,
                "collection_id": collection_id,
                "thscodes": thscodes,
                "core_sector_types": list(CORE_SECTOR_TYPES),
            },
        )
        result: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            result[_text(row["thscode"]) or ""].append(
                {
                    "sector_type": _text(row["sector_type"]),
                    "sector_code": row["sector_code"],
                    "sector_name": row["sector_name"],
                    "candidate_rank": row["candidate_rank"],
                    "candidate_score_v1": row["candidate_score_v1"],
                }
            )
        return dict(result)

    def _sector_state_map(
        self, trade_date_value: date, migration_rows: list[dict[str, Any]]
    ) -> dict[tuple[str, str, str], dict[str, Any]]:
        result: dict[tuple[str, str, str], dict[str, Any]] = {}
        table_map = {
            "concept": "market.hithink_concept_state",
            "industry": "market.hithink_industry_state",
            "style": "market.hithink_style_state",
        }
        for sector_type, table in table_map.items():
            source_ids = sorted(
                {
                    _text(row.get("state_source_collection_id"))
                    for row in migration_rows
                    if _text(row.get("sector_type")) == sector_type
                    and row.get("state_source_collection_id") is not None
                }
            )
            if not source_ids:
                continue
            rows = self._rows(
                f"""
                SELECT toString(collection_id) collection_id,sector_code,
                    index_change_ratio_pct,index_change_1m_pct
                FROM {table} FINAL
                WHERE trade_date={{trade_date:Date}}
                  AND toString(collection_id) IN {{collection_ids:Array(String)}}
                """,
                {"trade_date": trade_date_value, "collection_ids": source_ids},
            )
            for row in rows:
                result[(sector_type, row["collection_id"], row["sector_code"])] = row
        return result

    def _watchlist(self, trade_date_value: date, historical: bool) -> list[dict[str, Any]]:
        where = (
            "effective_from<={trade_date:Date} AND "
            "(effective_to IS NULL OR effective_to>={trade_date:Date})"
            if historical
            else "is_active=1"
        )
        return self._rows(
            f"""
            SELECT * FROM market.strategic_sector_watchlist FINAL
            WHERE {where}
            ORDER BY strategic_theme,watch_level,sector_type,sector_code
            """,
            {"trade_date": trade_date_value},
        )

    def build(
        self,
        trade_date_text: str | None,
        target_time_text: str,
        capital_top_n: int = 10,
        sector_top_n: int = 10,
        stock_top_n: int = 20,
        historical_watchlist: bool = False,
    ) -> dict[str, Any]:
        for name, value, upper in (
            ("capital_top_n", capital_top_n, 30),
            ("sector_top_n", sector_top_n, 30),
            ("stock_top_n", stock_top_n, 50),
        ):
            if not 1 <= value <= upper:
                raise ValueError(f"{name}必须在1到{upper}之间")

        trade_date_value = self._resolve_trade_date(trade_date_text)
        requested = datetime.combine(
            trade_date_value, _parse_target_time(target_time_text), SHANGHAI
        )
        target = self._resolve_target(trade_date_value, requested)
        collection_id = target["collection_id"]
        target_scheduled_time = target["scheduled_time"]
        business_period, _, _ = _business_period(target_scheduled_time)
        target_nodes = self._last_target_nodes(trade_date_value, target_scheduled_time)
        intraday_nodes = self._intraday_target_nodes(
            trade_date_value, target_scheduled_time
        )

        delta = self._table_row(
            "market.hithink_market_delta_15m", trade_date_value, collection_id
        )
        current_emotion = self._table_row(
            "market.hithink_emotion_state", trade_date_value, collection_id
        )
        emotion_intraday_trajectory = self._emotion_intraday_trajectory(
            trade_date_value, target_scheduled_time
        )
        current_state_source = _text(
            delta.get("state_source_collection_id") if delta else None
        ) or collection_id
        market_state = self._table_row(
            "market.hithink_market_state", trade_date_value, current_state_source
        )
        market_intraday_trajectory = self._market_intraday_trajectory(
            trade_date_value, intraday_nodes
        )

        migration_rows = self._rows(
            """
            SELECT *,node_seq FROM market.hithink_sector_capital_migration FINAL
            WHERE trade_date={trade_date:Date}
              AND toString(collection_id)={collection_id:String}
            """,
            {"trade_date": trade_date_value, "collection_id": collection_id},
        )
        migration_map = {
            (_text(row["sector_type"]), row["sector_code"]): row for row in migration_rows
        }

        core_sector_rows = self._rows(
            """
            SELECT *,node_seq FROM market.hithink_core_sector_candidate FINAL
            WHERE trade_date={trade_date:Date}
              AND toString(collection_id)={collection_id:String}
              AND sector_type IN {core_sector_types:Array(String)}
            ORDER BY sector_type,candidate_rank
            """,
            {
                "trade_date": trade_date_value,
                "collection_id": collection_id,
                "core_sector_types": list(CORE_SECTOR_TYPES),
            },
        )
        core_sector_map = {
            (_text(row["sector_type"]), row["sector_code"]): row
            for row in core_sector_rows
        }
        candidate_histories = self._candidate_histories(trade_date_value, target_nodes)
        selected_core_sector_rows = [
            row
            for sector_type in CORE_SECTOR_TYPES
            for row in [
                item
                for item in core_sector_rows
                if _text(item["sector_type"]) == sector_type
            ][:sector_top_n]
        ]
        core_trajectories = self._core_sector_trajectories(
            trade_date_value,
            target_scheduled_time,
            intraday_nodes,
            selected_core_sector_rows,
        )
        core_sectors: dict[str, list[dict[str, Any]]] = {}
        for sector_type in CORE_SECTOR_TYPES:
            rows = [
                row
                for row in core_sector_rows
                if _text(row["sector_type"]) == sector_type
            ][:sector_top_n]
            items = []
            for row in rows:
                item = _strip_internal(row) or {}
                item["candidate_reasons"] = [
                    label for field, label in SECTOR_REASON_FIELDS.items() if row.get(field) == 1
                ]
                item["candidate_rank_last_5"] = candidate_histories.get(
                    (sector_type, row["sector_code"]), []
                )
                trajectory = core_trajectories.get(
                    (sector_type, row["sector_code"]),
                    {"recent": [], "key_nodes": []},
                )
                item["trajectory_recent_5_nodes"] = trajectory["recent"]
                item["trajectory_15m_last_5"] = trajectory["key_nodes"][-5:]
                items.append(item)
            core_sectors[sector_type] = items

        core_sector_intraday = {
            "current_core_sector_count": len(selected_core_sector_rows),
            "trajectories": [
                {
                    "sector_type": _text(row["sector_type"]),
                    "sector_code": row["sector_code"],
                    "sector_name": row["sector_name"],
                    "current_candidate_rank": row["candidate_rank"],
                    "current_candidate_score_v1": row["candidate_score_v1"],
                    "nodes": core_trajectories.get(
                        (_text(row["sector_type"]) or "", row["sector_code"]),
                        {"key_nodes": []},
                    )["key_nodes"],
                }
                for row in selected_core_sector_rows
            ],
        }

        all_stock_rows = self._rows(
            """
            SELECT *,node_seq FROM market.hithink_core_stock_candidate FINAL
            WHERE trade_date={trade_date:Date}
              AND toString(collection_id)={collection_id:String}
            ORDER BY candidate_rank
            """,
            {"trade_date": trade_date_value, "collection_id": collection_id},
        )
        stock_codes = [_text(row["thscode"]) or "" for row in all_stock_rows]
        memberships = self._stock_memberships(
            trade_date_value, collection_id, stock_codes
        )
        auction_market = None
        if target["node_seq"] == 11:
            auction_rows = self._opening_auction_rows(trade_date_value)
            auction_market = self._opening_auction_market(auction_rows)
            core_stocks: Any = self._opening_auction_core_stocks(
                trade_date_value, stock_top_n, auction_rows
            )
        else:
            core_stocks = []
            for row in all_stock_rows[:stock_top_n]:
                item = _strip_internal(row) or {}
                thscode = _text(row["thscode"]) or ""
                item["candidate_reasons"] = [
                    label
                    for field, label in STOCK_REASON_FIELDS.items()
                    if row.get(field) == 1
                ]
                item["core_sector_memberships"] = memberships.get(thscode, [])
                core_stocks.append(item)

        watchlist = self._watchlist(trade_date_value, historical_watchlist)
        sector_state_map = self._sector_state_map(trade_date_value, migration_rows)
        history_rows = self._rows(
            """
            SELECT toString(collection_id) collection_id,scheduled_time,sector_type,
                sector_code,turnover_share_rank
            FROM market.hithink_sector_capital_migration FINAL
            WHERE trade_date={trade_date:Date}
              AND toString(collection_id) IN {collection_ids:Array(String)}
            """,
            {
                "trade_date": trade_date_value,
                "collection_ids": [row["collection_id"] for row in target_nodes],
            },
        )
        history_rank_map = {
            (
                row["collection_id"],
                _text(row["sector_type"]),
                row["sector_code"],
            ): row["turnover_share_rank"]
            for row in history_rows
        }
        strategic_watch: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for config in watchlist:
            key = (_text(config["sector_type"]), config["sector_code"])
            migration = migration_map.get(key)
            source_id = _text(
                migration.get("state_source_collection_id") if migration else None
            )
            state = sector_state_map.get((key[0], source_id, key[1])) if source_id else None
            candidate = core_sector_map.get(key)
            strategic_watch[config["strategic_theme"]].append(
                {
                    "strategic_subtheme": config.get("strategic_subtheme"),
                    "watch_level": _text(config.get("watch_level")),
                    "watch_reason": config.get("watch_reason"),
                    "sector_type": key[0],
                    "sector_code": key[1],
                    "sector_name": config.get("sector_name"),
                    "state_data_status": migration.get("state_data_status")
                    if migration
                    else "NO_SOURCE",
                    "state_source_scheduled_time": migration.get(
                        "state_source_scheduled_time"
                    )
                    if migration
                    else None,
                    "state_source_age_seconds": migration.get("state_source_age_seconds")
                    if migration
                    else None,
                    "index_change_ratio_pct": state.get("index_change_ratio_pct")
                    if state
                    else None,
                    "index_change_1m_pct": state.get("index_change_1m_pct")
                    if state
                    else None,
                    "current_class_rank": migration.get("turnover_share_rank")
                    if migration
                    else None,
                    "current_candidate_rank": candidate.get("candidate_rank")
                    if candidate
                    else None,
                    "turnover_market_share_pct": migration.get(
                        "turnover_market_share_pct"
                    )
                    if migration
                    else None,
                    "turnover_1m_market_share_pct": migration.get(
                        "turnover_1m_market_share_pct"
                    )
                    if migration
                    else None,
                    "turnover_market_share_delta_15m": migration.get(
                        "turnover_market_share_delta_15m"
                    )
                    if migration
                    else None,
                    "turnover_1m_market_share_delta_15m": migration.get(
                        "turnover_1m_market_share_delta_15m"
                    )
                    if migration
                    else None,
                    "up_ratio": migration.get("up_ratio") if migration else None,
                    "up_ratio_delta_15m": migration.get("up_ratio_delta_15m")
                    if migration
                    else None,
                    "limit_up_count": migration.get("limit_up_count")
                    if migration
                    else None,
                    "limit_break_count": migration.get("limit_break_count")
                    if migration
                    else None,
                    "new_high_ratio": migration.get("new_high_ratio")
                    if migration
                    else None,
                    "turnover_1m_top1_share_pct": migration.get(
                        "turnover_1m_top1_share_pct"
                    )
                    if migration
                    else None,
                    "turnover_1m_top3_share_pct": migration.get(
                        "turnover_1m_top3_share_pct"
                    )
                    if migration
                    else None,
                    "turnover_1m_top5_share_pct": migration.get(
                        "turnover_1m_top5_share_pct"
                    )
                    if migration
                    else None,
                    "turnover_rank_last_5": [
                        {
                            "scheduled_time": node["scheduled_time"],
                            "node_seq": node["node_seq"],
                            "rank": history_rank_map.get(
                                (node["collection_id"], key[0], key[1])
                            ),
                        }
                        for node in target_nodes
                    ],
                }
            )

        watch_keys = {
            (_text(row["sector_type"]), row["sector_code"]): row for row in watchlist
        }
        strategic_core = []
        for row in core_sector_rows:
            key = (_text(row["sector_type"]), row["sector_code"])
            if key in watch_keys:
                strategic_core.append(
                    {
                        "strategic_theme": watch_keys[key]["strategic_theme"],
                        "watch_level": _text(watch_keys[key]["watch_level"]),
                        "sector_type": key[0],
                        "sector_code": key[1],
                        "sector_name": row["sector_name"],
                        "candidate_rank": row["candidate_rank"],
                        "candidate_score_v1": row["candidate_score_v1"],
                    }
                )

        multi_sector_stocks = []
        for row in all_stock_rows:
            thscode = _text(row["thscode"]) or ""
            stock_memberships = memberships.get(thscode, [])
            if len(stock_memberships) >= 2:
                multi_sector_stocks.append(
                    {
                        "candidate_rank": row["candidate_rank"],
                        "thscode": thscode,
                        "stock_name": row.get("stock_name"),
                        "core_sector_count": len(stock_memberships),
                        "core_sector_memberships": stock_memberships,
                    }
                )
        multi_sector_stocks = multi_sector_stocks[:stock_top_n]

        capital_up_breadth_down: dict[str, list[dict[str, Any]]] = {}
        for sector_type in CAPITAL_SECTOR_TYPES:
            rows = [
                row
                for row in migration_rows
                if _text(row["sector_type"]) == sector_type
                and (_number(row.get("turnover_market_share_delta_15m")) or 0) > 0
                and (_number(row.get("up_ratio_delta_15m")) or 0) < 0
            ]
            rows.sort(
                key=lambda row: _number(row.get("turnover_market_share_delta_15m"))
                or float("-inf"),
                reverse=True,
            )
            capital_up_breadth_down[sector_type] = [
                _pick(row, CAPITAL_FIELDS) for row in rows[:capital_top_n]
            ]

        migration_statuses = Counter(
            _text(row.get("state_data_status")) or "UNKNOWN" for row in migration_rows
        )
        core_sector_statuses = Counter(
            _text(row.get("state_data_status")) or "UNKNOWN" for row in core_sector_rows
        )
        core_stock_statuses = Counter(
            _text(row.get("state_data_status")) or "UNKNOWN" for row in all_stock_rows
        )
        strategic_state_count = sum(
            1 for rows in strategic_watch.values() for row in rows if row["state_data_status"] != "NO_SOURCE"
        )

        package = {
            "package_type": "MARKET_STATE_RESTORE",
            "package_version": "V2",
            "package_id": collection_id,
            "target": {
                "trade_date": trade_date_value,
                "requested_time": requested,
                "resolved_collection_id": collection_id,
                "resolved_node_seq": target["node_seq"],
                "resolved_scheduled_time": target_scheduled_time,
                "resolution_distance_seconds": target["resolution_distance_seconds"],
                "business_period": business_period,
            },
            "data_quality": {
                "market_delta_status": delta.get("delta_status") if delta else "MISSING",
                "market_state_data_status": delta.get("state_data_status")
                if delta
                else "MISSING",
                "market_state_source_scheduled_time": delta.get(
                    "state_source_scheduled_time"
                )
                if delta
                else None,
                "market_state_source_age_seconds": delta.get("state_source_age_seconds")
                if delta
                else None,
                "emotion_pool_data_status": current_emotion.get("pool_data_status")
                if current_emotion
                else "MISSING",
                "emotion_pool_source_scheduled_time": current_emotion.get(
                    "pool_source_scheduled_time"
                )
                if current_emotion
                else None,
                "emotion_pool_source_age_seconds": current_emotion.get(
                    "pool_source_age_seconds"
                )
                if current_emotion
                else None,
                "capital_migration_status_counts": dict(migration_statuses),
                "core_sector_status_counts": dict(core_sector_statuses),
                "core_stock_status_counts": dict(core_stock_statuses),
                "strategic_watch_total": sum(len(rows) for rows in strategic_watch.values()),
                "strategic_watch_with_state": strategic_state_count,
                "intraday_target_node_count": len(intraday_nodes),
                "core_sector_intraday_count": core_sector_intraday[
                    "current_core_sector_count"
                ],
                "strategic_watch_basis": "HISTORICAL_EFFECTIVE_WINDOW"
                if historical_watchlist
                else "CURRENT_ACTIVE_RETROSPECTIVE",
            },
            "market": {
                "current_state": _strip_internal(market_state),
                "change_15m": _strip_internal(delta),
                "temporary_changes": self._market_intervals(
                    trade_date_value, target_scheduled_time, market_state
                ),
                "intraday_trajectory": market_intraday_trajectory,
            },
            **({"auction_market": auction_market} if auction_market is not None else {}),
            "emotion": {
                "intraday_trajectory": emotion_intraday_trajectory,
            },
            "capital_migration": self._capital_block(migration_rows, capital_top_n),
            "core_sectors": core_sectors,
            "core_sector_intraday": core_sector_intraday,
            "core_stocks": core_stocks,
            "strategic_watch": dict(strategic_watch),
            "cross_relations": {
                "strategic_sectors_in_current_core": strategic_core,
                "core_stocks_in_multiple_core_sectors": multi_sector_stocks,
                "capital_share_up_but_breadth_down": capital_up_breadth_down,
            },
            "interpretation_policy": {
                "contains_deterministic_facts_only": True,
                "does_not_assign_mainline_or_market_phase": True,
            },
        }
        return _json_safe(package)


def build_market_state_package(
    trade_date: str | None = None,
    target_time: str = "15:00",
    capital_top_n: int = 10,
    sector_top_n: int = 10,
    stock_top_n: int = 20,
    historical_watchlist: bool = False,
) -> dict[str, Any]:
    settings = Settings.load()
    writer = ClickHouseWriter(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        settings.clickhouse_username,
        settings.clickhouse_password,
    )
    try:
        return MarketStatePackageBuilder(writer.client).build(
            trade_date,
            target_time,
            capital_top_n,
            sector_top_n,
            stock_top_n,
            historical_watchlist,
        )
    finally:
        writer.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成只读市场状态恢复聚合包")
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD，默认最近可用交易日")
    parser.add_argument("--target-time", default="15:00", help="HH:MM或HH:MM:SS")
    parser.add_argument("--capital-top-n", type=int, default=10)
    parser.add_argument("--sector-top-n", type=int, default=10)
    parser.add_argument("--stock-top-n", type=int, default=20)
    parser.add_argument("--historical-watchlist", action="store_true")
    args = parser.parse_args()
    package = build_market_state_package(
        trade_date=args.trade_date,
        target_time=args.target_time,
        capital_top_n=args.capital_top_n,
        sector_top_n=args.sector_top_n,
        stock_top_n=args.stock_top_n,
        historical_watchlist=args.historical_watchlist,
    )
    print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
