from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time
from time import monotonic, perf_counter, sleep

from app.hithink.api import (
    AUCTION_SNAPSHOT_MAX_CODES,
    AuctionApiSnapshot,
    HithinkClient,
)
from app.hithink.schedule import SHANGHAI, ScheduleNode
from app.hithink.writer import ClickHouseWriter

LOG = logging.getLogger(__name__)
AUCTION_UNIVERSE_REFRESH_TIME = time(9, 13, 50)
AUCTION_UNIVERSE_REFRESH_BUDGET_SECONDS = 30.0
AUCTION_FIRST_TIME = time(9, 15)
AUCTION_LAST_TIME = time(9, 25)
AUCTION_MAX_LATENESS_SECONDS = 2.0
AUCTION_COLLECTION_BUDGET_SECONDS = 45.0
AUCTION_MAX_WORKERS = 64


def build_opening_auction_schedule(trade_date: date) -> list[ScheduleNode]:
    return [
        ScheduleNode(
            trade_date=trade_date,
            scheduled_time=datetime.combine(
                trade_date,
                time(9, minute),
                tzinfo=SHANGHAI,
            ),
            session="auction_open",
            sequence_no=minute - 14,
        )
        for minute in range(15, 26)
    ]


@dataclass(frozen=True)
class AuctionCollectionResult:
    node: ScheduleNode
    requested_count: int
    received_count: int
    inserted_count: int
    insert_ms: int
    duration_ms: int
    status: str
    failed_batches: tuple[str, ...]


class AuctionCollector:
    def __init__(self, api: HithinkClient, writer: ClickHouseWriter):
        self.api = api
        self.writer = writer

    def collect(
        self,
        node: ScheduleNode,
        thscodes: list[str],
    ) -> AuctionCollectionResult:
        codes = list(dict.fromkeys(thscodes))
        batches = [
            codes[offset : offset + AUCTION_SNAPSHOT_MAX_CODES]
            for offset in range(0, len(codes), AUCTION_SNAPSHOT_MAX_CODES)
        ]
        if not batches:
            raise RuntimeError("Opening auction universe is empty")

        stage = "final" if node.sequence_no == 11 else "live"
        started = perf_counter()
        deadline = monotonic() + AUCTION_COLLECTION_BUDGET_SECONDS
        snapshots: list[AuctionApiSnapshot] = []
        failures: list[str] = []
        with ThreadPoolExecutor(
            max_workers=min(AUCTION_MAX_WORKERS, len(batches)),
            thread_name_prefix="auction-snapshot",
        ) as executor:
            futures = {
                executor.submit(
                    self.api.fetch_auction_snapshot,
                    batch,
                    stage,
                    deadline=deadline,
                    rate_limit_deadline=deadline,
                ): index
                for index, batch in enumerate(batches, 1)
            }
            for future in as_completed(futures):
                batch_index = futures[future]
                try:
                    snapshot = future.result()
                    if snapshot.source_time.date() != node.trade_date:
                        raise RuntimeError(
                            f"source date {snapshot.source_time.date()} does not match "
                            f"trade date {node.trade_date}"
                        )
                    snapshots.append(snapshot)
                except Exception as exc:  # noqa: BLE001 - preserve successful batches.
                    failures.append(f"batch {batch_index}: {type(exc).__name__}: {exc}")

        received = sum(snapshot.total for snapshot in snapshots)
        status = "SUCCESS" if not failures and received == len(codes) else "PARTIAL"
        error_code = None if status == "SUCCESS" else "AUCTION_BATCH_INCOMPLETE"
        error_message = None if status == "SUCCESS" else "; ".join(failures)[:1000]
        completed_at = datetime.now(SHANGHAI)
        duration_ms = round((perf_counter() - started) * 1000)
        batch_id = f"{node.collection_id}-auction"
        inserted = 0
        insert_ms = 0
        if snapshots:
            inserted, insert_ms = self.writer.insert_auction_snapshot_once(
                node,
                snapshots,
                batch_id=batch_id,
                status=status,
                raw_completed_at=completed_at,
                total_duration_ms=duration_ms,
                error_code=error_code,
                error_message=error_message,
            )
        return AuctionCollectionResult(
            node=node,
            requested_count=len(codes),
            received_count=received,
            inserted_count=inserted,
            insert_ms=insert_ms,
            duration_ms=round((perf_counter() - started) * 1000),
            status=status,
            failed_batches=tuple(failures),
        )


