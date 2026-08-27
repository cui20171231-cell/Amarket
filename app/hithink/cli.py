from __future__ import annotations

import argparse
import logging
from datetime import datetime, time
from pathlib import Path

from app.hithink.api import HithinkClient
from app.hithink.config import Settings
from app.hithink.daily_pipeline import DailyPipeline
from app.hithink.runner import CollectorRunner
from app.hithink.schedule import SHANGHAI
from app.hithink.sector_mapping import SectorMappingSync
from app.hithink.writer import ClickHouseWriter

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


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
        "--not-before", type=time.fromisoformat, help="e.g. 13:00; earlier nodes become MISSED"
    )
    sub.add_parser(
        "serve", help="stay online continuously and run each weekday's fixed schedule"
    )
    sub.add_parser("daily-auto", help="run the independent daily-K pipeline for its current schedule slot")
    sub.add_parser("sector-sync", help="synchronize concept, industry and style sector mappings")
    state = sub.add_parser(
        "market-state-backfill", help="recalculate state rows for real snapshots already collected"
    )
    state.add_argument("--trade-date", type=lambda value: datetime.fromisoformat(value).date())
    membership = sub.add_parser("sector-membership", help="query sector memberships valid on a date")
    membership.add_argument("thscode")
    membership.add_argument("target_date", type=lambda value: datetime.fromisoformat(value).date())
    return command


def main() -> None:
    args = parser().parse_args()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.command in {"serve", "daily-auto", "sector-sync"}:
        log_name = {
            "serve": "collector.log",
            "daily-auto": "daily_pipeline.log",
            "sector-sync": "sector_mapping.log",
        }[args.command]
        log_path = ROOT / "data" / "logs" / log_name
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.load()
    writer = ClickHouseWriter(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        settings.clickhouse_username,
        settings.clickhouse_password,
    )
    try:
        if args.command == "init-db":
            writer.initialize_schema(ROOT / "sql" / "hithink_snapshot.sql")
            LOG.info("schema initialized")
            return
        if args.command == "market-state-backfill":
            trade_date = args.trade_date or datetime.now(SHANGHAI).date()
            nodes = writer.market_state_nodes(trade_date)
            for node in nodes:
                writer.upsert_market_state(node)
            LOG.info("market state backfill completed date=%s nodes=%s", trade_date, len(nodes))
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
                CollectorRunner(api, writer).serve_forever()
                return
            trade_date = args.trade_date or datetime.now(SHANGHAI).date()
            CollectorRunner(api, writer).run_day(trade_date, args.not_before)
        finally:
            api.close()
    finally:
        writer.close()


if __name__ == "__main__":
    main()
