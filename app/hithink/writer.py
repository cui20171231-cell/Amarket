from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import clickhouse_connect

from app.hithink.api import LimitPoolSnapshot, SectorIndexSnapshot
from app.hithink.models import DerivedSnapshot, RawSnapshot
from app.hithink.schedule import ScheduleNode

SCHEDULE = "market.hithink_snapshot_schedule"
RAW = "market.hithink_snapshot_raw"
DERIVED = "market.hithink_snapshot_derived"
MARKET_STATE = "market.hithink_market_state"
LIMIT_UP_POOL = "market.hithink_limit_up_pool"
LIMIT_DOWN_POOL = "market.hithink_limit_down_pool"
LIMIT_BREAK_POOL = "market.hithink_limit_break_pool"
CALENDAR = "market.trading_calendar"
DAILY_STATUS = "market.hithink_daily_sync_status"
DAILY_TASKS = (
    "calendar_gate",
    "sector_catalog_sync",
    "sector_membership_sync",
    "hithink_daily_k_raw_sync",
    "hithink_adjustment_events_sync",
    "daily_derivation",
)
SECTOR_INDEX = "market.hithink_sector_index_snapshot"
SECTOR_STATES = {
    "concept": "market.hithink_concept_state",
    "industry": "market.hithink_industry_state",
    "style": "market.hithink_style_state",
}


