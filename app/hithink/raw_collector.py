from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import sleep
from typing import Any

from app.hithink.api import (
    ApiSnapshot,
    HithinkApiError,
    HithinkClient,
    LimitPoolSnapshot,
    SectorIndexSnapshot,
)
from app.hithink.models import RawSnapshot
from app.hithink.schedule import ScheduleNode
from app.hithink.writer import ClickHouseWriter


@dataclass(frozen=True)
class RawCollectionResult:
    """The independently persisted external facts for one collection ID."""

    node: ScheduleNode
    batch_id: str
    prices: ApiSnapshot | None
    limit_up: LimitPoolSnapshot | None
    limit_down: LimitPoolSnapshot | None
    limit_break: LimitPoolSnapshot | None
    sector_index: SectorIndexSnapshot | None
    sectors: list[tuple[str, str, str, str]]
    raw_rows: list[RawSnapshot]
    raw_insert_count: int
    raw_insert_ms: int
    sector_index_insert_count: int
    sector_index_insert_ms: int
    errors: dict[str, tuple[str, str]]
    failures: dict[str, Exception]
    not_applicable: frozenset[str]

    def succeeded(self, task: str) -> bool:
        return task not in self.errors and task not in self.not_applicable

    @property
    def all_succeeded(self) -> bool:
        return not self.errors

    @property
    def raw_status(self) -> str:
        applicable = {
            "all_a_snapshot": self.prices,
            "limit_up_pool": self.limit_up,
            "limit_down_pool": self.limit_down,
            "limit_break_pool": self.limit_break,
            "sector_index": self.sector_index,
        }
        completed = [
            payload
            for task, payload in applicable.items()
            if task not in self.not_applicable
        ]
        succeeded = sum(payload is not None for payload in completed)
        if succeeded == len(completed):
            return "SUCCESS"
        return "PARTIAL" if succeeded else "FAILED"


