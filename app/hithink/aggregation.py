from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

from app.hithink.schedule import MARKET_REVIEW_NODE_SEQUENCES, SHANGHAI, ScheduleNode
from app.hithink.writer import ClickHouseWriter
from app.market_state_package import MarketStatePackageBuilder

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "data" / "market_state_packages"
# Public aggregation name retained for status and review-package callers.
TARGET_NODE_SEQUENCES = MARKET_REVIEW_NODE_SEQUENCES


@dataclass(frozen=True)
class AggregationResult:
    success: bool
    package_path: str | None = None


class MarketAggregationPipeline:
    """Build one of the 19 local market-state packages."""

    def __init__(
        self,
        writer: ClickHouseWriter,
        package_root: Path = PACKAGE_ROOT,
    ):
        self.writer = writer
        self.package_root = package_root

    @staticmethod
    def is_target_node(node: ScheduleNode) -> bool:
        return node.sequence_no in TARGET_NODE_SEQUENCES

    @classmethod
    def initial_status_values(cls, node: ScheduleNode) -> dict[str, object]:
        status = "PENDING" if cls.is_target_node(node) else "SKIPPED"
        return {
            "market_package_status": status,
            "market_package_path": None,
            "market_package_bytes": None,
            "market_package_duration_ms": None,
            "market_package_error_code": None,
            "market_package_error_message": None,
        }

    @classmethod
    def blocked_status_values(
        cls, node: ScheduleNode, error_code: str, error_message: str
    ) -> dict[str, object]:
        values = cls.initial_status_values(node)
        if cls.is_target_node(node):
            message = error_message[:1000]
            values.update(
                market_package_status="BLOCKED",
                market_package_error_code=error_code,
                market_package_error_message=message,
            )
        return values

    def block(self, node: ScheduleNode, error_code: str, error_message: str) -> None:
        if not self.is_target_node(node):
            return
        self.writer.set_schedule_status(
            node,
            "RUNNING",
            **self.blocked_status_values(node, error_code, error_message),
        )

    def run(self, node: ScheduleNode) -> AggregationResult:
        if not self.is_target_node(node):
            return AggregationResult(success=True)

        package_path = self._build_package(node)
        return AggregationResult(
            success=package_path is not None,
            package_path=str(package_path) if package_path is not None else None,
        )

    def _build_package(self, node: ScheduleNode) -> Path | None:
        started = perf_counter()
        self._set_task(node, "market_package", "RUNNING")
        try:
            package = MarketStatePackageBuilder(self.writer.client).build(
                str(node.trade_date), node.scheduled_time.strftime("%H:%M:%S")
            )
            if package.get("package_id") != node.collection_id:
                raise RuntimeError(
                    f"package target mismatch: {package.get('package_id')}/{node.collection_id}"
                )
            package["generated_at"] = datetime.now(SHANGHAI).isoformat()
            output_dir = self.package_root / node.trade_date.strftime("%Y%m%d")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{node.collection_id}.json"
            temporary_path = output_dir / f".{node.collection_id}.{os.getpid()}.tmp"
            data = json.dumps(package, ensure_ascii=False, separators=(",", ":"))
            temporary_path.write_text(data, encoding="utf-8")
            temporary_path.replace(output_path)
            size = output_path.stat().st_size
            self._set_task(
                node,
                "market_package",
                "SUCCESS",
                duration_ms=round((perf_counter() - started) * 1000),
                path=str(output_path),
                byte_count=size,
            )
            return output_path
        except Exception as exc:
            LOG.exception("MARKET_PACKAGE_FAILED sequence=%s", node.sequence_no)
            self._set_task(
                node,
                "market_package",
                "FAILED",
                duration_ms=round((perf_counter() - started) * 1000),
                error_code="MARKET_PACKAGE_ERROR",
                error_message=str(exc),
            )
            return None

    def _set_task(
        self,
        node: ScheduleNode,
        task: str,
        status: str,
        duration_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        path: str | None = None,
        byte_count: int | None = None,
    ) -> None:
        if task != "market_package":
            raise ValueError(f"unknown aggregation task: {task}")
        values: dict[str, object] = {
            "market_package_status": status,
            "market_package_path": path,
            "market_package_bytes": byte_count,
            "market_package_duration_ms": duration_ms,
            "market_package_error_code": error_code,
            "market_package_error_message": error_message[:1000]
            if error_message
            else None,
        }
        self.writer.set_schedule_status(node, "RUNNING", **values)