class ClickHouseWriter:
    def __init__(self, host: str, port: int, database: str, username: str, password: str):
        self.client = clickhouse_connect.get_client(
            host=host, port=port, username=username, password=password, database=database
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

    def initialize_daily_task_plan(self, trade_date: object) -> None:
        existing = {
            str(row[0])
            for row in self.client.query(
                f"SELECT task_name FROM {DAILY_STATUS} FINAL WHERE trade_date = {{date:Date}}",
                parameters={"date": trade_date},
            ).result_rows
        }
        missing = [task for task in DAILY_TASKS if task not in existing]
        if missing:
            self.client.insert(
                DAILY_STATUS,
                [(trade_date, task, "PENDING") for task in missing],
                column_names=["trade_date", "task_name", "status"],
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
        # Daily-K initialization is complete once the raw daily-K seed exists.
        # Adjustment events are deliberately refreshed only on Mondays, so they
        # must not force a full daily-K initialization on every weekday before
        # the next Monday arrives.
        return bool(raw)

    def statuses(self, trade_date: object) -> dict[datetime, str]:
        result = self.client.query(
            f"SELECT scheduled_time, status FROM {SCHEDULE} FINAL WHERE trade_date = {{date:Date}}",
            parameters={"date": trade_date},
        )
        return {row[0]: row[1] for row in result.result_rows}

    def set_schedule_status(self, node: ScheduleNode, status: str, **values: Any) -> None:
        """Write the next immutable schedule version without discarding prior fields.

        ``ReplacingMergeTree`` keeps the newest row for a collection node.  A
        partial insert therefore must first carry forward the existing values;
        otherwise a derivation status update would silently reset raw collection
        fields to their column defaults.
        """
        existing = self.client.query(
            f"SELECT * FROM {SCHEDULE} FINAL WHERE collection_id = {{collection_id:String}}",
            parameters={"collection_id": node.collection_id},
        )
        if existing.result_rows:
            row_data = dict(zip(existing.column_names, existing.result_rows[0]))
            row_data.pop("updated_at", None)
        else:
            row_data = {
                "trade_date": node.trade_date,
                "collection_id": node.collection_id,
                "scheduled_time": node.scheduled_time,
                "session": node.session,
                "sequence_no": node.sequence_no,
            }
        row_data["status"] = status
        row_data.update(values)
        self.client.insert(SCHEDULE, [list(row_data.values())], column_names=list(row_data))

    def repair_stale_running(
        self,
        trade_date: object,
        cutoff: datetime,
        active_collection_id: str | None,
    ) -> int:
        """Close overdue RUNNING rows when no live collector node owns them."""
        columns = (
            "collection_id",
            "scheduled_time",
            "session",
            "sequence_no",
            "status",
            "raw_status",
            "derivation_status",
            "all_a_snapshot_status",
            "limit_up_pool_status",
            "limit_down_pool_status",
            "limit_break_pool_status",
            "sector_index_status",
        )
        result = self.client.query(
            f"""
            SELECT {', '.join(columns)}
            FROM {SCHEDULE} FINAL
            WHERE trade_date = {{trade_date:Date}}
              AND scheduled_time < {{cutoff:DateTime64(3, 'Asia/Shanghai')}}
              AND (
                    status = 'RUNNING'
                 OR raw_status = 'RUNNING'
                 OR derivation_status = 'RUNNING'
                 OR all_a_snapshot_status = 'RUNNING'
                 OR limit_up_pool_status = 'RUNNING'
                 OR limit_down_pool_status = 'RUNNING'
                 OR limit_break_pool_status = 'RUNNING'
                 OR sector_index_status = 'RUNNING'
              )
            """,
            parameters={"trade_date": trade_date, "cutoff": cutoff},
        )
        repaired = 0
        state_columns = columns[5:]
        for values in result.result_rows:
            row = dict(zip(columns, values))
            collection_id = str(row["collection_id"]).rstrip("\x00")
            if collection_id == active_collection_id:
                continue
            node = ScheduleNode(
                trade_date=trade_date,
                scheduled_time=row["scheduled_time"],
                session=str(row["session"]),
                sequence_no=int(row["sequence_no"]),
            )
            updates = {
                column: "FAILED_TIMEOUT" if row[column] == "RUNNING" else row[column]
                for column in state_columns
            }
            self.set_schedule_status(
                node,
                "FAILED_TIMEOUT",
                **updates,
                request_end_time=datetime.now(cutoff.tzinfo),
                error_code="FAILED_TIMEOUT",
                error_message=(
                    "scheduled_time passed by more than 3 minutes without an active collector node"
                ),
            )
            repaired += 1
        return repaired

    def previous_derived(self, node: ScheduleNode) -> dict[str, DerivedSnapshot]:
        return self._derived_rows_for_collection(node.collection_id)

    def raw_rows_for_collection(self, collection_id: str) -> list[RawSnapshot]:
        """Read the persisted raw fact rows for one immutable collection ID."""
        columns = list(RawSnapshot.__dataclass_fields__)
        result = self.client.query(
            f"SELECT {', '.join(columns)} FROM {RAW} WHERE collection_id = {{collection_id:String}}",
            parameters={"collection_id": collection_id},
        )
        rows: list[RawSnapshot] = []
        for values in result.result_rows:
            row = dict(zip(columns, values))
            for field in ("collection_id", "session", "batch_id", "thscode", "ticker"):
                if isinstance(row[field], bytes):
                    row[field] = row[field].decode("ascii").rstrip("\x00")
            rows.append(RawSnapshot(**row))
        return rows

    def latest_derived_before(self, node: ScheduleNode) -> dict[str, DerivedSnapshot]:
        """Return the last successful snapshot before this node on the same day."""
        result = self.client.query(
            f"""
            SELECT collection_id
            FROM {SCHEDULE} FINAL
            WHERE trade_date = {{trade_date:Date}}
              AND sequence_no < {{sequence_no:UInt16}}
              AND derivation_status = 'SUCCESS'
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            parameters={
                "trade_date": node.trade_date,
                "sequence_no": node.sequence_no,
            },
        ).result_rows
        if not result:
            return {}
        collection_id = result[0][0]
        if isinstance(collection_id, bytes):
            collection_id = collection_id.decode("ascii")
        return self._derived_rows_for_collection(str(collection_id))

    def derived_for_successful_sequence(
        self, node: ScheduleNode, sequence_no: int
    ) -> dict[str, DerivedSnapshot]:
        """Return one exact earlier successful node, or no rows if it failed."""
        result = self.client.query(
            f"""
            SELECT collection_id
            FROM {SCHEDULE} FINAL
            WHERE trade_date = {{trade_date:Date}}
              AND sequence_no = {{sequence_no:UInt16}}
              AND derivation_status = 'SUCCESS'
            LIMIT 1
            """,
            parameters={"trade_date": node.trade_date, "sequence_no": sequence_no},
        ).result_rows
        if not result:
            return {}
        collection_id = result[0][0]
        if isinstance(collection_id, bytes):
            collection_id = collection_id.decode("ascii")
        return self._derived_rows_for_collection(str(collection_id))

    def _derived_rows_for_collection(self, collection_id: str) -> dict[str, DerivedSnapshot]:
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
            "prev_trade_day_same_time_turnover",
            "turnover_prev_trade_day_delta",
            "turnover_prev_trade_day_pct",
            "is_limit_up",
            "is_limit_down",
            "is_limit_break",
            "limit_break_open_times",
        ]
        result = self.client.query(
            f"SELECT {', '.join(columns)} FROM {DERIVED} WHERE collection_id = {{collection_id:String}}",
            parameters={"collection_id": collection_id},
        )
        found: dict[str, DerivedSnapshot] = {}
        raw_columns = list(RawSnapshot.__dataclass_fields__)
        for values in result.result_rows:
            row = dict(zip(columns, values))
            for field in ("collection_id", "session", "batch_id", "thscode", "ticker"):
                if isinstance(row[field], bytes):
                    row[field] = row[field].decode("ascii").rstrip("\x00")
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

    def previous_trade_day_turnovers(self, node: ScheduleNode) -> dict[str, int]:
        """Latest earlier trading-day turnover for this stock and planned sequence.

        A suspended stock simply has no row on that date.  When it later returns,
        argMax selects its last earlier trading day that did have this same sequence.
        """
        result = self.client.query(
            f"""
            SELECT raw.thscode, argMax(raw.turnover, schedule.trade_date) AS turnover
            FROM {RAW} AS raw
            INNER JOIN
            (
                SELECT collection_id, trade_date
                FROM {SCHEDULE} FINAL
                WHERE sequence_no = {{sequence_no:UInt16}}
                  AND trade_date < {{trade_date:Date}}
                  AND derivation_status = 'SUCCESS'
            ) AS schedule ON raw.collection_id = schedule.collection_id
            INNER JOIN
            (
                SELECT trade_date AS calendar_trade_date
                FROM {CALENDAR} FINAL
                WHERE is_trading_day = 1
            ) AS calendar ON schedule.trade_date = calendar.calendar_trade_date
            WHERE raw.turnover IS NOT NULL
            GROUP BY raw.thscode
            """,
            parameters={"sequence_no": node.sequence_no, "trade_date": node.trade_date},
        )
        return {str(thscode): int(turnover) for thscode, turnover in result.result_rows}

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
                row.prev_trade_day_same_time_turnover,
                row.turnover_prev_trade_day_delta,
                row.turnover_prev_trade_day_pct,
                row.is_limit_up,
                row.is_limit_down,
                row.is_limit_break,
                row.limit_break_open_times,
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

    def insert_limit_break_pool(
        self, node: ScheduleNode, batch_id: str, snapshot: LimitPoolSnapshot
    ) -> tuple[int, int]:
        pool_batch = f"{batch_id}-limit-break"
        rows = [
            (
                node.trade_date, node.collection_id, node.scheduled_time, pool_batch,
                record.source_timestamp, record.source_time, str(record.item["thscode"]),
                str(record.item["ticker"]), str(record.item.get("name") or ""),
                _optional_float(record.item.get("last_price")),
                _optional_float(record.item.get("price_change_ratio_pct")),
                _optional_int(record.item.get("open_times")),
                _optional_float(record.item.get("turnover_ratio_pct")),
                _optional_float(record.item.get("turnover")),
            )
            for record in snapshot.items
        ]
        columns = [
            "trade_date", "collection_id", "scheduled_time", "batch_id",
            "source_timestamp", "source_time", "thscode", "ticker", "name",
            "last_price", "price_change_ratio_pct", "open_times",
            "turnover_ratio_pct", "turnover",
        ]
        return self._insert_once(LIMIT_BREAK_POOL, rows, columns, batch_id=pool_batch)

    def active_sector_catalog(self) -> list[tuple[str, str, str, str]]:
        result = self.client.query(
            """
            SELECT sector_code, sector_name, sector_type, source_tag
            FROM market.sector_catalog FINAL
            WHERE is_active = 1 AND sector_type IN ('concept', 'industry', 'style')
            ORDER BY sector_type, sector_code
            """
        )
        return [(str(code), str(name), str(kind), str(tag)) for code, name, kind, tag in result.result_rows]

    def sector_index_fact_count(self, node: ScheduleNode) -> int:
        return int(
            self._scalar(
                f"SELECT count() FROM {SECTOR_INDEX} WHERE collection_id = {{collection_id:String}}",
                {"collection_id": node.collection_id},
            )
            or 0
        )

    def insert_sector_index_once(
        self,
        node: ScheduleNode,
        sectors: list[tuple[str, str, str, str]],
        snapshot: SectorIndexSnapshot,
    ) -> tuple[int, int]:
        records = {str(record.item["thscode"]): record for record in snapshot.items}
        if set(records) != {sector[0] for sector in sectors}:
            raise RuntimeError("Sector index records do not match active sector catalog")
        rows = []
        for sector_code, sector_name, sector_type, source_tag in sectors:
            record = records[sector_code]
            item = record.item
            rows.append(
                (
                    node.trade_date,
                    node.collection_id,
                    node.scheduled_time,
                    record.source_timestamp,
                    record.source_time,
                    node.session,
                    f"{node.collection_id}-sector-index",
                    sector_code,
                    sector_name,
                    sector_type,
                    source_tag,
                    _optional_float(item.get("last_price")),
                    _optional_float(item.get("price_change")),
                    _optional_float(item.get("price_change_ratio_pct")),
                    _optional_float(item.get("open_price")),
                    _optional_float(item.get("high_price")),
                    _optional_float(item.get("low_price")),
                    _optional_float(item.get("prev_price")),
                    _optional_int(item.get("volume")),
                    _optional_float(item.get("turnover")),
                )
            )
        columns = [
            "trade_date", "collection_id", "scheduled_time", "source_timestamp", "source_time",
            "session", "batch_id", "sector_code", "sector_name", "sector_type", "source_tag",
            "last_price", "price_change", "price_change_ratio_pct", "open_price", "high_price",
            "low_price", "prev_price", "volume", "turnover",
        ]
        return self._insert_once(
            SECTOR_INDEX, rows, columns, batch_id=f"{node.collection_id}-sector-index"
        )

    def limit_pool_collected(self, node: ScheduleNode) -> bool:
        return bool(
            self._scalar(
                f"SELECT count() FROM {SCHEDULE} FINAL WHERE collection_id = {{collection_id:String}} AND limit_pool_collected = 1",
                {"collection_id": node.collection_id},
            )
        )

    def limit_break_details(self, node: ScheduleNode) -> dict[str, int | None] | None:
        """None means the break-pool source was not captured for this collection."""
        status = self.client.query(
            f"SELECT limit_break_pool_status FROM {SCHEDULE} FINAL WHERE collection_id = {{collection_id:String}}",
            parameters={"collection_id": node.collection_id},
        ).result_rows
        if not status or status[0][0] != "SUCCESS":
            return None
        result = self.client.query(
            f"SELECT thscode, open_times FROM {LIMIT_BREAK_POOL} FINAL WHERE collection_id = {{collection_id:String}}",
            parameters={"collection_id": node.collection_id},
        )
        return {str(code): value for code, value in result.result_rows}

    def limit_pool_codes(self, node: ScheduleNode, pool_name: str) -> set[str] | None:
        """Stock codes for one successfully captured limit pool at this collection ID."""
        pools = {
            "up": ("limit_up_pool_status", LIMIT_UP_POOL),
            "down": ("limit_down_pool_status", LIMIT_DOWN_POOL),
        }
        try:
            status_column, table = pools[pool_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported limit pool: {pool_name}") from exc
        status = self.client.query(
            f"SELECT {status_column} FROM {SCHEDULE} FINAL WHERE collection_id = {{collection_id:String}}",
            parameters={"collection_id": node.collection_id},
        ).result_rows
        if not status or status[0][0] != "SUCCESS":
            return None
        result = self.client.query(
            f"SELECT DISTINCT thscode FROM {table} FINAL WHERE collection_id = {{collection_id:String}}",
            parameters={"collection_id": node.collection_id},
        )
        return {str(row[0]) for row in result.result_rows}

    def latest_limit_break_collection_before(self, node: ScheduleNode) -> str | None:
        """The immediately preceding successfully captured break-pool snapshot."""
        result = self.client.query(
            f"""
            SELECT collection_id
            FROM {SCHEDULE} FINAL
            WHERE trade_date = {{trade_date:Date}}
              AND sequence_no < {{sequence_no:UInt16}}
              AND limit_break_pool_status = 'SUCCESS'
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            parameters={"trade_date": node.trade_date, "sequence_no": node.sequence_no},
        )
        if not result.result_rows:
            return None
        value = result.result_rows[0][0]
        return value.decode("ascii") if isinstance(value, bytes) else str(value)

    def upsert_sector_state(
        self,
        node: ScheduleNode,
        sector_type: str,
        limit_pool_available: bool,
        derive_enabled: bool = True,
        previous_collection_id: str | None = None,
        previous_limit_break_collection_id: str | None = None,
        allow_gap_comparison: bool = False,
        previous_trade_day_enabled: bool | None = None,
    ) -> None:
        try:
            state_table = SECTOR_STATES[sector_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported sector type: {sector_type}") from exc
        self.client.command(
            f"""
            INSERT INTO {state_table}
            (
                trade_date, collection_id, scheduled_time, stock_source_time, index_source_time,
                session, sector_code, sector_name, member_total, valid_member_count,
                valid_member_ratio, index_last_price, index_change_ratio_pct, index_change_1m_pct,
                up_count, down_count, flat_count, up_ratio, down_ratio,
                limit_up_count, up_5_to_limit_count, up_1_to_5_count, up_0_to_1_count,
                down_0_to_1_count, down_1_to_5_count, down_5_to_limit_count, limit_down_count,
                limit_break_count, limit_break_count_delta_prev_available,
                turnover_total, turnover_delta_1m_total, prev_turnover_delta_1m_total,
                turnover_growth_1m, volume_total, volume_delta_1m_total,
                turnover_market_share_pct, turnover_1m_market_share_pct,
                prev_day_same_time_turnover, turnover_vs_prev_day_delta, turnover_vs_prev_day_pct,
                turnover_accel_count, turnover_decel_count, turnover_accel_50_count,
                turnover_accel_100_count, volume_expand_count, volume_contract_count,
                volume_ratio_1_5_count, volume_ratio_2_count, volume_ratio_3_count,
                new_high_count, new_low_count, new_high_ratio, new_low_ratio,
                price_up_1m_count, price_down_1m_count, price_flat_1m_count,
                volume_price_up_count, volume_price_down_count, contract_price_up_count,
                contract_price_down_count, turnover_1m_top1_share_pct,
                turnover_1m_top3_share_pct, turnover_1m_top5_share_pct,
                up_count_delta_1m, down_count_delta_1m, new_high_count_delta_1m,
                new_low_count_delta_1m, volume_price_up_delta_1m,
                volume_price_down_delta_1m, turnover_market_share_delta_1m,
                turnover_1m_market_share_delta_1m
            )
            WITH
                previous AS
                (
                    SELECT * REPLACE
                    (
                        if({{allow_gap_comparison:UInt8}} = 1,
                            {{time:DateTime64(3, 'Asia/Shanghai')}} - INTERVAL 60 SECOND,
                            scheduled_time
                        ) AS scheduled_time,
                        {{session:String}} AS session
                    ) FROM {state_table} FINAL
                    WHERE collection_id = {{previous_collection_id:String}}
                ),
                previous_trade_day AS
                (
                    SELECT
                        sector_code,
                        CAST(turnover_total, 'Nullable(Float64)') AS turnover_total
                    FROM {state_table} FINAL
                    WHERE collection_id = concat(
                        formatDateTime(
                            (
                                SELECT max(trade_date)
                                FROM {CALENDAR} FINAL
                                WHERE trade_date < {{date:Date}} AND is_trading_day = 1
                            ),
                            '%Y%m%d'
                        ),
                        leftPad(toString({{previous_day_sequence_no:UInt16}}), 3, '0')
                    )
                ),
                previous_limit_break AS
                (
                    SELECT sector_code, limit_break_count
                    FROM {state_table} FINAL
                    WHERE collection_id = {{previous_limit_break_collection_id:String}}
                ),
                member_rows AS
                (
                    SELECT
                        catalog.sector_code AS sector_code,
                        catalog.sector_name AS sector_name,
                        membership.thscode AS member_thscode,
                        derived.thscode AS snapshot_thscode,
                        derived.source_time AS source_time,
                        derived.price_change_ratio_pct AS price_change_ratio_pct,
                        derived.turnover AS turnover,
                        derived.volume AS volume,
                        derived.turnover_delta_1m AS turnover_delta_1m,
                        derived.volume_delta_1m AS volume_delta_1m,
                        derived.turnover_growth_1m AS turnover_growth_1m,
                        derived.volume_ratio_1m AS volume_ratio_1m,
                        derived.new_high_flag AS new_high_flag,
                        derived.new_low_flag AS new_low_flag,
                        derived.price_delta_1m AS price_delta_1m
                    FROM
                    (
                        SELECT * FROM market.sector_catalog FINAL
                    ) AS catalog
                    LEFT JOIN
                    (
                        SELECT * FROM market.sector_membership_history FINAL
                    ) AS membership
                        ON membership.sector_type = catalog.sector_type
                        AND membership.sector_code = catalog.sector_code
                        AND membership.observed_from <= {{date:Date}}
                        AND (membership.observed_to > {{date:Date}} OR membership.observed_to IS NULL)
                    LEFT JOIN
                    (
                        SELECT * FROM {DERIVED}
                        WHERE collection_id = {{collection_id:String}}
                    ) AS derived
                        ON derived.collection_id = {{collection_id:String}}
                        AND derived.thscode = membership.thscode
                    WHERE catalog.sector_type = {{sector_type:String}}
                      AND catalog.is_active = 1
                ),
                sector_current AS
                (
                    SELECT
                        sector_code,
                        any(sector_name) AS sector_name,
                        nullIf(
                            max(source_time),
                            toDateTime64(0, 3, 'Asia/Shanghai')
                        ) AS stock_source_time,
                        toUInt32(countIf(member_thscode != '')) AS member_total,
                        toUInt32(countIf(price_change_ratio_pct IS NOT NULL)) AS valid_member_count,
                        toUInt32(countIf(price_change_ratio_pct > 0)) AS up_count,
                        toUInt32(countIf(price_change_ratio_pct < 0)) AS down_count,
                        toUInt32(countIf(price_change_ratio_pct = 0)) AS flat_count,
                        if({{pool_available:UInt8}} = 1,
                            toUInt32(countIf(snapshot_thscode IN (SELECT DISTINCT thscode FROM {LIMIT_UP_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                            CAST(NULL, 'Nullable(UInt32)')) AS limit_up_count,
                        if({{pool_available:UInt8}} = 1,
                            toUInt32(countIf(price_change_ratio_pct >= 5 AND snapshot_thscode NOT IN (SELECT DISTINCT thscode FROM {LIMIT_UP_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                            CAST(NULL, 'Nullable(UInt32)')) AS up_5_to_limit_count,
                        toUInt32(countIf(price_change_ratio_pct >= 1 AND price_change_ratio_pct < 5)) AS up_1_to_5_count,
                        toUInt32(countIf(price_change_ratio_pct > 0 AND price_change_ratio_pct < 1)) AS up_0_to_1_count,
                        toUInt32(countIf(price_change_ratio_pct < 0 AND price_change_ratio_pct > -1)) AS down_0_to_1_count,
                        toUInt32(countIf(price_change_ratio_pct <= -1 AND price_change_ratio_pct > -5)) AS down_1_to_5_count,
                        if({{pool_available:UInt8}} = 1,
                            toUInt32(countIf(price_change_ratio_pct <= -5 AND snapshot_thscode NOT IN (SELECT DISTINCT thscode FROM {LIMIT_DOWN_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                            CAST(NULL, 'Nullable(UInt32)')) AS down_5_to_limit_count,
                        if({{pool_available:UInt8}} = 1,
                            toUInt32(countIf(snapshot_thscode IN (SELECT DISTINCT thscode FROM {LIMIT_DOWN_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                            CAST(NULL, 'Nullable(UInt32)')) AS limit_down_count,
                        if({{pool_available:UInt8}} = 1,
                            toUInt32(countIf(snapshot_thscode IN (SELECT DISTINCT thscode FROM {LIMIT_BREAK_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                            CAST(NULL, 'Nullable(UInt32)')) AS limit_break_count,
                        toFloat64(sum(turnover)) AS turnover_total,
                        if(countIf(turnover_delta_1m IS NOT NULL) = 0,
                            CAST(NULL, 'Nullable(Float64)'), toFloat64(sum(turnover_delta_1m))) AS turnover_delta_1m_total,
                        toUInt64(sum(volume)) AS volume_total,
                        if(countIf(volume_delta_1m IS NOT NULL) = 0,
                            CAST(NULL, 'Nullable(Int64)'), toInt64(sum(volume_delta_1m))) AS volume_delta_1m_total,
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
                        if(countIf(volume_ratio_1m IS NOT NULL AND price_delta_1m IS NOT NULL) = 0, CAST(NULL, 'Nullable(UInt32)'), toUInt32(countIf(volume_ratio_1m < 1 AND price_delta_1m < 0))) AS contract_price_down_count,
                        groupArrayIf(toFloat64(turnover_delta_1m), turnover_delta_1m IS NOT NULL) AS minute_turnovers
                    FROM member_rows
                    GROUP BY sector_code
                ),
                index_current AS
                (
                    SELECT sector_code, source_time, last_price, price_change_ratio_pct
                    FROM {SECTOR_INDEX} FINAL
                    WHERE collection_id = {{collection_id:String}}
                      AND sector_type = {{sector_type:String}}
                ),
                stock_snapshot AS
                (
                    SELECT max(source_time) AS source_time
                    FROM {DERIVED} FINAL
                    WHERE collection_id = {{collection_id:String}}
                ),
                market AS
                (
                    SELECT turnover_total, turnover_delta_1m_total
                    FROM {MARKET_STATE} FINAL
                    WHERE collection_id = {{collection_id:String}}
                )
            SELECT
                {{date:Date}},
                {{collection_id:String}},
                {{time:DateTime64(3, 'Asia/Shanghai')}},
                coalesce(current.stock_source_time, stock_snapshot.source_time),
                index_current.source_time,
                {{session:String}},
                current.sector_code,
                current.sector_name,
                current.member_total,
                current.valid_member_count,
                if(current.member_total > 0, current.valid_member_count / current.member_total, 0.0),
                index_current.last_price,
                index_current.price_change_ratio_pct,
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60
                    AND previous.index_last_price > 0 AND index_current.last_price IS NOT NULL,
                    (index_current.last_price / previous.index_last_price - 1) * 100,
                    CAST(NULL, 'Nullable(Float64)')),
                current.up_count,
                current.down_count,
                current.flat_count,
                if(current.valid_member_count > 0, current.up_count / current.valid_member_count, 0.0),
                if(current.valid_member_count > 0, current.down_count / current.valid_member_count, 0.0),
                current.limit_up_count,
                current.up_5_to_limit_count,
                current.up_1_to_5_count,
                current.up_0_to_1_count,
                current.down_0_to_1_count,
                current.down_1_to_5_count,
                current.down_5_to_limit_count,
                current.limit_down_count,
                current.limit_break_count,
                if(previous_limit_break.limit_break_count IS NOT NULL AND current.limit_break_count IS NOT NULL,
                    CAST(toInt64(current.limit_break_count) - toInt64(previous_limit_break.limit_break_count), 'Nullable(Int32)'),
                    CAST(NULL, 'Nullable(Int32)')),
                current.turnover_total,
                current.turnover_delta_1m_total,
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60,
                    previous.turnover_delta_1m_total, CAST(NULL, 'Nullable(Float64)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60
                    AND current.turnover_delta_1m_total IS NOT NULL AND previous.turnover_delta_1m_total > 0,
                    current.turnover_delta_1m_total / previous.turnover_delta_1m_total - 1,
                    CAST(NULL, 'Nullable(Float64)')),
                current.volume_total,
                current.volume_delta_1m_total,
                if(market.turnover_total > 0, current.turnover_total / toFloat64(market.turnover_total) * 100, CAST(NULL, 'Nullable(Float64)')),
                if(market.turnover_delta_1m_total > 0 AND current.turnover_delta_1m_total IS NOT NULL,
                    current.turnover_delta_1m_total / toFloat64(market.turnover_delta_1m_total) * 100, CAST(NULL, 'Nullable(Float64)')),
                previous_trade_day.turnover_total,
                if(previous_trade_day.turnover_total IS NOT NULL, current.turnover_total - previous_trade_day.turnover_total, CAST(NULL, 'Nullable(Float64)')),
                if(previous_trade_day.turnover_total > 0, (current.turnover_total - previous_trade_day.turnover_total) / previous_trade_day.turnover_total * 100, CAST(NULL, 'Nullable(Float64)')),
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
                if(current.valid_member_count > 0 AND current.new_high_count IS NOT NULL, current.new_high_count / current.valid_member_count, CAST(NULL, 'Nullable(Float64)')),
                if(current.valid_member_count > 0 AND current.new_low_count IS NOT NULL, current.new_low_count / current.valid_member_count, CAST(NULL, 'Nullable(Float64)')),
                current.price_up_1m_count,
                current.price_down_1m_count,
                current.price_flat_1m_count,
                current.volume_price_up_count,
                current.volume_price_down_count,
                current.contract_price_up_count,
                current.contract_price_down_count,
                if(length(current.minute_turnovers) > 0 AND arraySum(current.minute_turnovers) > 0,
                    arrayElement(arrayReverseSort(current.minute_turnovers), 1) / arraySum(current.minute_turnovers) * 100, CAST(NULL, 'Nullable(Float64)')),
                if(length(current.minute_turnovers) > 0 AND arraySum(current.minute_turnovers) > 0,
                    arraySum(arraySlice(arrayReverseSort(current.minute_turnovers), 1, 3)) / arraySum(current.minute_turnovers) * 100, CAST(NULL, 'Nullable(Float64)')),
                if(length(current.minute_turnovers) > 0 AND arraySum(current.minute_turnovers) > 0,
                    arraySum(arraySlice(arrayReverseSort(current.minute_turnovers), 1, 5)) / arraySum(current.minute_turnovers) * 100, CAST(NULL, 'Nullable(Float64)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60,
                    CAST(toInt64(current.up_count) - toInt64(previous.up_count), 'Nullable(Int32)'), CAST(NULL, 'Nullable(Int32)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60,
                    CAST(toInt64(current.down_count) - toInt64(previous.down_count), 'Nullable(Int32)'), CAST(NULL, 'Nullable(Int32)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60
                    AND current.new_high_count IS NOT NULL AND previous.new_high_count IS NOT NULL,
                    CAST(toInt64(current.new_high_count) - toInt64(previous.new_high_count), 'Nullable(Int32)'), CAST(NULL, 'Nullable(Int32)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60
                    AND current.new_low_count IS NOT NULL AND previous.new_low_count IS NOT NULL,
                    CAST(toInt64(current.new_low_count) - toInt64(previous.new_low_count), 'Nullable(Int32)'), CAST(NULL, 'Nullable(Int32)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60
                    AND current.volume_price_up_count IS NOT NULL AND previous.volume_price_up_count IS NOT NULL,
                    CAST(toInt64(current.volume_price_up_count) - toInt64(previous.volume_price_up_count), 'Nullable(Int32)'), CAST(NULL, 'Nullable(Int32)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60
                    AND current.volume_price_down_count IS NOT NULL AND previous.volume_price_down_count IS NOT NULL,
                    CAST(toInt64(current.volume_price_down_count) - toInt64(previous.volume_price_down_count), 'Nullable(Int32)'), CAST(NULL, 'Nullable(Int32)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60
                    AND previous.turnover_market_share_pct IS NOT NULL AND market.turnover_total > 0,
                    current.turnover_total / toFloat64(market.turnover_total) * 100 - previous.turnover_market_share_pct,
                    CAST(NULL, 'Nullable(Float64)')),
                if(previous.scheduled_time IS NOT NULL AND previous.session = {{session:String}}
                    AND dateDiff('second', previous.scheduled_time, {{time:DateTime64(3, 'Asia/Shanghai')}}) = 60
                    AND previous.turnover_1m_market_share_pct IS NOT NULL
                    AND market.turnover_delta_1m_total > 0 AND current.turnover_delta_1m_total IS NOT NULL,
                    current.turnover_delta_1m_total / toFloat64(market.turnover_delta_1m_total) * 100 - previous.turnover_1m_market_share_pct,
                    CAST(NULL, 'Nullable(Float64)'))
            FROM sector_current AS current
            LEFT JOIN index_current USING (sector_code)
            LEFT JOIN previous USING (sector_code)
            LEFT JOIN previous_limit_break USING (sector_code)
            LEFT JOIN previous_trade_day USING (sector_code)
            CROSS JOIN stock_snapshot
            CROSS JOIN market
            """,
            parameters={
                "date": node.trade_date,
                "collection_id": node.collection_id,
                "time": node.scheduled_time,
                "session": node.session,
                "sequence_no": node.sequence_no,
                "previous_collection_id": (
                    previous_collection_id
                    or f"{node.trade_date:%Y%m%d}{node.sequence_no - 1:03d}"
                    if derive_enabled and node.sequence_no > 1
                    else ""
                ),
                "previous_limit_break_collection_id": previous_limit_break_collection_id or "",
                "previous_day_sequence_no": node.sequence_no
                if (node.sequence_no >= 12 if previous_trade_day_enabled is None else previous_trade_day_enabled)
                else 0,
                "allow_gap_comparison": int(allow_gap_comparison and derive_enabled),
                "sector_type": sector_type,
                "pool_available": int(limit_pool_available),
            },
        )

    def sector_state_count(self, node: ScheduleNode, sector_type: str) -> int:
        return self._scalar(
            f"SELECT count() FROM {SECTOR_STATES[sector_type]} FINAL WHERE collection_id = {{collection_id:String}}",
            {"collection_id": node.collection_id},
        )

    def successful_schedule_node(self, trade_date: object, sequence_no: int) -> ScheduleNode | None:
        result = self.client.query(
            f"""
            SELECT scheduled_time, session, sequence_no
            FROM {SCHEDULE} FINAL
            WHERE trade_date = {{date:Date}} AND sequence_no = {{sequence_no:UInt16}}
              AND derivation_status = 'SUCCESS'
            """,
            parameters={"date": trade_date, "sequence_no": sequence_no},
        ).result_rows
        if not result:
            return None
        scheduled_time, session, found_sequence = result[0]
        return ScheduleNode(trade_date, scheduled_time, session, found_sequence)

    def sector_pipeline_succeeded(self, node: ScheduleNode) -> bool:
        return bool(
            self._scalar(
                f"SELECT count() FROM {SCHEDULE} FINAL WHERE collection_id = {{collection_id:String}} AND sector_state_status = 'SUCCESS'",
                {"collection_id": node.collection_id},
            )
        )

    def upsert_market_state(
        self,
        node: ScheduleNode,
        limit_pool_available: bool,
        derive_enabled: bool = True,
        previous_collection_id: str | None = None,
        previous_limit_break_collection_id: str | None = None,
        allow_gap_comparison: bool = False,
        previous_trade_day_enabled: bool | None = None,
    ) -> None:
        """Aggregate one real snapshot node; absent snapshot nodes are never synthesized."""
        self.client.command(
            f"""
            INSERT INTO {MARKET_STATE}
            (
                trade_date, collection_id, scheduled_time, source_time, session,
                stock_total, valid_stock_count, up_count, down_count, flat_count, up_ratio, down_ratio,
                limit_up_count, up_5_to_limit_count, up_1_to_5_count, up_0_to_1_count,
                down_0_to_1_count, down_1_to_5_count, down_5_to_limit_count, limit_down_count,
                limit_break_count, limit_break_count_delta_prev_available,
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
                    SELECT * REPLACE
                    (
                        if({{allow_gap_comparison:UInt8}} = 1,
                            {{time:DateTime64(3, 'Asia/Shanghai')}} - INTERVAL 60 SECOND,
                            scheduled_time
                        ) AS scheduled_time,
                        {{session:String}} AS session
                    ) FROM {MARKET_STATE} FINAL
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
                ),
                previous_limit_break AS
                (
                    SELECT limit_break_count
                    FROM {MARKET_STATE} FINAL
                    WHERE collection_id = {{previous_limit_break_collection_id:String}}
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
                current.limit_break_count,
                if(
                    previous_limit_break.limit_break_count IS NOT NULL
                    AND current.limit_break_count IS NOT NULL,
                    CAST(
                        toInt64(current.limit_break_count)
                        - toInt64(previous_limit_break.limit_break_count),
                        'Nullable(Int32)'
                    ),
                    CAST(NULL, 'Nullable(Int32)')
                ),
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
                    if({{pool_available:UInt8}} = 1,
                        toUInt32(countIf(thscode IN (SELECT DISTINCT thscode FROM {LIMIT_BREAK_POOL} FINAL WHERE collection_id = {{collection_id:String}}))),
                        CAST(NULL, 'Nullable(UInt32)')) AS limit_break_count,
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
            LEFT JOIN previous_limit_break ON 1 = 1
            """,
            parameters={
                "date": node.trade_date,
                "time": node.scheduled_time,
                "collection_id": node.collection_id,
                "session": node.session,
                "previous_collection_id": (
                    previous_collection_id
                    or f"{node.trade_date:%Y%m%d}{node.sequence_no - 1:03d}"
                    if derive_enabled and node.sequence_no > 1
                    else ""
                ),
                "previous_limit_break_collection_id": previous_limit_break_collection_id or "",
                "sequence_no": node.sequence_no
                if (node.sequence_no >= 12 if previous_trade_day_enabled is None else previous_trade_day_enabled)
                else 0,
                "allow_gap_comparison": int(allow_gap_comparison and derive_enabled),
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

    def raw_snapshot_nodes(self, trade_date: object) -> list[ScheduleNode]:
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
                FROM {RAW}
                WHERE trade_date = {{date:Date}}
            ) AS r USING (collection_id)
            ORDER BY s.sequence_no
            """,
            parameters={"date": trade_date},
        )
        return [
            ScheduleNode(trade_date, scheduled_time, session, sequence_no)
            for scheduled_time, session, sequence_no in result.result_rows
        ]

    def clear_rebuildable_derived_outputs(self, trade_date: object) -> None:
        for table in (DERIVED, MARKET_STATE, *SECTOR_STATES.values()):
            self.client.command(
                f"ALTER TABLE {table} DELETE WHERE trade_date = {{date:Date}} SETTINGS mutations_sync = 2",
                parameters={"date": trade_date},
            )

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
