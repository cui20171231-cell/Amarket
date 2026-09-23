from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path
from time import perf_counter, sleep

from app.hithink.api import HithinkClient
from app.hithink.auction_collector import AuctionCollectorRunner
from app.hithink.concurrent_api_test import ConcurrentApiTester, print_report
from app.hithink.config import Settings
from app.hithink.daily_pipeline import DailyPipeline
from app.hithink.models import RawSnapshot
from app.hithink.raw_collector import RawCollector
from app.hithink.runner import CollectorRunner
from app.hithink.schedule import SHANGHAI, build_daily_schedule
from app.hithink.sector_mapping import SectorMappingSync
from app.hithink.service_guard import SingleInstanceLock
from app.hithink.state_deriver import StateDeriver
from app.hithink.status import check_status, render_combined_status
from app.hithink.tencent_stock_basic import run_tencent_stock_basic_daily
from app.hithink.writer import ClickHouseWriter
from app.logging_utils import configure_bounded_root_logging

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_PID_PATH = ROOT / "data" / "hithink_snapshot_collector.pid"
COLLECTOR_LOCK_PATH = ROOT / ".runtime" / "hithink_snapshot_collector.lock"
TENCENT_STOCK_BASIC_LOCK_PATH = ROOT / ".runtime" / "tencent_stock_basic.lock"
LOG = logging.getLogger(__name__)


def _open_collector_writer(settings: Settings) -> ClickHouseWriter:
    """Wait for ClickHouse, then apply the idempotent schema at service start."""
    while True:
        writer: ClickHouseWriter | None = None
        try:
            writer = ClickHouseWriter(
                settings.clickhouse_host,
                settings.clickhouse_port,
                settings.clickhouse_database,
                settings.clickhouse_username,
                settings.clickhouse_password,
            )
            writer.initialize_schema(ROOT / "sql" / "hithink_snapshot.sql")
            writer.client.command("SELECT 1")
            LOG.info("STARTUP_READY ClickHouse connected and schema initialized")
            return writer
        except Exception as exc:  # noqa: BLE001 - service must keep retrying until DB is ready
            if writer is not None:
                try:
                    writer.close()
                except Exception:  # noqa: BLE001, S110 - cleanup must not stop retry
                    pass
            LOG.error(
                "STARTUP_WAIT ClickHouse is not ready; retrying in 60 seconds: %s: %s",
                type(exc).__name__,
                exc,
            )
            sleep(60)


def _run_daily_collection_with_fresh_connection(
    settings: Settings, trade_date: object
) -> bool:
    """Run daily collection independently from a possibly retrying node 254."""
    writer = ClickHouseWriter(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        settings.clickhouse_username,
        settings.clickhouse_password,
    )
    api = HithinkClient(settings.api_key)
    try:
        writer.initialize_daily_task_plan(trade_date)
        existing = writer.daily_status(trade_date, "daily_collection")
        if existing in {"SUCCESS", "SKIPPED"}:
            LOG.info(
                "DAILY_COLLECTION_ALREADY_CLOSED date=%s status=%s",
                trade_date,
                existing,
            )
            return True
        initialize = not writer.daily_seed_complete()
        DailyPipeline(api, writer).run_pipeline(trade_date, initialize=initialize)
        status = writer.daily_status(trade_date, "daily_collection")
        return status in {"SUCCESS", "SKIPPED"}
    finally:
        api.close()
        writer.close()


def _run_auction_collection_with_fresh_connection(
    settings: Settings, trade_date: object
) -> None:
    """Run opening-auction collection inside the resident service with isolated clients."""
    writer = ClickHouseWriter(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        settings.clickhouse_username,
        settings.clickhouse_password,
    )
    api = HithinkClient(settings.api_key)
    try:
        AuctionCollectorRunner(api, writer).run_day(trade_date)
    finally:
        api.close()
        writer.close()


