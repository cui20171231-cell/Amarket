from __future__ import annotations

from datetime import date

from app.hithink.aggregation import TARGET_NODE_SEQUENCES
from app.hithink.candidate_config import (
    CORE_SECTOR_CANDIDATE_LIMITS,
    CORE_SECTOR_CANDIDATE_MAX_ROWS,
)
from app.hithink.post_derivation import PostDerivationPipeline
from app.hithink.schedule import build_daily_schedule


class FakeWriter:
    def __init__(self) -> None:
        self.statuses: list[dict[str, object]] = []

    def set_schedule_status(self, node, status, **values) -> None:
        self.statuses.append({"status": status, **values})

    def active_sector_catalog(self) -> list[tuple[str, str, str, str]]:
        return [("S1", "板块", "concept", "source")]


class RecordingPipeline(PostDerivationPipeline):
    def __init__(self, writer: FakeWriter) -> None:
        super().__init__(writer)
        self.calls: list[str] = []

    def _derive_emotion(self, node) -> bool:
        self.calls.append("emotion")
        return True

    def _derive_sql_task(self, node, task, **kwargs) -> bool:
        self.calls.append(task)
        return True

    def _derive_core_stocks(self, node) -> bool:
        self.calls.append("core_stock_candidate")
        return True

def _node(sequence_no: int):
    return build_daily_schedule(date(2026, 8, 28))[sequence_no - 1]


def test_fixed_target_axis_contains_the_19_business_nodes() -> None:
    assert TARGET_NODE_SEQUENCES == {
        11,
        12,
        27,
        42,
        57,
        72,
        87,
        102,
        117,
        132,
        133,
        148,
        163,
        178,
        193,
        208,
        223,
        238,
        254,
    }
    assert [
        _node(value).scheduled_time.strftime("%H:%M:%S")
        for value in sorted(TARGET_NODE_SEQUENCES)
    ] == [
        "09:25:15",
        "09:30:15",
        "09:45:15",
        "10:00:15",
        "10:15:15",
        "10:30:15",
        "10:45:15",
        "11:00:15",
        "11:15:15",
        "11:30:15",
        "13:00:15",
        "13:15:15",
        "13:30:15",
        "13:45:15",
        "14:00:15",
        "14:15:15",
        "14:30:15",
        "14:45:15",
        "15:00:00",
    ]


def test_post_derivatives_run_in_fixed_dependency_order() -> None:
    pipeline = RecordingPipeline(FakeWriter())

    result = pipeline.run(_node(72), sector_states_ready=True)

    assert result.success is True
    assert pipeline.calls == [
        "emotion",
        "market_delta_15m",
        "capital_migration",
        "core_sector_candidate",
        "core_stock_candidate",
    ]


def test_non_target_node_only_generates_emotion() -> None:
    pipeline = RecordingPipeline(FakeWriter())

    result = pipeline.run(_node(73), sector_states_ready=True)

    assert result.success is True
    assert pipeline.calls == ["emotion"]
    initial = pipeline.initial_status_values(_node(73))
    assert initial["emotion_state_status"] == "PENDING"
    assert initial["market_delta_15m_status"] == "SKIPPED"
    assert "ccchat_notify_status" not in initial


def test_core_sector_candidate_limits_are_v1_parameters() -> None:
    assert CORE_SECTOR_CANDIDATE_LIMITS == {"concept": 20, "industry": 15}
    assert CORE_SECTOR_CANDIDATE_MAX_ROWS == 35


def test_table_count_reads_the_numeric_cell_not_first_item_mapping() -> None:
    class QueryResult:
        def __init__(self) -> None:
            self.first_item = {"count()": 7}
            self.result_rows = [(7,)]

    class Client:
        def query(self, *args, **kwargs):
            return QueryResult()

    writer = FakeWriter()
    writer.client = Client()

    assert PostDerivationPipeline(writer)._table_count("market.example", _node(27)) == 7
