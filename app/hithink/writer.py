from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import clickhouse_connect

from app.hithink.api import LimitPoolSnapshot
from app.hithink.models import DerivedSnapshot, RawSnapshot
from app.hithink.schedule import ScheduleNode

SCHEDULE = "market.hithink_snapshot_schedule"
RAW = "market.hithink_snapshot_raw"
DERIVED = "market.hithink_snapshot_derived"
MARKET_STATE = "market.hithink_market_state"
LIMIT_UP_POOL = "market.hithink_limit_up_pool"
LIMIT_DOWN_POOL = "market.hithink_limit_down_pool"
CALENDAR = "market.trading_calendar"
DAILY_STATUS = "market.hithink_daily_sync_status"


class ClickHouseWriter:
    def __init__(self, host: str, port: int, database: str, username: str, password: str):
        self.client = clickhouse_connect.get_client(
            host=host, port=port, username=username, password=password, database="default"
        )

    def close(self) -> None:
        self.client.close()

    def initialize_schema(self, schema_path: Path) -> None:
        for statement in schema_path.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                self.client.command(statement)

    def initialize_schedule(self, nodes: list[ScheduleNode]) -> None:
        if self._scalar(
            f"SELECT count() FROM {SCHEDULE} FINAL WHERE trade_date = {{date:Date}}",
            {"date": nodes[0].trade_date},
        ):
            return
        self.client.insert(
            SCHEDULE,
            [
                (n.trade_date, n.collection_id, n.scheduled_time, n.session, n.sequence_no, "PENDING")
                for n in nodes
            ],
            column_names=[
                "trade_date",
                "collection_id",
                "scheduled_time",
                "session",
                "sequence_no",
                "status",
            ],
        )

    def cache_trading_calendar(self, trade_dates: set[object], today: object) -> None:
        rows = [(trade_date, 1, "hithink") for trade_date in trade_dates]
        if today not in trade_dates:
            rows.append((today, 0, "hithink"))
        self.client.insert(
            CALENDAR,
            rows,
            column_names=["trade_date", "is_trading_day", "source"],
        )

    def cached_trading_day(self, trade_date: object) -> bool | None:
        result = self.client.query(
            f"SELECT is_trading_day FROM {CALENDAR} FINAL WHERE trade_date = {{date:Date}}",
            parameters={"date": trade_date},
        ).result_rows
        return bool(result[0][0]) if result else None

    def set_daily_status(
        self, trade_date: object, task_name: str, status: str, **values: Any
    ) -> None:
        columns = ["trade_date", "task_name", "status", *values.keys()]
        row = [trade_date, task_name, status, *values.values()]
        self.client.insert(DAILY_STATUS, [row], column_names=columns)

    def daily_status(self, trade_date: object, task_name: str) -> str | None:
        result = self.client.query(
            f"SELECT status FROM {DAILY_STATUS} FINAL WHERE trade_date = {{date:Date}} AND task_name = {{task:String}}",
            parameters={"date": trade_date, "task": task_name},
        ).result_rows
        return result[0][0] if result else None

    def daily_seed_complete(self) -> bool:
        raw = self.client.query(
            f"SELECT count() FROM {DAILY_STATUS} FINAL WHERE task_name = 'hithink_daily_k_raw_sync' AND status = 'SUCCESS'"
        ).first_item
        events = self.client.query(
            f"SELECT count() FROM {DAILY_STATUS} FINAL WHERE task_name = 'hithink_adjustment_events_sync' AND status = 'SUCCESS'"
        ).first_item
        return bool(raw and events)

    def statuses(self, trade_date: object) -> dict[datetime, str]:
        result = self.client.query(
            f"SELECT scheduled_time, status FROM {SCHEDULE} FINAL WHERE trade_date = {{date:Date}}",
            parameters={"date": trade_date},
        )
        return {row[0]: row[1] for row in result.result_rows}

    def set_schedule_status(self, node: ScheduleNode, status: str, **values: Any) -> None:
        columns = [
            "trade_date",
            "collection_id",
            "scheduled_time",
            "session",
            "sequence_no",
            "status",
            *values.keys(),
        ]
        row = [
            node.trade_date,
            node.collection_id,
            node.scheduled_time,
            node.session,
            node.sequence_no,
            status,
            *values.values(),
        ]
        self.client.insert(SCHEDULE, [row], column_names=columns)

    def previous_derived(self, node: ScheduleNode) -> dict[str, DerivedSnapshot]:
        columns = [
            "trade_date",
            "collection_id",
            "scheduled_time",
            "source_timestamp",
            "source_time",
            "session",
            "batch_id",
            "thscode",
            "ticker",
            "last_price",
            "price_change",
            "price_change_ratio_pct",
            "open_price",
            "high_price",
            "low_price",
            "prev_price",
            "volume",
            "turnover",
            "turnover_delta_1m",
            "volume_delta_1m",
            "turnover_growth_1m",
            "volume_ratio_1m",
            "new_high_flag",
            "new_low_flag",
            "price_delta_1m",
            "price_change_1m_pct",
        ]
        result = self.client.query(
            f"SELECT {', '.join(columns)} FROM {DERIVED} WHERE collection_id = {{collection_id:String}}",
            parameters={"collection_id": node.collection_id},
        )
        found: dict[str, DerivedSnapshot] = {}
        raw_columns = list(RawSnapshot.__dataclass_fields__)
        for values in result.result_rows:
            row = dict(zip(columns, values))
            raw = RawSnapshot(**{key: row[key] for key in raw_columns})
            found[raw.thscode] = DerivedSnapshot(
                raw,
                *[
                    row[key]
                    for key in DerivedSnapshot.__dataclass_fields__
                    if key != "raw"
                ],
            )
        return found

    def insert_raw_once(self, rows: list[RawSnapshot]) -> tuple[int, int]:
        return self._insert_once(RAW, rows, [field for field in RawSnapshot.__dataclass_fields__])

    def insert_derived_once(self, rows: list[DerivedSnapshot]) -> tuple[int, int]:
        flattened = [
            (
                *asdict(row.raw).values(),
                row.turnover_delta_1m,
                row.volume_delta_1m,
                row.turnover_growth_1m,
                row.volume_ratio_1m,
                row.new_high_flag,
                row.new_low_flag,
                row.price_delta_1m,
                row.price_change_1m_pct,
            )
            for row in rows
        ]
        columns = [
            *RawSnapshot.__dataclass_fields__,
            *[field for field in DerivedSnapshot.__dataclass_fields__ if field != "raw"],
        ]
        return self._insert_once(
            DERIVED, flattened, columns, batch_id=rows[0].raw.batch_id if rows else None
        )

    def insert_limit_up_pool(
        self, node: ScheduleNode, batch_id: str, snapshot: LimitPoolSnapshot
    ) -> tuple[int, int]:
        pool_batch = f"{batch_id}-limit-up"
        rows = []
        for record in snapshot.items:
            item = record.item
            rows.append(
                (
                    node.trade_date,
                    node.collection_id,
                    node.scheduled_time,
                    pool_batch,
                    record.source_timestamp,
                    record.source_time,
                    str(item["thscode"]),
                    str(item["ticker"]),
                    str(item.get("name") or ""),
                    int(item.get("is_st") or 0),
                    int(item.get("is_new") or 0),
                    float(item["last_price"]),
                    float(item["price_change_ratio_pct"]),
                    _optional_string(item.get("limit_up_time")),
                    _optional_string(item.get("limit_up_reason")),
                    _optional_string(item.get("continue_day_text")),
                    _optional_int(item.get("continue_day_cnt")),
                    _optional_float(item.get("seal_money")),
                    _optional_float(item.get("max_seal_money")),
                )
            )
        columns = [
            "trade_date", "collection_id", "scheduled_time", "batch_id",
            "source_timestamp", "source_time", "thscode", "ticker", "name",
            "is_st", "is_new", "last_price", "price_change_ratio_pct",
            "limit_up_time", "limit_up_reason", "continue_day_text",
            "continue_day_cnt", "seal_money", "max_seal_money",
        ]
        return self._insert_once(LIMIT_UP_POOL, rows, columns, batch_id=pool_batch)

    def insert_limit_down_pool(
        self, node: ScheduleNode, batch_id: str, snapshot: LimitPoolSnapshot
    ) -> tuple[int, int]:
        pool_batch = f"{batch_id}-limit-down"
        rows = []
        for record in snapshot.items:
            item = record.item
            rows.append(
                (
                    node.trade_date,
                    node.collection_id,
                    node.scheduled_time,
                    pool_batch,
                    record.source_timestamp,
                    record.source_time,
                    str(item["thscode"]),
                    str(item["ticker"]),
                    str(item.get("name") or ""),
                    float(item["last_price"]),
                    float(item["price_change_ratio_pct"]),
                    _optional_string(item.get("first_limit_time")),
                    _optional_string(item.get("last_limit_time")),
                    _optional_float(item.get("turnover_ratio_pct")),
                )
            )
        columns = [
            "trade_date", "collection_id", "scheduled_time", "batch_id",
            "source_timestamp", "source_time", "thscode", "ticker", "name",
            "last_price", "price_change_ratio_pct", "first_limit_time",
            "last_limit_time", "turnover_ratio_pct",
        ]
        return self._insert_once(LIMIT_DOWN_POOL, rows, columns, batch_id=pool_batch)

    def limit_pool_collected(self, node: ScheduleNode) -> bool:
        return bool(
            self._scalar(
                f"SELECT count() FROM {SCHEDULE} FINAL WHERE collection_id = {{collection_id:String}} AND limit_pool_collected = 1",
                {"collection_id": node.collection_id},
            )
        )

    def upsert_market_state(self, node: ScheduleNode, limit_pool_available: bool) -> None:
        """Aggregate one real snapshot node; absent snapshot nodes are never synthesized."""
        self.client.command(
            f"""
            INSERT INTO {MARKET_STATE}
            (
                trade_date, collection_id, scheduled_time, source_time, session,
                stock_total, valid_stock_count, up_count, down_count, flat_count, up_ratio, down_ratio,
                limit_up_count, up_5_to_limit_count, up_1_to_5_count, up_0_to_1_count,
                down_0_to_1_count, down_1_to_5_count, down_5_to_limit_count, limit_down_count,
                turnover_total, turnover_delta_1m_total, prev_turnover_delta_1m_total,
                turnover_growth_1m_market, volume_total, volume_delta_1m_total,
                yesterday_same_time_turnover, turnover_prev_day_delta, turnover_prev_day_pct,
                turnover_accel_count, turnover_decel_count, turnover_accel_50_count,
                turnover_accel_100_count, volume_expand_count, volume_contract_count,
                volume_ratio_1_5_count, volume_ratio_2_count, volume_ratio_3_count,
                new_high_count, new_low_count, price_up_1m_count, price_down_1m_count,
                price_flat_1m_count, volume_price_up_count, volume_price_down_count,
                contract_price_up_count, contract_price_down_count, up_count_delta_1m,
                down_count_delta_1m, new_high_count_delta_1m, new_low_count_delta_1m,
                volume_price_up_delta_1m, volume_price_down_delta_1m
            )
            WITH
                previous AS
                (
                    SELECT * FROM {MARKET_STATE} FINAL
                    WHERE collection_id = {{previous_collection_id:String}}
                    LIMIT 1
                ),
                prior_trade_day AS
                (
                    SELECT turnover_total, 1 AS found FROM {MARKET_STATE} FINAL
                    WHERE collection_id = concat(
                        formatDateTime(
                            (
                                SELECT max(trade_date)
                                FROM {CALENDAR} FINAL
                                WHERE trade_date < {{date:Date}} AND is_trading_day = 1
                            ),
                            '%Y%m%d'
                        ),
                        leftPad(toString({{sequence_no:UInt16}}), 3, '0')
                    )
                    LIMIT 1
                )
            SELECT
                current.trade_date,
                current.collection_id,
                current.scheduled_time,
                current.source_time,
                current.session,
                current.stock_total,
                current.valid_stock_count,
                current.up_count,
                current.down_count,
                current.flat_count,
                if(current.valid_stock_count > 0, current.up_count / current.valid_stock_count, 0.0),
                if(current.valid_stock_count > 0, current.down_count / current.valid_stock_count, 0.0),
                current.limit_up_count,
                current.up_5_to_limit_count,
                current.up_1_to_5_count,
                current.up_0_to_1_count,
                current.down_0_to_1_count,
                current.down_1_to_5_count,
                current.down_5_to_limit_count,
                current.limit_down_count,
                current.turnover_total,
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60
                    AND current.turnover_delta_1m_total IS NOT NULL,
                    current.turnover_delta_1m_total,
                    CAST(NULL, 'Nullable(Decimal64(2))')
                ),
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60,
                    previous.turnover_delta_1m_total,
                    CAST(NULL, 'Nullable(Decimal64(2))')
                ),
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60
                    AND current.turnover_delta_1m_total IS NOT NULL
                    AND previous.turnover_delta_1m_total > 0,
                    toFloat64(current.turnover_delta_1m_total)
                        / toFloat64(previous.turnover_delta_1m_total) - 1,
                    CAST(NULL, 'Nullable(Float64)')
                ),
                current.volume_total,
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60
                    AND current.volume_delta_1m_total IS NOT NULL,
                    current.volume_delta_1m_total,
                    CAST(NULL, 'Nullable(Int64)')
                ),
                if(prior_trade_day.found = 1, prior_trade_day.turnover_total,
                    CAST(NULL, 'Nullable(Decimal64(2))')),
                if(prior_trade_day.found = 1,
                    CAST(current.turnover_total - prior_trade_day.turnover_total, 'Nullable(Decimal64(2))'),
                    CAST(NULL, 'Nullable(Decimal64(2))')),
                if(prior_trade_day.found = 1 AND prior_trade_day.turnover_total > 0,
                    toFloat64(current.turnover_total - prior_trade_day.turnover_total)
                        / toFloat64(prior_trade_day.turnover_total),
                    CAST(NULL, 'Nullable(Float64)')),
                current.turnover_accel_count,
                current.turnover_decel_count,
                current.turnover_accel_50_count,
                current.turnover_accel_100_count,
                current.volume_expand_count,
                current.volume_contract_count,
                current.volume_ratio_1_5_count,
                current.volume_ratio_2_count,
                current.volume_ratio_3_count,
                current.new_high_count,
                current.new_low_count,
                current.price_up_1m_count,
                current.price_down_1m_count,
                current.price_flat_1m_count,
                current.volume_price_up_count,
                current.volume_price_down_count,
                current.contract_price_up_count,
                current.contract_price_down_count,
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60,
                    CAST(toInt64(current.up_count) - toInt64(previous.up_count), 'Nullable(Int32)'),
                    CAST(NULL, 'Nullable(Int32)')),
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60,
                    CAST(toInt64(current.down_count) - toInt64(previous.down_count), 'Nullable(Int32)'),
                    CAST(NULL, 'Nullable(Int32)')),
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60
                    AND current.new_high_count IS NOT NULL AND previous.new_high_count IS NOT NULL,
                    CAST(toInt64(current.new_high_count) - toInt64(previous.new_high_count), 'Nullable(Int32)'),
                    CAST(NULL, 'Nullable(Int32)')),
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60
                    AND current.new_low_count IS NOT NULL AND previous.new_low_count IS NOT NULL,
                    CAST(toInt64(current.new_low_count) - toInt64(previous.new_low_count), 'Nullable(Int32)'),
                    CAST(NULL, 'Nullable(Int32)')),
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60
                    AND current.volume_price_up_count IS NOT NULL AND previous.volume_price_up_count IS NOT NULL,
                    CAST(toInt64(current.volume_price_up_count) - toInt64(previous.volume_price_up_count), 'Nullable(Int32)'),
                    CAST(NULL, 'Nullable(Int32)')),
                if(
                    previous.scheduled_time IS NOT NULL
                    AND previous.session = current.session
                    AND dateDiff('second', previous.scheduled_time, current.scheduled_time) = 60
                    AND current.volume_price_down_count IS NOT NULL AND previous.volume_price_down_count IS NOT NULL,
                    CAST(toInt64(current.volume_price_down_count) - toInt64(previous.volume_price_down_count), 'Nullable(Int32)'),
                    CAST(NULL, 'Nullable(Int32)'))
            FROM
            (
                SELECT
                    any(trade_date) AS trade_date,
                    any(collection_id) AS collection_id,
                    any(scheduled_time) AS scheduled_time,
                    max(source_time) AS source_time,
                    any(session) AS session,
                    toUInt32(count()) AS stock_total,
                    toUInt32(countIf(price_change_ratio_pct IS NOT NULL)) AS valid_stock_count,
                    toUInt32(countIf(price_change_ratio_pct > 0)) AS up_count,
                    toUInt32(countIf(price_change_ratio_pct < 0)) AS down_count,
                    toUInt32(countIf(price_change_ratio_pct = 0)) AS flat_count,
                    if({{pool_available:UInt8}} = 1,
                        toUInt32(countIf(thscode IN (SELECT DISTINCT thscode FROM {LIMIT_UP_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                        CAST(NULL, 'Nullable(UInt32)')) AS limit_up_count,
                    if({{pool_available:UInt8}} = 1,
                        toUInt32(countIf(price_change_ratio_pct >= 5 AND thscode NOT IN (SELECT DISTINCT thscode FROM {LIMIT_UP_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                        CAST(NULL, 'Nullable(UInt32)')) AS up_5_to_limit_count,
                    toUInt32(countIf(price_change_ratio_pct >= 1 AND price_change_ratio_pct < 5)) AS up_1_to_5_count,
                    toUInt32(countIf(price_change_ratio_pct > 0 AND price_change_ratio_pct < 1)) AS up_0_to_1_count,
                    toUInt32(countIf(price_change_ratio_pct < 0 AND price_change_ratio_pct > -1)) AS down_0_to_1_count,
                    toUInt32(countIf(price_change_ratio_pct <= -1 AND price_change_ratio_pct > -5)) AS down_1_to_5_count,
                    if({{pool_available:UInt8}} = 1,
                        toUInt32(countIf(price_change_ratio_pct <= -5 AND thscode NOT IN (SELECT DISTINCT thscode FROM {LIMIT_DOWN_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                        CAST(NULL, 'Nullable(UInt32)')) AS down_5_to_limit_count,
                    if({{pool_available:UInt8}} = 1,
                        toUInt32(countIf(thscode IN (SELECT DISTINCT thscode FROM {LIMIT_DOWN_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                        CAST(NULL, 'Nullable(UInt32)')) AS limit_down_count,
                    toDecimal64(sum(turnover), 2) AS turnover_total,
                    if(countIf(turnover_delta_1m IS NOT NULL) = 0,
                        CAST(NULL, 'Nullable(Decimal64(2))'),
                        CAST(sum(turnover_delta_1m), 'Nullable(Decimal64(2))')) AS turnover_delta_1m_total,
                    toUInt64(sum(volume)) AS volume_total,
                    if(countIf(volume_delta_1m IS NOT NULL) = 0,
                        CAST(NULL, 'Nullable(Int64)'),
                        CAST(sum(volume_delta_1m), 'Nullable(Int64)')) AS volume_delta_1m_total,
                    if(countIf(turnover_growth_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(turnover_growth_1m > 0))) AS turnover_accel_count,
                    if(countIf(turnover_growth_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(turnover_growth_1m < 0))) AS turnover_decel_count,
                    if(countIf(turnover_growth_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(turnover_growth_1m >= 0.5))) AS turnover_accel_50_count,
                    if(countIf(turnover_growth_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(turnover_growth_1m >= 1))) AS turnover_accel_100_count,
                    if(countIf(volume_ratio_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m > 1))) AS volume_expand_count,
                    if(countIf(volume_ratio_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m < 1))) AS volume_contract_count,
                    if(countIf(volume_ratio_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m > 1.5))) AS volume_ratio_1_5_count,
                    if(countIf(volume_ratio_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m > 2))) AS volume_ratio_2_count,
                    if(countIf(volume_ratio_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m > 3))) AS volume_ratio_3_count,
                    if(countIf(new_high_flag IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(new_high_flag = 1))) AS new_high_count,
                    if(countIf(new_low_flag IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(new_low_flag = 1))) AS new_low_count,
                    if(countIf(price_delta_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(price_delta_1m > 0))) AS price_up_1m_count,
                    if(countIf(price_delta_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(price_delta_1m < 0))) AS price_down_1m_count,
                    if(countIf(price_delta_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(price_delta_1m = 0))) AS price_flat_1m_count,
                    if(countIf(volume_ratio_1m IS NOT NULL AND price_delta_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m > 1 AND price_delta_1m > 0))) AS volume_price_up_count,
                    if(countIf(volume_ratio_1m IS NOT NULL AND price_delta_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m > 1 AND price_delta_1m < 0))) AS volume_price_down_count,
                    if(countIf(volume_ratio_1m IS NOT NULL AND price_delta_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m < 1 AND price_delta_1m > 0))) AS contract_price_up_count,
                    if(countIf(volume_ratio_1m IS NOT NULL AND price_delta_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m < 1 AND price_delta_1m < 0))) AS contract_price_down_count
                FROM {DERIVED} AS d
                WHERE d.collection_id = {{collection_id:String}}
            ) AS current
            LEFT JOIN previous ON 1 = 1
            LEFT JOIN prior_trade_day ON 1 = 1
            """,
            parameters={
                "date": node.trade_date,
                "time": node.scheduled_time,
                "collection_id": node.collection_id,
                "previous_collection_id": (
                    f"{node.trade_date:%Y%m%d}{node.sequence_no - 1:03d}"
                    if node.sequence_no > 1
                    else ""
                ),
                "sequence_no": node.sequence_no,
                "pool_available": int(limit_pool_available),
            },
        )

    def market_state_nodes(self, trade_date: object) -> list[ScheduleNode]:
        result = self.client.query(
            f"""
            SELECT s.scheduled_time, s.session, s.sequence_no
            FROM
            (
                SELECT collection_id, scheduled_time, session, sequence_no
                FROM {SCHEDULE} FINAL
                WHERE trade_date = {{date:Date}}
            ) AS s
            INNER JOIN
            (
                SELECT DISTINCT collection_id
                FROM {DERIVED}
                WHERE trade_date = {{date:Date}}
            ) AS d USING (collection_id)
            ORDER BY s.sequence_no
            """,
            parameters={"date": trade_date},
        )
        return [
            ScheduleNode(trade_date, scheduled_time, session, sequence_no)
            for scheduled_time, session, sequence_no in result.result_rows
        ]

    def migrate_collection_ids(self, trade_date: object) -> None:
        """Backfill the immutable YYYYMMDD + 3-digit planned sequence key."""
        self.client.command(
            f"""
            ALTER TABLE {SCHEDULE} UPDATE
                collection_id = concat(
                    formatDateTime(trade_date, '%Y%m%d'),
                    leftPad(toString(sequence_no), 3, '0')
                )
            WHERE trade_date = {{date:Date}}
            """,
            parameters={"date": trade_date},
            settings={"mutations_sync": 2},
        )
        sequence_expression = """
            multiIf(
                session = 'auction_open',
                    1 + dateDiff('minute', toDateTime64(concat(toString(trade_date), ' 09:15:00'), 3, 'Asia/Shanghai'), scheduled_time),
                session = 'continuous_am',
                    12 + dateDiff('minute', toDateTime64(concat(toString(trade_date), ' 09:30:15'), 3, 'Asia/Shanghai'), scheduled_time),
                session = 'continuous_pm',
                    133 + dateDiff('minute', toDateTime64(concat(toString(trade_date), ' 13:00:15'), 3, 'Asia/Shanghai'), scheduled_time),
                scheduled_time = toDateTime64(concat(toString(trade_date), ' 14:56:55'), 3, 'Asia/Shanghai'), 250,
                scheduled_time = toDateTime64(concat(toString(trade_date), ' 14:57:00'), 3, 'Asia/Shanghai'), 251,
                scheduled_time = toDateTime64(concat(toString(trade_date), ' 14:58:00'), 3, 'Asia/Shanghai'), 252,
                scheduled_time = toDateTime64(concat(toString(trade_date), ' 14:59:00'), 3, 'Asia/Shanghai'), 253,
                scheduled_time = toDateTime64(concat(toString(trade_date), ' 15:00:00'), 3, 'Asia/Shanghai'), 254,
                0
            )
        """
        for table in (RAW, DERIVED, MARKET_STATE):
            self.client.command(
                f"""
                ALTER TABLE {table} UPDATE
                    collection_id = concat(
                        formatDateTime(trade_date, '%Y%m%d'),
                        leftPad(toString({sequence_expression}), 3, '0')
                    )
                WHERE trade_date = {{date:Date}}
                """,
                parameters={"date": trade_date},
                settings={"mutations_sync": 2},
            )

    def _insert_once(
        self, table: str, rows: list[Any], columns: Iterable[str], batch_id: str | None = None
    ) -> tuple[int, int]:
        if not rows:
            return 0, 0
        actual_batch = batch_id or rows[0].batch_id
        existing = self._scalar(
            f"SELECT count() FROM {table} WHERE batch_id = {{batch:String}}",
            {"batch": actual_batch},
        )
        if existing:
            if existing != len(rows):
                raise RuntimeError(
                    f"{table} has partial batch {actual_batch}: {existing}/{len(rows)}"
                )
            return existing, 0
        started = perf_counter()
        values = (
            [tuple(asdict(row).values()) for row in rows]
            if isinstance(rows[0], RawSnapshot)
            else rows
        )
        self.client.insert(table, values, column_names=list(columns))
        return len(rows), round((perf_counter() - started) * 1000)

    def _scalar(self, query: str, parameters: dict[str, Any]) -> int:
        return int(self.client.query(query, parameters=parameters).result_rows[0][0])


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
