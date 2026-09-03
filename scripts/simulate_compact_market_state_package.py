"""Create Compact V3 simulation packages from persisted full packages.

This script is deliberately independent from the running aggregation and reader paths.
It only reads existing JSON packages and writes new files under a separate output root.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

CAPITAL_SCHEMA = [
    "sector_code",
    "sector_name",
    "cum_share",
    "cum_share_change_15m",
    "instant_share",
    "instant_share_change_15m",
    "cum_rank",
    "instant_rank",
    "up_ratio",
    "limit_up",
    "limit_break",
    "new_high_ratio",
]

CORE_SECTOR_SCHEMA = [
    "sector_type",
    "sector_code",
    "sector_name",
    "rank",
    "score",
    "change_pct",
    "change_1m_pct",
    "up_ratio",
    "up_ratio_change_15m",
    "cum_share",
    "instant_share",
    "instant_share_change_15m",
    "cum_rank",
    "instant_rank",
    "limit_up",
    "limit_break",
    "new_high_ratio",
    "reasons",
]

CORE_TRAJECTORY_SCHEMA = [
    "sector_type",
    "sector_code",
    "sector",
    "time",
    "rank",
    "score",
    "change_pct",
    "up_ratio",
    "cum_share",
    "cum_share_change_15m",
    "instant_share",
    "instant_share_change_15m",
    "limit_up",
    "limit_break",
    "new_high_ratio",
]

CORE_STOCK_SCHEMA = [
    "stock_code",
    "stock_name",
    "rank",
    "score",
    "change_pct",
    "change_1m_pct",
    "turnover",
    "turnover_1m",
    "turnover_rank",
    "turnover_1m_rank",
    "new_high",
    "limit_up",
    "limit_break",
    "best_core_sector",
    "sector_turnover_rank",
    "sector_price_rank",
    "main_reasons",
    "main_sector_memberships",
]

STRATEGIC_SCHEMA = [
    "strategic_theme",
    "strategic_subtheme",
    "watch_level",
    "sector_code",
    "sector_name",
    "change_pct",
    "up_ratio",
    "cum_share",
    "instant_share",
    "instant_share_change_15m",
    "current_rank",
    "candidate_rank",
    "limit_up",
    "limit_break",
    "new_high_ratio",
]

MARKET_TRAJECTORY_SCHEMA = [
    "time",
    "minute_metric_context",
    "up_count",
    "down_count",
    "flat_count",
    "up_ratio",
    "limit_up",
    "limit_down",
    "limit_break",
    "turnover",
    "turnover_1m",
    "turnover_prev_day_pct",
    "new_high",
    "new_low",
    "price_up_1m",
    "price_down_1m",
]

EMOTION_SCHEMA = [
    "time",
    "limit_up",
    "limit_down",
    "limit_break",
    "limit_attempt",
    "limit_success_rate",
    "limit_break_rate",
    "first_board",
    "second_board",
    "third_board",
    "fourth_board",
    "fifth_plus_board",
    "max_board_height",
    "promotion_base",
    "promotion_success",
    "promotion_fail",
    "promotion_rate",
    "high_board",
    "high_board_break",
    "high_board_fail",
]

# These labels describe trading eligibility or broad ownership, not a market theme.
GENERIC_STOCK_LABELS = {
    "融资融券",
    "沪股通",
    "深股通",
    "陆股通",
    "转融券标的",
    "MSCI概念",
    "富时罗素概念",
    "标普道琼斯A股",
    "QFII重仓",
}

KEY_TRAJECTORY_NODE_SEQUENCES = (12, 27, 42, 72, 132, 163, 223, 254)
MAX_TRAJECTORY_SECTORS = 16


def round_two_places(value: Any) -> Any:
    """Round JSON floating-point facts half-up to no more than two decimals."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, (float, Decimal)):
        return float(
            Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: round_two_places(item) for key, item in value.items()}
    if isinstance(value, list):
        return [round_two_places(item) for item in value]
    return value


def minute_metric_context(value: Any) -> str:
    time_text = short_time(value)
    if time_text == "09:25":
        return "OPEN_AUCTION"
    if time_text == "09:30":
        return "OPEN_TRANSITION"
    if time_text == "13:00":
        return "AFTER_LUNCH_BASE"
    if time_text == "15:00":
        return "CLOSE_AUCTION"
    return "NORMAL"


