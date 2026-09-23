from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import Lock, Thread
from time import perf_counter, sleep

from app.hithink.aggregation import AggregationResult, MarketAggregationPipeline
from app.hithink.api import HithinkApiError, HithinkClient
from app.hithink.daily_pipeline import DailyPipeline
from app.hithink.post_derivation import PostDerivationPipeline, PostDerivationResult
from app.hithink.raw_collector import RawCollectionResult, RawCollector
from app.hithink.schedule import (
    SCHEDULE_V3_EFFECTIVE_DATE,
    SHANGHAI,
    ScheduleNode,
    build_daily_schedule,
)
from app.hithink.sector_mapping import SectorMappingSync
from app.hithink.state_deriver import DerivationResult, StateDeriver
from app.hithink.trading_day_gate import SharedTradingDayGate
from app.hithink.writer import ClickHouseWriter

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
ACTIVE_NODE_PATH = ROOT / ".runtime" / "hithink_snapshot_active_node.json"
CLOSING_NODE_RETRY_SECONDS = 60
CLOSING_NODE_RETRY_CUTOFF_TIME = time(16, 0)
CLOSING_NODE_V3_RETRY_CUTOFF_TIME = time(15, 50, 8)
COLLECTOR_PREPARE_TIME = time(8, 50)
DAILY_COLLECTION_TIME = time(16, 0)
DAILY_RETRY_DEADLINE_TIME = time(16, 15)
DAILY_RETRY_SECONDS = 60
DAILY_MAX_RETRIES = 15
CLOSE_COMPLETENESS_CHECK_TIME = time(16, 20)
SECTOR_MAPPING_DEADLINE_TIME = time(9, 10)

DailyCollectionJob = Callable[[date], bool]
AuctionCollectionJob = Callable[[date], None]
HealthProbe = Callable[[date], dict[str, object]]


def next_daily_collection_at(now: datetime) -> datetime:
    """Return 16:00, or now when today's daily collection needs catch-up."""
    scheduled = datetime.combine(now.date(), DAILY_COLLECTION_TIME, tzinfo=SHANGHAI)
    return max(scheduled, now)


def should_run_mapping_after_confirmation(
    startup_now: datetime, confirmation_date: object
) -> bool:
    startup_prepare = datetime.combine(
        startup_now.date(), COLLECTOR_PREPARE_TIME, tzinfo=SHANGHAI
    )
    return not (
        startup_now.date() == confirmation_date and startup_now > startup_prepare
    )