class RawCollector:
    """Fetch and persist facts. This module never calculates a derived value."""

    def __init__(self, api: HithinkClient, writer: ClickHouseWriter):
        self.api = api
        self.writer = writer

    def collect(self, node: ScheduleNode, batch_id: str) -> RawCollectionResult:
        errors: dict[str, tuple[str, str]] = {}
        failures: dict[str, Exception] = {}
        task_columns = {
            "all_a_snapshot": "all_a_snapshot_status",
            "limit_up_pool": "limit_up_pool_status",
            "limit_down_pool": "limit_down_pool_status",
            "limit_break_pool": "limit_break_pool_status",
            "sector_index": "sector_index_status",
        }
        pool_tasks = frozenset(
            {"limit_up_pool", "limit_down_pool", "limit_break_pool"}
        )
        not_applicable = frozenset() if node.limit_pools_applicable else pool_tasks
        called: set[str] = set()
        finished: set[str] = set()
        sectors: list[tuple[str, str, str, str]] = []
        prices: ApiSnapshot | None = None
        limit_up: LimitPoolSnapshot | None = None
        limit_down: LimitPoolSnapshot | None = None
        limit_break: LimitPoolSnapshot | None = None
        sector_index: SectorIndexSnapshot | None = None
        raw_rows: list[RawSnapshot] = []
        raw_insert_count = 0
        raw_insert_ms = 0
        sector_index_insert_count = 0
        sector_index_insert_ms = 0

        def write_task_status(task: str, status: str) -> None:
            self.writer.set_schedule_status(
                node,
                "RUNNING",
                **{task_columns[task]: status},
            )
            finished.add(task)

        def capture_error(task: str, exc: Exception) -> None:
            failures[task] = exc
            if isinstance(exc, HithinkApiError):
                code = exc.error_code
            else:
                code = str(getattr(exc, "code", type(exc).__name__))
            errors[task] = (code, str(exc))

        def resolve_and_write(
            task: str,
            future: Any,
            write: Callable[[Any], None],
        ) -> Any | None:
            try:
                payload = future.result()
                write(payload)
                write_task_status(task, "SUCCESS")
                return payload
            except Exception as exc:  # noqa: BLE001 - isolate one interface failure.
                capture_error(task, exc)
                write_task_status(task, "FAILED")
                return None

        sector_catalog_error: Exception | None = None
        try:
            sectors = self.writer.active_sector_catalog()
        except Exception as exc:  # noqa: BLE001 - sector lookup must not stop other APIs.
            sector_catalog_error = exc
            capture_error("sector_index", exc)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                called.add("all_a_snapshot")
                prices_future = executor.submit(self.api.fetch)
                sleep(0.3)

                sector_index_future = None
                if sectors:
                    called.add("sector_index")
                    sector_index_future = executor.submit(
                        self.api.fetch_sector_index_snapshot, [sector[0] for sector in sectors]
                    )
                sleep(0.3)

                limit_up_future = None
                limit_down_future = None
                limit_break_future = None
                if node.limit_pools_applicable:
                    called.add("limit_up_pool")
                    limit_up_future = executor.submit(
                        self.api.fetch_limit_pool, "limit-up-pool", node.trade_date
                    )
                    sleep(0.3)
                    called.add("limit_down_pool")
                    limit_down_future = executor.submit(
                        self.api.fetch_limit_pool, "limit-down-pool", node.trade_date
                    )
                    sleep(0.3)
                    called.add("limit_break_pool")
                    limit_break_future = executor.submit(
                        self.api.fetch_limit_pool, "limit-break-pool", node.trade_date
                    )

                def write_prices(snapshot: ApiSnapshot) -> None:
                    nonlocal raw_rows, raw_insert_count, raw_insert_ms
                    raw_rows = [
                        RawSnapshot.from_api(
                            item,
                            trade_date=node.trade_date,
                            collection_id=node.collection_id,
                            scheduled_time=node.scheduled_time,
                            source_timestamp=snapshot.source_timestamp,
                            source_time=snapshot.source_time,
                            session=node.session,
                            batch_id=batch_id,
                        )
                        for item in snapshot.items
                    ]
                    raw_insert_count, raw_insert_ms = self.writer.insert_raw_once(raw_rows)

                prices = resolve_and_write("all_a_snapshot", prices_future, write_prices)
                if sector_index_future is None:
                    if sector_catalog_error is None:
                        errors["sector_index"] = (
                            "EMPTY_SECTOR_CATALOG",
                            "No active concept, industry, or style sectors exist",
                        )
                    write_task_status("sector_index", "FAILED")
                else:
                    def write_sector_index(snapshot: SectorIndexSnapshot) -> None:
                        nonlocal sector_index_insert_count, sector_index_insert_ms
                        sector_index_insert_count, sector_index_insert_ms = (
                            self.writer.insert_sector_index_once(node, sectors, snapshot)
                        )

                    sector_index = resolve_and_write(
                        "sector_index", sector_index_future, write_sector_index
                    )
                if limit_up_future is not None:
                    limit_up = resolve_and_write(
                        "limit_up_pool",
                        limit_up_future,
                        lambda snapshot: self.writer.insert_limit_up_pool(node, batch_id, snapshot),
                    )
                if limit_down_future is not None:
                    limit_down = resolve_and_write(
                        "limit_down_pool",
                        limit_down_future,
                        lambda snapshot: self.writer.insert_limit_down_pool(node, batch_id, snapshot),
                    )
                if limit_break_future is not None:
                    limit_break = resolve_and_write(
                        "limit_break_pool",
                        limit_break_future,
                        lambda snapshot: self.writer.insert_limit_break_pool(node, batch_id, snapshot),
                    )
        finally:
            for task, column in task_columns.items():
                if task in finished:
                    continue
                if task in not_applicable:
                    status = "SKIPPED"
                else:
                    status = "FAILED" if task in called else "SKIPPED"
                self.writer.set_schedule_status(node, "RUNNING", **{column: status})

        return RawCollectionResult(
            node=node,
            batch_id=batch_id,
            prices=prices,
            limit_up=limit_up,
            limit_down=limit_down,
            limit_break=limit_break,
            sector_index=sector_index,
            sectors=sectors,
            raw_rows=raw_rows,
            raw_insert_count=raw_insert_count,
            raw_insert_ms=raw_insert_ms,
            sector_index_insert_count=sector_index_insert_count,
            sector_index_insert_ms=sector_index_insert_ms,
            errors=errors,
            failures=failures,
            not_applicable=not_applicable,
        )
