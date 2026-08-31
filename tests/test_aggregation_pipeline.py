from __future__ import annotations

from datetime import date
from pathlib import Path

from app.hithink.aggregation import MarketAggregationPipeline
from app.hithink.post_derivation import PostDerivationPipeline
from app.hithink.schedule import build_daily_schedule


class FakeWriter:
    def __init__(self) -> None:
        self.statuses: list[dict[str, object]] = []

    def set_schedule_status(self, node, status, **values) -> None:
        self.statuses.append({"status": status, **values})


class RecordingAggregation(MarketAggregationPipeline):
    def __init__(self, writer: FakeWriter) -> None:
        super().__init__(writer)
        self.calls: list[str] = []

    def _build_package(self, node) -> Path:
        self.calls.append("market_package")
        return Path(f"{node.collection_id}.json")

def _node(sequence_no: int):
    return build_daily_schedule(date(2026, 8, 28))[sequence_no - 1]


def test_aggregation_is_a_separate_module_after_derivation() -> None:
    assert not hasattr(PostDerivationPipeline, "_build_package")
    assert not hasattr(PostDerivationPipeline, "_notify_ccchat")

    pipeline = RecordingAggregation(FakeWriter())
    result = pipeline.run(_node(72))

    assert result.success is True
    assert result.package_path == "20260828072.json"
    assert pipeline.calls == ["market_package"]


def test_non_target_node_does_not_build_a_package() -> None:
    pipeline = RecordingAggregation(FakeWriter())

    result = pipeline.run(_node(73))

    assert result.success is True
    assert pipeline.calls == []
    assert pipeline.initial_status_values(_node(73))["market_package_status"] == "SKIPPED"

def test_aggregation_has_no_external_notification_step() -> None:
    assert not hasattr(MarketAggregationPipeline, "_notify_ccchat")
    assert not any(
        "ccchat" in key
        for key in MarketAggregationPipeline.initial_status_values(_node(72))
    )
