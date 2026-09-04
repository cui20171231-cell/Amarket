from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from clickhouse_connect.driver.external import ExternalData

from app.hithink.candidate_config import (
    CORE_SECTOR_CANDIDATE_LIMITS,
    CORE_SECTOR_CANDIDATE_MAX_ROWS,
)
from app.hithink.schedule import MARKET_REVIEW_NODE_SEQUENCES, ScheduleNode
from app.hithink.writer import ClickHouseWriter

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
SQL_ROOT = ROOT / "sql"

STOCK_SUMMARY_STRUCTURE = (
    "collection_id FixedString(11),thscode String,"
    "core_sector_count UInt16,core_concept_count UInt16,"
    "core_industry_count UInt16,core_style_count UInt16,"
    "best_core_sector_type String,best_core_sector_code String,"
    "best_core_sector_name String,best_core_sector_candidate_rank UInt16,"
    "best_sector_turnover_rank UInt16,best_sector_turnover_1m_rank UInt16,"
    "best_sector_price_rank UInt16,best_sector_turnover_share_pct Float64"
)


@dataclass(frozen=True)
class PostDerivationResult:
    success: bool


class PostDerivationPipeline:
    """Run persisted post-derivatives in dependency order."""

    def __init__(self, writer: ClickHouseWriter):
        self.writer = writer

    @staticmethod
    def is_target_node(node: ScheduleNode) -> bool:
        return node.sequence_no in MARKET_REVIEW_NODE_SEQUENCES

    @classmethod
    def initial_status_values(cls, node: ScheduleNode) -> dict[str, object]:
        target_status = "PENDING" if cls.is_target_node(node) else "SKIPPED"
        return {
            "emotion_state_status": "PENDING",
            "emotion_state_row_count": None,
            "emotion_state_duration_ms": None,
            "emotion_error_code": None,
            "emotion_error_message": None,
            "market_delta_15m_status": target_status,
            "market_delta_15m_row_count": None,
            "market_delta_15m_duration_ms": None,
            "market_delta_15m_error_code": None,
            "market_delta_15m_error_message": None,
            "capital_migration_status": target_status,
            "capital_migration_row_count": None,
            "capital_migration_duration_ms": None,
            "capital_migration_error_code": None,
            "capital_migration_error_message": None,
            "core_sector_candidate_status": target_status,
            "core_sector_candidate_row_count": None,
            "core_sector_candidate_duration_ms": None,
            "core_sector_candidate_error_code": None,
            "core_sector_candidate_error_message": None,
            "core_stock_candidate_status": target_status,
            "core_stock_candidate_row_count": None,
            "core_stock_candidate_duration_ms": None,
            "core_stock_candidate_error_code": None,
            "core_stock_candidate_error_message": None,
        }

    @classmethod
    def blocked_status_values(
        cls, node: ScheduleNode, error_code: str, error_message: str, block_emotion: bool
    ) -> dict[str, object]:
        values = cls.initial_status_values(node)
        message = error_message[:1000]
        if block_emotion:
            values.update(
                emotion_state_status="BLOCKED",
                emotion_error_code=error_code,
                emotion_error_message=message,
            )
        if cls.is_target_node(node):
            values.update(
                market_delta_15m_status="BLOCKED",
                market_delta_15m_error_code=error_code,
                market_delta_15m_error_message=message,
                capital_migration_status="BLOCKED",
                capital_migration_error_code=error_code,
                capital_migration_error_message=message,
                core_sector_candidate_status="BLOCKED",
                core_sector_candidate_error_code=error_code,
                core_sector_candidate_error_message=message,
                core_stock_candidate_status="BLOCKED",
                core_stock_candidate_error_code=error_code,
                core_stock_candidate_error_message=message,
            )
        return values

    def run_emotion_and_block(
        self, node: ScheduleNode, error_code: str, error_message: str
    ) -> PostDerivationResult:
        emotion_ok = self._derive_emotion(node)
        if self.is_target_node(node):
            values = self.blocked_status_values(
                node, error_code, error_message, block_emotion=False
            )
            values = {key: value for key, value in values.items() if not key.startswith("emotion_")}
            self.writer.set_schedule_status(node, "RUNNING", **values)
        return PostDerivationResult(success=False if self.is_target_node(node) else emotion_ok)

    def run(self, node: ScheduleNode, sector_states_ready: bool) -> PostDerivationResult:
        if not self._derive_emotion(node):
            self._block_after(node, "EMOTION_DERIVATION_FAILED", "情绪状态未成功生成")
            return PostDerivationResult(success=False)
        if not self.is_target_node(node):
            return PostDerivationResult(success=True)

        if not self._derive_sql_task(
            node,
            task="market_delta_15m",
            sql_name="hithink_market_delta_15m_backfill.sql",
            marker="FROM paired\nSETTINGS join_use_nulls = 1;",
            replacement=(
                "FROM paired\nWHERE toString(collection_id) = "
                "{target_collection_id:String}\nSETTINGS join_use_nulls = 1;"
            ),
            table="market.hithink_market_delta_15m",
            exact_rows=1,
        ):
            self._block_from(
                node, "capital_migration", "MARKET_DELTA_FAILED", "15分钟市场变化未成功生成"
            )
            return PostDerivationResult(success=False)

        # Intraday review nodes may use the latest complete sector-state minute.
        # The capital-migration SQL resolves and labels that source as FALLBACK.
        # The closing node remains strict and must use its own complete state.
        if not sector_states_ready and node.sequence_no == 254:
            self._block_from(
                node,
                "capital_migration",
                "SECTOR_STATES_UNAVAILABLE",
                "三类板块状态未完整生成",
            )
            return PostDerivationResult(success=False)

        expected_sectors = len(self.writer.active_sector_catalog())
        if not self._derive_sql_task(
            node,
            task="capital_migration",
            sql_name="hithink_sector_capital_migration_backfill.sql",
            marker="FROM paired_rows\nSETTINGS join_use_nulls = 1;",
            replacement=(
                "FROM paired_rows\nWHERE toString(collection_id) = "
                "{target_collection_id:String}\nSETTINGS join_use_nulls = 1;"
            ),
            table="market.hithink_sector_capital_migration",
            exact_rows=expected_sectors,
        ):
            self._block_from(
                node, "core_sector_candidate", "CAPITAL_MIGRATION_FAILED", "板块成交份额迁移未成功生成"
            )
            return PostDerivationResult(success=False)

        if not self._derive_sql_task(
            node,
            task="core_sector_candidate",
            sql_name="hithink_core_sector_candidate_backfill.sql",
            marker="FROM with_best WHERE candidate_rank IS NOT NULL;",
            replacement=(
                "FROM with_best WHERE candidate_rank IS NOT NULL "
                "AND toString(collection_id) = {target_collection_id:String};"
            ),
            table="market.hithink_core_sector_candidate",
            minimum_rows=0,
            maximum_rows=CORE_SECTOR_CANDIDATE_MAX_ROWS,
            extra_parameters={
                "concept_candidate_limit": CORE_SECTOR_CANDIDATE_LIMITS["concept"],
                "industry_candidate_limit": CORE_SECTOR_CANDIDATE_LIMITS["industry"],
            },
        ):
            self._block_from(
                node, "core_stock_candidate", "CORE_SECTOR_FAILED", "核心板块候选未成功生成"
            )
            return PostDerivationResult(success=False)

        if not self._derive_core_stocks(node):
            return PostDerivationResult(success=False)

        return PostDerivationResult(success=True)

    def _derive_emotion(self, node: ScheduleNode) -> bool:
        started = perf_counter()
        self.writer.set_schedule_status(node, "RUNNING", emotion_state_status="RUNNING")
        try:
            self.writer.upsert_emotion_state(node)
            row_count = self.writer.emotion_state_count(node)
            if row_count != 1:
                raise RuntimeError(
                    f"emotion state row mismatch for {node.collection_id}: {row_count}/1"
                )
            self.writer.set_schedule_status(
                node,
                "RUNNING",
                emotion_state_status="SUCCESS",
                emotion_state_row_count=1,
                emotion_state_duration_ms=round((perf_counter() - started) * 1000),
                emotion_error_code=None,
                emotion_error_message=None,
            )
            return True
        except Exception as exc:
            LOG.exception("EMOTION_STATE_FAILED sequence=%s", node.sequence_no)
            self.writer.set_schedule_status(
                node,
                "RUNNING",
                emotion_state_status="FAILED",
                emotion_state_row_count=0,
                emotion_state_duration_ms=round((perf_counter() - started) * 1000),
                emotion_error_code="EMOTION_DERIVATION_ERROR",
                emotion_error_message=str(exc)[:1000],
            )
            return False

    def _derive_sql_task(
        self,
        node: ScheduleNode,
        task: str,
        sql_name: str,
        marker: str,
        replacement: str,
        table: str,
        exact_rows: int | None = None,
        minimum_rows: int | None = None,
        maximum_rows: int | None = None,
        extra_parameters: dict[str, object] | None = None,
    ) -> bool:
        started = perf_counter()
        self._set_task(node, task, "RUNNING")
        try:
            sql = (SQL_ROOT / sql_name).read_text(encoding="utf-8")
            if sql.count(marker) != 1:
                raise RuntimeError(f"{sql_name} target filter marker mismatch")
            sql = sql.replace(marker, replacement, 1)
            parameters: dict[str, object] = {
                "trade_date": node.trade_date,
                "target_collection_id": node.collection_id,
            }
            if extra_parameters:
                parameters.update(extra_parameters)
            self.writer.client.command(sql, parameters=parameters)
            row_count = self._table_count(table, node)
            if exact_rows is not None and row_count != exact_rows:
                raise RuntimeError(f"{table} row mismatch: {row_count}/{exact_rows}")
            if minimum_rows is not None and row_count < minimum_rows:
                raise RuntimeError(f"{table} rows below minimum: {row_count}/{minimum_rows}")
            if maximum_rows is not None and row_count > maximum_rows:
                raise RuntimeError(f"{table} rows above maximum: {row_count}/{maximum_rows}")
            self._set_task(
                node,
                task,
                "SUCCESS",
                row_count=row_count,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            return True
        except Exception as exc:
            LOG.exception("%s_FAILED sequence=%s", task.upper(), node.sequence_no)
            self._set_task(
                node,
                task,
                "FAILED",
                row_count=0,
                duration_ms=round((perf_counter() - started) * 1000),
                error_code=f"{task.upper()}_ERROR",
                error_message=str(exc),
            )
            return False

    def _derive_core_stocks(self, node: ScheduleNode) -> bool:
        task = "core_stock_candidate"
        started = perf_counter()
        self._set_task(node, task, "RUNNING")
        try:
            summary_sql = (SQL_ROOT / "hithink_core_stock_candidate_summary.sql").read_text(
                encoding="utf-8"
            )
            insert_prefix = "INSERT INTO tmp_core_stock_summary\n"
            summary_tail = "FROM links GROUP BY collection_id,thscode;"
            if not summary_sql.startswith(insert_prefix) or summary_sql.count(summary_tail) != 1:
                raise RuntimeError("core stock summary SQL shape mismatch")
            summary_sql = summary_sql.removeprefix(insert_prefix).replace(
                summary_tail,
                (
                    "FROM links WHERE toString(collection_id) = "
                    "{target_collection_id:String} GROUP BY collection_id,thscode;"
                ),
                1,
            ).rstrip().removesuffix(";")
            parameters = {
                "trade_date": node.trade_date,
                "target_collection_id": node.collection_id,
            }
            summary_data = self.writer.client.raw_query(
                summary_sql, parameters=parameters, fmt="CSV"
            )
            external_data = ExternalData(
                file_name="tmp_core_stock_summary",
                data=summary_data,
                fmt="CSV",
                structure=STOCK_SUMMARY_STRUCTURE,
            )
            stock_sql = (SQL_ROOT / "hithink_core_stock_candidate_backfill.sql").read_text(
                encoding="utf-8"
            )
            marker = "FROM final_ranked WHERE is_eligible=1 AND candidate_rank<=50;"
            if stock_sql.count(marker) != 1:
                raise RuntimeError("core stock candidate target filter marker mismatch")
            stock_sql = stock_sql.replace(
                marker,
                (
                    "FROM final_ranked WHERE is_eligible=1 AND candidate_rank<=50 "
                    "AND toString(collection_id)={target_collection_id:String};"
                ),
                1,
            )
            self.writer.client.command(
                stock_sql,
                parameters=parameters,
                external_data=external_data,
            )
            row_count = self._table_count("market.hithink_core_stock_candidate", node)
            if not 0 <= row_count <= 50:
                raise RuntimeError(f"core stock candidate row mismatch: {row_count}/0..50")
            self._set_task(
                node,
                task,
                "SUCCESS",
                row_count=row_count,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            return True
        except Exception as exc:
            LOG.exception("CORE_STOCK_CANDIDATE_FAILED sequence=%s", node.sequence_no)
            self._set_task(
                node,
                task,
                "FAILED",
                row_count=0,
                duration_ms=round((perf_counter() - started) * 1000),
                error_code="CORE_STOCK_CANDIDATE_ERROR",
                error_message=str(exc),
            )
            return False

    def _table_count(self, table: str, node: ScheduleNode) -> int:
        result = self.writer.client.query(
            f"""
            SELECT count() FROM {table} FINAL
            WHERE trade_date={{trade_date:Date}}
              AND toString(collection_id)={{collection_id:String}}
            """,
            parameters={
                "trade_date": node.trade_date,
                "collection_id": node.collection_id,
            },
        )
        return int(result.result_rows[0][0])

    def _set_task(
        self,
        node: ScheduleNode,
        task: str,
        status: str,
        row_count: int | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if task == "market_delta_15m":
            values: dict[str, Any] = {
                "market_delta_15m_status": status,
                "market_delta_15m_row_count": row_count,
                "market_delta_15m_duration_ms": duration_ms,
                "market_delta_15m_error_code": error_code,
                "market_delta_15m_error_message": error_message[:1000]
                if error_message
                else None,
            }
        elif task == "capital_migration":
            values = {
                "capital_migration_status": status,
                "capital_migration_row_count": row_count,
                "capital_migration_duration_ms": duration_ms,
                "capital_migration_error_code": error_code,
                "capital_migration_error_message": error_message[:1000]
                if error_message
                else None,
            }
        elif task in {"core_sector_candidate", "core_stock_candidate"}:
            values = {
                f"{task}_status": status,
                f"{task}_row_count": row_count,
                f"{task}_duration_ms": duration_ms,
                f"{task}_error_code": error_code,
                f"{task}_error_message": error_message[:1000] if error_message else None,
            }
        else:
            raise ValueError(f"unknown post-derivation task: {task}")
        self.writer.set_schedule_status(node, "RUNNING", **values)

    def _block_after(self, node: ScheduleNode, error_code: str, error_message: str) -> None:
        if not self.is_target_node(node):
            return
        self._block_from(node, "market_delta_15m", error_code, error_message)

    def _block_from(
        self, node: ScheduleNode, first_task: str, error_code: str, error_message: str
    ) -> None:
        task_order = (
            "market_delta_15m",
            "capital_migration",
            "core_sector_candidate",
            "core_stock_candidate",
        )
        start = task_order.index(first_task)
        for task in task_order[start:]:
            self._set_task(
                node,
                task,
                "BLOCKED",
                error_code=error_code,
                error_message=error_message,
            )
