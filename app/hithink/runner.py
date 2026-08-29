from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from time import perf_counter, sleep

from app.hithink.api import HithinkApiError, HithinkClient
from app.hithink.raw_collector import RawCollectionResult, RawCollector
from app.hithink.schedule import SHANGHAI, ScheduleNode, build_daily_schedule
from app.hithink.state_deriver import DerivationResult, StateDeriver
from app.hithink.writer import ClickHouseWriter

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
ACTIVE_NODE_PATH = ROOT / ".runtime" / "hithink_snapshot_active_node.json"


class CollectorRunner:
    """One resident service with independently auditable raw and derivation stages."""

    def __init__(self, api: HithinkClient, writer: ClickHouseWriter):
        self.api = api
        self.writer = writer
        self.raw_collector = RawCollector(api, writer)
        self.state_deriver = StateDeriver(writer)

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

    def run_day(self, trade_date: object, not_before: time | None = None) -> None:
        nodes = self.initialize_day(trade_date)
        status = self.writer.statuses(trade_date)
        now = datetime.now(SHANGHAI)
        for node in nodes:
            if status.get(node.scheduled_time) != "PENDING":
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
            raise RuntimeError("Closing baseline is only allowed after the 15:00 planned node")
        if self.writer.statuses(trade_date).get(node.scheduled_time) == "SUCCESS":
            LOG.info("Closing baseline already exists collection_id=%s", node.collection_id)
            return
        self.run_node(node)

    def serve_forever(self) -> None:
        startup_now = datetime.now(SHANGHAI)
        startup_date = startup_now.date()
        decision = self._wait_for_trading_day_decision(startup_date)
        if decision:
            nodes = self.initialize_day(startup_date)
            LOG.info(
                "STARTUP_PLAN_READY date=%s nodes=%s first=%s last=%s",
                startup_date,
                len(nodes),
                nodes[0].scheduled_time,
                nodes[-1].scheduled_time,
            )
        else:
            LOG.info("STARTUP_NON_TRADING_DAY date=%s; no schedule generated", startup_date)

        while True:
            now = datetime.now(SHANGHAI)
            prepare_at = datetime.combine(now.date(), time(8, 50), tzinfo=SHANGHAI)
            if now < prepare_at:
                sleep((prepare_at - now).total_seconds())
                decision = None
                continue
            if decision is None or now.date() != startup_date:
                decision = self._wait_for_trading_day_decision(now.date())
            if decision:
                self.run_day(now.date())
            else:
                LOG.info("NON_TRADING_DAY date=%s; no schedule generated", now.date())
            next_prepare = datetime.combine(
                now.date() + timedelta(days=1), time(8, 50), tzinfo=SHANGHAI
            )
            sleep(max(1.0, (next_prepare - datetime.now(SHANGHAI)).total_seconds()))
            decision = None

    def _wait_for_trading_day_decision(self, trade_date: object) -> bool:
        while True:
            decision = self._trading_day_decision(trade_date)
            if decision is not None:
                return decision
            LOG.error("CALENDAR_UNKNOWN date=%s; retrying in 5 minutes", trade_date)
            sleep(300)

    def _trading_day_decision(self, trade_date: object) -> bool | None:
        try:
            calendar = self.api.fetch_trading_days()
            self.writer.cache_trading_calendar(calendar.trade_dates, trade_date)
            return trade_date in calendar.trade_dates
        except HithinkApiError as exc:
            cached = self.writer.cached_trading_day(trade_date)
            if cached is None:
                LOG.error("calendar refresh failed without cache: %s", exc)
                return None
            LOG.warning("calendar refresh failed; using cached decision=%s", cached)
            return cached

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
            sector_index_status="RUNNING",
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

        self._mark_success(
            node, raw_values, raw_completed_at, derivation_started_at, started, derived
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
        return values

    def _mark_success(
        self,
        node: ScheduleNode,
        raw_values: dict[str, object],
        raw_completed_at: datetime,
        derivation_started_at: datetime,
        started: float,
        derived: DerivationResult,
    ) -> None:
        derivation_completed_at = datetime.now(SHANGHAI)
        self.writer.set_schedule_status(
            node,
            "SUCCESS" if raw_values["raw_status"] == "SUCCESS" else "PARTIAL",
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
        self.writer.set_schedule_status(
            node,
            "FAILED",
            batch_id=batch_id,
            request_start_time=started_at,
            request_end_time=datetime.now(SHANGHAI),
            raw_status="FAILED",
            derivation_status="BLOCKED",
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
            all_a_snapshot_status="SKIPPED",
            limit_up_pool_status="SKIPPED",
            limit_down_pool_status="SKIPPED",
            limit_break_pool_status="SKIPPED",
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