def comparable_new_high_ratio(scheduled_time: Any, value: Any) -> Any:
    """09:30 is an opening transition baseline, not a comparable new-high sample."""
    return None if short_time(scheduled_time) == "09:30" else value


def short_time(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, str) and "T" in value:
        return value.split("T", 1)[1][:5]
    return value


def without_nulls(source: dict[str, Any], mapping: Iterable[tuple[str, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for output_name, source_name in mapping:
        value = source.get(source_name)
        if value is not None:
            result[output_name] = value
    return result


def capital_row(item: dict[str, Any]) -> list[Any]:
    return [
        item.get("sector_code"),
        item.get("sector_name"),
        item.get("turnover_market_share_pct"),
        item.get("turnover_market_share_delta_15m"),
        item.get("turnover_1m_market_share_pct"),
        item.get("turnover_1m_market_share_delta_15m"),
        item.get("turnover_share_rank"),
        item.get("turnover_1m_share_rank"),
        item.get("up_ratio"),
        item.get("limit_up_count"),
        item.get("limit_break_count"),
        item.get("new_high_ratio"),
    ]


def compact_capital(source: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"schema": CAPITAL_SCHEMA}
    for sector_type in ("concept", "industry", "style"):
        limit = 3 if sector_type == "style" else 5
        block = source.get(sector_type) or {}
        result[sector_type] = {
            name: [capital_row(item) for item in (block.get(name) or [])[:limit]]
            for name in (
                "cumulative_share_rising_top",
                "cumulative_share_falling_top",
                "instant_1m_share_rising_top",
                "instant_1m_share_falling_top",
            )
        }
    return result


def compact_core_sectors(source: dict[str, Any]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    for sector_type in ("concept", "industry"):
        for item in source.get(sector_type) or []:
            rows.append(
                [
                    item.get("sector_type"),
                    item.get("sector_code"),
                    item.get("sector_name"),
                    item.get("candidate_rank"),
                    item.get("candidate_score_v1"),
                    item.get("index_change_ratio_pct"),
                    item.get("index_change_1m_pct"),
                    item.get("up_ratio"),
                    item.get("up_ratio_delta_15m"),
                    item.get("turnover_market_share_pct"),
                    item.get("turnover_1m_market_share_pct"),
                    item.get("turnover_1m_market_share_delta_15m"),
                    item.get("turnover_share_rank"),
                    item.get("turnover_1m_share_rank"),
                    item.get("limit_up_count"),
                    item.get("limit_break_count"),
                    item.get("new_high_ratio"),
                    item.get("candidate_reasons") or [],
                ]
            )
    return {"schema": CORE_SECTOR_SCHEMA, "rows": rows}


def compact_core_trajectory(source: dict[str, Any]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    for sector in source.get("trajectories") or []:
        for node in (sector.get("nodes") or [])[-6:]:
            rows.append(
                [
                    sector.get("sector_type"),
                    sector.get("sector_code"),
                    sector.get("sector_name"),
                    short_time(node.get("scheduled_time")),
                    node.get("candidate_rank"),
                    node.get("candidate_score_v1"),
                    node.get("index_change_ratio_pct"),
                    node.get("up_ratio"),
                    node.get("turnover_market_share_pct"),
                    node.get("turnover_market_share_delta_15m"),
                    node.get("turnover_1m_market_share_pct"),
                    node.get("turnover_1m_market_share_delta_15m"),
                    node.get("limit_up_count"),
                    node.get("limit_break_count"),
                    comparable_new_high_ratio(
                        node.get("scheduled_time"), node.get("new_high_ratio")
                    ),
                ]
            )
    return {"schema": CORE_TRAJECTORY_SCHEMA, "rows": rows}


def build_key_core_trajectory(builder: Any, full: dict[str, Any]) -> dict[str, Any]:
    """Build all-day key-node trajectories for important sectors from read-only facts."""
    target = full["target"]
    trade_date = target["trade_date"]
    target_time = datetime.fromisoformat(target["resolved_scheduled_time"])
    target_node_seq = int(target["resolved_node_seq"])
    wanted_sequences = [
        sequence
        for sequence in KEY_TRAJECTORY_NODE_SEQUENCES
        if sequence <= target_node_seq
    ]
    if target_node_seq not in wanted_sequences:
        wanted_sequences.append(target_node_seq)
    wanted_sequences.sort()

    nodes = builder._rows(
        """
        SELECT toString(collection_id) collection_id,node_seq,scheduled_time
        FROM market.hithink_market_delta_15m FINAL
        WHERE trade_date={trade_date:Date}
          AND node_seq IN {node_sequences:Array(UInt16)}
          AND scheduled_time<={target:DateTime64(3,'Asia/Shanghai')}
        ORDER BY scheduled_time
        """,
        {
            "trade_date": trade_date,
            "node_sequences": wanted_sequences,
            "target": target_time,
        },
    )
    if not nodes:
        return {
            "selection": {
                "max_sectors": MAX_TRAJECTORY_SECTORS,
                "sector_count": 0,
                "key_times": [],
            },
            "schema": CORE_TRAJECTORY_SCHEMA,
            "rows": [],
        }

    node_ids = [row["collection_id"] for row in nodes]
    current_id = target["resolved_collection_id"]
    candidates = builder._rows(
        """
        SELECT toString(collection_id) collection_id,sector_type,sector_code,
            sector_name,candidate_rank,candidate_score_v1
        FROM market.hithink_core_sector_candidate FINAL
        WHERE trade_date={trade_date:Date}
          AND toString(collection_id) IN {collection_ids:Array(String)}
          AND sector_type IN ['concept','industry']
        """,
        {"trade_date": trade_date, "collection_ids": node_ids},
    )
    strategic_keys = {
        (item.get("sector_type"), item.get("sector_code"))
        for items in (full.get("strategic_watch") or {}).values()
        for item in items or []
        if item.get("sector_type") in {"concept", "industry"}
    }
    candidate_map = {
        (row["collection_id"], row["sector_type"], row["sector_code"]): row
        for row in candidates
    }
    pool: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidates:
        key = (row["sector_type"], row["sector_code"])
        is_top_five = (row.get("candidate_rank") or 999) <= 5
        is_current = row["collection_id"] == current_id
        is_strategic_candidate = key in strategic_keys
        if is_top_five or is_current or is_strategic_candidate:
            pool.setdefault(key, row)

    if not pool:
        return {
            "selection": {
                "max_sectors": MAX_TRAJECTORY_SECTORS,
                "sector_count": 0,
                "key_times": [short_time(row["scheduled_time"]) for row in nodes],
            },
            "schema": CORE_TRAJECTORY_SCHEMA,
            "rows": [],
        }

    pool_codes = sorted({key[1] for key in pool})
    migration_rows = builder._rows(
        """
        SELECT toString(collection_id) collection_id,node_seq,scheduled_time,
            sector_type,sector_code,sector_name,state_data_status,
            toString(state_source_collection_id) state_source_collection_id,
            state_source_scheduled_time,state_source_age_seconds,
            up_ratio,turnover_market_share_pct,turnover_market_share_delta_15m,
            turnover_1m_market_share_pct,turnover_1m_market_share_delta_15m,
            limit_up_count,limit_break_count,new_high_ratio
        FROM market.hithink_sector_capital_migration FINAL
        WHERE trade_date={trade_date:Date}
          AND sector_type IN ['concept','industry']
          AND sector_code IN {sector_codes:Array(String)}
          AND toString(collection_id) IN {collection_ids:Array(String)}
        """,
        {
            "trade_date": trade_date,
            "sector_codes": pool_codes,
            "collection_ids": node_ids,
        },
    )
    migration_map = {
        (row["collection_id"], row["sector_type"], row["sector_code"]): row
        for row in migration_rows
    }

    def rank_value(row: dict[str, Any]) -> int:
        value = row.get("candidate_rank")
        return int(value) if value is not None else 999

    def selection_key(key: tuple[str, str]) -> tuple[Any, ...]:
        key_candidates = [
            row
            for row in candidates
            if (row["sector_type"], row["sector_code"]) == key
        ]
        top_five_count = sum(rank_value(row) <= 5 for row in key_candidates)
        best_rank = min((rank_value(row) for row in key_candidates), default=999)
        current_rank = min(
            (
                rank_value(row)
                for row in key_candidates
                if row["collection_id"] == current_id
            ),
            default=999,
        )
        current_migration = migration_map.get((current_id, key[0], key[1])) or {}
        current_share = current_migration.get("turnover_market_share_pct") or 0
        return (-top_five_count, best_rank, current_rank, -float(current_share), key)

    selected_keys = sorted(pool, key=selection_key)[:MAX_TRAJECTORY_SECTORS]
    selected_codes_by_type = {
        sector_type: [code for item_type, code in selected_keys if item_type == sector_type]
        for sector_type in ("concept", "industry")
    }
    source_ids = sorted(
        {
            row["state_source_collection_id"]
            for row in migration_rows
            if row.get("state_source_collection_id")
            and (row["sector_type"], row["sector_code"]) in selected_keys
        }
    )
    state_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for sector_type, table in (
        ("concept", "market.hithink_concept_state"),
        ("industry", "market.hithink_industry_state"),
    ):
        codes = selected_codes_by_type[sector_type]
        if not codes or not source_ids:
            continue
        state_rows = builder._rows(
            f"""
            SELECT toString(collection_id) collection_id,sector_code,
                index_change_ratio_pct
            FROM {table} FINAL
            WHERE trade_date={{trade_date:Date}}
              AND sector_code IN {{sector_codes:Array(String)}}
              AND toString(collection_id) IN {{collection_ids:Array(String)}}
            """,
            {
                "trade_date": trade_date,
                "sector_codes": codes,
                "collection_ids": source_ids,
            },
        )
        for row in state_rows:
            state_map[(sector_type, row["collection_id"], row["sector_code"])] = row

    rows: list[list[Any]] = []
    for sector_type, sector_code in selected_keys:
        identity = pool[(sector_type, sector_code)]
        sector_name = identity.get("sector_name")
        for node in nodes:
            candidate = candidate_map.get(
                (node["collection_id"], sector_type, sector_code)
            ) or {}
            migration = migration_map.get(
                (node["collection_id"], sector_type, sector_code)
            ) or {}
            source_id = migration.get("state_source_collection_id")
            state = state_map.get((sector_type, source_id, sector_code)) or {}
            rows.append(
                [
                    sector_type,
                    sector_code,
                    migration.get("sector_name") or sector_name,
                    short_time(node["scheduled_time"]),
                    candidate.get("candidate_rank"),
                    candidate.get("candidate_score_v1"),
                    state.get("index_change_ratio_pct"),
                    migration.get("up_ratio"),
                    migration.get("turnover_market_share_pct"),
                    migration.get("turnover_market_share_delta_15m"),
                    migration.get("turnover_1m_market_share_pct"),
                    migration.get("turnover_1m_market_share_delta_15m"),
                    migration.get("limit_up_count"),
                    migration.get("limit_break_count"),
                    comparable_new_high_ratio(
                        node["scheduled_time"], migration.get("new_high_ratio")
                    ),
                ]
            )
    return {
        "selection": {
            "max_sectors": MAX_TRAJECTORY_SECTORS,
            "sector_count": len(selected_keys),
            "key_times": [short_time(row["scheduled_time"]) for row in nodes],
        },
        "schema": CORE_TRAJECTORY_SCHEMA,
        "rows": rows,
    }


def compact_core_stocks(source: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    for item in source[:20]:
        memberships = [
            membership.get("sector_name")
            for membership in (item.get("core_sector_memberships") or [])
            if membership.get("sector_name")
            and membership.get("sector_name") not in GENERIC_STOCK_LABELS
        ][:3]
        rows.append(
            [
                item.get("thscode") or item.get("ticker"),
                item.get("stock_name"),
                item.get("candidate_rank"),
                item.get("candidate_score_v1"),
                item.get("price_change_ratio_pct"),
                item.get("price_change_1m_pct"),
                item.get("turnover"),
                item.get("turnover_delta_1m"),
                item.get("turnover_rank_market"),
                item.get("turnover_delta_1m_rank_market"),
                item.get("new_high_flag"),
                item.get("is_limit_up"),
                item.get("is_limit_break"),
                item.get("best_core_sector_name"),
                item.get("best_sector_turnover_rank"),
                item.get("best_sector_price_rank"),
                item.get("candidate_reasons") or [],
                memberships,
            ]
        )
    return {"schema": CORE_STOCK_SCHEMA, "rows": rows}


def compact_strategic(source: dict[str, Any]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    for strategic_theme, items in source.items():
        for item in items or []:
            rows.append(
                [
                    strategic_theme,
                    item.get("strategic_subtheme"),
                    item.get("watch_level"),
                    item.get("sector_code"),
                    item.get("sector_name"),
                    item.get("index_change_ratio_pct"),
                    item.get("up_ratio"),
                    item.get("turnover_market_share_pct"),
                    item.get("turnover_1m_market_share_pct"),
                    item.get("turnover_1m_market_share_delta_15m"),
                    item.get("current_class_rank"),
                    item.get("current_candidate_rank"),
                    item.get("limit_up_count"),
                    item.get("limit_break_count"),
                    item.get("new_high_ratio"),
                ]
            )
    return {"schema": STRATEGIC_SCHEMA, "rows": rows}


def compact_market(source: dict[str, Any]) -> dict[str, Any]:
    current = source.get("current_state") or {}
    change = source.get("change_15m") or {}
    current_fields = (
        ("session", "session"),
        ("stock_total", "stock_total"),
        ("valid_stock_count", "valid_stock_count"),
        ("up_count", "up_count"),
        ("down_count", "down_count"),
        ("flat_count", "flat_count"),
        ("up_ratio", "up_ratio"),
        ("down_ratio", "down_ratio"),
        ("limit_up", "limit_up_count"),
        ("up_5_to_limit", "up_5_to_limit_count"),
        ("up_1_to_5", "up_1_to_5_count"),
        ("up_0_to_1", "up_0_to_1_count"),
        ("down_0_to_1", "down_0_to_1_count"),
        ("down_1_to_5", "down_1_to_5_count"),
        ("down_5_to_limit", "down_5_to_limit_count"),
        ("limit_down", "limit_down_count"),
        ("limit_break", "limit_break_count"),
        ("turnover", "turnover_total"),
        ("turnover_1m", "turnover_delta_1m_total"),
        ("turnover_prev_day_pct", "turnover_prev_day_pct"),
        ("new_high", "new_high_count"),
        ("new_low", "new_low_count"),
        ("price_up_1m", "price_up_1m_count"),
        ("price_down_1m", "price_down_1m_count"),
        ("price_flat_1m", "price_flat_1m_count"),
    )
    change_fields = (
        ("status", "delta_status"),
        ("type", "delta_type"),
        ("up_count_change", "up_count_delta_15m"),
        ("down_count_change", "down_count_delta_15m"),
        ("flat_count_change", "flat_count_delta_15m"),
        ("up_ratio_change", "up_ratio_delta_15m"),
        ("down_ratio_change", "down_ratio_delta_15m"),
        ("limit_up_change", "limit_up_count_delta_15m"),
        ("limit_down_change", "limit_down_count_delta_15m"),
        ("limit_break_change", "limit_break_count_delta_15m"),
        ("turnover_increment", "turnover_increment_15m"),
        ("turnover_speed_current", "turnover_speed_current"),
        ("turnover_speed_base", "turnover_speed_base"),
        ("turnover_speed_change", "turnover_speed_delta_15m"),
        ("turnover_speed_change_pct", "turnover_speed_change_pct"),
        ("new_high_change", "new_high_count_delta_15m"),
        ("new_low_change", "new_low_count_delta_15m"),
        ("price_up_1m_change", "price_up_1m_count_delta_15m"),
        ("price_down_1m_change", "price_down_1m_count_delta_15m"),
        ("price_flat_1m_change", "price_flat_1m_count_delta_15m"),
    )
    trajectory_rows = []
    for node in source.get("intraday_trajectory") or []:
        trajectory_rows.append(
            [
                short_time(node.get("scheduled_time")),
                minute_metric_context(node.get("scheduled_time")),
                node.get("up_count"),
                node.get("down_count"),
                node.get("flat_count"),
                node.get("up_ratio"),
                node.get("limit_up_count"),
                node.get("limit_down_count"),
                node.get("limit_break_count"),
                node.get("turnover_total"),
                node.get("turnover_delta_1m_total"),
                node.get("turnover_prev_day_pct"),
                node.get("new_high_count"),
                node.get("new_low_count"),
                node.get("price_up_1m_count"),
                node.get("price_down_1m_count"),
            ]
        )
    current_compact = without_nulls(current, current_fields)
    current_compact["minute_metric_context"] = minute_metric_context(
        current.get("scheduled_time")
    )
    return {
        "current": current_compact,
        "change_15m": without_nulls(change, change_fields),
        "trajectory": {"schema": MARKET_TRAJECTORY_SCHEMA, "rows": trajectory_rows},
    }


def compact_emotion(source: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for node in source.get("intraday_trajectory") or []:
        rows.append(
            [
                short_time(node.get("scheduled_time")),
                node.get("limit_up_count"),
                node.get("limit_down_count"),
                node.get("limit_break_count"),
                node.get("limit_attempt_count"),
                node.get("limit_success_rate"),
                node.get("limit_break_rate"),
                node.get("first_board_count"),
                node.get("second_board_count"),
                node.get("third_board_count"),
                node.get("fourth_board_count"),
                node.get("fifth_plus_board_count"),
                node.get("max_board_height"),
                node.get("promotion_base_count"),
                node.get("promotion_success_count"),
                node.get("promotion_fail_count"),
                node.get("promotion_rate"),
                node.get("high_board_count"),
                node.get("high_board_break_count"),
                node.get("high_board_fail_count"),
            ]
        )
    return {"trajectory": {"schema": EMOTION_SCHEMA, "rows": rows}}


def compact_overlap(source: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            item.get("strategic_theme"),
            item.get("sector_name"),
            item.get("candidate_rank"),
            item.get("candidate_score_v1"),
        ]
        for item in source.get("strategic_sectors_in_current_core") or []
    ]


def compact_quality(full: dict[str, Any]) -> dict[str, Any]:
    source_quality = full.get("data_quality") or {}
    fallback_modules: list[str] = []
    fallback_count = 0

    def add_status(module: str, status: Any, count: int = 1) -> None:
        nonlocal fallback_count
        if isinstance(status, str) and "FALLBACK" in status.upper():
            fallback_count += count
            if module not in fallback_modules:
                fallback_modules.append(module)

    add_status("market", source_quality.get("market_state_data_status"))
    add_status("emotion", source_quality.get("emotion_pool_data_status"))
    for module, field in (
        ("capital_migration", "capital_migration_status_counts"),
        ("core_sectors", "core_sector_status_counts"),
        ("core_stocks", "core_stock_status_counts"),
    ):
        for status, count in (source_quality.get(field) or {}).items():
            if isinstance(count, int):
                add_status(module, status, count)

    ages: list[float] = []

    def collect_ages(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("source_age_seconds") and isinstance(item, (int, float)):
                    ages.append(float(item))
                else:
                    collect_ages(item)
        elif isinstance(value, list):
            for item in value:
                collect_ages(item)

    collect_ages(full)
    return {
        "market_status": source_quality.get("market_state_data_status") or "MISSING",
        "emotion_status": source_quality.get("emotion_pool_data_status") or "MISSING",
        "fallback_count": fallback_count,
        "max_source_age_seconds": max(ages, default=0),
        "fallback_modules": fallback_modules,
    }


def add_reader_compatible_envelope(
    compact_body: dict[str, Any], original_target: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Add the persisted-package identity required by the existing read validator."""
    meta = compact_body.get("meta") or {}
    package_id = meta.get("package_id")
    scheduled_time = meta.get("scheduled_time")
    trade_date = meta.get("trade_date")
    if not isinstance(package_id, str) or len(package_id) != 11:
        raise ValueError("Compact包缺少有效package_id")
    if not isinstance(scheduled_time, str) or not isinstance(trade_date, str):
        raise TypeError("Compact包缺少有效交易日期或节点时间")

    target = dict(original_target or {})
    if not target:
        hour = int(scheduled_time.split("T", 1)[1][:2])
        target = {
            "trade_date": trade_date,
            "requested_time": scheduled_time,
            "resolved_collection_id": package_id,
            "resolved_node_seq": int(package_id[-3:]),
            "resolved_scheduled_time": scheduled_time,
            "resolution_distance_seconds": 0.0,
            "business_period": "AM" if hour < 12 else "PM",
        }

    body = {
        key: value
        for key, value in compact_body.items()
        if key
        not in {
            "package_type",
            "package_version",
            "package_id",
            "target",
            "generated_at",
        }
    }
    return {
        "package_type": "MARKET_STATE_RESTORE",
        "package_version": "COMPACT_V3",
        "package_id": package_id,
        "target": target,
        **body,
        "generated_at": datetime.now(SHANGHAI).isoformat(),
    }


def build_compact(
    full: dict[str, Any],
    source_file: Path,
    core_trajectory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = full.get("target") or {}
    quality = full.get("data_quality") or {}
    source_time = (full.get("market") or {}).get("current_state", {}).get("source_time")
    compact = {
        "meta": {
            "package_id": full.get("package_id"),
            "trade_date": target.get("trade_date"),
            "scheduled_time": target.get("resolved_scheduled_time"),
            "source_time": source_time,
            "source_age_seconds": quality.get("market_state_source_age_seconds"),
            "full_package_generated_at": full.get("generated_at"),
        },
        "quality": compact_quality(full),
        "market": compact_market(full.get("market") or {}),
        "emotion": compact_emotion(full.get("emotion") or {}),
        "capital_migration": compact_capital(full.get("capital_migration") or {}),
        "core_sectors": {
            "current_top20": compact_core_sectors(full.get("core_sectors") or {})
        },
        "core_sector_trajectory": core_trajectory
        or compact_core_trajectory(full.get("core_sector_intraday") or {}),
        "core_stocks": compact_core_stocks(full.get("core_stocks") or []),
        "strategic_watch": compact_strategic(full.get("strategic_watch") or {}),
        "strategic_core_overlap": compact_overlap(full.get("cross_relations") or {}),
    }
    compact = round_two_places(add_reader_compatible_envelope(compact, target))
    validate_compact(compact)
    return compact


def validate_compact(compact: dict[str, Any]) -> None:
    core_sector_rows = compact["core_sectors"]["current_top20"]["rows"]
    core_stock_rows = compact["core_stocks"]["rows"]
    strategic_rows = compact["strategic_watch"]["rows"]
    trajectory_rows = compact["core_sector_trajectory"]["rows"]
    if len(core_sector_rows) > 20:
        raise ValueError(f"核心板块超过20个：{len(core_sector_rows)}")
    if len(core_stock_rows) > 20:
        raise ValueError(f"核心个股超过20只：{len(core_stock_rows)}")
    if len(strategic_rows) != 57:
        raise ValueError(f"战略方向观察不是57条：{len(strategic_rows)}")
    selection = compact["core_sector_trajectory"].get("selection") or {}
    sector_count = selection.get("sector_count")
    key_times = selection.get("key_times") or []
    if isinstance(sector_count, int) and sector_count > MAX_TRAJECTORY_SECTORS:
        raise ValueError(f"核心板块轨迹超过{MAX_TRAJECTORY_SECTORS}个板块")
    if (
        isinstance(sector_count, int)
        and len(trajectory_rows) != sector_count * len(key_times)
    ):
        raise ValueError(f"核心板块轨迹超过20×6：{len(trajectory_rows)}")
    for sector_type, expected in (("concept", 5), ("industry", 5), ("style", 3)):
        for list_name, rows in compact["capital_migration"][sector_type].items():
            if len(rows) > expected:
                raise ValueError(f"{sector_type}.{list_name}超过Top{expected}")


def write_compact(source_file: Path, output_root: Path) -> tuple[Path, int, int]:
    with source_file.open("r", encoding="utf-8") as handle:
        full = json.load(handle)
    if (
        isinstance(full.get("meta"), dict)
        and "current_top20" in (full.get("core_sectors") or {})
        and "rows" in (full.get("strategic_watch") or {})
    ):
        compact = add_reader_compatible_envelope(full)
        validate_compact(compact)
    else:
        compact = build_compact(full, source_file)
    trade_date = str(compact["meta"]["trade_date"]).replace("-", "")
    output_file = output_root / trade_date / source_file.name
    output_file.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    output_file.write_bytes(encoded)
    return output_file, source_file.stat().st_size, len(encoded)


def write_database_compact(
    builder: Any,
    trade_date: str,
    target_time: str,
    output_root: Path,
) -> tuple[Path, int, int]:
    """Build a full package in memory, then persist only final Compact V3 output."""
    full = builder.build(trade_date, target_time)
    full["generated_at"] = datetime.now(SHANGHAI).isoformat()
    trajectory = build_key_core_trajectory(builder, full)
    compact = build_compact(full, Path("DATABASE_READ_ONLY"), trajectory)
    date_directory = trade_date.replace("-", "")
    output_file = output_root / date_directory / f"{compact['package_id']}.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    full_bytes = len(
        json.dumps(full, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    output_file.write_bytes(encoded)
    return output_file, full_bytes, len(encoded)


def main() -> int:
    parser = argparse.ArgumentParser(description="模拟生成Compact V3市场状态包")
    parser.add_argument("source_files", nargs="+", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    for source_file in args.source_files:
        output_file, full_size, compact_size = write_compact(source_file, args.output_root)
        ratio = compact_size / full_size if full_size else 0
        print(
            f"{source_file}\t{output_file}\t{full_size}\t{compact_size}\t{ratio:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
