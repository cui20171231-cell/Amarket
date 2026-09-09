from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from random import uniform
from time import perf_counter, sleep
from typing import Any

from app.hithink.api import (
    RATE_LIMIT_BUDGET_SECONDS,
    ApiSnapshot,
    HithinkApiError,
    HithinkClient,
    LimitPoolSnapshot,
    MarketIndexSnapshot,
    SectorIndexSnapshot,
)
from app.hithink.models import RawSnapshot
from app.hithink.schedule import ScheduleNode
from app.hithink.writer import ClickHouseWriter

CLOSING_POOL_RETRY_SECONDS = 60
NODE_COLLECTION_BUDGET_SECONDS = 45
SUBMISSION_PAUSE_MIN_SECONDS = 0.5
SUBMISSION_PAUSE_MAX_SECONDS = 1.5
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawCollectionResult:
    """The independently persisted external facts for one collection ID."""

    node: ScheduleNode
    batch_id: str
    prices: ApiSnapshot | None
    limit_up: LimitPoolSnapshot | None
    limit_down: LimitPoolSnapshot | None
    limit_break: LimitPoolSnapshot | None
    market_index: MarketIndexSnapshot | None
    sector_index: SectorIndexSnapshot | None
    sectors: list[tuple[str, str, str, str]]
    raw_rows: list[RawSnapshot]
    raw_insert_count: int
    raw_insert_ms: int
    market_index_insert_count: int
    market_index_insert_ms: int
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
        collection_started = perf_counter()
        deadline = collection_started + NODE_COLLECTION_BUDGET_SECONDS
        rate_limit_deadline = collection_started + RATE_LIMIT_BUDGET_SECONDS
        errors: dict[str, tuple[str, str]] = {}
        failures: dict[str, Exception] = {}
        task_columns = {
            "all_a_snapshot": "all_a_snapshot_status",
            "limit_up_pool": "limit_up_pool_status",
            "limit_down_pool": "limit_down_pool_status",
            "limit_break_pool": "limit_break_pool_status",
            "market_index": "market_index_status",
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
        market_index: MarketIndexSnapshot | None = None
        sector_index: SectorIndexSnapshot | None = None
        raw_rows: list[RawSnapshot] = []
        raw_insert_count = 0
        raw_insert_ms = 0
        market_index_insert_count = 0
        market_index_insert_ms = 0
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
            # Each endpoint owns its retries.  Six workers prevent a failed
            # endpoint from queueing the other five behind it and consuming the
            # next minute's collection slot.
            with ThreadPoolExecutor(max_workers=6) as executor:
                request_options: dict[str, Any] = {
                    "deadline": deadline,
                    "rate_limit_deadline": rate_limit_deadline,
                }
                if node.sequence_no == 250 and node.scheduled_time.second == 53:
                    request_options["allow_retries"] = False

                def pause_between_submissions() -> None:
                    sleep(uniform(SUBMISSION_PAUSE_MIN_SECONDS, SUBMISSION_PAUSE_MAX_SECONDS))

                called.add("all_a_snapshot")
                prices_future = executor.submit(
                    self.api.fetch,
                    **request_options,
                )
                pause_between_submissions()

                called.add("market_index")
                market_index_future = executor.submit(
                    self.api.fetch_market_index_snapshot,
                    node.trade_date,
                    **request_options,
                )
                if sectors or node.limit_pools_applicable:
                    pause_between_submissions()

                sector_index_future = None
                if sectors:
                    called.add("sector_index")
                    sector_index_future = executor.submit(
                        self.api.fetch_sector_index_snapshot,
                        [sector[0] for sector in sectors],
                        **request_options,
                    )
                    if node.limit_pools_applicable:
                        pause_between_submissions()

                limit_up_future = None
                limit_down_future = None
                limit_break_future = None
                if node.limit_pools_applicable:
                    called.add("limit_up_pool")
                    limit_up_future = executor.submit(
                        self.api.fetch_limit_pool,
                        "limit-up-pool",
                        node.trade_date,
                        **request_options,
                    )
                    pause_between_submissions()
                    called.add("limit_down_pool")
                    limit_down_future = executor.submit(
                        self.api.fetch_limit_pool,
                        "limit-down-pool",
                        node.trade_date,
                        **request_options,
                    )
                    pause_between_submissions()
                    called.add("limit_break_pool")
                    limit_break_future = executor.submit(
                        self.api.fetch_limit_pool,
                        "limit-break-pool",
                        node.trade_date,
                        **request_options,
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

                def write_market_index(snapshot: MarketIndexSnapshot) -> None:
                    nonlocal market_index_insert_count, market_index_insert_ms
                    market_index_insert_count, market_index_insert_ms = (
                        self.writer.insert_market_index_once(node, snapshot)
                    )

                market_index = resolve_and_write(
                    "market_index", market_index_future, write_market_index
                )
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
            market_index=market_index,
            sector_index=sector_index,
            sectors=sectors,
            raw_rows=raw_rows,
            raw_insert_count=raw_insert_count,
            raw_insert_ms=raw_insert_ms,
            market_index_insert_count=market_index_insert_count,
            market_index_insert_ms=market_index_insert_ms,
            sector_index_insert_count=sector_index_insert_count,
            sector_index_insert_ms=sector_index_insert_ms,
            errors=errors,
            failures=failures,
            not_applicable=not_applicable,
        )

    def collect_closing_pools_until_success(
        self,
        node: ScheduleNode,
        batch_id: str,
        *,
        limit_up: LimitPoolSnapshot | None = None,
        limit_down: LimitPoolSnapshot | None = None,
        limit_break: LimitPoolSnapshot | None = None,
        wait_before_first_retry: bool = False,
    ) -> tuple[LimitPoolSnapshot, LimitPoolSnapshot, LimitPoolSnapshot]:
        """Persist the 254th node's three pools, retrying missing pools until complete."""
        if node.sequence_no != 254:
            raise ValueError("Continuous closing-pool retry is only valid for node 254")

        snapshots: dict[str, LimitPoolSnapshot | None] = {
            "limit_up": limit_up,
            "limit_down": limit_down,
            "limit_break": limit_break,
        }
        specs = {
            "limit_up": (
                "limit-up-pool",
                "limit_up_pool_status",
                self.writer.insert_limit_up_pool,
            ),
            "limit_down": (
                "limit-down-pool",
                "limit_down_pool_status",
                self.writer.insert_limit_down_pool,
            ),
            "limit_break": (
                "limit-break-pool",
                "limit_break_pool_status",
                self.writer.insert_limit_break_pool,
            ),
        }
        retry_round = 0
        should_wait = wait_before_first_retry
        while any(snapshot is None for snapshot in snapshots.values()):
            retry_round += 1
            if should_wait:
                sleep(CLOSING_POOL_RETRY_SECONDS)
            should_wait = True
            round_errors: list[str] = []
            for name, snapshot in list(snapshots.items()):
                if snapshot is not None:
                    continue
                endpoint, status_column, insert = specs[name]
                self.writer.set_schedule_status(
                    node,
                    "RUNNING",
                    **{status_column: "RUNNING"},
                    emotion_state_status="BLOCKED",
                    emotion_error_code="CLOSING_POOLS_INCOMPLETE",
                    emotion_error_message="254号收盘三池尚未全部成功，正在持续重采",
                )
                try:
                    captured = self.api.fetch_limit_pool(endpoint, node.trade_date)
                    insert(node, batch_id, captured)
                except Exception as exc:  # noqa: BLE001 - node 254 must keep retrying
                    error_code = str(getattr(exc, "error_code", type(exc).__name__))
                    round_errors.append(f"{name}:{error_code}:{exc}")
                    self.writer.set_schedule_status(
                        node,
                        "RUNNING",
                        **{status_column: "FAILED"},
                        emotion_state_status="BLOCKED",
                        emotion_error_code="CLOSING_POOLS_INCOMPLETE",
                        emotion_error_message=str(exc)[:1000],
                    )
                    continue
                snapshots[name] = captured
                self.writer.set_schedule_status(
                    node,
                    "RUNNING",
                    **{
                        status_column: "SUCCESS",
                        f"{name}_source_time": captured.source_time,
                        f"{name}_received_count": captured.total,
                        f"{name}_api_duration_ms": captured.duration_ms,
                    },
                )
            if round_errors:
                LOG.warning(
                    "CLOSING_POOLS_RETRY collection_id=%s round=%s errors=%s",
                    node.collection_id,
                    retry_round,
                    "; ".join(round_errors),
                )

        self.writer.set_schedule_status(
            node,
            "RUNNING",
            limit_up_pool_status="SUCCESS",
            limit_down_pool_status="SUCCESS",
            limit_break_pool_status="SUCCESS",
            limit_pool_collected=1,
            emotion_state_status="PENDING",
            emotion_error_code=None,
            emotion_error_message=None,
        )
        assert snapshots["limit_up"] is not None
        assert snapshots["limit_down"] is not None
        assert snapshots["limit_break"] is not None
        return (
            snapshots["limit_up"],
            snapshots["limit_down"],
            snapshots["limit_break"],
        )

    def complete_closing_node_until_success(
        self,
        node: ScheduleNode,
        batch_id: str,
        result: RawCollectionResult,
        *,
        wait_before_first_retry: bool = False,
    ) -> RawCollectionResult:
        """Keep node 254 open until all five original inputs are persisted."""
        if node.sequence_no != 254:
            raise ValueError("Continuous closing retry is only valid for node 254")

        pools_were_missing = any(
            snapshot is None
            for snapshot in (result.limit_up, result.limit_down, result.limit_break)
        )
        limit_up, limit_down, limit_break = self.collect_closing_pools_until_success(
            node,
            batch_id,
            limit_up=result.limit_up,
            limit_down=result.limit_down,
            limit_break=result.limit_break,
            wait_before_first_retry=wait_before_first_retry,
        )

        prices = result.prices
        sector_index = result.sector_index
        sectors = result.sectors
        raw_rows = result.raw_rows
        raw_insert_count = result.raw_insert_count
        raw_insert_ms = result.raw_insert_ms
        sector_insert_count = result.sector_index_insert_count
        sector_insert_ms = result.sector_index_insert_ms
        should_wait = wait_before_first_retry and not pools_were_missing
        retry_round = 0

        while prices is None or sector_index is None:
            retry_round += 1
            if should_wait:
                sleep(CLOSING_POOL_RETRY_SECONDS)
            should_wait = True
            round_errors: list[str] = []

            if prices is None:
                self.writer.set_schedule_status(
                    node,
                    "RUNNING",
                    all_a_snapshot_status="RUNNING",
                )
                try:
                    captured_prices = self.api.fetch()
                    captured_rows = [
                        RawSnapshot.from_api(
                            item,
                            trade_date=node.trade_date,
                            collection_id=node.collection_id,
                            scheduled_time=node.scheduled_time,
                            source_timestamp=captured_prices.source_timestamp,
                            source_time=captured_prices.source_time,
                            session=node.session,
                            batch_id=batch_id,
                        )
                        for item in captured_prices.items
                    ]
                    captured_count, captured_ms = self.writer.insert_raw_once(captured_rows)
                except Exception as exc:  # noqa: BLE001 - closing node must remain open.
                    round_errors.append(f"all_a_snapshot:{type(exc).__name__}:{exc}")
                    self.writer.set_schedule_status(
                        node,
                        "RUNNING",
                        all_a_snapshot_status="FAILED",
                    )
                else:
                    prices = captured_prices
                    raw_rows = captured_rows
                    raw_insert_count = captured_count
                    raw_insert_ms = captured_ms
                    self.writer.set_schedule_status(
                        node,
                        "RUNNING",
                        all_a_snapshot_status="SUCCESS",
                    )

            if sector_index is None:
                self.writer.set_schedule_status(
                    node,
                    "RUNNING",
                    sector_index_status="RUNNING",
                )
                try:
                    sectors = self.writer.active_sector_catalog()
                    if not sectors:
                        raise RuntimeError("No active sector catalog exists")
                    captured_sector = self.api.fetch_sector_index_snapshot(
                        [sector[0] for sector in sectors]
                    )
                    captured_count, captured_ms = self.writer.insert_sector_index_once(
                        node, sectors, captured_sector
                    )
                except Exception as exc:  # noqa: BLE001 - closing node must remain open.
                    round_errors.append(f"sector_index:{type(exc).__name__}:{exc}")
                    self.writer.set_schedule_status(
                        node,
                        "RUNNING",
                        sector_index_status="FAILED",
                    )
                else:
                    sector_index = captured_sector
                    sector_insert_count = captured_count
                    sector_insert_ms = captured_ms
                    self.writer.set_schedule_status(
                        node,
                        "RUNNING",
                        sector_index_status="SUCCESS",
                    )

            if round_errors:
                LOG.warning(
                    "CLOSING_INPUTS_RETRY collection_id=%s round=%s errors=%s",
                    node.collection_id,
                    retry_round,
                    "; ".join(round_errors),
                )

        recovered_tasks = {
            "all_a_snapshot",
            "sector_index",
            "limit_up_pool",
            "limit_down_pool",
            "limit_break_pool",
        }
        return replace(
            result,
            prices=prices,
            limit_up=limit_up,
            limit_down=limit_down,
            limit_break=limit_break,
            sector_index=sector_index,
            sectors=sectors,
            raw_rows=raw_rows,
            raw_insert_count=raw_insert_count,
            raw_insert_ms=raw_insert_ms,
            sector_index_insert_count=sector_insert_count,
            sector_index_insert_ms=sector_insert_ms,
            errors={key: value for key, value in result.errors.items() if key not in recovered_tasks},
            failures={
                key: value for key, value in result.failures.items() if key not in recovered_tasks
            },
        )
