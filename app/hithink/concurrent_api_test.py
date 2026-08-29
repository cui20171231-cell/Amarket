from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
from time import perf_counter

from app.hithink.api import HithinkApiError, HithinkClient
from app.hithink.writer import ClickHouseWriter


@dataclass(frozen=True)
class InterfaceResult:
    name: str
    status: str
    duration_ms: int
    row_count: int | None
    retry_count: int | None
    error_code: int | None
    error_message: str | None


@dataclass(frozen=True)
class ConcurrentRound:
    round_no: int
    wall_clock_ms: int
    results: list[InterfaceResult]


class ConcurrentApiTester:
    """Read-only concurrency test; it never writes ClickHouse data or schedules."""

    def __init__(self, api: HithinkClient, writer: ClickHouseWriter):
        self.api = api
        self.writer = writer

    def run(self, trade_date: date, rounds: int) -> list[ConcurrentRound]:
        if rounds < 1:
            raise ValueError("rounds must be positive")
        sector_codes = [sector[0] for sector in self.writer.active_sector_catalog()]
        if not sector_codes:
            raise RuntimeError("No active sector codes are available for the index test")

        completed: list[ConcurrentRound] = []
        for round_no in range(1, rounds + 1):
            started = perf_counter()
            work: dict[str, Callable[[], object]] = {
                "all_a_snapshot": self.api.fetch,
                "limit_up_pool": lambda: self.api.fetch_limit_pool("limit-up-pool", trade_date),
                "limit_down_pool": lambda: self.api.fetch_limit_pool("limit-down-pool", trade_date),
                "sector_index_snapshot": lambda: self.api.fetch_sector_index_snapshot(sector_codes),
            }
            results: list[InterfaceResult] = []
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="hithink-api-test") as pool:
                futures = {pool.submit(call): name for name, call in work.items()}
                for future in as_completed(futures):
                    name = futures[future]
                    results.append(self._result(name, future))
            completed.append(
                ConcurrentRound(
                    round_no=round_no,
                    wall_clock_ms=round((perf_counter() - started) * 1000),
                    results=sorted(results, key=lambda result: result.name),
                )
            )
        return completed

    @staticmethod
    def _result(name: str, future: Future[object]) -> InterfaceResult:
        try:
            response = future.result()
            return InterfaceResult(
                name=name,
                status="SUCCESS",
                duration_ms=int(response.duration_ms),
                row_count=int(response.total),
                retry_count=int(response.retry_count),
                error_code=None,
                error_message=None,
            )
        except HithinkApiError as exc:
            return InterfaceResult(
                name=name,
                status="FAILED",
                duration_ms=0,
                row_count=None,
                retry_count=None,
                error_code=exc.code,
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - test must report every interface outcome
            return InterfaceResult(
                name=name,
                status="FAILED",
                duration_ms=0,
                row_count=None,
                retry_count=None,
                error_code=None,
                error_message=str(exc),
            )


def print_report(rounds: list[ConcurrentRound]) -> None:
    for result in rounds:
        print(
            json.dumps(
                {
                    "round": result.round_no,
                    "wall_clock_ms": result.wall_clock_ms,
                    "interfaces": [asdict(interface) for interface in result.results],
                },
                ensure_ascii=False,
            )
        )
