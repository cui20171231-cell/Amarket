from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from time import perf_counter, sleep
from uuid import uuid4

from app.hithink.api import RETRYABLE_CODES, HithinkApiError, HithinkClient
from app.hithink.schedule import SHANGHAI
from app.hithink.writer import ClickHouseWriter

LOG = logging.getLogger(__name__)

CATALOG = "market.sector_catalog"
MEMBERSHIP = "market.sector_membership_history"
STATUS = "market.sector_mapping_sync_status"
TYPES = {"cn_concept": "concept", "industry": "industry", "tszs": "style"}


@dataclass(frozen=True)
class Sector:
    code: str
    name: str


@dataclass(frozen=True)
class Constituents:
    sector: Sector
    timestamp: int | None
    items: list[dict[str, str]]


class SectorMappingSync:
    def __init__(self, api: HithinkClient, writer: ClickHouseWriter, workers: int = 4):
        self.api = api
        self.writer = writer
        self.workers = workers

    def sync_all(self) -> str:
        started = datetime.now(SHANGHAI)
        sync_date = started.date()
        run_id = f"sector-{started:%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
        self.writer.initialize_daily_task_plan(sync_date)
        for task_name in ("sector_catalog_sync", "sector_membership_sync"):
            self.writer.set_daily_status(
                sync_date, task_name, "RUNNING", request_start_time=started
            )
        try:
            for source_tag, sector_type in TYPES.items():
                self.sync_type(run_id, source_tag, sector_type)
            self._finish_daily_plan(sync_date, run_id, started)
            return run_id
        except Exception as exc:
            completed = datetime.now(SHANGHAI)
            for task_name in ("sector_catalog_sync", "sector_membership_sync"):
                self.writer.set_daily_status(
                    sync_date,
                    task_name,
                    "FAILED",
                    request_start_time=started,
                    request_end_time=completed,
                    duration_ms=round((completed - started).total_seconds() * 1000),
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:1000],
                )
            raise

    def _finish_daily_plan(self, sync_date: date, run_id: str, started: datetime) -> None:
        rows = self.writer.client.query(
            f"""
            SELECT status, sector_count, sector_failed_count, membership_count,
                   added_count, removed_count, error_code, error_message
            FROM {STATUS} FINAL
            WHERE sync_run_id = {{run_id:String}}
            """,
            parameters={"run_id": run_id},
        ).result_rows
        completed = datetime.now(SHANGHAI)
        duration_ms = round((completed - started).total_seconds() * 1000)
        catalog_failed = any(row[6] == "CATALOG_ERROR" for row in rows)
        membership_partial = any(row[0] != "SUCCESS" for row in rows)
        messages = [str(row[7]) for row in rows if row[7]]
        self.writer.set_daily_status(
            sync_date,
            "sector_catalog_sync",
            "FAILED" if catalog_failed else "SUCCESS",
            request_start_time=started,
            request_end_time=completed,
            download_rows=sum(int(row[1]) for row in rows),
            duration_ms=duration_ms,
            error_code="CATALOG_ERROR" if catalog_failed else None,
            error_message="; ".join(messages)[:1000] if catalog_failed else None,
        )
        self.writer.set_daily_status(
            sync_date,
            "sector_membership_sync",
            "PARTIAL" if membership_partial else "SUCCESS",
            request_start_time=started,
            request_end_time=completed,
            download_rows=sum(int(row[3]) for row in rows),
            insert_rows=sum(int(row[3]) + int(row[5]) for row in rows),
            updated_rows=sum(int(row[4]) + int(row[5]) for row in rows),
            duration_ms=duration_ms,
            error_code="MEMBERSHIP_PARTIAL" if membership_partial else None,
            error_message="; ".join(messages)[:1000] if membership_partial else None,
        )

    def sync_type(self, run_id: str, source_tag: str, sector_type: str) -> None:
        started = datetime.now(SHANGHAI)
        started_perf = perf_counter()
        sync_date = started.date()
        self._status(run_id, sync_date, sector_type, source_tag, "RUNNING", start_time=started)
        try:
            sectors, catalog_timestamp = self._catalog(source_tag)
        except Exception as exc:  # noqa: BLE001 - records catalog failure as task status
            self._status(
                run_id, sync_date, sector_type, source_tag, "FAILED", start_time=started,
                end_time=datetime.now(SHANGHAI), duration_ms=self._duration(started_perf),
                error_code="CATALOG_ERROR", error_message=str(exc)[:1000],
            )
            return

        successful: list[Constituents] = []
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="sector-sync") as pool:
            futures = {pool.submit(self._constituents, sector): sector for sector in sectors}
            for future in as_completed(futures):
                sector = futures[future]
                try:
                    successful.append(future.result())
                except Exception as exc:  # noqa: BLE001 - isolates a sector request failure
                    failures.append(f"{sector.code}: {exc}")
                    LOG.warning("sector constituents failed code=%s error=%s", sector.code, exc)

        is_complete = not failures
        added, removed, unchanged, membership_count = self._apply(
            sectors, successful, sector_type, source_tag, sync_date, catalog_timestamp, is_complete
        )
        state = "SUCCESS" if is_complete else "PARTIAL"
        self._status(
            run_id, sync_date, sector_type, source_tag, state,
            sector_count=len(sectors), sector_success_count=len(successful),
            sector_failed_count=len(failures), membership_count=membership_count,
            added_count=added, removed_count=removed, unchanged_count=unchanged,
            source_timestamp=catalog_timestamp, start_time=started,
            end_time=datetime.now(SHANGHAI), duration_ms=self._duration(started_perf),
            error_code="MEMBERSHIP_PARTIAL" if failures else None,
            error_message="; ".join(failures[:10]) if failures else None,
        )

    def _request(self, path: str, params: dict[str, str]) -> dict:
        last_error: HithinkApiError | None = None
        for attempt, pause in enumerate((0, 1, 2, 4, 8)):
            if pause:
                sleep(pause)
            try:
                response = self.api.client.get(f"https://fuyao.aicubes.cn{path}", params=params)
                payload = response.json()
                code = int(payload.get("code", response.status_code))
                if response.status_code >= 400 or code != 0:
                    raise HithinkApiError(code, str(payload.get("message", response.text[:300])))
                return payload
            except Exception as exc:  # noqa: BLE001 - normalizes transport and API errors
                error = exc if isinstance(exc, HithinkApiError) else HithinkApiError(None, str(exc))
                last_error = error
                if error.code not in RETRYABLE_CODES and error.code is not None:
                    break
        assert last_error is not None
        raise last_error

    def _catalog(self, source_tag: str) -> tuple[list[Sector], int | None]:
        payload = self._request("/api/a-share-index/catalog/ths-index-list", {"tag": source_tag})
        data = payload.get("data") or {}
        return (
            [Sector(str(item["thscode"]), str(item["name"])) for item in data.get("item") or []],
            int(data["timestamp"]) if data.get("timestamp") is not None else None,
        )

    def _constituents(self, sector: Sector) -> Constituents:
        payload = self._request(
            "/api/a-share-index/constituents/ths-stock-list", {"thscode": sector.code}
        )
        data = payload.get("data") or {}
        return Constituents(
            sector=sector,
            timestamp=int(data["timestamp"]) if data.get("timestamp") is not None else None,
            items=[
                {"thscode": str(item["thscode"]), "ticker": str(item["ticker"]), "name": str(item["name"])}
                for item in data.get("item") or []
            ],
        )

    def _apply(
        self,
        sectors: list[Sector],
        responses: list[Constituents],
        sector_type: str,
        source_tag: str,
        sync_date: date,
        catalog_timestamp: int | None,
        is_complete: bool,
    ) -> tuple[int, int, int, int]:
        now = datetime.now(SHANGHAI)
        existing_catalog = self._catalog_rows(sector_type)
        catalog_rows = []
        current_codes = {sector.code for sector in sectors}
        for sector in sectors:
            previous = existing_catalog.get(sector.code)
            catalog_rows.append((
                sector.code, sector.name, sector_type, source_tag, 1,
                previous["first_seen_at"] if previous else now, now, catalog_timestamp, now,
            ))
        if is_complete:
            for code, previous in existing_catalog.items():
                if code not in current_codes and previous["is_active"]:
                    catalog_rows.append((
                        code, previous["sector_name"], sector_type, previous["source_tag"], 0,
                        previous["first_seen_at"], previous["last_seen_at"],
                        previous["source_timestamp"], now,
                    ))
        self._insert(CATALOG, catalog_rows, [
            "sector_code", "sector_name", "sector_type", "source_tag", "is_active",
            "first_seen_at", "last_seen_at", "source_timestamp", "version_time",
        ])

        existing = self._active_memberships(sector_type)
        current: dict[tuple[str, str], tuple[dict[str, str], int | None]] = {}
        for response in responses:
            for item in response.items:
                current[(response.sector.code, item["thscode"])] = (item, response.timestamp)
        rows = []
        added = unchanged = removed = 0
        for key, (item, timestamp) in current.items():
            previous = existing.get(key)
            if previous:
                unchanged += 1
                rows.append((
                    key[0], sector_type, key[1], item["ticker"], item["name"],
                    previous["observed_from"], None, 1, previous["first_seen_at"], now, timestamp, now,
                ))
            else:
                added += 1
                rows.append((
                    key[0], sector_type, key[1], item["ticker"], item["name"],
                    sync_date, None, 1, now, now, timestamp, now,
                ))
        if is_complete:
            for key, previous in existing.items():
                if key not in current:
                    removed += 1
                    rows.append((
                        key[0], sector_type, key[1], previous["ticker"], previous["stock_name"],
                        previous["observed_from"], sync_date, 0, previous["first_seen_at"], now,
                        previous["source_timestamp"], now,
                    ))
        self._insert(MEMBERSHIP, rows, [
            "sector_code", "sector_type", "thscode", "ticker", "stock_name", "observed_from",
            "observed_to", "is_active", "first_seen_at", "last_confirmed_at", "source_timestamp",
            "version_time",
        ])
        return added, removed, unchanged, len(current)

    def _catalog_rows(self, sector_type: str) -> dict[str, dict]:
        columns = ["sector_code", "sector_name", "source_tag", "is_active", "first_seen_at", "last_seen_at", "source_timestamp"]
        result = self.writer.client.query(
            f"SELECT {', '.join(columns)} FROM {CATALOG} FINAL WHERE sector_type = {{type:String}}",
            parameters={"type": sector_type},
        )
        return {row[0]: dict(zip(columns, row)) for row in result.result_rows}

    def _active_memberships(self, sector_type: str) -> dict[tuple[str, str], dict]:
        columns = [
            "sector_code", "thscode", "ticker", "stock_name", "observed_from", "first_seen_at",
            "source_timestamp",
        ]
        result = self.writer.client.query(
            f"SELECT {', '.join(columns)} FROM {MEMBERSHIP} FINAL WHERE sector_type = {{type:String}} AND is_active = 1",
            parameters={"type": sector_type},
        )
        return {(row[0], row[1]): dict(zip(columns, row)) for row in result.result_rows}

    def get_sector_membership(self, thscode: str, target_date: date) -> list[tuple[str, str, str]]:
        memberships = self.writer.client.query(
            f"SELECT sector_type, sector_code FROM {MEMBERSHIP} FINAL "
            "WHERE thscode = {stock:String} AND observed_from <= {date:Date} "
            "AND (observed_to > {date:Date} OR observed_to IS NULL) "
            "ORDER BY sector_type, sector_code",
            parameters={"stock": thscode, "date": target_date},
        ).result_rows
        names = {
            (row[0], row[1]): row[2]
            for row in self.writer.client.query(
                f"SELECT sector_type, sector_code, sector_name FROM {CATALOG} FINAL"
            ).result_rows
        }
        return [(sector_type, code, names.get((sector_type, code), "")) for sector_type, code in memberships]

    def _status(self, run_id: str, sync_date: date, sector_type: str, source_tag: str, status: str, **values: object) -> None:
        defaults: dict[str, object] = {
            "sector_count": 0,
            "sector_success_count": 0,
            "sector_failed_count": 0,
            "membership_count": 0,
            "added_count": 0,
            "removed_count": 0,
            "unchanged_count": 0,
        }
        defaults.update(values)
        columns = ["sync_date", "sync_run_id", "sector_type", "source_tag", "status", *defaults]
        row = [sync_date, run_id, sector_type, source_tag, status, *defaults.values()]
        self.writer.client.insert(STATUS, [row], column_names=columns)

    @staticmethod
    def _duration(started: float) -> int:
        return round((perf_counter() - started) * 1000)

    def _insert(self, table: str, rows: list[tuple], columns: list[str]) -> None:
        if not rows:
            return
        for start in range(0, len(rows), 50_000):
            self.writer.client.insert(table, rows[start:start + 50_000], column_names=columns)