def _collector_health_probe(
    settings: Settings, trade_date: object
) -> dict[str, object]:
    writer = ClickHouseWriter(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        settings.clickhouse_username,
        settings.clickhouse_password,
    )
    try:
        schedule = writer.client.query(
            """
            SELECT sequence_no, status
            FROM market.hithink_snapshot_schedule FINAL
            WHERE trade_date = {trade_date:Date}
              AND sequence_no IN (1, 254)
            """,
            parameters={"trade_date": trade_date},
        ).result_rows
        node_status = {int(sequence_no): str(status) for sequence_no, status in schedule}
        daily_plan_count = writer.client.query(
            """
            SELECT count()
            FROM market.hithink_daily_sync_status FINAL
            WHERE trade_date = {trade_date:Date}
              AND task_name IN (
                  'calendar_gate',
                  'tencent_stock_basic_sync',
                  'sector_catalog_sync',
                  'sector_membership_sync',
                  'hithink_daily_k_raw_sync',
                  'hithink_adjustment_events_sync'
              )
            """,
            parameters={"trade_date": trade_date},
        ).result_rows[0][0]
        schedule_count = writer.client.query(
            """
            SELECT count()
            FROM market.hithink_snapshot_schedule FINAL
            WHERE trade_date = {trade_date:Date}
            """,
            parameters={"trade_date": trade_date},
        ).result_rows[0][0]
        return {
            "calendar_status": writer.daily_status(trade_date, "calendar_gate"),
            "daily_plan_count": int(daily_plan_count),
            "schedule_count": int(schedule_count),
            "first_node_status": node_status.get(1),
            "closing_node_status": node_status.get(254),
            "daily_collection_status": writer.daily_status(
                trade_date, "daily_collection"
            ),
        }
    finally:
        writer.close()


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="hithink-snapshot")
    sub = command.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "init-db", help="explicitly create the market database and the three collector tables"
    )
    run = sub.add_parser(
        "run", help="generate today's 254-node plan and collect until market close"
    )
    run.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    run.add_argument(
        "--not-before", type=time.fromisoformat, help="e.g. 13:00; earlier nodes become SKIPPED"
    )
    sub.add_parser(
        "serve", help="stay online continuously and run each weekday's fixed schedule"
    )
    sub.add_parser("daily-auto", help="run the independent daily-K pipeline for its current schedule slot")
    sub.add_parser("sector-sync", help="synchronize concept, industry and style sector mappings")
    sub.add_parser(
        "tencent-stock-basic",
        help="collect the independent daily Tencent total-share and float-share snapshot",
    )
    sub.add_parser("status", help="run one local, read-only combined service and progress check")
    state = sub.add_parser(
        "market-state-backfill", help="recalculate state rows for real snapshots already collected"
    )
    state.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    emotion = sub.add_parser(
        "emotion-state-backfill",
        help="recalculate emotion rows from persisted limit-pool snapshots",
    )
    emotion.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    rebuild = sub.add_parser(
        "rederive-existing",
        help="rebuild individual and market derived tables from persisted raw snapshots",
    )
    rebuild.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    rebuild.add_argument("--start-sequence", type=int, default=1)
    rebuild.add_argument("--end-sequence", type=int, default=254)
    rebuild.add_argument(
        "--no-clear",
        action="store_true",
        help="keep already rebuilt outputs; used only for a bounded continuation run",
    )
    migrate = sub.add_parser(
        "migrate-collection-ids", help="backfill YYYYMMDD001-YYYYMMDD254 identifiers"
    )
    migrate.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    baseline = sub.add_parser(
        "closing-baseline",
        help="create the current day's one real post-close baseline at collection sequence 254",
    )
    baseline.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    raw_closing = sub.add_parser(
        "raw-all-a-closing-snapshot",
        help="write only one current all-A raw snapshot to the 15:30 collection ID",
    )
    raw_closing.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    sector_closing = sub.add_parser(
        "raw-sector-closing-snapshot",
        help="write only current concept, industry, and style index facts to the 15:30 collection ID",
    )
    sector_closing.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    pools_closing = sub.add_parser(
        "raw-limit-pools-closing-snapshot",
        help="write only current limit-up, limit-down, and limit-break raw pools to the 15:30 collection ID",
    )
    pools_closing.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    api_test = sub.add_parser(
        "concurrent-api-test",
        help="read-only concurrent test of all-A, limit pools, and sector-index APIs",
    )
    api_test.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    api_test.add_argument("--rounds", type=int, default=5)
    membership = sub.add_parser("sector-membership", help="query sector memberships valid on a date")
    membership.add_argument("thscode")
    membership.add_argument("target_date", type=lambda value: datetime.fromisoformat(value).date())
    return command


