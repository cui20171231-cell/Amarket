from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from app.hithink.api import HithinkClient
from app.hithink.schedule import ScheduleNode
from app.hithink.writer import ClickHouseWriter


@dataclass(frozen=True)
class SectorPipelineResult:
    index_rows: int
    index_duration_ms: int
    state_rows: int
    state_duration_ms: int


class SectorPipeline:
    """Collect one sector-index fact set and derive all three sector state sets."""

    def __init__(self, api: HithinkClient, writer: ClickHouseWriter):
        self.api = api
        self.writer = writer

    def collect_and_derive(
        self, node: ScheduleNode, limit_pool_available: bool
    ) -> SectorPipelineResult:
        sectors = self.writer.active_sector_catalog()
        if not sectors:
            raise RuntimeError("No active concept, industry, or style sectors exist")
        snapshot = self.api.fetch_sector_index_snapshot([sector[0] for sector in sectors])
        self.writer.insert_sector_index_once(node, sectors, snapshot)

        started = perf_counter()
        state_rows = 0
        for sector_type in ("concept", "industry", "style"):
            expected = sum(sector[2] == sector_type for sector in sectors)
            self.writer.upsert_sector_state(node, sector_type, limit_pool_available)
            actual = self.writer.sector_state_count(node, sector_type)
            if actual != expected:
                raise RuntimeError(
                    f"{sector_type} state row mismatch for {node.collection_id}: {actual}/{expected}"
                )
            state_rows += actual
        return SectorPipelineResult(
            index_rows=snapshot.total,
            index_duration_ms=snapshot.duration_ms,
            state_rows=state_rows,
            state_duration_ms=round((perf_counter() - started) * 1000),
        )
