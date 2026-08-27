from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from time import perf_counter, sleep

from app.hithink.api import HithinkApiError, HithinkClient
from app.hithink.derive import calculate
from app.hithink.models import RawSnapshot
from app.hithink.schedule import (
    SHANGHAI,
    ScheduleNode,
    allows_one_minute_derivation,
    build_daily_schedule,
)
from app.hithink.sector_pipeline import SectorPipeline
from app.hithink.writer import ClickHouseWriter

LOG = logging.getLogger(__name__)


class CollectorRunner:
    def __init__(self, api: HithinkClient, writer: ClickHouseWriter):
        self.api = api
        self.writer = writer

    def initialize_day(self, trade_date: object) -> list[ScheduleNode]:
        nodes = build_daily_schedule(trade_date)
        self.writer.initialize_schedule(nodes)
        return nodes

    def run_day(self, trade_date: object, not_before: time | None = None) -> None:
        nodes = self.initialize_day(trade_date)
        status = self.writer.statuses(trade_date)
        now = datetime.now(SHANGHAI)
        for index, node in enumerate(nodes):
            if status.get(node.scheduled_time) in {"SUCCESS", "FAILED", "MISSED"}:
                continue
            if not_before and node.scheduled_time.timetz().replace(tzinfo=None) < not_before:
                self.writer.set_schedule_status(node, "MISSED", error_code="NOT_BEFORE")
                continue
            if node.scheduled_time < now:
                self.writer.set_schedule_status(node, "MISSED", error_code="MISSED_STARTUP")
                continue
            delay = (node.scheduled_time - datetime.now(SHANGHAI)).total_seconds()
            if delay > 0:
                sleep(delay)
            previous = nodes[index - 1] if index else None
            self.run_node(node, previous)

    def run_closing_baseline(self, trade_date: object) -> None:
        if trade_date != datetime.now(SHANGHAI).date():
            raise RuntimeError("Closing baseline is only allowed for the current trading day")
        node = build_daily_schedule(trade_date)[-1]
        if datetime.now(SHANGHAI) < node.scheduled_time:
            raise RuntimeError("Closing baseline is only allowed after the 15:00 planned node")
        if (
            self.writer.statuses(trade_date).get(node.scheduled_time) == "SUCCESS"
            and self.writer.sector_pipeline_succeeded(node)
        ):
            LOG.info("Closing baseline already exists collection_id=%s", node.collection_id)
            return
        self.run_node(node, previous_node=None)

    def serve_forever(self) -> None:
        """Stay online; create a schedule only after calendar-gate confirmation."""
        while True:
            now = datetime.now(SHANGHAI)
            prepare_at = datetime.combine(now.date(), time(8, 50), tzinfo=SHANGHAI)
            if now < prepare_at:
                delay = (prepare_at - now).total_seconds()
                LOG.info("waiting %.0f seconds for the 08:50 calendar gate", delay)
                sleep(delay)
                continue

            decision = self._trading_day_decision(now.date())
            if decision is None:
                LOG.error("CALENDAR_UNKNOWN date=%s; no schedule will be generated", now.date())
                sleep(300)
                continue
            if decision:
                LOG.info("TRADING_DAY date=%s; preparing fixed schedule", now.date())
                self.run_day(now.date())
            else:
                LOG.info("NON_TRADING_DAY date=%s; no schedule generated", now.date())

            next_prepare = datetime.combine(
                now.date() + timedelta(days=1), time(8, 50), tzinfo=SHANGHAI
            )
            delay = max(1.0, (next_prepare - datetime.now(SHANGHAI)).total_seconds())
            LOG.info("waiting %.0f seconds for the next calendar gate", delay)
            sleep(delay)

    def _trading_day_decision(self, trade_date: object) -> bool | None:
        try:
            calendar = self.api.fetch_trading_days()
            self.writer.cache_trading_calendar(calendar.trade_dates, trade_date)
            decision = trade_date in calendar.trade_dates
            LOG.info(
                "calendar refreshed date=%s decision=%s days=%s duration_ms=%s retries=%s",
                trade_date,
                "TRADING_DAY" if decision else "NON_TRADING_DAY",
                len(calendar.trade_dates),
                calendar.duration_ms,
                calendar.retry_count,
            )
            return decision
        except HithinkApiError as exc:
            cached = self.writer.cached_trading_day(trade_date)
            if cached is None:
                LOG.error("calendar refresh failed without cache: %s", exc)
                return None
            LOG.warning("calendar refresh failed; using cached decision=%s: %s", cached, exc)
            return cached

    def run_node(self, node: ScheduleNode, previous_node: ScheduleNode | None) -> None:
        started_at = datetime.now(SHANGHAI)
        started = perf_counter()
        batch_id = node.collection_id
        self.writer.set_schedule_status(
            node, "RUNNING", batch_id=batch_id, request_start_time=started_at
        )
        try:
            # All three facts belong to this exact planned collection_id.  Fetching
            # them together minimizes timestamp skew without changing slot identity.
            with ThreadPoolExecutor(max_workers=3) as executor:
                snapshot_future = executor.submit(self.api.fetch)
                limit_up_future = executor.submit(
                    self.api.fetch_limit_pool, "limit-up-pool", node.trade_date
                )
                limit_down_future = executor.submit(
                    self.api.fetch_limit_pool, "limit-down-pool", node.trade_date
                )
                response = snapshot_future.result()
                limit_up = limit_up_future.result()
                limit_down = limit_down_future.result()
            raw_rows = [
                RawSnapshot.from_api(
                    item,
                    trade_date=node.trade_date,
                    collection_id=node.collection_id,
                    scheduled_time=node.scheduled_time,
                    source_timestamp=response.source_timestamp,
                    source_time=response.source_time,
                    session=node.session,
                    batch_id=batch_id,
                )
                for item in response.items
            ]
            raw_count, raw_ms = self.writer.insert_raw_once(raw_rows)
            permitted = allows_one_minute_derivation(node, previous_node)
            previous = (
                self.writer.previous_derived(previous_node) if permitted and previous_node else {}
            )
            derive_started = perf_counter()
            derived_rows = [
                calculate(row, previous.get(row.thscode), permitted) for row in raw_rows
            ]
            derive_ms = round((perf_counter() - derive_started) * 1000)
            derived_count, derived_ms = self.writer.insert_derived_once(derived_rows)
            self.writer.insert_limit_up_pool(node, batch_id, limit_up)
            self.writer.insert_limit_down_pool(node, batch_id, limit_down)
            self.writer.upsert_market_state(node, limit_pool_available=True)
            sector_values: dict[str, object]
            try:
                sector = SectorPipeline(self.api, self.writer).collect_and_derive(
                    node, limit_pool_available=True
                )
                sector_values = {
                    "sector_index_status": "SUCCESS",
                    "sector_index_received_count": sector.index_rows,
                    "sector_index_api_duration_ms": sector.index_duration_ms,
                    "sector_state_status": "SUCCESS",
                    "sector_state_row_count": sector.state_rows,
                    "sector_state_duration_ms": sector.state_duration_ms,
                }
            except Exception as exc:
                LOG.exception("SECTOR_FAILED sequence=%s", node.sequence_no)
                sector_values = {
                    "sector_index_status": "FAILED",
                    "sector_state_status": "FAILED",
                    "sector_error_code": "SECTOR_PIPELINE_ERROR",
                    "sector_error_message": str(exc)[:1000],
                }
            self.writer.set_schedule_status(
                node,
                "SUCCESS",
                batch_id=batch_id,
                request_start_time=started_at,
                request_end_time=datetime.now(SHANGHAI),
                source_timestamp=response.source_timestamp,
                source_time=response.source_time,
                api_code=response.code,
                api_total=response.total,
                received_count=len(raw_rows),
                api_duration_ms=response.duration_ms,
                raw_insert_count=raw_count,
                raw_insert_ms=raw_ms,
                derived_insert_count=derived_count,
                derive_ms=derive_ms + derived_ms,
                total_duration_ms=round((perf_counter() - started) * 1000),
                retry_count=response.retry_count,
                limit_pool_collected=1,
                limit_up_source_time=limit_up.source_time,
                limit_down_source_time=limit_down.source_time,
                limit_up_received_count=limit_up.total,
                limit_down_received_count=limit_down.total,
                limit_up_api_duration_ms=limit_up.duration_ms,
                limit_down_api_duration_ms=limit_down.duration_ms,
                **sector_values,
            )
            LOG.info(
                "SUCCESS sequence=%s count=%s total_ms=%s",
                node.sequence_no,
                len(raw_rows),
                round((perf_counter() - started) * 1000),
            )
        except HithinkApiError as exc:
            self.writer.set_schedule_status(
                node,
                "FAILED",
                batch_id=batch_id,
                request_start_time=started_at,
                request_end_time=datetime.now(SHANGHAI),
                error_code=str(exc.code),
                error_message=str(exc)[:1000],
                total_duration_ms=round((perf_counter() - started) * 1000),
            )
            LOG.exception("FAILED sequence=%s api_code=%s", node.sequence_no, exc.code)
        except Exception as exc:
            self.writer.set_schedule_status(
                node,
                "FAILED",
                batch_id=batch_id,
                request_start_time=started_at,
                request_end_time=datetime.now(SHANGHAI),
                error_code="COLLECTOR_ERROR",
                error_message=str(exc)[:1000],
                total_duration_ms=round((perf_counter() - started) * 1000),
            )
            LOG.exception("FAILED sequence=%s", node.sequence_no)