def main() -> None:
    args = parser().parse_args()
    if args.command == "status":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        print(render_combined_status(check_status()))
        return
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.command in {"serve", "daily-auto", "sector-sync", "tencent-stock-basic"}:
        log_name = {
            "serve": "collector.log",
            "daily-auto": "daily_pipeline.log",
            "sector-sync": "sector_mapping.log",
            "tencent-stock-basic": "tencent_stock_basic.log",
        }[args.command]
        log_path = ROOT / "data" / "logs" / log_name
        handlers = configure_bounded_root_logging(log_path)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    service_lock: SingleInstanceLock | None = None
    if args.command == "serve":
        service_lock = SingleInstanceLock(COLLECTOR_LOCK_PATH)
        service_lock.acquire()
        COLLECTOR_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        COLLECTOR_PID_PATH.write_text(str(os.getpid()), encoding="ascii")
        LOG.info("STARTUP_LOCK service=%s pid=%s", args.command, os.getpid())

    if args.command == "tencent-stock-basic":
        service_lock = SingleInstanceLock(TENCENT_STOCK_BASIC_LOCK_PATH)
        service_lock.acquire()
        LOG.info("STARTUP_LOCK service=%s pid=%s", args.command, os.getpid())

    settings = Settings.load(require_api_key=args.command != "tencent-stock-basic")
    writer = (
        _open_collector_writer(settings)
        if args.command == "serve"
        else ClickHouseWriter(
            settings.clickhouse_host,
            settings.clickhouse_port,
            settings.clickhouse_database,
            settings.clickhouse_username,
            settings.clickhouse_password,
        )
    )
    try:
        if args.command == "init-db":
            writer.initialize_schema(ROOT / "sql" / "hithink_snapshot.sql")
            LOG.info("schema initialized")
            return
        if args.command == "tencent-stock-basic":
            run_tencent_stock_basic_daily(writer)
            return
        if args.command == "market-state-backfill":
            trade_date = args.trade_date or datetime.now(SHANGHAI).date()
            nodes = writer.market_state_nodes(trade_date)
            for node in nodes:
                writer.upsert_market_state(node, writer.limit_pool_collected(node))
            LOG.info("market state backfill completed date=%s nodes=%s", trade_date, len(nodes))
            return
        if args.command == "emotion-state-backfill":
            trade_date = args.trade_date or datetime.now(SHANGHAI).date()
            nodes = writer.emotion_state_nodes(trade_date)
            node_statuses = writer.statuses(trade_date)
            for node in nodes:
                started = perf_counter()
                try:
                    writer.upsert_emotion_state(node)
                    row_count = writer.emotion_state_count(node)
                    if row_count != 1:
                        raise RuntimeError(
                            f"emotion state row mismatch for {node.collection_id}: {row_count}/1"
                        )
                    writer.set_schedule_status(
                        node,
                        node_statuses.get(node.scheduled_time, "PARTIAL"),
                        emotion_state_status="SUCCESS",
                        emotion_state_row_count=1,
                        emotion_state_duration_ms=round((perf_counter() - started) * 1000),
                        emotion_error_code=None,
                        emotion_error_message=None,
                    )
                except Exception as exc:
                    writer.set_schedule_status(
                        node,
                        node_statuses.get(node.scheduled_time, "PARTIAL"),
                        emotion_state_status="FAILED",
                        emotion_state_row_count=0,
                        emotion_state_duration_ms=round((perf_counter() - started) * 1000),
                        emotion_error_code="EMOTION_BACKFILL_ERROR",
                        emotion_error_message=str(exc)[:1000],
                    )
                    raise
            LOG.info("emotion state backfill completed date=%s nodes=%s", trade_date, len(nodes))
            return
        if args.command == "rederive-existing":
            trade_date = args.trade_date or datetime.now(SHANGHAI).date()
            nodes = [
                node
                for node in writer.raw_snapshot_nodes(trade_date)
                if args.start_sequence <= node.sequence_no <= args.end_sequence
            ]
            if not args.no_clear:
                writer.clear_rebuildable_derived_outputs(trade_date)
            deriver = StateDeriver(writer)
            sector_ready_nodes = 0
            for node in nodes:
                derivation_started_at = datetime.now(SHANGHAI)
                derivation_started = perf_counter()
                writer.set_schedule_status(
                    node,
                    "RUNNING",
                    derivation_status="RUNNING",
                    derivation_started_at=derivation_started_at,
                    derivation_completed_at=None,
                    derivation_duration_ms=None,
                    derivation_error_code=None,
                    derivation_error_message=None,
                )
                include_sector_states = bool(
                    writer.sector_index_fact_count(node) == len(writer.active_sector_catalog())
                )
                try:
                    deriver.derive(node, include_sector_states=include_sector_states)
                except Exception as exc:
                    derivation_completed_at = datetime.now(SHANGHAI)
                    writer.set_schedule_status(
                        node,
                        "PARTIAL",
                        derivation_status="FAILED",
                        derivation_started_at=derivation_started_at,
                        derivation_completed_at=derivation_completed_at,
                        derivation_duration_ms=round((perf_counter() - derivation_started) * 1000),
                        derivation_error_code="MANUAL_REDERIVE_ERROR",
                        derivation_error_message=str(exc)[:1000],
                    )
                    raise
                derivation_completed_at = datetime.now(SHANGHAI)
                writer.set_schedule_status(
                    node,
                    "SUCCESS",
                    derivation_status="SUCCESS",
                    derivation_started_at=derivation_started_at,
                    derivation_completed_at=derivation_completed_at,
                    derivation_duration_ms=round((perf_counter() - derivation_started) * 1000),
                )
                sector_ready_nodes += int(include_sector_states)
            LOG.info(
                "derived tables rebuilt date=%s nodes=%s sector_ready_nodes=%s",
                trade_date,
                len(nodes),
                sector_ready_nodes,
            )
            return
        if args.command == "migrate-collection-ids":
            trade_date = args.trade_date or datetime.now(SHANGHAI).date()
            writer.migrate_collection_ids(trade_date)
            LOG.info("collection IDs migrated date=%s", trade_date)
            return
        api = HithinkClient(settings.api_key)
        try:
            if args.command == "daily-auto":
                DailyPipeline(api, writer).run_auto()
                return
            if args.command == "sector-sync":
                run_id = SectorMappingSync(api, writer).sync_all()
                LOG.info("sector mapping synchronization completed run_id=%s", run_id)
                return
            if args.command == "sector-membership":
                for row in SectorMappingSync(api, writer).get_sector_membership(
                    args.thscode, args.target_date
                ):
                    print(*row, sep="\t")
                return
            if args.command == "serve":
                try:
                    CollectorRunner(
                        api,
                        writer,
                        daily_collection_job=lambda trade_date: (
                            _run_daily_collection_with_fresh_connection(
                                settings, trade_date
                            )
                        ),
                        auction_collection_job=lambda trade_date: (
                            _run_auction_collection_with_fresh_connection(
                                settings, trade_date
                            )
                        ),
                        health_probe=lambda trade_date: _collector_health_probe(
                            settings, trade_date
                        ),
                    ).serve_forever()
                finally:
                    try:
                        if COLLECTOR_PID_PATH.read_text(encoding="ascii").strip() == str(
                            os.getpid()
                        ):
                            COLLECTOR_PID_PATH.unlink(missing_ok=True)
                    except OSError:
                        LOG.warning("Could not remove collector PID file: %s", COLLECTOR_PID_PATH)
                return
            if args.command == "closing-baseline":
                trade_date = args.trade_date or datetime.now(SHANGHAI).date()
                CollectorRunner(api, writer).run_closing_baseline(trade_date)
                return
            if args.command == "raw-all-a-closing-snapshot":
                trade_date = args.trade_date or datetime.now(SHANGHAI).date()
                if trade_date != datetime.now(SHANGHAI).date():
                    raise RuntimeError("Raw closing snapshot is only allowed for the current date")
                node = build_daily_schedule(trade_date)[-1]
                if writer.raw_rows_for_collection(node.collection_id):
                    LOG.info("all-A raw snapshot already exists collection_id=%s", node.collection_id)
                    return
                started_at = datetime.now(SHANGHAI)
                started = perf_counter()
                snapshot = api.fetch()
                if snapshot.source_time.date() != trade_date:
                    raise RuntimeError(
                        f"API source date {snapshot.source_time.date()} does not match {trade_date}"
                    )
                batch_id = f"{node.collection_id}-manual-all-a"
                rows = [
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
                inserted, insert_ms = writer.insert_raw_once(rows)
                completed_at = datetime.now(SHANGHAI)
                writer.set_schedule_status(
                    node,
                    "PARTIAL",
                    raw_status="PARTIAL",
                    derivation_status="SKIPPED",
                    all_a_snapshot_status="SUCCESS",
                    raw_completed_at=completed_at,
                    request_start_time=started_at,
                    request_end_time=completed_at,
                    source_timestamp=snapshot.source_timestamp,
                    source_time=snapshot.source_time,
                    api_code=snapshot.code,
                    api_total=snapshot.total,
                    received_count=snapshot.total,
                    api_duration_ms=snapshot.duration_ms,
                    raw_insert_count=inserted,
                    raw_insert_ms=insert_ms,
                    total_duration_ms=round((perf_counter() - started) * 1000),
                )
                LOG.info(
                    "all-A raw closing snapshot written collection_id=%s source_time=%s rows=%s",
                    node.collection_id,
                    snapshot.source_time,
                    inserted,
                )
                return
            if args.command == "raw-sector-closing-snapshot":
                trade_date = args.trade_date or datetime.now(SHANGHAI).date()
                if trade_date != datetime.now(SHANGHAI).date():
                    raise RuntimeError("Raw sector snapshot is only allowed for the current date")
                node = build_daily_schedule(trade_date)[-1]
                sectors = writer.active_sector_catalog()
                if not sectors:
                    raise RuntimeError("No active concept, industry, or style sectors exist")
                existing_count = writer.sector_index_fact_count(node)
                if existing_count:
                    if existing_count != len(sectors):
                        raise RuntimeError(
                            f"Partial sector-index batch exists: {existing_count}/{len(sectors)}"
                        )
                    LOG.info("sector raw snapshot already exists collection_id=%s", node.collection_id)
                    return
                started = perf_counter()
                snapshot = api.fetch_sector_index_snapshot([sector[0] for sector in sectors])
                if any(record.source_time.date() != trade_date for record in snapshot.items):
                    raise RuntimeError("Sector API returned a source date other than the requested date")
                inserted, insert_ms = writer.insert_sector_index_once(node, sectors, snapshot)
                writer.set_schedule_status(
                    node,
                    "PARTIAL",
                    raw_status="PARTIAL",
                    derivation_status="SKIPPED",
                    sector_index_status="SUCCESS",
                    raw_completed_at=datetime.now(SHANGHAI),
                    sector_index_received_count=snapshot.total,
                    sector_index_api_duration_ms=snapshot.duration_ms,
                    total_duration_ms=round((perf_counter() - started) * 1000),
                )
                LOG.info(
                    "sector raw closing snapshot written collection_id=%s rows=%s source_time=%s insert_ms=%s",
                    node.collection_id,
                    inserted,
                    max(record.source_time for record in snapshot.items),
                    insert_ms,
                )
                return
            if args.command == "raw-limit-pools-closing-snapshot":
                trade_date = args.trade_date or datetime.now(SHANGHAI).date()
                if trade_date != datetime.now(SHANGHAI).date():
                    raise RuntimeError("Raw pool snapshot is only allowed for the current date")
                node = build_daily_schedule(trade_date)[-1]
                batch_id = node.collection_id
                started = perf_counter()
                up, down, broken = RawCollector(
                    api, writer
                ).collect_closing_pools_until_success(node, batch_id)
                up_count = up.total
                down_count = down.total
                break_count = broken.total
                writer.set_schedule_status(
                    node,
                    "SUCCESS",
                    raw_status="SUCCESS",
                    all_a_snapshot_status="SUCCESS",
                    limit_up_pool_status="SUCCESS",
                    limit_down_pool_status="SUCCESS",
                    limit_break_pool_status="SUCCESS",
                    sector_index_status="SUCCESS",
                    limit_pool_collected=1,
                    limit_up_source_time=up.source_time,
                    limit_down_source_time=down.source_time,
                    limit_break_source_time=broken.source_time,
                    limit_up_received_count=up.total,
                    limit_down_received_count=down.total,
                    limit_break_received_count=broken.total,
                    limit_up_api_duration_ms=up.duration_ms,
                    limit_down_api_duration_ms=down.duration_ms,
                    limit_break_api_duration_ms=broken.duration_ms,
                    raw_completed_at=datetime.now(SHANGHAI),
                    total_duration_ms=round((perf_counter() - started) * 1000),
                )
                LOG.info(
                    "closing pools written collection_id=%s up=%s down=%s break=%s",
                    node.collection_id, up_count, down_count, break_count,
                )
                return
            if args.command == "concurrent-api-test":
                trade_date = args.trade_date or datetime.now(SHANGHAI).date()
                print_report(ConcurrentApiTester(api, writer).run(trade_date, args.rounds))
                return
            trade_date = args.trade_date or datetime.now(SHANGHAI).date()
            CollectorRunner(api, writer).run_day(trade_date, args.not_before)
        finally:
            api.close()
    finally:
        writer.close()
        if service_lock is not None:
            service_lock.release()


if __name__ == "__main__":
    main()