class CollectorRunner:
    """One resident service with independently auditable collection, derivation and aggregation."""

    def __init__(
        self,
        api: HithinkClient,
        writer: ClickHouseWriter,
        *,
        daily_collection_job: DailyCollectionJob | None = None,
        auction_collection_job: AuctionCollectionJob | None = None,
        health_probe: HealthProbe | None = None,
    ):
        self.api = api
        self.writer = writer
        self.raw_collector = RawCollector(api, writer)
        self.state_deriver = StateDeriver(writer)
        self.post_deriver = PostDerivationPipeline(writer)
        self.aggregator = MarketAggregationPipeline(writer)
        self.trading_day_gate = SharedTradingDayGate(api, writer)
        self.sector_mapping = SectorMappingSync(api, writer)
        self.daily_pipeline = DailyPipeline(api, writer)
        self.daily_collection_job = daily_collection_job
        self.auction_collection_job = auction_collection_job
        self.health_probe = health_probe
        self._auction_thread_lock = Lock()
        self._auction_threads: dict[date, Thread] = {}
        self._daily_thread_lock = Lock()
        self._daily_threads: dict[date, Thread] = {}
        self._monitor_thread_lock = Lock()
        self._monitor_threads: dict[date, Thread] = {}

    def initialize_day(self, trade_date: object) -> list[ScheduleNode]:
        nodes = build_daily_schedule(trade_date)
        self.writer.initialize_schedule(nodes)
        self.writer.initialize_daily_task_plan(trade_date)
        self.writer.repair_stale_running(
            trade_date,
            datetime.now(SHANGHAI) - timedelta(minutes=3),
            self._active_collection_id(),
        )
        return nodes

    def run_day(
        self,
        trade_date: object,
        not_before: time | None = None,
        *,
        prepared_nodes: list[ScheduleNode] | None = None,
    ) -> None:
        nodes = prepared_nodes or self.initialize_day(trade_date)
        status = self.writer.statuses(trade_date)
        now = datetime.now(SHANGHAI)
        for node in nodes:
            current_status = status.get(node.scheduled_time)
            if node.sequence_no == 254:
                if current_status == "SUCCESS":
                    continue
                delay = (node.scheduled_time - datetime.now(SHANGHAI)).total_seconds()
                if delay > 0:
                    sleep(delay)
                self._run_closing_node_until_success(node)
                continue
            if current_status != "PENDING":
                continue
            if not_before and node.scheduled_time.timetz().replace(tzinfo=None) < not_before:
                self._mark_skipped(node, "NOT_BEFORE")
                continue
            if node.scheduled_time < now:
                self._mark_skipped(node, "MISSED_STARTUP")
                continue
            delay = (node.scheduled_time - datetime.now(SHANGHAI)).total_seconds()
            if delay > 0:
                sleep(delay)
            self.run_node(node)

    def run_closing_baseline(self, trade_date: object) -> None:
        if trade_date != datetime.now(SHANGHAI).date():
            raise RuntimeError("Closing baseline is only allowed for the current trading day")
        node = build_daily_schedule(trade_date)[-1]
        if datetime.now(SHANGHAI) < node.scheduled_time:
            raise RuntimeError("Closing baseline is only allowed after the 15:30 planned node")
        if self.writer.statuses(trade_date).get(node.scheduled_time) == "SUCCESS":
            LOG.info("Closing baseline already exists collection_id=%s", node.collection_id)
            return
        self._run_closing_node_until_success(node)

    def _run_closing_node_until_success(self, node: ScheduleNode) -> None:
        if node.sequence_no != 254:
            raise ValueError("Persistent closing execution is only valid for node 254")
        retry_cutoff_time = (
            CLOSING_NODE_V3_RETRY_CUTOFF_TIME
            if node.trade_date >= SCHEDULE_V3_EFFECTIVE_DATE
            else CLOSING_NODE_RETRY_CUTOFF_TIME
        )
        retry_cutoff = datetime.combine(
            node.trade_date,
            retry_cutoff_time,
            tzinfo=SHANGHAI,
        )
        while True:
            now = datetime.now(SHANGHAI)
            if now >= retry_cutoff:
                LOG.critical(
                    "CLOSING_NODE_RETRY_CUTOFF collection_id=%s cutoff=%s; "
                    "stopping retries after the 20-minute closing window",
                    node.collection_id,
                    retry_cutoff,
                )
                return
            self.run_node(node)
            if self.writer.statuses(node.trade_date).get(node.scheduled_time) == "SUCCESS":
                return
            LOG.error(
                "CLOSING_NODE_INCOMPLETE collection_id=%s; retrying in %s seconds",
                node.collection_id,
                CLOSING_NODE_RETRY_SECONDS,
            )
            sleep(CLOSING_NODE_RETRY_SECONDS)

    def serve_forever(self) -> None:
        startup_now = datetime.now(SHANGHAI)
        prepared_date = None
        while True:
            now = datetime.now(SHANGHAI)
            if prepared_date == now.date():
                next_prepare = datetime.combine(
                    now.date() + timedelta(days=1),
                    COLLECTOR_PREPARE_TIME,
                    tzinfo=SHANGHAI,
                )
                sleep(max(1.0, (next_prepare - now).total_seconds()))
                continue
            prepare_at = datetime.combine(
                now.date(), COLLECTOR_PREPARE_TIME, tzinfo=SHANGHAI
            )
            if now < prepare_at:
                sleep(max(1.0, (prepare_at - now).total_seconds()))
                continue
            decision = self._wait_for_trading_day_decision(now.date())
            prepared_date = now.date()
            if decision:
                # The 254-node plan and every intraday source, including the
                # fixed market-index batch, start only after this 08:50 gate.
                nodes = self.initialize_day(prepared_date)
                LOG.info(
                    "INTRADAY_PLAN_READY date=%s nodes=%s first=%s last=%s",
                    prepared_date,
                    len(nodes),
                    nodes[0].scheduled_time,
                    nodes[-1].scheduled_time,
                )
                self._start_auction_collection_worker(prepared_date)
                self._start_daily_collection_worker(prepared_date)
                self._start_day_monitor(prepared_date)
                if not should_run_mapping_after_confirmation(
                    startup_now, prepared_date
                ):
                    LOG.info(
                        "SECTOR_MAPPING_MISSED_STARTUP date=%s; next run waits for "
                        "the next trading-day confirmation",
                        prepared_date,
                    )
                else:
                    self._run_sector_mapping_once(prepared_date)
                self.run_day(prepared_date, prepared_nodes=nodes)
            else:
                self._mark_non_trading_day_skipped(prepared_date)
                LOG.info(
                    "NON_TRADING_DAY date=%s; sector mapping, 254-node schedule, "
                    "and daily-K are all skipped",
                    now.date(),
                )

    def _run_sector_mapping_once(self, trade_date: object) -> None:
        attempted = self.writer.client.query(
            "SELECT count() FROM market.sector_mapping_sync_status "
            "WHERE sync_date = {date:Date}",
            parameters={"date": trade_date},
        ).result_rows[0][0]
        if int(attempted) > 0:
            LOG.info("SECTOR_MAPPING_ALREADY_ATTEMPTED date=%s; skipping", trade_date)
            return
        self._run_sector_mapping()

    def _run_sector_mapping(self) -> None:
        try:
            now = datetime.now(SHANGHAI)
            deadline = datetime.combine(
                now.date(), SECTOR_MAPPING_DEADLINE_TIME, tzinfo=SHANGHAI
            )
            run_id = self.sector_mapping.sync_all(deadline=deadline)
            LOG.info("SECTOR_MAPPING_COMPLETED run_id=%s", run_id)
        except Exception:
            # Mapping failure must not terminate or restart the 254-node collector.
            LOG.exception("SECTOR_MAPPING_FAILED; intraday collector will continue")

    def _start_auction_collection_worker(self, trade_date: date) -> None:
        if self.auction_collection_job is None:
            return
        with self._auction_thread_lock:
            existing = self._auction_threads.get(trade_date)
            if existing is not None and existing.is_alive():
                return
            worker = Thread(
                target=self._execute_auction_collection,
                args=(trade_date,),
                name=f"auction-collection-{trade_date:%Y%m%d}",
                daemon=True,
            )
            self._auction_threads[trade_date] = worker
            worker.start()

    def _execute_auction_collection(self, trade_date: date) -> None:
        try:
            if self.auction_collection_job is not None:
                self.auction_collection_job(trade_date)
        except Exception:
            LOG.exception(
                "AUCTION_COLLECTION_JOB_FAILED date=%s; intraday collector will continue",
                trade_date,
            )

    def _run_daily_collection(self, trade_date: object) -> bool:
        try:
            self.writer.initialize_daily_task_plan(trade_date)
            if self.writer.daily_status(trade_date, "daily_collection") == "SUCCESS":
                LOG.info("DAILY_COLLECTION_ALREADY_SUCCESS date=%s", trade_date)
                return True
            initialize = not self.writer.daily_seed_complete()
            self.daily_pipeline.run_pipeline(trade_date, initialize=initialize)
            status = self.writer.daily_status(trade_date, "daily_collection")
            if status in {"SUCCESS", "SKIPPED"}:
                LOG.info("DAILY_COLLECTION_COMPLETED date=%s status=%s", trade_date, status)
                return True
            LOG.error(
                "DAILY_COLLECTION_INCOMPLETE date=%s status=%s; retrying in %s seconds",
                trade_date,
                status,
                DAILY_RETRY_SECONDS,
            )
            return False
        except Exception:
            # A failed attempt is retried by the resident scheduler without
            # terminating or restarting the 254-node collector.
            LOG.exception(
                "DAILY_COLLECTION_FAILED date=%s; retrying in %s seconds",
                trade_date,
                DAILY_RETRY_SECONDS,
            )
            return False

    def _execute_daily_collection(self, trade_date: date) -> bool:
        if self.daily_collection_job is not None:
            try:
                return bool(self.daily_collection_job(trade_date))
            except Exception:
                LOG.exception("DAILY_COLLECTION_JOB_FAILED date=%s", trade_date)
                return False
        return self._run_daily_collection(trade_date)

    def _start_daily_collection_worker(
        self, trade_date: date, *, run_immediately: bool = False
    ) -> None:
        with self._daily_thread_lock:
            existing = self._daily_threads.get(trade_date)
            if existing is not None and existing.is_alive():
                return
            worker = Thread(
                target=self._daily_collection_retry_loop,
                args=(trade_date, run_immediately),
                name=f"daily-collection-{trade_date:%Y%m%d}",
                daemon=True,
            )
            self._daily_threads[trade_date] = worker
            worker.start()

    def _daily_collection_retry_loop(
        self, trade_date: date, run_immediately: bool = False
    ) -> None:
        scheduled_at = datetime.combine(
            trade_date, DAILY_COLLECTION_TIME, tzinfo=SHANGHAI
        )
        retry_deadline = datetime.combine(
            trade_date,
            DAILY_RETRY_DEADLINE_TIME,
            tzinfo=SHANGHAI,
        )
        now = datetime.now(SHANGHAI)
        if not run_immediately and now < scheduled_at:
            sleep(max(1.0, (scheduled_at - now).total_seconds()))

        retry_count = 0
        while datetime.now(SHANGHAI) <= retry_deadline:
            if self._execute_daily_collection(trade_date):
                return
            if retry_count >= DAILY_MAX_RETRIES:
                break
            now = datetime.now(SHANGHAI)
            if now >= retry_deadline:
                break
            retry_count += 1
            sleep(min(DAILY_RETRY_SECONDS, (retry_deadline - now).total_seconds()))

        LOG.critical(
            "DAILY_COLLECTION_DEADLINE_EXCEEDED date=%s retries=%s deadline=%s",
            trade_date,
            retry_count,
            retry_deadline,
        )

    def _mark_non_trading_day_skipped(self, trade_date: date) -> None:
        for task_name in (
            "tencent_stock_basic_sync",
            "sector_catalog_sync",
            "sector_membership_sync",
            "hithink_daily_k_raw_sync",
            "hithink_adjustment_events_sync",
            "daily_collection",
        ):
            if self.writer.daily_status(trade_date, task_name) == "SUCCESS":
                continue
            self.writer.set_daily_status(
                trade_date,
                task_name,
                "SKIPPED",
                error_code="NON_TRADING_DAY",
            )

    def _wait_for_trading_day_decision(self, trade_date: object) -> bool:
        while True:
            decision = self._trading_day_decision(trade_date)
            if decision is not None:
                return decision
            level = (
                logging.CRITICAL
                if datetime.now(SHANGHAI).time() >= time(8, 55)
                else logging.ERROR
            )
            LOG.log(
                level,
                "CHECKPOINT_ALERT date=%s calendar is still unknown; retrying in 5 minutes",
                trade_date,
            )
            sleep(300)

    def _start_day_monitor(self, trade_date: date) -> None:
        if self.health_probe is None:
            return
        with self._monitor_thread_lock:
            existing = self._monitor_threads.get(trade_date)
            if existing is not None and existing.is_alive():
                return
            worker = Thread(
                target=self._monitor_day,
                args=(trade_date,),
                name=f"collector-monitor-{trade_date:%Y%m%d}",
                daemon=True,
            )
            self._monitor_threads[trade_date] = worker
            worker.start()

    def _monitor_day(self, trade_date: date) -> None:
        checks: list[
            tuple[time, str, Callable[[dict[str, object]], bool], str]
        ] = [
            (
                time(8, 55),
                "08:55",
                lambda state: state.get("calendar_status") == "SUCCESS",
                "交易日确认尚未成功",
            ),
            (
                time(9, 0),
                "09:00",
                lambda state: int(state.get("daily_plan_count") or 0) >= 6,
                "每日任务状态计划未准备完整",
            ),
            (
                time(9, 10),
                "09:10",
                lambda state: int(state.get("schedule_count") or 0) == 254,
                "盘中计划不是完整254条",
            ),
            (
                time(9, 16),
                "09:16",
                lambda state: state.get("first_node_status")
                not in {None, "PENDING", "RUNNING"},
                "第一号节点尚未结束",
            ),
            (
                time(15, 35),
                "15:35",
                lambda state: state.get("closing_node_status") == "SUCCESS",
                "第254号节点尚未成功，程序将每分钟重试到15:50:08",
            ),
            (
                time(16, 5),
                "16:05",
                lambda state: state.get("daily_collection_status")
                in {"RUNNING", "SUCCESS"},
                "16:00日K尚未开始或已经失败",
            ),
            (
                CLOSE_COMPLETENESS_CHECK_TIME,
                "16:20",
                lambda state: state.get("daily_collection_status") == "SUCCESS",
                "16:00日K尚未成功",
            ),
        ]
        for check_time, label, predicate, message in checks:
            check_at = datetime.combine(trade_date, check_time, tzinfo=SHANGHAI)
            now = datetime.now(SHANGHAI)
            if now < check_at:
                sleep(max(1.0, (check_at - now).total_seconds()))
            try:
                state = self.health_probe(trade_date) if self.health_probe else {}
                if predicate(state):
                    LOG.info("CHECKPOINT_OK date=%s time=%s", trade_date, label)
                else:
                    LOG.critical(
                        "CHECKPOINT_ALERT date=%s time=%s message=%s state=%s",
                        trade_date,
                        label,
                        message,
                        state,
                    )
            except Exception:
                LOG.exception(
                    "CHECKPOINT_ALERT date=%s time=%s health check failed",
                    trade_date,
                    label,
                )

    def _trading_day_decision(self, trade_date: object) -> bool | None:
        started = datetime.now(SHANGHAI)
        self.writer.initialize_daily_task_plan(trade_date)
        confirmation = self.trading_day_gate.confirm(trade_date)
        if confirmation.is_trading_day is None:
            self.writer.set_daily_status(
                trade_date,
                "calendar_gate",
                "FAILED",
                request_start_time=started,
                request_end_time=datetime.now(SHANGHAI),
                error_code="CALENDAR_UNKNOWN",
                error_message=confirmation.error_message,
            )
            LOG.error("calendar confirmation failed: %s", confirmation.error_message)
        else:
            self.writer.set_daily_status(
                trade_date,
                "calendar_gate",
                "SUCCESS",
                request_start_time=started,
                request_end_time=datetime.now(SHANGHAI),
                retry_count=confirmation.retry_count,
                error_code=(
                    None if confirmation.source == "API" else confirmation.source
                ),
                error_message=confirmation.error_message,
            )
            LOG.info(
                "CALENDAR_DECISION date=%s trading_day=%s source=%s confirmed_at=%s",
                trade_date,
                confirmation.is_trading_day,
                confirmation.source,
                confirmation.confirmed_at,
            )
        return confirmation.is_trading_day

    def run_node(self, node: ScheduleNode) -> None:
        self._write_active_node(node)
        try:
            self._run_node(node)
        finally:
            self._clear_active_node(node.collection_id)

    def _run_node(self, node: ScheduleNode) -> None:
        started_at = datetime.now(SHANGHAI)
        started = perf_counter()
        batch_id = node.collection_id
        pool_initial_status = "RUNNING" if node.limit_pools_applicable else "SKIPPED"
        self.writer.set_schedule_status(
            node,
            "RUNNING",
            batch_id=batch_id,
            request_start_time=started_at,
            raw_status="RUNNING",
            derivation_status="PENDING",
            all_a_snapshot_status="RUNNING",
            limit_up_pool_status=pool_initial_status,
            limit_down_pool_status=pool_initial_status,
            limit_break_pool_status=pool_initial_status,
            market_index_status="RUNNING",
            sector_index_status="RUNNING",
            **self.post_deriver.initial_status_values(node),
            **self.aggregator.initial_status_values(node),
        )
        try:
            facts = self.raw_collector.collect(node, batch_id)
        except HithinkApiError as exc:
            self._mark_raw_failure(node, batch_id, started_at, started, str(exc.code), str(exc))
            LOG.exception("RAW_FAILED sequence=%s api_code=%s", node.sequence_no, exc.code)
            return
        except Exception as exc:
            self._mark_raw_failure(node, batch_id, started_at, started, "RAW_COLLECTION_ERROR", str(exc))
            LOG.exception("RAW_FAILED sequence=%s", node.sequence_no)
            return

        if node.sequence_no == 254 and any(
            payload is None
            for payload in (
                facts.prices,
                facts.limit_up,
                facts.limit_down,
                facts.limit_break,
                facts.sector_index,
            )
        ):
            facts = self.raw_collector.complete_closing_node_until_success(
                node,
                batch_id,
                facts,
                wait_before_first_retry=True,
            )

        raw_completed_at = datetime.now(SHANGHAI)
        raw_values = self._raw_values(facts, started_at, raw_completed_at)
        self.writer.set_schedule_status(
            node,
            "RUNNING",
            **raw_values,
            derivation_status="PENDING",
            raw_completed_at=raw_completed_at,
        )
        if facts.prices is None:
            self.post_deriver.run_emotion_and_block(
                node,
                "ALL_A_SNAPSHOT_UNAVAILABLE",
                "全A快照缺失，原有市场状态及后续派生不能生成",
            )
            self.aggregator.block(
                node,
                "ALL_A_SNAPSHOT_UNAVAILABLE",
                "全A快照缺失，聚合数据包不能生成",
            )
            final_status = "FAILED" if facts.raw_status == "FAILED" else "PARTIAL"
            self.writer.set_schedule_status(
                node,
                final_status,
                **raw_values,
                derivation_status="BLOCKED",
                raw_completed_at=raw_completed_at,
                derivation_error_code="ALL_A_SNAPSHOT_UNAVAILABLE",
                derivation_error_message=(
                    "全A快照失败；其他接口已独立执行并按实际结果保存"
                ),
                total_duration_ms=round((perf_counter() - started) * 1000),
            )
            LOG.error(
                "RAW_SNAPSHOT_FAILED sequence=%s status=%s errors=%s",
                node.sequence_no,
                final_status,
                facts.errors,
            )
            return
        derivation_started_at = datetime.now(SHANGHAI)
        self.writer.set_schedule_status(
            node,
            "RUNNING",
            **raw_values,
            derivation_status="RUNNING",
            raw_completed_at=raw_completed_at,
            derivation_started_at=derivation_started_at,
        )
        try:
            derived = self.state_deriver.derive(
                node, include_sector_states=facts.sector_index is not None
            )
        except Exception as exc:
            self.post_deriver.run_emotion_and_block(
                node,
                "ORIGINAL_DERIVATION_FAILED",
                "原有个股、市场或板块状态派生失败",
            )
            self.aggregator.block(
                node,
                "ORIGINAL_DERIVATION_FAILED",
                "基础派生失败，聚合数据包不能生成",
            )
            self._mark_derivation_failure(
                node,
                raw_values,
                raw_completed_at,
                derivation_started_at,
                started,
                "DERIVATION_ERROR",
                str(exc),
            )
            LOG.exception("DERIVATION_FAILED sequence=%s", node.sequence_no)
            return

        post_result = self.post_deriver.run(
            node,
            sector_states_ready=facts.sector_index is not None,
        )
        derivation_completed_at = datetime.now(SHANGHAI)
        if post_result.success:
            aggregation_result = self.aggregator.run(node)
        else:
            self.aggregator.block(
                node,
                "DERIVATION_CHAIN_FAILED",
                "派生链未完整成功，聚合数据包不能生成",
            )
            aggregation_result = AggregationResult(success=False)
        self._mark_success(
            node,
            raw_values,
            raw_completed_at,
            derivation_started_at,
            derivation_completed_at,
            started,
            derived,
            post_result,
            aggregation_result,
        )

    def _raw_values(
        self, facts: RawCollectionResult, started_at: datetime, raw_completed_at: datetime
    ) -> dict[str, object]:
        def task_status(task: str, payload: object | None) -> str:
            if task in facts.not_applicable:
                return "SKIPPED"
            return "SUCCESS" if payload is not None else "FAILED"

        values: dict[str, object] = {
            "batch_id": facts.batch_id,
            "request_start_time": started_at,
            "request_end_time": raw_completed_at,
            "received_count": len(facts.raw_rows),
            "raw_insert_count": facts.raw_insert_count,
            "raw_insert_ms": facts.raw_insert_ms,
            "raw_status": facts.raw_status,
            "all_a_snapshot_status": "SUCCESS" if facts.prices is not None else "FAILED",
            "limit_up_pool_status": task_status("limit_up_pool", facts.limit_up),
            "limit_down_pool_status": task_status("limit_down_pool", facts.limit_down),
            "limit_break_pool_status": task_status("limit_break_pool", facts.limit_break),
            "market_index_status": (
                "SUCCESS" if facts.market_index is not None else "FAILED"
            ),
            "market_index_received_count": (
                facts.market_index.total if facts.market_index is not None else None
            ),
            "market_index_api_duration_ms": (
                facts.market_index.duration_ms if facts.market_index is not None else None
            ),
            "sector_index_status": "SUCCESS" if facts.sector_index is not None else "FAILED",
            "limit_pool_collected": int(
                facts.limit_up is not None
                and facts.limit_down is not None
                and facts.limit_break is not None
            ),
            "raw_error_code": None,
            "raw_error_message": None,
        }
        if facts.errors:
            values["raw_error_code"] = ",".join(
                f"{task}:{error[0]}" for task, error in facts.errors.items()
            )[:1000]
            values["raw_error_message"] = "; ".join(
                f"{task}: {error[1]}" for task, error in facts.errors.items()
            )[:1000]
        if facts.prices is not None:
            values.update(
                source_timestamp=facts.prices.source_timestamp,
                source_time=facts.prices.source_time,
                api_code=facts.prices.code,
                api_total=facts.prices.total,
                api_duration_ms=facts.prices.duration_ms,
                retry_count=facts.prices.retry_count,
            )
        else:
            failure = facts.failures.get("all_a_snapshot")
            if isinstance(failure, HithinkApiError):
                if failure.code is not None:
                    values["api_code"] = failure.code
                if failure.duration_ms is not None:
                    values["api_duration_ms"] = failure.duration_ms
                values["retry_count"] = failure.retry_index
        for name, snapshot in (
            ("limit_up", facts.limit_up),
            ("limit_down", facts.limit_down),
            ("limit_break", facts.limit_break),
        ):
            if snapshot is not None:
                values[f"{name}_source_time"] = snapshot.source_time
                values[f"{name}_received_count"] = snapshot.total
                values[f"{name}_api_duration_ms"] = snapshot.duration_ms
            else:
                failure = facts.failures.get(f"{name}_pool")
                if isinstance(failure, HithinkApiError) and failure.duration_ms is not None:
                    values[f"{name}_api_duration_ms"] = failure.duration_ms
        if facts.sector_index is not None:
            values["sector_index_received_count"] = facts.sector_index.total
            values["sector_index_api_duration_ms"] = facts.sector_index.duration_ms
        else:
            failure = facts.failures.get("sector_index")
            if isinstance(failure, HithinkApiError) and failure.duration_ms is not None:
                values["sector_index_api_duration_ms"] = failure.duration_ms
        if facts.market_index is None:
            failure = facts.failures.get("market_index")
            if isinstance(failure, HithinkApiError) and failure.duration_ms is not None:
                values["market_index_api_duration_ms"] = failure.duration_ms
        return values

    def _mark_success(
        self,
        node: ScheduleNode,
        raw_values: dict[str, object],
        raw_completed_at: datetime,
        derivation_started_at: datetime,
        derivation_completed_at: datetime,
        started: float,
        derived: DerivationResult,
        post_result: PostDerivationResult,
        aggregation_result: AggregationResult,
    ) -> None:
        self.writer.set_schedule_status(
            node,
            "SUCCESS"
            if raw_values["raw_status"] == "SUCCESS"
            and post_result.success
            and aggregation_result.success
            else "PARTIAL",
            **raw_values,
            derivation_status="SUCCESS",
            raw_completed_at=raw_completed_at,
            derivation_started_at=derivation_started_at,
            derivation_completed_at=derivation_completed_at,
            derivation_duration_ms=round(
                (derivation_completed_at - derivation_started_at).total_seconds() * 1000
            ),
            derived_insert_count=derived.derived_insert_count,
            derive_ms=derived.derive_ms + derived.derived_insert_ms,
            sector_state_status=(
                "SUCCESS" if raw_values["sector_index_status"] == "SUCCESS" else "SKIPPED"
            ),
            sector_state_row_count=derived.sector_state_row_count,
            sector_state_duration_ms=derived.sector_state_duration_ms,
            total_duration_ms=round((perf_counter() - started) * 1000),
        )

    def _mark_raw_failure(
        self,
        node: ScheduleNode,
        batch_id: str,
        started_at: datetime,
        started: float,
        error_code: str,
        error_message: str,
    ) -> None:
        completed_at = datetime.now(SHANGHAI)
        pool_status = "FAILED" if node.limit_pools_applicable else "SKIPPED"
        self.writer.set_schedule_status(
            node,
            "FAILED",
            batch_id=batch_id,
            request_start_time=started_at,
            request_end_time=completed_at,
            raw_completed_at=completed_at,
            raw_status="FAILED",
            derivation_status="BLOCKED",
            all_a_snapshot_status="FAILED",
            limit_up_pool_status=pool_status,
            limit_down_pool_status=pool_status,
            limit_break_pool_status=pool_status,
            market_index_status="FAILED",
            sector_index_status="FAILED",
            sector_state_status="BLOCKED",
            **self.post_deriver.blocked_status_values(
                node, error_code, error_message, block_emotion=True
            ),
            **self.aggregator.blocked_status_values(node, error_code, error_message),
            raw_error_code=error_code,
            raw_error_message=error_message[:1000],
            total_duration_ms=round((perf_counter() - started) * 1000),
        )

    def _mark_derivation_failure(
        self,
        node: ScheduleNode,
        raw_values: dict[str, object],
        raw_completed_at: datetime,
        derivation_started_at: datetime,
        started: float,
        error_code: str,
        error_message: str,
    ) -> None:
        self.writer.set_schedule_status(
            node,
            "PARTIAL",
            **raw_values,
            derivation_status="FAILED",
            raw_completed_at=raw_completed_at,
            derivation_started_at=derivation_started_at,
            derivation_completed_at=datetime.now(SHANGHAI),
            derivation_duration_ms=round(
                (datetime.now(SHANGHAI) - derivation_started_at).total_seconds() * 1000
            ),
            derivation_error_code=error_code,
            derivation_error_message=error_message[:1000],
            total_duration_ms=round((perf_counter() - started) * 1000),
        )

    def _mark_skipped(self, node: ScheduleNode, error_code: str) -> None:
        self.writer.set_schedule_status(
            node,
            "SKIPPED",
            raw_status="SKIPPED",
            derivation_status="SKIPPED",
            **{
                key: ("SKIPPED" if key.endswith("_status") else value)
                for key, value in {
                    **self.post_deriver.initial_status_values(node),
                    **self.aggregator.initial_status_values(node),
                }.items()
            },
            all_a_snapshot_status="SKIPPED",
            limit_up_pool_status="SKIPPED",
            limit_down_pool_status="SKIPPED",
            limit_break_pool_status="SKIPPED",
            market_index_status="SKIPPED",
            sector_index_status="SKIPPED",
            error_code=error_code,
        )

    def _write_active_node(self, node: ScheduleNode) -> None:
        ACTIVE_NODE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE_NODE_PATH.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "collection_id": node.collection_id,
                    "started_at": datetime.now(SHANGHAI).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _active_collection_id(self) -> str | None:
        try:
            record = json.loads(ACTIVE_NODE_PATH.read_text(encoding="utf-8"))
            pid = int(record["pid"])
            os.kill(pid, 0)
            return str(record["collection_id"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _clear_active_node(self, collection_id: str) -> None:
        try:
            record = json.loads(ACTIVE_NODE_PATH.read_text(encoding="utf-8"))
            if str(record.get("collection_id")) == collection_id:
                ACTIVE_NODE_PATH.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
