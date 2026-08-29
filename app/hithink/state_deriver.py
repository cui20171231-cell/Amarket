from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from time import perf_counter

from app.hithink.derive import calculate
from app.hithink.schedule import ScheduleNode
from app.hithink.sector_pipeline import SectorPipeline
from app.hithink.writer import ClickHouseWriter


@dataclass(frozen=True)
class DerivationResult:
    derived_insert_count: int
    derived_insert_ms: int
    derive_ms: int
    sector_state_row_count: int
    sector_state_duration_ms: int


class StateDeriver:
    """Read one persisted collection ID and generate derived/state tables only."""

    def __init__(self, writer: ClickHouseWriter):
        self.writer = writer

    def derive(self, node: ScheduleNode, include_sector_states: bool = True) -> DerivationResult:
        """Use only facts already committed to ClickHouse for this collection ID."""
        raw_rows = self.writer.raw_rows_for_collection(node.collection_id)
        if not raw_rows:
            raise RuntimeError(f"No persisted raw rows for {node.collection_id}")
        sectors: list[tuple[str, str, str, str]] = []
        if include_sector_states:
            sectors = self.writer.active_sector_catalog()
            if not sectors:
                raise RuntimeError("No active concept, industry, or style sectors exist")
            index_fact_count = self.writer.sector_index_fact_count(node)
            if index_fact_count != len(sectors):
                raise RuntimeError(
                    f"Missing sector-index facts for {node.collection_id}: "
                    f"{index_fact_count}/{len(sectors)}"
                )
        is_continuous = node.session in {"continuous_am", "continuous_pm"}
        if is_continuous:
            previous = self.writer.latest_derived_before(node)
        elif node.sequence_no == 254:
            previous = self.writer.derived_for_successful_sequence(node, 250)
        else:
            previous = {}

        comparison_enabled = bool(previous) and (is_continuous or node.sequence_no == 254)
        previous_collection_id: str | None = None
        previous_time = None
        if previous:
            prior = next(iter(previous.values())).raw
            previous_collection_id = prior.collection_id
            if isinstance(previous_collection_id, bytes):
                previous_collection_id = previous_collection_id.decode("ascii")
            previous_time = prior.scheduled_time
        allow_gap_comparison = bool(
            previous_time and node.scheduled_time - previous_time != timedelta(minutes=1)
        )
        previous_trade_day_turnovers = (
            self.writer.previous_trade_day_turnovers(node) if node.sequence_no >= 12 else {}
        )
        previous_limit_break_collection_id = self.writer.latest_limit_break_collection_before(node)
        limit_pool_available = self.writer.limit_pool_collected(node)

        started = perf_counter()
        derived_rows = [
            calculate(
                row,
                previous.get(row.thscode),
                comparison_enabled,
                previous_trade_day_turnovers.get(row.thscode),
            )
            for row in raw_rows
        ]
        limit_up_codes = self.writer.limit_pool_codes(node, "up")
        limit_down_codes = self.writer.limit_pool_codes(node, "down")
        break_details = self.writer.limit_break_details(node)
        if limit_up_codes is not None or limit_down_codes is not None or break_details is not None:
            derived_rows = [
                replace(
                    row,
                    is_limit_up=(
                        int(row.raw.thscode in limit_up_codes)
                        if limit_up_codes is not None
                        else None
                    ),
                    is_limit_down=(
                        int(row.raw.thscode in limit_down_codes)
                        if limit_down_codes is not None
                        else None
                    ),
                    is_limit_break=(
                        int(row.raw.thscode in break_details)
                        if break_details is not None
                        else None
                    ),
                    limit_break_open_times=(
                        break_details.get(row.raw.thscode) if break_details is not None else None
                    ),
                )
                for row in derived_rows
            ]
        derive_ms = round((perf_counter() - started) * 1000)
        derived_insert_count, derived_insert_ms = self.writer.insert_derived_once(derived_rows)
        self.writer.upsert_market_state(
            node,
            limit_pool_available=limit_pool_available,
            derive_enabled=comparison_enabled,
            previous_collection_id=previous_collection_id,
            previous_limit_break_collection_id=previous_limit_break_collection_id,
            allow_gap_comparison=allow_gap_comparison,
            previous_trade_day_enabled=node.sequence_no >= 12,
        )
        if include_sector_states:
            sector_result = SectorPipeline(self.writer).derive_states(
                node=node,
                sectors=sectors,
                limit_pool_available=limit_pool_available,
                comparison_enabled=comparison_enabled,
                previous_collection_id=previous_collection_id,
                previous_limit_break_collection_id=previous_limit_break_collection_id,
                allow_gap_comparison=allow_gap_comparison,
                previous_trade_day_enabled=node.sequence_no >= 12,
            )
            sector_state_row_count = sector_result.state_rows
            sector_state_duration_ms = sector_result.state_duration_ms
        else:
            sector_state_row_count = 0
            sector_state_duration_ms = 0
        return DerivationResult(
            derived_insert_count=derived_insert_count,
            derived_insert_ms=derived_insert_ms,
            derive_ms=derive_ms,
            sector_state_row_count=sector_state_row_count,
            sector_state_duration_ms=sector_state_duration_ms,
        )
