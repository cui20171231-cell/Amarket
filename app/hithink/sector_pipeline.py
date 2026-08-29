from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from app.hithink.schedule import ScheduleNode
from app.hithink.writer import ClickHouseWriter


@dataclass(frozen=True)
class SectorPipelineResult:
    index_rows: int
    index_duration_ms: int
    state_rows: int
    state_duration_ms: int


class SectorPipeline:
    """Derive the three board-state tables from facts already stored locally."""

    def __init__(self, writer: ClickHouseWriter):
        self.writer = writer

    def derive_states(
        self,
        node: ScheduleNode,
        sectors: list[tuple[str, str, str, str]],
        limit_pool_available: bool,
        comparison_enabled: bool,
        previous_collection_id: str | None,
        previous_limit_break_collection_id: str | None,
        allow_gap_comparison: bool,
        previous_trade_day_enabled: bool,
    ) -> SectorPipelineResult:
        if not sectors:
            raise RuntimeError("No active concept, industry, or style sectors exist")

        started = perf_counter()
        state_rows = 0
        for sector_type in ("concept", "industry", "style"):
            expected = sum(sector[2] == sector_type for sector in sectors)
            self.writer.upsert_sector_state(
                node,
                sector_type,
                limit_pool_available,
                comparison_enabled,
                previous_collection_id,
                previous_limit_break_collection_id,
                allow_gap_comparison,
                previous_trade_day_enabled,
            )
            actual = self.writer.sector_state_count(node, sector_type)
            if actual != expected:
                raise RuntimeError(
                    f"{sector_type} state row mismatch for {node.collection_id}: {actual}/{expected}"
                )
            state_rows += actual
        return SectorPipelineResult(
            index_rows=0,
            index_duration_ms=0,
            state_rows=state_rows,
            state_duration_ms=round((perf_counter() - started) * 1000),
        )
