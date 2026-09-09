"""按需读取个股或板块上下文，不写库、不返回完整成员分钟明细。"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

from app.hithink.config import Settings
from app.hithink.schedule import SHANGHAI
from app.hithink.writer import ClickHouseWriter
from app.market_context_compact import compact_market_context_response
from app.market_state_package_reader import PACKAGE_ROOT, _default_trade_date

SECTOR_STATE_TABLES = {
    "concept": "market.hithink_concept_state",
    "industry": "market.hithink_industry_state",
    "style": "market.hithink_style_state",
}
TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2}))?$")
DATE_PATTERN = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
SECTOR_NAME_NORMALIZER = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
MAX_STOCKS = 50
STOCK_TRAJECTORY_MINUTES = 30
MULTI_STOCK_TRAJECTORY_MINUTES = 20
SECTOR_TRAJECTORY_MINUTES = 30
CORE_STOCK_TRAJECTORY_MINUTES = 15
AUCTION_KEY_NODE_SEQUENCES = (1, 6, 10, 11)
AUCTION_TRAJECTORY_SCHEMA = ["time", "price", "change_pct", "amount"]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _parse_date(value: str) -> date:
    if not isinstance(value, str):
        raise TypeError("trade_date必须是YYYY-MM-DD或YYYYMMDD")
    match = DATE_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("trade_date必须是YYYY-MM-DD或YYYYMMDD")
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError as exc:
        raise ValueError("trade_date不是有效日期") from exc


def _parse_time(value: str) -> tuple[time, bool]:
    if not isinstance(value, str):
        raise TypeError("target_time必须是HH:MM或HH:MM:SS")
    match = TIME_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("target_time必须是HH:MM或HH:MM:SS")
    second_text = match.group(3)
    try:
        parsed = time(int(match.group(1)), int(match.group(2)), int(second_text or 0))
    except ValueError as exc:
        raise ValueError("target_time不是有效时间") from exc
    return parsed, second_text is not None


def _requested_time_quality(
    trade_date: date,
    parsed_time: time | None,
    has_seconds: bool,
    requested_time: str | None,
    actual_at: datetime,
) -> tuple[str, int]:
    if parsed_time is None:
        return actual_at.strftime("%H:%M"), 0
    requested_at = datetime.combine(trade_date, parsed_time, tzinfo=SHANGHAI)
    if has_seconds:
        age = max(0, round((requested_at - actual_at).total_seconds()))
    else:
        actual_minute = actual_at.replace(second=0, microsecond=0)
        age = max(0, round((requested_at - actual_minute).total_seconds()))
    assert requested_time is not None
    return requested_time, age


def _normalize_stock_code(value: Any) -> tuple[str, str]:
    text = str(value).strip().upper()
    if re.fullmatch(r"\d{1,6}", text):
        ticker = text.zfill(6)
        if ticker.startswith(("4", "8")):
            exchange = "BJ"
        elif ticker.startswith(("5", "6", "9")):
            exchange = "SH"
        else:
            exchange = "SZ"
        return ticker, f"{ticker}.{exchange}"
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", text)
    if match:
        return match.group(1), text
    match = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", text)
    if match:
        return match.group(2), f"{match.group(2)}.{match.group(1)}"
    raise ValueError(f"股票代码{text!r}无效，需使用6位代码或带交易所后缀的代码")


def _stock_code_from_thscode(thscode: str) -> tuple[str, str]:
    match = re.search(r"(\d{6})", str(thscode))
    if not match:
        raise ValueError(f"本地名称映射返回了无法识别的股票代码：{thscode}")
    return _normalize_stock_code(match.group(1))


def _resolve_stock_input(
    reader: MarketContextReader, trade_date: date, value: Any
) -> tuple[str, str]:
    try:
        return _normalize_stock_code(value)
    except ValueError:
        name = str(value).strip()
        if not name:
            raise ValueError("股票名称不能为空") from None
        matches = reader.resolve_stock_name(trade_date, name)
        if not matches:
            raise ValueError(f"本地股票名称映射中没有找到{name!r}") from None
        if len(matches) > 1:
            raise ValueError(f"股票名称{name!r}对应多个代码，请改用6位代码") from None
        return _stock_code_from_thscode(matches[0]["thscode"])


def _normalize_sector_name(value: str) -> str:
    return SECTOR_NAME_NORMALIZER.sub("", value.strip().lower())


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _difference(current: Any, base: Any) -> float | None:
    current_number, base_number = _number(current), _number(base)
    if current_number is None or base_number is None:
        return None
    return current_number - base_number


def _pct_change(current: Any, base: Any) -> float | None:
    current_number, base_number = _number(current), _number(base)
    if current_number is None or base_number in (None, 0):
        return None
    return (current_number / base_number - 1) * 100


def _share(value: Any, total: Any) -> float | None:
    value_number, total_number = _number(value), _number(total)
    if value_number is None or total_number is None or total_number <= 0:
        return None
    return value_number / total_number * 100


def _round_metrics(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _round_metrics(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_metrics(item) for item in value]
    return value


class MarketContextReader:
    def __init__(self, client: Any):
        self.client = client
        self.query_count = 0
        self.database_elapsed_ms = 0.0

    def rows(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        *,
        max_rows: int = 10_000,
    ) -> list[dict[str, Any]]:
        started = perf_counter()
        result = self.client.query(
            sql,
            parameters=parameters or {},
            settings={
                "readonly": 1,
                "max_execution_time": 30,
                "max_result_rows": max_rows,
                "result_overflow_mode": "throw",
            },
        )
        self.query_count += 1
        self.database_elapsed_ms += (perf_counter() - started) * 1000
        columns = list(result.column_names)
        return [dict(zip(columns, row, strict=True)) for row in result.result_rows]

    def latest_database_date(self) -> date | None:
        rows = self.rows(
            "SELECT max(trade_date) AS trade_date FROM market.hithink_snapshot_derived"
        )
        return rows[0]["trade_date"] if rows and rows[0].get("trade_date") else None

    def resolve_stock_node(
        self, trade_date: date, target_at: datetime, thscodes: list[str]
    ) -> dict[str, Any] | None:
        rows = self.rows(
            """
            SELECT toString(collection_id) AS collection_id,scheduled_time,
                   any(session) AS session,any(node_seq) AS node_seq
            FROM market.hithink_snapshot_derived
            WHERE trade_date={trade_date:Date}
              AND scheduled_time<={target_at:DateTime64(3,'Asia/Shanghai')}
              AND thscode IN {thscodes:Array(String)}
            GROUP BY collection_id,scheduled_time
            HAVING uniqExact(thscode)={stock_count:UInt8}
            ORDER BY scheduled_time DESC
            LIMIT 1
            """,
            {
                "trade_date": trade_date,
                "target_at": target_at,
                "thscodes": thscodes,
                "stock_count": len(thscodes),
            },
        )
        return rows[0] if rows else None

    def resolve_sector_node(
        self,
        trade_date: date,
        target_at: datetime,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        codes_by_type: dict[str, list[str]] = defaultdict(list)
        for candidate in candidates:
            codes_by_type[str(candidate["sector_type"])].append(str(candidate["sector_code"]))
        unions = []
        parameters: dict[str, Any] = {"trade_date": trade_date, "target_at": target_at}
        for index, (sector_type, codes) in enumerate(codes_by_type.items()):
            table = SECTOR_STATE_TABLES[sector_type]
            parameter = f"sector_codes_{index}"
            parameters[parameter] = list(dict.fromkeys(codes))
            unions.append(
                f"""
                SELECT toString(collection_id) AS collection_id,scheduled_time,
                       session,node_seq
                FROM {table} FINAL
                WHERE trade_date={{trade_date:Date}}
                  AND scheduled_time<={{target_at:DateTime64(3,'Asia/Shanghai')}}
                  AND sector_code IN {{{parameter}:Array(String)}}
                """
            )
        if not unions:
            return None
        rows = self.rows(
            f"""
            SELECT collection_id,scheduled_time,session,node_seq
            FROM ({' UNION ALL '.join(unions)})
            ORDER BY scheduled_time DESC
            LIMIT 1
            """,
            parameters,
        )
        return rows[0] if rows else None

    def resolve_stock_name(self, trade_date: date, stock_name: str) -> list[dict[str, Any]]:
        return self.rows(
            """
            SELECT thscode,any(stock_name) AS resolved_name
            FROM
            (
                SELECT thscode,stock_name
                FROM market.sector_membership_history FINAL
                WHERE stock_name={stock_name:String}
                  AND observed_from<={trade_date:Date}
                  AND (observed_to IS NULL OR observed_to>={trade_date:Date})
            )
            GROUP BY thscode
            ORDER BY thscode
            LIMIT 2
            """,
            {"trade_date": trade_date, "stock_name": stock_name.strip()},
        )

    def sector_market_facts(
        self,
        trade_date: date,
        collection_ids: list[str],
        sector_type: str,
        sector_codes: list[str],
    ) -> list[dict[str, Any]]:
        if not collection_ids or not sector_codes:
            return []
        table = SECTOR_STATE_TABLES[sector_type]
        return self.rows(
            f"""
            WITH ranked AS
            (
                SELECT toString(collection_id) AS collection_id,
                       sector_code,turnover_market_share_pct,
                       turnover_1m_market_share_pct,
                       turnover_market_share_delta_1m,
                       turnover_1m_market_share_delta_1m,
                       rank() OVER (
                         PARTITION BY collection_id
                         ORDER BY turnover_market_share_pct DESC
                       ) AS turnover_share_rank,
                       rank() OVER (
                         PARTITION BY collection_id
                         ORDER BY turnover_1m_market_share_pct DESC
                       ) AS turnover_1m_share_rank
                FROM {table} FINAL
                WHERE trade_date={{trade_date:Date}}
                  AND toString(collection_id) IN {{collection_ids:Array(String)}}
            )
            SELECT r.*,
                   nullIf(c.candidate_rank,0) AS candidate_rank
            FROM ranked AS r
            LEFT JOIN market.hithink_core_sector_candidate AS c FINAL
              ON c.trade_date={{trade_date:Date}}
             AND toString(c.collection_id)=r.collection_id
             AND c.sector_type={{sector_type:String}}
             AND c.sector_code=r.sector_code
            WHERE r.sector_code IN {{sector_codes:Array(String)}}
            """,
            {
                "trade_date": trade_date,
                "collection_ids": collection_ids,
                "sector_type": sector_type,
                "sector_codes": sector_codes,
            },
        )

    def strategic_relations(
        self, trade_date: date, sector_type: str, sector_codes: list[str]
    ) -> list[dict[str, Any]]:
        if not sector_codes:
            return []
        return self.rows(
            """
            SELECT sector_type,sector_code,sector_name,watch_level,
                   strategic_theme,strategic_subtheme
            FROM market.strategic_sector_watchlist FINAL
            WHERE is_active=1 AND effective_from<={trade_date:Date}
              AND (effective_to IS NULL OR effective_to>={trade_date:Date})
              AND sector_type={sector_type:String}
              AND sector_code IN {sector_codes:Array(String)}
            ORDER BY watch_level,strategic_theme,strategic_subtheme
            """,
            {
                "trade_date": trade_date,
                "sector_type": sector_type,
                "sector_codes": sector_codes,
            },
        )

    def available_time_range(self, trade_date: date) -> dict[str, Any]:
        rows = self.rows(
            """
            SELECT min(scheduled_time) AS first_time,max(scheduled_time) AS last_time,
                   uniqExact(collection_id) AS node_count
            FROM market.hithink_market_state FINAL
            WHERE trade_date={trade_date:Date}
            """,
            {"trade_date": trade_date},
        )
        return rows[0] if rows else {}

    def stock_timeline(
        self, trade_date: date, actual_at: datetime, thscode: str
    ) -> list[dict[str, Any]]:
        return self.rows(
            """
            SELECT toString(d.collection_id) AS collection_id,d.node_seq,d.scheduled_time,
                   d.session,d.thscode,toString(d.ticker) AS ticker,
                   (SELECT any(stock_name)
                    FROM market.sector_membership_history FINAL
                    WHERE thscode={thscode:String} AND observed_from<={trade_date:Date}
                      AND (observed_to IS NULL OR observed_to>={trade_date:Date})) AS stock_name,
                   d.last_price,d.price_change_ratio_pct,d.price_change_1m_pct,
                   d.price_delta_1m,d.turnover,d.turnover_delta_1m,d.turnover_growth_1m,
                   d.total_market_cap,d.float_market_cap,
                   d.new_high_flag,d.new_low_flag,
                   toUInt8((toString(d.collection_id),d.thscode) IN
                     (SELECT toString(collection_id),thscode FROM market.hithink_limit_up_pool FINAL
                      WHERE trade_date={trade_date:Date} AND collection_id IS NOT NULL)) AS is_limit_up,
                   toUInt8((toString(d.collection_id),d.thscode) IN
                     (SELECT toString(collection_id),thscode FROM market.hithink_limit_down_pool FINAL
                      WHERE trade_date={trade_date:Date} AND collection_id IS NOT NULL)) AS is_limit_down,
                   toUInt8((toString(d.collection_id),d.thscode) IN
                     (SELECT toString(collection_id),thscode FROM market.hithink_limit_break_pool FINAL
                      WHERE trade_date={trade_date:Date})) AS is_limit_break
            FROM market.hithink_snapshot_derived AS d
            WHERE d.trade_date={trade_date:Date} AND d.thscode={thscode:String}
              AND d.scheduled_time<={actual_at:DateTime64(3,'Asia/Shanghai')}
            ORDER BY d.scheduled_time
            """,
            {"trade_date": trade_date, "actual_at": actual_at, "thscode": thscode},
        )

    def stock_auction_rows(
        self, trade_date: date, actual_at: datetime, thscode: str
    ) -> list[dict[str, Any]]:
        return self.rows(
            """
            SELECT node_seq,formatDateTime(scheduled_time,'%H:%i') AS time,
                   auction_phase,auction_price,auction_pct,auction_amount,
                   auction_volume,auction_turnover_pct,
                   auction_yesterday_ratio_pct,pre_close_price
            FROM market.hithink_auction_snapshot FINAL
            WHERE trade_date={trade_date:Date} AND thscode={thscode:String}
              AND node_seq IN {node_sequences:Array(UInt16)}
              AND scheduled_time<={actual_at:DateTime64(3,'Asia/Shanghai')}
              AND (node_seq!=11 OR (auction_phase='matched' AND data_status='final'))
            ORDER BY node_seq
            """,
            {
                "trade_date": trade_date,
                "actual_at": actual_at,
                "thscode": thscode,
                "node_sequences": list(AUCTION_KEY_NODE_SEQUENCES),
            },
            max_rows=len(AUCTION_KEY_NODE_SEQUENCES),
        )

    def stock_sector_rows(
        self,
        trade_date: date,
        actual_at: datetime,
        thscode: str,
    ) -> list[dict[str, Any]]:
        half_start = datetime.combine(
            trade_date,
            time(9, 0) if actual_at.hour < 12 else time(13, 0),
            tzinfo=SHANGHAI,
        )
        return self.rows(
            """
            WITH memberships AS
            (
                SELECT sector_type,sector_code
                FROM market.sector_membership_history FINAL
                WHERE thscode={thscode:String} AND observed_from<={trade_date:Date}
                  AND (observed_to IS NULL OR observed_to>={trade_date:Date})
                  AND sector_type IN ('concept','industry')
            ),
            states AS
            (
                SELECT 'concept' AS sector_type,* EXCEPT(node_seq,version_time)
                FROM market.hithink_concept_state FINAL
                WHERE trade_date={trade_date:Date}
                  AND scheduled_time<={actual_at:DateTime64(3,'Asia/Shanghai')}
                  AND scheduled_time>={half_start:DateTime64(3,'Asia/Shanghai')}
                UNION ALL
                SELECT 'industry' AS sector_type,* EXCEPT(node_seq,version_time)
                FROM market.hithink_industry_state FINAL
                WHERE trade_date={trade_date:Date}
                  AND scheduled_time<={actual_at:DateTime64(3,'Asia/Shanghai')}
                  AND scheduled_time>={half_start:DateTime64(3,'Asia/Shanghai')}
            ),
            filtered AS
            (
                SELECT s.*
                FROM states AS s
                INNER JOIN memberships AS m USING (sector_type,sector_code)
            ),
            points AS
            (
                SELECT *
                FROM
                (
                    SELECT *,row_number() OVER (
                      PARTITION BY sector_type,sector_code ORDER BY scheduled_time DESC
                    ) AS relative_row
                    FROM filtered
                )
                WHERE relative_row<=31
            )
            SELECT sector_type,sector_code,sector_name,relative_row,scheduled_time,
                   member_total,valid_member_count,index_last_price,
                   index_change_ratio_pct,index_change_1m_pct,
                   up_count,down_count,flat_count,up_ratio,down_ratio,
                   limit_up_count,limit_down_count,limit_break_count,
                   turnover_total,turnover_delta_1m_total,prev_turnover_delta_1m_total,
                   turnover_growth_1m,turnover_market_share_pct,
                   turnover_1m_market_share_pct,turnover_market_share_delta_1m,
                   turnover_1m_market_share_delta_1m,
                   new_high_count,new_low_count,new_high_ratio,new_low_ratio,
                   turnover_1m_top1_share_pct,turnover_1m_top3_share_pct,
                   turnover_1m_top5_share_pct,total_market_cap,float_market_cap
            FROM points
            ORDER BY sector_type,sector_code,relative_row
            """,
            {
                "trade_date": trade_date,
                "actual_at": actual_at,
                "half_start": half_start,
                "thscode": thscode,
            },
        )

    def stock_sector_profiles(
        self,
        trade_date: date,
        actual_at: datetime,
        collection_id: str,
        thscode: str,
    ) -> list[dict[str, Any]]:
        recent_start = actual_at - timedelta(minutes=60)
        return self.rows(
            """
            WITH target_memberships AS
            (
                SELECT sector_type,sector_code
                FROM market.sector_membership_history FINAL
                WHERE thscode={thscode:String} AND observed_from<={trade_date:Date}
                  AND (observed_to IS NULL OR observed_to>={trade_date:Date})
                  AND sector_type IN ('concept','industry')
            ),
            all_members AS
            (
                SELECT h.sector_type,h.sector_code,h.thscode
                FROM (SELECT * FROM market.sector_membership_history FINAL) AS h
                INNER JOIN target_memberships AS target USING (sector_type,sector_code)
                WHERE h.observed_from<={trade_date:Date}
                  AND (h.observed_to IS NULL OR h.observed_to>={trade_date:Date})
            ),
            current_rows AS
            (
                SELECT m.sector_type,m.sector_code,d.thscode,d.turnover,
                       d.turnover_delta_1m,d.price_change_ratio_pct
                FROM all_members AS m
                INNER JOIN market.hithink_snapshot_derived AS d ON d.thscode=m.thscode
                WHERE d.trade_date={trade_date:Date}
                  AND d.collection_id={collection_id:String}
                  AND d.last_price IS NOT NULL
            ),
            target_values AS
            (
                SELECT turnover,turnover_delta_1m,price_change_ratio_pct
                FROM market.hithink_snapshot_derived
                WHERE trade_date={trade_date:Date}
                  AND collection_id={collection_id:String} AND thscode={thscode:String}
                LIMIT 1
            ),
            positions AS
            (
                SELECT sector_type,sector_code,count() AS valid_members,
                       toUInt32(countIf(turnover>(SELECT turnover FROM target_values))+1)
                         AS turnover_rank,
                       toUInt32(countIf(turnover_delta_1m>
                         (SELECT turnover_delta_1m FROM target_values))+1)
                         AS turnover_1m_rank,
                       toUInt32(countIf(price_change_ratio_pct>
                         (SELECT price_change_ratio_pct FROM target_values))+1)
                         AS gain_rank,
                       (SELECT turnover FROM target_values)/sum(turnover)*100
                         AS turnover_share_pct,
                       if(sum(turnover_delta_1m)>0,
                         (SELECT turnover_delta_1m FROM target_values)/sum(turnover_delta_1m)*100,
                         CAST(NULL,'Nullable(Float64)')) AS turnover_1m_share_pct
                FROM current_rows
                GROUP BY sector_type,sector_code
            ),
            states AS
            (
                SELECT 'concept' AS sector_type,toString(collection_id) AS collection_id,
                       scheduled_time,sector_code,index_change_1m_pct
                FROM market.hithink_concept_state FINAL
                WHERE trade_date={trade_date:Date}
                  AND scheduled_time<={actual_at:DateTime64(3,'Asia/Shanghai')}
                UNION ALL
                SELECT 'industry',toString(collection_id),scheduled_time,sector_code,
                       index_change_1m_pct
                FROM market.hithink_industry_state FINAL
                WHERE trade_date={trade_date:Date}
                  AND scheduled_time<={actual_at:DateTime64(3,'Asia/Shanghai')}
            ),
            stock_moves AS
            (
                SELECT toString(collection_id) AS collection_id,price_change_1m_pct
                FROM market.hithink_snapshot_derived
                WHERE trade_date={trade_date:Date} AND thscode={thscode:String}
                  AND scheduled_time<={actual_at:DateTime64(3,'Asia/Shanghai')}
            ),
            movement AS
            (
                SELECT s.sector_type AS sector_type,s.sector_code AS sector_code,
                       count() AS observations,
                       corr(toFloat64(stock_moves.price_change_1m_pct),
                            toFloat64(s.index_change_1m_pct)) AS corr_all,
                       corrIf(toFloat64(stock_moves.price_change_1m_pct),
                              toFloat64(s.index_change_1m_pct),
                              s.scheduled_time>={recent_start:DateTime64(3,'Asia/Shanghai')})
                         AS corr_recent,
                       countIf(s.scheduled_time>=
                         {recent_start:DateTime64(3,'Asia/Shanghai')}) AS recent_observations,
                       sqrt(avg(pow(toFloat64(stock_moves.price_change_1m_pct)-
                         toFloat64(s.index_change_1m_pct),2))) AS tracking_error_1m_pct
                FROM states AS s
                INNER JOIN target_memberships AS target USING (sector_type,sector_code)
                INNER JOIN stock_moves USING (collection_id)
                WHERE stock_moves.price_change_1m_pct IS NOT NULL
                  AND s.index_change_1m_pct IS NOT NULL
                GROUP BY s.sector_type,s.sector_code
            ),
            watch AS
            (
                SELECT sector_type,sector_code,
                       argMax(watch_level,version_time) AS watch_level,
                       argMax(strategic_theme,version_time) AS strategic_theme,
                       argMax(strategic_subtheme,version_time) AS strategic_subtheme,
                       argMax(watch_reason,version_time) AS watch_reason
                FROM market.strategic_sector_watchlist FINAL
                WHERE is_active=1 AND effective_from<={trade_date:Date}
                  AND (effective_to IS NULL OR effective_to>={trade_date:Date})
                GROUP BY sector_type,sector_code
            )
            SELECT p.sector_type AS sector_type,p.sector_code AS sector_code,
                   p.valid_members,p.turnover_rank,
                   p.turnover_1m_rank,p.gain_rank,p.turnover_share_pct,
                   p.turnover_1m_share_pct,m.observations,m.corr_all,m.corr_recent,
                   m.recent_observations,m.tracking_error_1m_pct,
                   w.watch_level,w.strategic_theme,w.strategic_subtheme,w.watch_reason
            FROM positions AS p
            LEFT JOIN movement AS m
              ON p.sector_type=m.sector_type AND p.sector_code=m.sector_code
            LEFT JOIN watch AS w
              ON p.sector_type=w.sector_type AND p.sector_code=w.sector_code
            ORDER BY p.sector_type,p.sector_code
            """,
            {
                "trade_date": trade_date,
                "actual_at": actual_at,
                "recent_start": recent_start,
                "collection_id": collection_id,
                "thscode": thscode,
            },
        )

    def sector_timeline(
        self,
        sector_type: str,
        sector_code: str,
        trade_date: date,
        actual_at: datetime,
    ) -> list[dict[str, Any]]:
        table = SECTOR_STATE_TABLES[sector_type]
        timeline = self.rows(
            f"""
            SELECT toString(collection_id) AS collection_id,node_seq,scheduled_time,session,
                   sector_code,sector_name,member_total,valid_member_count,
                   index_last_price,index_change_ratio_pct,index_change_1m_pct,
                   up_count,down_count,flat_count,up_ratio,down_ratio,
                   limit_up_count,up_5_to_limit_count,up_1_to_5_count,up_0_to_1_count,
                   down_0_to_1_count,down_1_to_5_count,down_5_to_limit_count,
                   limit_down_count,limit_break_count,turnover_total,
                   turnover_delta_1m_total,prev_turnover_delta_1m_total,
                   turnover_growth_1m,turnover_market_share_pct,
                   turnover_1m_market_share_pct,turnover_market_share_delta_1m,
                   turnover_1m_market_share_delta_1m,new_high_count,new_low_count,
                   new_high_ratio,new_low_ratio,turnover_1m_top1_share_pct,
                   turnover_1m_top3_share_pct,turnover_1m_top5_share_pct,
                   total_market_cap,float_market_cap
            FROM {table} FINAL
            WHERE trade_date={{trade_date:Date}} AND sector_code={{sector_code:String}}
              AND scheduled_time<={{actual_at:DateTime64(3,'Asia/Shanghai')}}
            ORDER BY scheduled_time
            """,
            {
                "trade_date": trade_date,
                "sector_code": sector_code,
                "actual_at": actual_at,
            },
        )
        facts = self.sector_market_facts(
            trade_date,
            [str(row["collection_id"]) for row in timeline],
            sector_type,
            [sector_code],
        )
        fact_map = {row["collection_id"]: row for row in facts}
        return [{**row, **fact_map.get(str(row["collection_id"]), {})} for row in timeline]

    def resolve_sector_candidates(
        self,
        sector_id: str | None,
        sector_name: str | None,
        trade_date: date,
    ) -> tuple[list[dict[str, Any]], str]:
        if sector_id:
            rows = self.rows(
                """
                SELECT sector_type,sector_code,sector_name
                FROM market.sector_catalog FINAL
                WHERE sector_code={sector_id:String} AND is_active=1
                ORDER BY sector_type
                """,
                {"sector_id": sector_id.strip().upper()},
            )
            return rows, "sector_id_exact"
        assert sector_name is not None
        normalized = _normalize_sector_name(sector_name)
        rows = self.rows(
            """
            WITH lowerUTF8(
                replaceAll(replaceAll(replaceAll(replaceAll(replaceAll(
                    sector_name,'(',''),')',''),'（',''),'）',''),' ','')
            ) AS normalized_name
            SELECT sector_type,sector_code,sector_name
            FROM market.sector_catalog FINAL
            WHERE is_active=1 AND sector_type IN ('concept','industry','style')
              AND (
                normalized_name={name:String}
                OR positionCaseInsensitiveUTF8(normalized_name,{name:String})>0
                OR positionCaseInsensitiveUTF8({name:String},normalized_name)>0
              )
            ORDER BY sector_type,sector_name
            LIMIT 50
            """,
            {"name": normalized},
        )
        exact = [row for row in rows if _normalize_sector_name(row["sector_name"]) == normalized]
        if exact or rows:
            return (exact or rows), ("sector_name_exact" if exact else "sector_name_fuzzy")
        rows = self.rows(
            """
            SELECT sector_type,sector_code,
                   argMax(sector_name,version_time) AS matched_sector_name
            FROM market.strategic_sector_watchlist FINAL
            WHERE is_active=1 AND effective_from<={trade_date:Date}
              AND (effective_to IS NULL OR effective_to>={trade_date:Date})
              AND (
                positionCaseInsensitiveUTF8(strategic_theme,{name:String})>0
                OR positionCaseInsensitiveUTF8({name:String},strategic_theme)>0
                OR positionCaseInsensitiveUTF8(strategic_subtheme,{name:String})>0
                OR positionCaseInsensitiveUTF8({name:String},strategic_subtheme)>0
                OR positionCaseInsensitiveUTF8(sector_name,{name:String})>0
              )
            GROUP BY sector_type,sector_code
            ORDER BY sector_type,matched_sector_name
            LIMIT 50
            """,
            {"trade_date": trade_date, "name": sector_name.strip()},
        )
        return [
            {
                "sector_type": row["sector_type"],
                "sector_code": row["sector_code"],
                "sector_name": row["matched_sector_name"],
            }
            for row in rows
        ], "strategic_direction"

    def current_sector_states(
        self, trade_date: date, collection_id: str, sector_codes: list[str]
    ) -> list[dict[str, Any]]:
        if not sector_codes:
            return []
        return self.rows(
            """
            WITH states AS
            (
                SELECT 'concept' AS sector_type,* EXCEPT(node_seq,version_time)
                FROM market.hithink_concept_state FINAL
                WHERE trade_date={trade_date:Date} AND collection_id={collection_id:String}
                UNION ALL
                SELECT 'industry' AS sector_type,* EXCEPT(node_seq,version_time)
                FROM market.hithink_industry_state FINAL
                WHERE trade_date={trade_date:Date} AND collection_id={collection_id:String}
                UNION ALL
                SELECT 'style' AS sector_type,* EXCEPT(node_seq,version_time)
                FROM market.hithink_style_state FINAL
                WHERE trade_date={trade_date:Date} AND collection_id={collection_id:String}
            )
            SELECT sector_type,sector_code,sector_name,member_total,valid_member_count,
                   index_change_ratio_pct,index_change_1m_pct,up_ratio,down_ratio,
                   limit_up_count,limit_down_count,limit_break_count,
                   turnover_total,turnover_delta_1m_total,turnover_growth_1m,
                   new_high_ratio,new_low_ratio,total_market_cap,float_market_cap
            FROM states
            WHERE sector_code IN {sector_codes:Array(String)}
            """,
            {
                "trade_date": trade_date,
                "collection_id": collection_id,
                "sector_codes": sector_codes,
            },
        )

    def ranked_members(
        self,
        trade_date: date,
        collection_id: str,
        sector_codes: list[str],
        *,
        target_thscode: str = "",
        group_limit: int = 3,
    ) -> list[dict[str, Any]]:
        if not sector_codes:
            return []
        return self.rows(
            """
            WITH members AS
            (
                SELECT sector_type,sector_code,thscode AS member_thscode,
                       any(stock_name) AS stock_name
                FROM market.sector_membership_history FINAL
                WHERE sector_code IN {sector_codes:Array(String)}
                  AND observed_from<={trade_date:Date}
                  AND (observed_to IS NULL OR observed_to>={trade_date:Date})
                GROUP BY sector_type,sector_code,member_thscode
            ),
            target_clock AS
            (
                SELECT max(scheduled_time) AS scheduled_time
                FROM market.hithink_snapshot_derived FINAL
                WHERE trade_date={trade_date:Date}
                  AND collection_id={collection_id:String}
            ),
            member_minutes AS
            (
                SELECT m.sector_type,m.sector_code,d.thscode AS member_thscode,
                       d.turnover_delta_1m,
                       row_number() OVER (
                         PARTITION BY m.sector_type,m.sector_code,d.thscode
                         ORDER BY d.scheduled_time DESC
                       ) AS minute_rank
                FROM members AS m
                INNER JOIN market.hithink_snapshot_derived AS d
                  ON d.thscode=m.member_thscode
                WHERE d.trade_date={trade_date:Date}
                  AND d.scheduled_time<=(SELECT scheduled_time FROM target_clock)
                  AND d.scheduled_time>=if(
                    toHour((SELECT scheduled_time FROM target_clock))<12,
                    toDateTime64(concat(toString({trade_date:Date}),' 09:30:00'),3,'Asia/Shanghai'),
                    toDateTime64(concat(toString({trade_date:Date}),' 13:00:00'),3,'Asia/Shanghai')
                  )
                  AND d.turnover_delta_1m IS NOT NULL
            ),
            member_turnover_15m AS
            (
                SELECT sector_type,sector_code,member_thscode,
                       sumIf(turnover_delta_1m,minute_rank<=15) AS turnover_15m,
                       countIf(minute_rank<=15) AS turnover_15m_valid_minutes
                FROM member_minutes
                GROUP BY sector_type,sector_code,member_thscode
            ),
            joined AS
            (
                SELECT m.sector_type AS sector_type,m.sector_code AS sector_code,
                       m.member_thscode AS thscode,m.stock_name AS stock_name,
                       toString(d.ticker) AS ticker,d.last_price,d.price_change_ratio_pct,
                       d.price_change_1m_pct,d.turnover,d.turnover_delta_1m,
                       d.total_market_cap,d.float_market_cap,
                       if(d.float_market_cap>0,d.turnover/d.float_market_cap*100,
                          CAST(NULL,'Nullable(Float64)')) AS turnover_to_float_cap_pct,
                       t.turnover_15m,t.turnover_15m_valid_minutes,
                       if(d.float_market_cap>0,t.turnover_15m/d.float_market_cap*100,
                          CAST(NULL,'Nullable(Float64)')) AS turnover_15m_to_float_cap_pct,
                       d.turnover_growth_1m,d.new_high_flag,d.new_low_flag,
                       toUInt8(d.thscode IN
                         (SELECT thscode FROM market.hithink_limit_up_pool FINAL
                          WHERE trade_date={trade_date:Date} AND collection_id={collection_id:String})) AS is_limit_up,
                       toUInt8(d.thscode IN
                         (SELECT thscode FROM market.hithink_limit_down_pool FINAL
                          WHERE trade_date={trade_date:Date} AND collection_id={collection_id:String})) AS is_limit_down,
                       toUInt8(d.thscode IN
                         (SELECT thscode FROM market.hithink_limit_break_pool FINAL
                          WHERE trade_date={trade_date:Date} AND collection_id={collection_id:String})) AS is_limit_break
                FROM members AS m
                INNER JOIN market.hithink_snapshot_derived AS d
                  ON d.thscode=m.member_thscode
                LEFT JOIN member_turnover_15m AS t
                  ON t.sector_type=m.sector_type AND t.sector_code=m.sector_code
                 AND t.member_thscode=m.member_thscode
                WHERE d.trade_date={trade_date:Date} AND d.collection_id={collection_id:String}
                  AND d.last_price IS NOT NULL
            ),
            ranked AS
            (
                SELECT *,
                       rank() OVER (PARTITION BY sector_type,sector_code ORDER BY turnover DESC) AS turnover_rank,
                       rank() OVER (PARTITION BY sector_type,sector_code ORDER BY turnover_delta_1m DESC) AS turnover_1m_rank,
                       rank() OVER (PARTITION BY sector_type,sector_code ORDER BY price_change_ratio_pct DESC) AS gain_rank,
                       rank() OVER (PARTITION BY sector_type,sector_code ORDER BY price_change_ratio_pct ASC) AS loss_rank,
                       rank() OVER (PARTITION BY sector_type,sector_code ORDER BY float_market_cap DESC NULLS LAST) AS float_market_cap_rank,
                       rank() OVER (PARTITION BY sector_type,sector_code ORDER BY total_market_cap DESC NULLS LAST) AS total_market_cap_rank,
                       rank() OVER (PARTITION BY sector_type,sector_code ORDER BY turnover_to_float_cap_pct DESC NULLS LAST) AS turnover_to_float_cap_rank,
                       rank() OVER (PARTITION BY sector_type,sector_code ORDER BY turnover_15m_to_float_cap_pct DESC NULLS LAST) AS turnover_15m_to_float_cap_rank,
                       row_number() OVER (PARTITION BY sector_type,sector_code ORDER BY float_market_cap DESC NULLS LAST) AS float_market_cap_order,
                       row_number() OVER (PARTITION BY sector_type,sector_code ORDER BY total_market_cap DESC NULLS LAST) AS total_market_cap_order,
                       count() OVER (PARTITION BY sector_type,sector_code) AS valid_member_count,
                       countIf(float_market_cap IS NOT NULL AND float_market_cap>0) OVER (PARTITION BY sector_type,sector_code) AS float_market_cap_valid_member_count,
                       countIf(total_market_cap IS NOT NULL AND total_market_cap>0) OVER (PARTITION BY sector_type,sector_code) AS total_market_cap_valid_member_count,
                       countIf(turnover_to_float_cap_pct IS NOT NULL) OVER (PARTITION BY sector_type,sector_code) AS turnover_to_float_cap_valid_member_count,
                       countIf(turnover_15m_to_float_cap_pct IS NOT NULL) OVER (PARTITION BY sector_type,sector_code) AS turnover_15m_to_float_cap_valid_member_count,
                       sum(if(float_market_cap>0,float_market_cap,0)) OVER (PARTITION BY sector_type,sector_code) AS sector_float_market_cap_sum,
                       sum(if(total_market_cap>0,total_market_cap,0)) OVER (PARTITION BY sector_type,sector_code) AS sector_total_market_cap_sum,
                       row_number() OVER (
                         PARTITION BY sector_type,sector_code
                         ORDER BY (price_change_ratio_pct<0) DESC,
                                  if(price_change_ratio_pct<0,turnover,0) DESC
                       ) AS important_weak_rank,
                       row_number() OVER (
                         PARTITION BY sector_type,sector_code
                         ORDER BY (new_high_flag=1) DESC,if(new_high_flag=1,turnover,0) DESC
                       ) AS new_high_rank,
                       row_number() OVER (
                         PARTITION BY sector_type,sector_code
                         ORDER BY (price_change_1m_pct<0 AND turnover_growth_1m>=0.5) DESC,
                                  if(price_change_1m_pct<0 AND turnover_growth_1m>=0.5,turnover,0) DESC
                       ) AS volume_down_rank,
                       sum(ifNull(turnover,0)) OVER (PARTITION BY sector_type,sector_code) AS sector_turnover_sum,
                       sum(ifNull(turnover_delta_1m,0)) OVER (PARTITION BY sector_type,sector_code) AS sector_turnover_1m_sum
                FROM joined
            )
            SELECT *
            FROM ranked
            WHERE thscode={target_thscode:String}
               OR turnover_rank<={group_limit:UInt8}
               OR turnover_1m_rank<={group_limit:UInt8}
               OR gain_rank<={group_limit:UInt8}
               OR loss_rank<={group_limit:UInt8}
               OR float_market_cap_order<=5
               OR total_market_cap_order<=5
               OR (price_change_ratio_pct<0 AND important_weak_rank<={group_limit:UInt8})
               OR is_limit_up=1 OR is_limit_break=1
               OR (new_high_flag=1 AND new_high_rank<={group_limit:UInt8})
               OR (price_change_1m_pct<0 AND turnover_growth_1m>=0.5
                   AND volume_down_rank<={group_limit:UInt8})
            ORDER BY sector_type,sector_code,turnover_rank
            """,
            {
                "trade_date": trade_date,
                "collection_id": collection_id,
                "sector_codes": sector_codes,
                "target_thscode": target_thscode,
                "group_limit": group_limit,
            },
        )


def _same_session_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    current_at = rows[-1]["scheduled_time"]
    is_morning = current_at.hour < 12
    return [
        row
        for row in rows
        if (row["scheduled_time"].hour < 12) == is_morning
        and (is_morning or row["scheduled_time"].hour >= 13)
    ]


def _turnover_windows(
    rows: list[dict[str, Any]], field: str, window: int = 15
) -> tuple[float | None, int, float | None, int]:
    valid = [
        _number(row.get(field))
        for row in _same_session_rows(rows)
        if _number(row.get(field)) is not None
    ]
    current_values = valid[-window:]
    previous_values = valid[-(window * 2) : -window]
    return (
        sum(current_values) if current_values else None,
        len(current_values),
        sum(previous_values) if previous_values else None,
        len(previous_values),
    )


def _turnover_to_float_cap_pct(turnover: Any, float_market_cap: Any) -> float | None:
    turnover_value = _number(turnover)
    cap_value = _number(float_market_cap)
    if turnover_value is None or cap_value is None or cap_value <= 0:
        return None
    return turnover_value / cap_value * 100


def _row_at_offset(rows: list[dict[str, Any]], minutes: int) -> dict[str, Any] | None:
    if not rows:
        return None
    current_at = rows[-1]["scheduled_time"]
    expected = current_at - timedelta(minutes=minutes)
    candidates = [
        row
        for row in _same_session_rows(rows)
        if row["scheduled_time"] < current_at
        and abs((row["scheduled_time"] - expected).total_seconds()) <= 30
    ]
    return (
        min(candidates, key=lambda row: abs((row["scheduled_time"] - expected).total_seconds()))
        if candidates
        else None
    )


def _row_offsets(
    rows: list[dict[str, Any]],
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    return rows[-1], _row_at_offset(rows, 1), _row_at_offset(rows, 5), _row_at_offset(rows, 15)


def _volume_price_label(row: dict[str, Any]) -> str | None:
    growth = _number(row.get("turnover_growth_1m"))
    price = _number(row.get("price_change_1m_pct"))
    if growth is None or price is None:
        return None
    if growth >= 0.5 and price >= 0.2:
        return "明显放量上涨"
    if growth >= 0.5 and price <= -0.2:
        return "明显放量下跌"
    if growth <= -0.3:
        return "成交明显减速"
    return None


def _compress_stock_trajectory(
    rows: list[dict[str, Any]],
    recent_minutes: int = STOCK_TRAJECTORY_MINUTES,
    early_event_limit: int = 8,
) -> list[dict[str, Any]]:
    """Return continuous recent minutes plus a few earlier event nodes."""
    candidates: dict[int, set[str]] = defaultdict(set)
    if not rows:
        return []
    candidates[0].add("首个有效节点")
    candidates[len(rows) - 1].add("当前节点")
    for index, row in enumerate(rows):
        previous = rows[index - 1] if index else None
        for field, label in (
            ("new_high_flag", "创新高"),
            ("new_low_flag", "创新低"),
            ("is_limit_up", "涨停状态"),
            ("is_limit_break", "炸板状态"),
        ):
            if row.get(field) and (previous is None or not previous.get(field)):
                candidates[index].add(label)
        if 0 < index < len(rows) - 1:
            before = _number(rows[index - 1].get("last_price"))
            here = _number(row.get("last_price"))
            after = _number(rows[index + 1].get("last_price"))
            if before and here and after:
                prominence = min(abs(here / before - 1), abs(here / after - 1)) * 100
                if prominence >= 0.2 and (
                    (here > before and here > after) or (here < before and here < after)
                ):
                    candidates[index].add("价格拐点")
    valid_prices = [(i, _number(row.get("last_price"))) for i, row in enumerate(rows)]
    valid_prices = [(i, value) for i, value in valid_prices if value is not None]
    if valid_prices:
        candidates[max(valid_prices, key=lambda item: item[1])[0]].add("区间最高价")
        candidates[min(valid_prices, key=lambda item: item[1])[0]].add("区间最低价")
    valid_turnover = [(i, _number(row.get("turnover_delta_1m"))) for i, row in enumerate(rows)]
    valid_turnover = [(i, value) for i, value in valid_turnover if value is not None]
    if valid_turnover:
        candidates[max(valid_turnover, key=lambda item: item[1])[0]].add("区间最大1分钟成交")
    priority = {
        "当前节点": 100,
        "涨停状态": 90,
        "炸板状态": 90,
        "创新高": 80,
        "创新低": 80,
        "区间最高价": 75,
        "区间最低价": 75,
        "区间最大1分钟成交": 70,
        "价格拐点": 60,
        "首个有效节点": 50,
    }
    recent_rows = _same_session_rows(rows)[-recent_minutes:]
    recent_times = {row["scheduled_time"] for row in recent_rows}
    recent_indexes = {
        index for index, row in enumerate(rows) if row["scheduled_time"] in recent_times
    }
    earlier = [index for index in candidates if index not in recent_indexes]
    earlier.sort(
        key=lambda index: (
            max(priority.get(reason, 0) for reason in candidates[index]),
            rows[index]["scheduled_time"],
        ),
        reverse=True,
    )
    selected_indexes = sorted(set(earlier[:early_event_limit]) | recent_indexes)
    result = []
    for index in selected_indexes:
        row = rows[index]
        result.append(
            {
                "time": row["scheduled_time"],
                "price": row.get("last_price"),
                "change_pct": row.get("price_change_ratio_pct"),
                "turnover": row.get("turnover"),
                "turnover_1m": row.get("turnover_delta_1m"),
                "price_change_1m_pct": row.get("price_change_1m_pct"),
                "new_high": bool(row.get("new_high_flag")),
                "new_low": bool(row.get("new_low_flag")),
                "limit_up": bool(row.get("is_limit_up")),
                "limit_break": bool(row.get("is_limit_break")),
                "event": sorted(candidates.get(index, set())),
            }
        )
    return result


def _stock_auction_context(
    auction_rows: list[dict[str, Any]],
    stock_rows: list[dict[str, Any]],
    actual_at: datetime,
) -> dict[str, Any]:
    """Compress opening-auction facts without exposing collection metadata."""
    final_row = next(
        (
            row
            for row in auction_rows
            if int(row.get("node_seq") or 0) == 11
            and row.get("auction_phase") == "matched"
        ),
        None,
    )
    final = (
        {
            "time": "09:25",
            "price": final_row.get("auction_price"),
            "change_pct": final_row.get("auction_pct"),
            "amount": final_row.get("auction_amount"),
            "volume": final_row.get("auction_volume"),
            "turnover_pct": final_row.get("auction_turnover_pct"),
            "yesterday_ratio_pct": final_row.get("auction_yesterday_ratio_pct"),
            "pre_close_price": final_row.get("pre_close_price"),
        }
        if final_row
        else None
    )
    trajectory = {
        "schema": AUCTION_TRAJECTORY_SCHEMA,
        "rows": [
            [
                row.get("time"),
                row.get("auction_price"),
                row.get("auction_pct"),
                row.get("auction_amount"),
            ]
            for row in auction_rows
        ],
    }

    open_start = datetime.combine(actual_at.date(), time(9, 30), tzinfo=SHANGHAI)
    first_15m_ready = datetime.combine(actual_at.date(), time(9, 45), tzinfo=SHANGHAI)
    first_15m_end = datetime.combine(actual_at.date(), time(9, 45, 59), tzinfo=SHANGHAI)
    open_row = next(
        (
            row
            for row in stock_rows
            if row.get("scheduled_time") is not None
            and row["scheduled_time"] >= open_start
        ),
        None,
    )
    open_price = _number(open_row.get("last_price")) if open_row else None
    pre_close_price = _number(final_row.get("pre_close_price")) if final_row else None
    open_pct = _number(open_row.get("price_change_ratio_pct")) if open_row else None
    if open_pct is None:
        open_pct = _pct_change(open_price, pre_close_price)

    first_15m_rows = [
        row
        for row in stock_rows
        if row.get("scheduled_time") is not None
        and open_start <= row["scheduled_time"] <= first_15m_end
    ]
    first_15m_pct = (
        _pct_change(first_15m_rows[-1].get("last_price"), open_price)
        if actual_at >= first_15m_ready and first_15m_rows
        else None
    )
    auction_final_pct = _number(final_row.get("auction_pct")) if final_row else None
    return {
        "final": final,
        "trajectory": trajectory,
        "auction_to_open": {
            "auction_final_pct": auction_final_pct,
            "open_price": open_price,
            "open_pct": open_pct,
            "change_from_auction_to_open_pct": _difference(
                open_pct, auction_final_pct
            ),
            "first_15m_pct": first_15m_pct,
        },
    }


def _compress_sector_trajectory(
    rows: list[dict[str, Any]], recent_minutes: int = SECTOR_TRAJECTORY_MINUTES
) -> list[dict[str, Any]]:
    if not rows:
        return []
    selected = _same_session_rows(rows)[-recent_minutes:]
    return [
        {
            "time": row["scheduled_time"],
            "change_pct": row.get("index_change_ratio_pct"),
            "up_ratio": row.get("up_ratio"),
            "turnover_1m": row.get("turnover_delta_1m_total"),
            "cum_share": row.get("turnover_market_share_pct"),
            "instant_share": row.get("turnover_1m_market_share_pct"),
            "limit_up_count": row.get("limit_up_count"),
            "limit_break_count": row.get("limit_break_count"),
            "new_high_ratio": (
                None
                if row["scheduled_time"].strftime("%H:%M") == "09:30"
                else row.get("new_high_ratio")
            ),
        }
        for row in selected
    ]


def _sector_expression_score(row: dict[str, Any]) -> float:
    change = abs(_number(row.get("index_change_ratio_pct")) or 0)
    breadth = abs((_number(row.get("up_ratio")) or 0.5) - 0.5) * 1.5
    minute_change = min(abs(_number(row.get("index_change_1m_pct")) or 0) * 5, 0.5)
    valid = max(_number(row.get("valid_member_count")) or 1, 1)
    limit_density = min(
        ((_number(row.get("limit_up_count")) or 0) + (_number(row.get("limit_break_count")) or 0))
        / valid
        * 20,
        0.5,
    )
    specificity = 0.2 if valid <= 300 else 0
    broadness_penalty = min(max(math.log10(valid / 300), 0) * 0.7, 0.7)
    return change + breadth + minute_change + limit_density + specificity - broadness_penalty


def _select_stock_sectors(
    rows: list[dict[str, Any]],
    maximum: int,
    stock_change_pct: float | None = None,
    profiles: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    maximum = min(maximum, 4)
    profile_map = {(row["sector_type"], row["sector_code"]): row for row in (profiles or [])}
    weights = {
        "strategic_direction_priority": 0.10,
        "intraday_co_movement": 0.35,
        "stock_capacity_role": 0.25,
        "sector_market_expression": 0.15,
        "current_relative_fit": 0.15,
    }
    current_rows = [row for row in rows if int(row.get("relative_row", 0)) == 1]
    for row in current_rows:
        profile = profile_map.get((row["sector_type"], row["sector_code"]), {})
        excess = _difference(stock_change_pct, row.get("index_change_ratio_pct"))
        watch_level = profile.get("watch_level")
        strategic_base = {"CORE": 1.0, "IMPORTANT": 0.6, "WATCH": 0.3}.get(
            str(watch_level), 0.0
        )
        corr_all = max(-1.0, min(1.0, _number(profile.get("corr_all")) or 0.0))
        corr_recent = _number(profile.get("corr_recent"))
        recent_observations = int(profile.get("recent_observations") or 0)
        if corr_recent is None or recent_observations < 15:
            corr_recent = corr_all
        corr_recent = max(-1.0, min(1.0, corr_recent))
        co_movement_raw = max(0.0, corr_all * 0.6 + corr_recent * 0.4)

        valid_members = max(int(profile.get("valid_members") or 1), 1)
        rank_scale = max(min(valid_members, 20), 1)
        turnover_rank = int(profile.get("turnover_rank") or valid_members)
        turnover_1m_rank = int(profile.get("turnover_1m_rank") or valid_members)
        turnover_rank_raw = max(0.0, 1 - (turnover_rank - 1) / rank_scale)
        turnover_1m_rank_raw = max(0.0, 1 - (turnover_1m_rank - 1) / rank_scale)
        turnover_share_raw = min(max((_number(profile.get("turnover_share_pct")) or 0) / 10, 0), 1)
        capacity_role_raw = (
            turnover_rank_raw * 0.45 + turnover_1m_rank_raw * 0.20 + turnover_share_raw * 0.35
        )
        expression_raw = min(max(_sector_expression_score(row) / 3, 0), 1)
        relative_fit_raw = max(0.0, 1 - abs(excess or 0) / 3)
        trading_relevance = max(
            co_movement_raw,
            capacity_role_raw,
            expression_raw,
            min(abs(excess or 0) / 2, 1),
        )
        strategic_raw = strategic_base if trading_relevance >= 0.2 else 0.0
        row["primary_direction_eligible"] = bool(
            co_movement_raw >= 0.15
            or capacity_role_raw >= 0.20
            or expression_raw >= 0.40
            or abs(excess or 0) >= 0.75
        )
        raw_components = {
            "strategic_direction_priority": strategic_raw,
            "intraday_co_movement": co_movement_raw,
            "stock_capacity_role": capacity_role_raw,
            "sector_market_expression": expression_raw,
            "current_relative_fit": relative_fit_raw,
        }
        weighted_components = {
            name: raw_components[name] * weight for name, weight in weights.items()
        }
        row["selection_score"] = sum(weighted_components.values()) * 100
        row["selection_excess_change_pct"] = excess
        row["selection_score_breakdown"] = {
            "weights": weights,
            "raw_components": raw_components,
            "weighted_components": weighted_components,
            "profile_facts": {
                "watch_level": watch_level,
                "strategic_theme": profile.get("strategic_theme"),
                "strategic_subtheme": profile.get("strategic_subtheme"),
                "watch_reason": profile.get("watch_reason"),
                "turnover_rank": profile.get("turnover_rank"),
                "turnover_1m_rank": profile.get("turnover_1m_rank"),
                "turnover_share_pct": profile.get("turnover_share_pct"),
                "intraday_correlation": profile.get("corr_all"),
                "recent_60m_correlation": profile.get("corr_recent"),
                "recent_60m_observations": profile.get("recent_observations"),
                "tracking_error_1m_pct": profile.get("tracking_error_1m_pct"),
            },
        }
    eligible_rows = [row for row in current_rows if row["primary_direction_eligible"]]
    concepts = sorted(
        (row for row in eligible_rows if row["sector_type"] == "concept"),
        key=lambda row: row["selection_score"],
        reverse=True,
    )
    industries = sorted(
        (row for row in eligible_rows if row["sector_type"] == "industry"),
        key=lambda row: row["selection_score"],
        reverse=True,
    )
    concept_slots = maximum - 1 if industries and maximum > 1 else maximum
    selected = concepts[:concept_slots]
    if industries and len(selected) < maximum:
        selected.append(industries[0])
    return selected[:maximum]


def _group_relative_rows(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[int, dict[str, Any]]]:
    grouped: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[(row["sector_type"], row["sector_code"])][int(row["relative_row"])] = row
    return grouped


def _stock_current_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current, prev_1m, prev_5m, prev_15m = _row_offsets(rows)
    prev_10m = _row_at_offset(rows, 10)
    turnover_5m = _difference(
        current.get("turnover"), prev_5m.get("turnover") if prev_5m else None
    )
    prev_turnover_5m = (
        _difference(prev_5m.get("turnover"), prev_10m.get("turnover"))
        if prev_5m and prev_10m
        else None
    )
    turnover_15m, turnover_15m_valid_minutes, prev_turnover_15m, _ = (
        _turnover_windows(rows, "turnover_delta_1m")
    )
    turnover_1m = current.get("turnover_delta_1m")
    prev_turnover_1m = prev_1m.get("turnover_delta_1m") if prev_1m else None
    return {
        "price": current.get("last_price"),
        "change_pct": current.get("price_change_ratio_pct"),
        "turnover": current.get("turnover"),
        "total_market_cap": current.get("total_market_cap"),
        "float_market_cap": current.get("float_market_cap"),
        "turnover_to_float_cap_pct": _turnover_to_float_cap_pct(
            current.get("turnover"), current.get("float_market_cap")
        ),
        "change_1m_pct": current.get("price_change_1m_pct"),
        "change_5m_pct": _pct_change(
            current.get("last_price"), prev_5m.get("last_price") if prev_5m else None
        ),
        "change_15m_pct": _pct_change(
            current.get("last_price"), prev_15m.get("last_price") if prev_15m else None
        ),
        "turnover_1m": turnover_1m,
        "turnover_5m": turnover_5m,
        "turnover_15m": turnover_15m,
        "turnover_15m_valid_minutes": turnover_15m_valid_minutes,
        "turnover_1m_to_float_cap_pct": _turnover_to_float_cap_pct(
            turnover_1m, current.get("float_market_cap")
        ),
        "turnover_15m_to_float_cap_pct": _turnover_to_float_cap_pct(
            turnover_15m, current.get("float_market_cap")
        ),
        "prev_turnover_1m": prev_turnover_1m,
        "prev_turnover_5m": prev_turnover_5m,
        "prev_turnover_15m": prev_turnover_15m,
        "turnover_1m_change_pct": _pct_change(turnover_1m, prev_turnover_1m),
        "turnover_5m_change_pct": _pct_change(turnover_5m, prev_turnover_5m),
        "turnover_15m_change_pct": _pct_change(turnover_15m, prev_turnover_15m),
        "new_high": bool(current.get("new_high_flag")),
        "new_low": bool(current.get("new_low_flag")),
        "limit_up": bool(current.get("is_limit_up")),
        "limit_down": bool(current.get("is_limit_down")),
        "limit_break": bool(current.get("is_limit_break")),
        "volume_price_fact": _volume_price_label(current),
        "comparison_window": {
            "current_time": current.get("scheduled_time"),
            "prev_1m_time": prev_1m.get("scheduled_time") if prev_1m else None,
            "prev_5m_time": prev_5m.get("scheduled_time") if prev_5m else None,
            "prev_15m_time": prev_15m.get("scheduled_time") if prev_15m else None,
            "same_half_day_only": True,
        },
    }


def _public_member(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": row.get("ticker"),
        "thscode": row.get("thscode"),
        "name": row.get("stock_name"),
        "price": row.get("last_price"),
        "change_pct": row.get("price_change_ratio_pct"),
        "change_1m_pct": row.get("price_change_1m_pct"),
        "turnover": row.get("turnover"),
        "turnover_1m": row.get("turnover_delta_1m"),
        "total_market_cap": row.get("total_market_cap"),
        "float_market_cap": row.get("float_market_cap"),
        "turnover_to_float_cap_pct": row.get("turnover_to_float_cap_pct"),
        "turnover_rank": row.get("turnover_rank"),
        "turnover_1m_rank": row.get("turnover_1m_rank"),
        "gain_rank": row.get("gain_rank"),
        "new_high": bool(row.get("new_high_flag")),
        "limit_up": bool(row.get("is_limit_up")),
        "limit_break": bool(row.get("is_limit_break")),
    }


def _member_groups(rows: list[dict[str, Any]], limit: int) -> dict[str, list[dict[str, Any]]]:
    def take(predicate, order_key) -> list[dict[str, Any]]:
        selected = sorted((row for row in rows if predicate(row)), key=order_key)[:limit]
        return [_public_member(row) for row in selected]

    return {
        "turnover_top": take(
            lambda row: int(row["turnover_rank"]) <= limit, lambda row: int(row["turnover_rank"])
        ),
        "turnover_1m_top": take(
            lambda row: int(row["turnover_1m_rank"]) <= limit,
            lambda row: int(row["turnover_1m_rank"]),
        ),
        "price_gain_top": take(
            lambda row: int(row["gain_rank"]) <= limit, lambda row: int(row["gain_rank"])
        ),
        "price_loss_top": take(
            lambda row: int(row["loss_rank"]) <= limit, lambda row: int(row["loss_rank"])
        ),
        "important_weak": take(
            lambda row: (
                (_number(row.get("price_change_ratio_pct")) or 0) < 0
                and int(row["important_weak_rank"]) <= limit
            ),
            lambda row: int(row["important_weak_rank"]),
        ),
        "limit_up": take(
            lambda row: bool(row.get("is_limit_up")), lambda row: int(row["turnover_rank"])
        ),
        "limit_break": take(
            lambda row: bool(row.get("is_limit_break")), lambda row: int(row["turnover_rank"])
        ),
        "new_high": take(
            lambda row: bool(row.get("new_high_flag")) and int(row["new_high_rank"]) <= limit,
            lambda row: int(row["new_high_rank"]),
        ),
        "high_volume_decline": take(
            lambda row: (
                (_number(row.get("price_change_1m_pct")) or 0) < 0
                and (_number(row.get("turnover_growth_1m")) or 0) >= 0.5
                and int(row["volume_down_rank"]) <= limit
            ),
            lambda row: int(row["volume_down_rank"]),
        ),
    }


def _positive_number(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _top_share(
    rows: list[dict[str, Any]], field: str, denominator: Any, count: int
) -> float | None:
    total = _positive_number(denominator)
    if total is None:
        return None
    values = sorted(
        (
            value
            for row in rows
            if (value := _positive_number(row.get(field))) is not None
        ),
        reverse=True,
    )
    if not values:
        return None
    return sum(values[:count]) / total * 100


def _sector_structure_blocks(
    rows: list[dict[str, Any]], current: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    sample = rows[0] if rows else {}
    float_total = sample.get("sector_float_market_cap_sum")
    total_total = sample.get("sector_total_market_cap_sum")
    market_cap_structure = {
        "valid_member_count": current.get("valid_member_count"),
        "float_market_cap_valid_member_count": sample.get(
            "float_market_cap_valid_member_count"
        ),
        "total_market_cap_valid_member_count": sample.get(
            "total_market_cap_valid_member_count"
        ),
        **{
            f"float_cap_top{count}_share_pct": _top_share(
                rows, "float_market_cap", float_total, count
            )
            for count in (1, 3, 5)
        },
        **{
            f"total_cap_top{count}_share_pct": _top_share(
                rows, "total_market_cap", total_total, count
            )
            for count in (1, 3, 5)
        },
    }
    largest_members = [
        {
            "ticker": row.get("thscode") or row.get("ticker"),
            "name": row.get("stock_name"),
            "float_market_cap": row.get("float_market_cap"),
            "total_market_cap": row.get("total_market_cap"),
            "change_pct": row.get("price_change_ratio_pct"),
            "turnover": row.get("turnover"),
            "turnover_to_float_cap_pct": row.get("turnover_to_float_cap_pct"),
        }
        for row in sorted(
            (
                row
                for row in rows
                if _positive_number(row.get("float_market_cap")) is not None
            ),
            key=lambda row: _number(row.get("float_market_cap")) or 0,
            reverse=True,
        )[:5]
    ]
    turnover_denominator = sample.get("sector_turnover_sum")
    turnover_structure = {
        **{
            f"turnover_top{count}_share_pct": _top_share(
                rows, "turnover", turnover_denominator, count
            )
            for count in (1, 3, 5)
        },
        "turnover_1m_top1_share_pct": current.get(
            "turnover_1m_top1_share_pct"
        ),
        "turnover_1m_top3_share_pct": current.get(
            "turnover_1m_top3_share_pct"
        ),
        "turnover_1m_top5_share_pct": current.get(
            "turnover_1m_top5_share_pct"
        ),
    }
    return market_cap_structure, largest_members, turnover_structure


def _core_member_trajectories(
    reader: MarketContextReader,
    trade_date: date,
    actual_at: datetime,
    ranked_rows: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sorted(
        ranked_rows,
        key=lambda item: (
            int(item.get("turnover_1m_rank") or 999999),
            int(item.get("turnover_rank") or 999999),
        ),
    ):
        thscode = str(row.get("thscode") or "")
        if thscode and thscode not in seen:
            selected.append(row)
            seen.add(thscode)
        if len(selected) >= limit:
            break
    result = []
    for member in selected:
        timeline = reader.stock_timeline(trade_date, actual_at, str(member["thscode"]))
        result.append(
            {
                "ticker": member.get("thscode") or member.get("ticker"),
                "name": member.get("stock_name"),
                "trajectory": _compress_stock_trajectory(
                    timeline,
                    recent_minutes=CORE_STOCK_TRAJECTORY_MINUTES,
                    early_event_limit=0,
                ),
            }
        )
    return result


def _sector_metrics(
    current: dict[str, Any],
    prev_1m: dict[str, Any] | None,
    prev_5m: dict[str, Any] | None,
    prev_15m: dict[str, Any] | None,
    prev_10m: dict[str, Any] | None = None,
    prev_30m: dict[str, Any] | None = None,
    timeline_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    turnover_1m = current.get("turnover_delta_1m_total")
    prev_turnover_1m = prev_1m.get("turnover_delta_1m_total") if prev_1m else None
    turnover_5m = _difference(
        current.get("turnover_total"), prev_5m.get("turnover_total") if prev_5m else None
    )
    prev_turnover_5m = (
        _difference(prev_5m.get("turnover_total"), prev_10m.get("turnover_total"))
        if prev_5m and prev_10m
        else None
    )
    turnover_15m, turnover_15m_valid_minutes, prev_turnover_15m, _ = (
        _turnover_windows(timeline_rows or [current], "turnover_delta_1m_total")
    )
    return {
        "change_pct": current.get("index_change_ratio_pct"),
        "change_1m_pct": current.get("index_change_1m_pct"),
        "change_5m_pct": _pct_change(
            current.get("index_last_price"),
            prev_5m.get("index_last_price") if prev_5m else None,
        ),
        "change_15m_pct": _pct_change(
            current.get("index_last_price"),
            prev_15m.get("index_last_price") if prev_15m else None,
        ),
        "member_count": current.get("member_total"),
        "valid_member_count": current.get("valid_member_count"),
        "up_count": current.get("up_count"),
        "down_count": current.get("down_count"),
        "flat_count": current.get("flat_count"),
        "up_ratio": current.get("up_ratio"),
        "up_ratio_change_1m": _difference(
            current.get("up_ratio"), prev_1m.get("up_ratio") if prev_1m else None
        ),
        "up_ratio_change_5m": _difference(
            current.get("up_ratio"), prev_5m.get("up_ratio") if prev_5m else None
        ),
        "up_ratio_change_15m": _difference(
            current.get("up_ratio"), prev_15m.get("up_ratio") if prev_15m else None
        ),
        "down_ratio": current.get("down_ratio"),
        "limit_up_count": current.get("limit_up_count"),
        "limit_down_count": current.get("limit_down_count"),
        "limit_break_count": current.get("limit_break_count"),
        "new_high_count": current.get("new_high_count"),
        "new_low_count": current.get("new_low_count"),
        "new_high_ratio": current.get("new_high_ratio"),
        "new_low_ratio": current.get("new_low_ratio"),
        "turnover": current.get("turnover_total"),
        "total_market_cap": current.get("total_market_cap"),
        "float_market_cap": current.get("float_market_cap"),
        "turnover_to_float_cap_pct": _turnover_to_float_cap_pct(
            current.get("turnover_total"), current.get("float_market_cap")
        ),
        "turnover_1m": turnover_1m,
        "turnover_5m": turnover_5m,
        "turnover_15m": turnover_15m,
        "turnover_15m_valid_minutes": turnover_15m_valid_minutes,
        "turnover_15m_to_float_cap_pct": _turnover_to_float_cap_pct(
            turnover_15m, current.get("float_market_cap")
        ),
        "prev_turnover_1m": prev_turnover_1m,
        "prev_turnover_5m": prev_turnover_5m,
        "prev_turnover_15m": prev_turnover_15m,
        "turnover_1m_change_pct": _pct_change(turnover_1m, prev_turnover_1m),
        "turnover_5m_change_pct": _pct_change(turnover_5m, prev_turnover_5m),
        "turnover_15m_change_pct": _pct_change(turnover_15m, prev_turnover_15m),
        "cum_market_share": current.get("turnover_market_share_pct"),
        "instant_market_share": current.get("turnover_1m_market_share_pct"),
        "instant_share_change_1m": _difference(
            current.get("turnover_1m_market_share_pct"),
            prev_1m.get("turnover_1m_market_share_pct") if prev_1m else None,
        ),
        "instant_share_change_5m": _difference(
            current.get("turnover_1m_market_share_pct"),
            prev_5m.get("turnover_1m_market_share_pct") if prev_5m else None,
        ),
        "instant_share_change_15m": _difference(
            current.get("turnover_1m_market_share_pct"),
            prev_15m.get("turnover_1m_market_share_pct") if prev_15m else None,
        ),
        "cum_rank": current.get("turnover_share_rank"),
        "instant_rank": current.get("turnover_1m_share_rank"),
        "candidate_rank": current.get("candidate_rank"),
        "data_status": current.get("state_data_status"),
        "source_age_seconds": current.get("state_source_age_seconds"),
        "is_fallback": current.get("state_is_fallback"),
        "turnover_1m_concentration_pct": {
            "top1": current.get("turnover_1m_top1_share_pct"),
            "top3": current.get("turnover_1m_top3_share_pct"),
            "top5": current.get("turnover_1m_top5_share_pct"),
        },
        "comparison_window": {
            "current_time": current.get("scheduled_time"),
            "prev_1m_time": prev_1m.get("scheduled_time") if prev_1m else None,
            "prev_5m_time": prev_5m.get("scheduled_time") if prev_5m else None,
            "prev_15m_time": prev_15m.get("scheduled_time") if prev_15m else None,
            "same_half_day_only": True,
        },
    }


def _stock_sector_contexts(
    selected: list[dict[str, Any]],
    all_relative_rows: list[dict[str, Any]],
    ranked_rows: list[dict[str, Any]],
    stock_rows: list[dict[str, Any]],
    thscode: str,
    group_limit: int,
) -> list[dict[str, Any]]:
    relative = _group_relative_rows(all_relative_rows)
    ranked: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ranked_rows:
        ranked[(row["sector_type"], row["sector_code"])].append(row)
    _, _, _, stock_prev_15m = _row_offsets(stock_rows)
    contexts = []
    for selected_row in selected:
        key = (selected_row["sector_type"], selected_row["sector_code"])
        points = relative[key]
        point_rows = sorted(points.values(), key=lambda row: row["scheduled_time"])
        current, prev_1m, prev_5m, prev_15m = _row_offsets(point_rows)
        prev_10m = _row_at_offset(point_rows, 10)
        prev_30m = _row_at_offset(point_rows, 30)
        members = ranked.get(key, [])
        target = next((row for row in members if row["thscode"] == thscode), None)
        current_excess = _difference(
            stock_rows[-1].get("price_change_ratio_pct"), current.get("index_change_ratio_pct")
        )
        base_excess = (
            _difference(
                stock_prev_15m.get("price_change_ratio_pct"),
                prev_15m.get("index_change_ratio_pct"),
            )
            if stock_prev_15m and prev_15m
            else None
        )
        if current_excess is None:
            relative_label = None
        elif current_excess >= 1:
            relative_label = "明显强于板块"
        elif current_excess <= -1:
            relative_label = "明显弱于板块"
        else:
            relative_label = "与板块大致同步"
        position = None
        if target:
            position = {
                "turnover_rank": target.get("turnover_rank"),
                "turnover_1m_rank": target.get("turnover_1m_rank"),
                "gain_rank": target.get("gain_rank"),
                "float_market_cap_rank": (
                    target.get("float_market_cap_rank")
                    if _number(target.get("float_market_cap")) not in {None, 0}
                    else None
                ),
                "total_market_cap_rank": (
                    target.get("total_market_cap_rank")
                    if _number(target.get("total_market_cap")) not in {None, 0}
                    else None
                ),
                "turnover_to_float_cap_rank": (
                    target.get("turnover_to_float_cap_rank")
                    if target.get("turnover_to_float_cap_pct") is not None
                    else None
                ),
                "turnover_15m_to_float_cap_rank": (
                    target.get("turnover_15m_to_float_cap_rank")
                    if target.get("turnover_15m_to_float_cap_pct") is not None
                    else None
                ),
                "valid_member_count": target.get("valid_member_count"),
                "float_market_cap_valid_member_count": target.get(
                    "float_market_cap_valid_member_count"
                ),
                "total_market_cap_valid_member_count": target.get(
                    "total_market_cap_valid_member_count"
                ),
                "turnover_to_float_cap_valid_member_count": target.get(
                    "turnover_to_float_cap_valid_member_count"
                ),
                "turnover_15m_to_float_cap_valid_member_count": target.get(
                    "turnover_15m_to_float_cap_valid_member_count"
                ),
                "turnover_share_pct": _share(target.get("turnover"), current.get("turnover_total")),
                "turnover_1m_share_pct": _share(
                    target.get("turnover_delta_1m"), current.get("turnover_delta_1m_total")
                ),
                "excess_change_pct": current_excess,
                "relative_strength": relative_label,
                "relative_strength_threshold_pct": 1.0,
                "relative_strength_change_15m_pct": _difference(current_excess, base_excess),
                "top5_turnover_carrier": int(target.get("turnover_rank") or 999999) <= 5,
                "top5_price_expression": int(target.get("gain_rank") or 999999) <= 5,
            }
        contexts.append(
            {
                "sector_type": key[0],
                "sector_id": key[1],
                "sector_name": current.get("sector_name"),
                "selection_facts": {
                    "selection_score": selected_row.get("selection_score"),
                    "selection_model": "TRADING_DIRECTION_V2",
                    "stock_excess_change_pct_used_for_selection": selected_row.get(
                        "selection_excess_change_pct"
                    ),
                    "score_breakdown": selected_row.get("selection_score_breakdown"),
                    "score_is_for_selection_only": True,
                    "all_valid_memberships_considered": len(
                        {
                            item["sector_code"]
                            for item in all_relative_rows
                            if item.get("relative_row") == 1
                        }
                    ),
                },
                "current_state": _sector_metrics(
                    current,
                    prev_1m,
                    prev_5m,
                    prev_15m,
                    prev_10m,
                    prev_30m,
                    point_rows,
                ),
                "stock_position": position,
                "reference_stocks": {
                    "turnover_carriers": _member_groups(members, group_limit)["turnover_top"],
                    "strong_price_expression": _member_groups(members, group_limit)[
                        "price_gain_top"
                    ],
                    "important_weak": _member_groups(members, group_limit)["important_weak"],
                },
            }
        )
    return contexts


def _build_stock_context(
    reader: MarketContextReader,
    trade_date: date,
    node: dict[str, Any],
    ticker: str,
    thscode: str,
    *,
    sector_limit: int,
    reference_limit: int,
    trajectory_minutes: int,
) -> dict[str, Any]:
    stock_rows = reader.stock_timeline(trade_date, node["scheduled_time"], thscode)
    if not stock_rows:
        return {
            "status": "NOT_FOUND",
            "ticker": ticker,
            "thscode": thscode,
            "error": "目标时间及之前没有找到该股票的有效分钟派生行情",
        }
    auction_rows = reader.stock_auction_rows(
        trade_date, node["scheduled_time"], thscode
    )
    sector_rows = reader.stock_sector_rows(trade_date, node["scheduled_time"], thscode)
    profiles = reader.stock_sector_profiles(
        trade_date,
        node["scheduled_time"],
        node["collection_id"],
        thscode,
    )
    selected = _select_stock_sectors(
        sector_rows,
        sector_limit,
        _number(stock_rows[-1].get("price_change_ratio_pct")),
        profiles,
    )
    selected_keys = {(row["sector_type"], row["sector_code"]) for row in selected}
    alternatives = sorted(
        (
            row
            for row in sector_rows
            if int(row.get("relative_row", 0)) == 1
            and (row["sector_type"], row["sector_code"]) not in selected_keys
        ),
        key=lambda row: row.get("selection_score") or 0,
        reverse=True,
    )[:3]
    selected_codes = [row["sector_code"] for row in selected]
    ranked = reader.ranked_members(
        trade_date,
        node["collection_id"],
        selected_codes,
        target_thscode=thscode,
        group_limit=reference_limit,
    )
    current = stock_rows[-1]
    return {
        "status": "OK",
        "ticker": ticker,
        "thscode": thscode,
        "name": current.get("stock_name"),
        "data_status": node.get("state_data_status"),
        "source_age_seconds": node.get("state_source_age_seconds"),
        "is_fallback": node.get("state_is_fallback"),
        "current_state": _stock_current_block(stock_rows),
        "key_trajectory": _compress_stock_trajectory(
            stock_rows, recent_minutes=trajectory_minutes
        ),
        "auction_context": _stock_auction_context(
            auction_rows, stock_rows, node["scheduled_time"]
        ),
        "direction_selection": {
            "model": "TRADING_DIRECTION_V2",
            "policy": (
                "最多返回5个方向；有行业归属时保留1个行业位置，其余按战略方向优先级、"
                "盘中共同运动、个股成交角色、板块市场表达和当前相对贴合度综合排序"
            ),
            "score_range": "0_to_100",
            "selected_count": len(selected),
            "considered_count": len(
                {row["sector_code"] for row in sector_rows if row.get("relative_row") == 1}
            ),
            "next_alternatives": [
                {
                    "sector_type": row["sector_type"],
                    "sector_id": row["sector_code"],
                    "sector_name": row["sector_name"],
                    "selection_score": row.get("selection_score"),
                    "score_breakdown": row.get("selection_score_breakdown"),
                }
                for row in alternatives
            ],
        },
        "primary_sectors": _stock_sector_contexts(
            selected, sector_rows, ranked, stock_rows, thscode, reference_limit
        ),
        "compression": {
            "source_stock_nodes": len(stock_rows),
            "returned_trajectory_nodes": len(
                _compress_stock_trajectory(stock_rows, recent_minutes=trajectory_minutes)
            ),
            "valid_sector_memberships_considered": len(
                {row["sector_code"] for row in sector_rows if row.get("relative_row") == 1}
            ),
            "returned_primary_sectors": len(selected),
            "full_member_minutes_returned": False,
        },
    }


def _choose_sector(
    candidates: list[dict[str, Any]], states: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    state_map = {(row["sector_type"], row["sector_code"]): row for row in states}
    scored = []
    for candidate in candidates:
        state = state_map.get((candidate["sector_type"], candidate["sector_code"]))
        if not state:
            continue
        score = _sector_expression_score(state) + (
            0.15 if candidate["sector_type"] != "style" else 0
        )
        scored.append({**candidate, "current_expression_score": score})
    scored.sort(key=lambda row: row["current_expression_score"], reverse=True)
    return (scored[0] if scored else None), scored


def _build_sector_context(
    reader: MarketContextReader,
    trade_date: date,
    node: dict[str, Any],
    candidates: list[dict[str, Any]],
    match_type: str,
) -> dict[str, Any]:
    states = reader.current_sector_states(
        trade_date, node["collection_id"], [row["sector_code"] for row in candidates]
    )
    selected, scored = _choose_sector(candidates, states)
    if not selected:
        return {
            "status": "NOT_FOUND",
            "error": "找到了板块映射，但目标时间没有对应板块状态",
            "matched_candidates": candidates[:5],
        }
    timeline = reader.sector_timeline(
        selected["sector_type"], selected["sector_code"], trade_date, node["scheduled_time"]
    )
    current, prev_1m, prev_5m, prev_15m = _row_offsets(timeline)
    prev_10m = _row_at_offset(timeline, 10)
    prev_30m = _row_at_offset(timeline, 30)
    matched_facts = reader.sector_market_facts(
        trade_date,
        [node["collection_id"]],
        selected["sector_type"],
        [row["sector_code"] for row in scored],
    )
    matched_fact_map = {row["sector_code"]: row for row in matched_facts}
    strategic_relation = reader.strategic_relations(
        trade_date,
        selected["sector_type"],
        [row["sector_code"] for row in scored],
    )
    ranked = reader.ranked_members(
        trade_date,
        node["collection_id"],
        [selected["sector_code"]],
        group_limit=5,
    )
    groups = _member_groups(ranked, 5)
    market_cap_structure, largest_members, turnover_structure = (
        _sector_structure_blocks(ranked, current)
    )
    core_member_trajectories = _core_member_trajectories(
        reader, trade_date, node["scheduled_time"], ranked
    )
    turnover_top = sorted(
        (row for row in ranked if int(row["turnover_rank"]) <= 5),
        key=lambda row: int(row["turnover_rank"]),
    )
    cumulative_concentration = {
        f"top{count}": _share(
            sum(
                (_number(row.get("turnover")) or 0)
                for row in turnover_top
                if int(row["turnover_rank"]) <= count
            ),
            current.get("turnover_total"),
        )
        for count in (1, 3, 5)
    }
    matched_sectors = []
    state_map = {(row["sector_type"], row["sector_code"]): row for row in states}
    for row in scored[:10]:
        state = state_map.get((row["sector_type"], row["sector_code"]), {})
        fact = matched_fact_map.get(row["sector_code"], {})
        matched_sectors.append(
            {
                "sector_name": row["sector_name"],
                "sector_type": row["sector_type"],
                "sector_id": row["sector_code"],
                "match_score": row["current_expression_score"],
                "change_pct": state.get("index_change_ratio_pct"),
                "instant_share": fact.get("turnover_1m_market_share_pct"),
                "candidate_rank": fact.get("candidate_rank"),
            }
        )
    return {
        "status": "OK",
        "resolution": {
            "match_type": match_type,
            "sector_type": selected["sector_type"],
            "sector_id": selected["sector_code"],
            "sector_name": selected["sector_name"],
            "current_expression_score": selected["current_expression_score"],
            "other_matches": [
                {
                    "sector_type": row["sector_type"],
                    "sector_id": row["sector_code"],
                    "sector_name": row["sector_name"],
                    "current_expression_score": row["current_expression_score"],
                }
                for row in scored[1:5]
            ],
        },
        "current_state": {
            **_sector_metrics(
                current,
                prev_1m,
                prev_5m,
                prev_15m,
                prev_10m,
                prev_30m,
                timeline,
            ),
            "data_status": node.get("state_data_status"),
            "source_age_seconds": node.get("state_source_age_seconds"),
            "is_fallback": node.get("state_is_fallback"),
            "price_distribution": {
                "limit_up": current.get("limit_up_count"),
                "up_5_to_limit": current.get("up_5_to_limit_count"),
                "up_1_to_5": current.get("up_1_to_5_count"),
                "up_0_to_1": current.get("up_0_to_1_count"),
                "flat": current.get("flat_count"),
                "down_0_to_1": current.get("down_0_to_1_count"),
                "down_1_to_5": current.get("down_1_to_5_count"),
                "down_5_to_limit": current.get("down_5_to_limit_count"),
                "limit_down": current.get("limit_down_count"),
            },
            "cumulative_turnover_concentration_pct": cumulative_concentration,
        },
        "key_trajectory": _compress_sector_trajectory(timeline),
        "key_stocks": groups,
        "market_cap_structure": market_cap_structure,
        "largest_members": largest_members,
        "turnover_structure": turnover_structure,
        "core_stock_trajectories": core_member_trajectories,
        "strategic_relation": strategic_relation,
        "matched_sectors": matched_sectors,
        "compression": {
            "source_sector_nodes": len(timeline),
            "returned_trajectory_nodes": len(_compress_sector_trajectory(timeline)),
            "member_count": current.get("member_total"),
            "returned_unique_key_stocks": len({row["thscode"] for row in ranked}),
            "group_limit": 5,
            "full_member_minutes_returned": False,
        },
    }


def get_market_context_state(
    target_type: str,
    target_time: str | None = None,
    ticker: str | int | None = None,
    tickers: list[str | int] | None = None,
    sector_id: str | None = None,
    sector_name: str | None = None,
    trade_date: str | None = None,
    *,
    package_root: Path = PACKAGE_ROOT,
    client: Any | None = None,
) -> dict[str, Any]:
    """Return compact stock(s) or sector context using only existing ClickHouse facts."""

    started = perf_counter()
    try:
        if target_type not in {"stock", "stocks", "sector"}:
            raise ValueError("target_type必须是stock、stocks或sector")
        parsed_time: time | None = None
        has_seconds = False
        if target_time is not None:
            parsed_time, has_seconds = _parse_time(target_time)
        stock_inputs: list[Any] = []
        if target_type == "stock":
            if ticker is None:
                raise ValueError("stock模式必须提供ticker")
            stock_inputs = [ticker]
        elif target_type == "stocks":
            if not isinstance(tickers, list) or not 2 <= len(tickers) <= MAX_STOCKS:
                raise ValueError(f"stocks模式必须提供2到{MAX_STOCKS}只股票")
            stock_inputs = tickers
        else:
            if bool(sector_id) == bool(sector_name):
                raise ValueError("sector模式必须且只能提供sector_id或sector_name中的一个")
    except (TypeError, ValueError) as exc:
        return {"status": "INVALID_REQUEST", "error": str(exc)}

    own_client = client is None
    writer = None
    if own_client:
        settings = Settings.load()
        writer = ClickHouseWriter(
            settings.clickhouse_host,
            settings.clickhouse_port,
            settings.clickhouse_database,
            settings.clickhouse_username,
            settings.clickhouse_password,
        )
        client = writer.client
    reader = MarketContextReader(client)
    try:
        if trade_date is not None:
            selected_date = _parse_date(trade_date)
            date_source = "explicit"
        else:
            selected_date = _default_trade_date(package_root)
            date_source = "latest_valid_market_package"
            if selected_date is None:
                selected_date = reader.latest_database_date()
                date_source = "latest_database_trade_date"
            if selected_date is None:
                return {"status": "NOT_FOUND", "error": "没有找到可用交易日"}
        normalized_stocks: list[tuple[str, str]] = []
        sector_candidates: list[dict[str, Any]] = []
        sector_match_type = ""
        if target_type in {"stock", "stocks"}:
            normalized_stocks = list(
                dict.fromkeys(
                    _resolve_stock_input(reader, selected_date, item) for item in stock_inputs
                )
            )
            if target_type == "stocks" and len(normalized_stocks) < 2:
                raise ValueError("stocks模式去重后至少需要2只股票")
        else:
            sector_candidates, sector_match_type = reader.resolve_sector_candidates(
                sector_id, sector_name, selected_date
            )
            if not sector_candidates:
                return {"status": "NOT_FOUND", "error": "没有找到匹配的有效板块"}
        if parsed_time is None:
            target_at = datetime.combine(selected_date, time.max, tzinfo=SHANGHAI)
        else:
            target_at = datetime.combine(selected_date, parsed_time, tzinfo=SHANGHAI)
            if not has_seconds:
                target_at += timedelta(seconds=59)
        if target_type in {"stock", "stocks"}:
            node = reader.resolve_stock_node(
                selected_date, target_at, [thscode for _, thscode in normalized_stocks]
            )
        else:
            node = reader.resolve_sector_node(selected_date, target_at, sector_candidates)
        if node is None:
            return {
                "status": "NOT_FOUND",
                "trade_date": selected_date.isoformat(),
                "requested_time": target_time,
                "error": "目标时间及之前没有可用市场状态节点",
                "available_time_range": reader.available_time_range(selected_date),
            }
        resolved_requested_time, source_age_seconds = _requested_time_quality(
            selected_date,
            parsed_time,
            has_seconds,
            target_time,
            node["scheduled_time"],
        )
        is_fallback = source_age_seconds > 0
        node.update(
            {
                "state_data_status": "FALLBACK" if is_fallback else "CURRENT",
                "state_source_age_seconds": source_age_seconds,
                "state_is_fallback": is_fallback,
            }
        )
        if target_type in {"stock", "stocks"}:
            sector_limit = 4
            reference_limit = 3
            contexts = [
                _build_stock_context(
                    reader,
                    selected_date,
                    node,
                    normalized_ticker,
                    thscode,
                    sector_limit=sector_limit,
                    reference_limit=reference_limit,
                    trajectory_minutes=(
                        STOCK_TRAJECTORY_MINUTES
                        if target_type == "stock"
                        else MULTI_STOCK_TRAJECTORY_MINUTES
                    ),
                )
                for normalized_ticker, thscode in normalized_stocks
            ]
            data: dict[str, Any] = {
                "stocks": contexts,
                "comparison_note": (
                    "每只股票按各自主要方向提供事实；不强行把不同方向放进同一板块比较"
                    if target_type == "stocks"
                    else None
                ),
            }
            ok_count = sum(context.get("status") == "OK" for context in contexts)
            overall_status = (
                "OK" if ok_count == len(contexts) else ("PARTIAL" if ok_count else "NOT_FOUND")
            )
        else:
            sector_context = _build_sector_context(
                reader, selected_date, node, sector_candidates, sector_match_type
            )
            data = {"sector": sector_context}
            overall_status = str(sector_context.get("status", "QUERY_ERROR"))
        response = {
            "status": overall_status,
            "target_type": target_type,
            "request": {
                "ticker": ticker,
                "tickers": tickers,
                "sector_id": sector_id,
                "sector_name": sector_name,
                "target_time": target_time,
                "trade_date": trade_date,
            },
            "resolved": {
                "trade_date": selected_date,
                "trade_date_source": date_source,
                "collection_id": node["collection_id"],
                "node_seq": node["node_seq"],
                "requested_time": resolved_requested_time,
                "actual_time": node["scheduled_time"],
                "session": node["session"],
                "time_offset_seconds": -source_age_seconds,
                "data_status": node["state_data_status"],
                "source_age_seconds": source_age_seconds,
                "is_fallback": is_fallback,
            },
            "data": data,
            "metric_semantics": {
                "turnover": "成交承载、成交关注度和成交份额；不代表净资金流入或流出",
                "relative_strength": "个股涨跌幅减板块指数涨跌幅；明显强弱阈值为1个百分点",
                "selection_score": (
                    "0到100分的方向筛选分；由战略方向优先级、盘中共同运动、个股成交角色、"
                    "板块市场表达和当前相对贴合度组成，只用于压缩方向，不是投资结论"
                ),
            },
            "limitations": [
                "没有逐笔主动买卖方向，不能可靠计算真实净资金流入或流出",
                "板块相关性来自现有成员映射和当前市场表达，不代表业务收入相关度",
                "近期轨迹来自分钟快照，不替代逐笔成交或盘口数据",
            ],
            "performance": {
                "query_count": reader.query_count,
                "database_elapsed_ms": round(reader.database_elapsed_ms, 3),
                "total_elapsed_ms": round((perf_counter() - started) * 1000, 3),
            },
        }
        return compact_market_context_response(_json_safe(response))
    except (TypeError, ValueError) as exc:
        return {"status": "INVALID_REQUEST", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - MCP调用需要明确返回查询失败事实
        return {
            "status": "QUERY_ERROR",
            "error": str(exc),
            "performance": {
                "query_count": reader.query_count,
                "database_elapsed_ms": round(reader.database_elapsed_ms, 3),
                "total_elapsed_ms": round((perf_counter() - started) * 1000, 3),
            },
        }
    finally:
        if writer is not None:
            writer.close()