class AuctionCollectorRunner:
    """Opening-auction worker hosted by the resident snapshot collector."""

    def __init__(self, api: HithinkClient, writer: ClickHouseWriter):
        self.api = api
        self.writer = writer
        self.collector = AuctionCollector(api, writer)

    def _request_current_universe(
        self,
        trade_date: date,
        request_name: str,
        *,
        deadline: float | None = None,
    ) -> list[str]:
        snapshot = self.api.fetch(
            deadline=deadline,
            rate_limit_deadline=deadline,
        )
        codes = list(dict.fromkeys(str(item["thscode"]) for item in snapshot.items))
        if not codes:
            raise RuntimeError("all-A API returned an empty universe")
        LOG.info(
            "AUCTION_UNIVERSE_READY date=%s request=%s source=API stocks=%s",
            trade_date,
            request_name,
            len(codes),
        )
        return codes

    def _previous_close_universe(self, trade_date: date, refresh_error: Exception) -> list[str]:
        codes = self.writer.latest_all_a_codes_before(trade_date)
        if not codes:
            raise RuntimeError(
                f"Could not load yesterday's opening-auction universe: {refresh_error}"
            ) from refresh_error
        LOG.warning(
            "AUCTION_UNIVERSE_FALLBACK date=%s source=PREVIOUS_CLOSE stocks=%s "
            "refresh_error=%s",
            trade_date,
            len(codes),
            refresh_error,
        )
        return codes

    def _prepare_universe(self, trade_date: date) -> list[str]:
        refresh_at = datetime.combine(
            trade_date,
            AUCTION_UNIVERSE_REFRESH_TIME,
            tzinfo=SHANGHAI,
        )
        now = datetime.now(SHANGHAI)
        if now < refresh_at:
            try:
                self._request_current_universe(trade_date, "08:50_INITIAL")
            except Exception:
                LOG.exception(
                    "AUCTION_UNIVERSE_INITIAL_FAILED date=%s; the 09:13:50 refresh will continue",
                    trade_date,
                )
            delay = (refresh_at - datetime.now(SHANGHAI)).total_seconds()
            if delay > 0:
                sleep(delay)

        try:
            deadline = monotonic() + AUCTION_UNIVERSE_REFRESH_BUDGET_SECONDS
            return self._request_current_universe(
                trade_date,
                "09:13:50_REFRESH",
                deadline=deadline,
            )
        except Exception as exc:  # noqa: BLE001 - explicitly fall back to yesterday.
            return self._previous_close_universe(trade_date, exc)

    def run_day(self, trade_date: date) -> None:
        codes = self._prepare_universe(trade_date)
        for node in build_opening_auction_schedule(trade_date):
            if self.writer.auction_snapshot_count(node.collection_id):
                LOG.info(
                    "AUCTION_NODE_ALREADY_EXISTS collection_id=%s",
                    node.collection_id,
                )
                continue
            delay = (node.scheduled_time - datetime.now(SHANGHAI)).total_seconds()
            if delay > 0:
                sleep(delay)
            lateness = (datetime.now(SHANGHAI) - node.scheduled_time).total_seconds()
            if lateness > AUCTION_MAX_LATENESS_SECONDS:
                LOG.error(
                    "AUCTION_NODE_MISSED collection_id=%s scheduled_time=%s "
                    "lateness_seconds=%.3f; no late substitute was collected",
                    node.collection_id,
                    node.scheduled_time,
                    lateness,
                )
                continue
            result = self.collector.collect(node, codes)
            LOG.info(
                "AUCTION_NODE_COMPLETED collection_id=%s status=%s requested=%s "
                "received=%s inserted=%s duration_ms=%s failed_batches=%s",
                node.collection_id,
                result.status,
                result.requested_count,
                result.received_count,
                result.inserted_count,
                result.duration_ms,
                len(result.failed_batches),
            )
