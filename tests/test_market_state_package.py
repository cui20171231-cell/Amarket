import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.hithink.candidate_config import CAPITAL_SECTOR_TYPES, CORE_SECTOR_TYPES
from app.hithink.schedule import MARKET_REVIEW_NODE_SEQUENCES
from app.market_state_package import (
    EMOTION_BUSINESS_FIELDS,
    EMOTION_POOL_SOURCE_FIELDS,
    OPEN_AUCTION_CURRENT_SCHEMA,
    OPEN_AUCTION_TRAJECTORY_SCHEMA,
    MarketStatePackageBuilder,
    _business_period,
    _json_safe,
    _parse_target_time,
    _subtract,
)

PACKAGE_SOURCE = Path("app/market_state_package.py").read_text(encoding="utf-8")


def test_target_time_accepts_minutes_or_seconds() -> None:
    assert _parse_target_time("10:30").isoformat() == "10:30:00"
    assert _parse_target_time("10:30:15").isoformat() == "10:30:15"


def test_business_period_never_crosses_lunch() -> None:
    morning = datetime.fromisoformat("2026-08-28T10:30:15+08:00")
    afternoon = datetime.fromisoformat("2026-08-28T13:30:15+08:00")
    assert _business_period(morning)[0] == "AM"
    assert _business_period(afternoon)[0] == "PM"
    assert _business_period(afternoon)[1].hour == 13


def test_null_propagation_is_preserved_for_temporary_deltas() -> None:
    assert _subtract(10, 4) == 6
    assert _subtract(None, 4) is None
    assert _subtract(10, None) is None


def test_json_conversion_preserves_decimal_precision_and_fixed_strings() -> None:
    value = _json_safe({"amount": Decimal("123.45"), "collection_id": b"20260828072"})
    assert value == {"amount": "123.45", "collection_id": "20260828072"}


def test_0925_core_stocks_use_all_eleven_opening_auction_minutes() -> None:
    columns = [
        "node_seq",
        "scheduled_time",
        "auction_phase",
        "stock_code",
        "stock_name",
        "auction_price",
        "auction_pct",
        "auction_amount",
        "auction_unmatched",
        "auction_turnover_pct",
        "auction_yesterday_ratio_pct",
    ]
    rows = []
    for stock_index, stock_code in enumerate(("000001.SZ", "000002.SZ"), start=1):
        for node_seq, minute in enumerate(range(15, 26), start=1):
            phase = (
                "order_entry"
                if node_seq <= 5
                else "no_cancel"
                if node_seq <= 10
                else "matched"
            )
            rows.append(
                (
                    node_seq,
                    datetime.fromisoformat(f"2026-09-04T09:{minute:02d}:00+08:00"),
                    phase,
                    stock_code,
                    f"stock-{stock_index}",
                    10.125 + stock_index,
                    None if stock_index == 2 and node_seq == 1 else node_seq / 3,
                    stock_index * 100 + node_seq + 0.555,
                    -100 * stock_index + node_seq,
                    node_seq / 7,
                    node_seq / 9,
                )
            )

    class Result:
        column_names = columns
        result_rows = rows

    class Client:
        def __init__(self) -> None:
            self.sql = ""

        def query(self, sql, parameters, settings):
            self.sql = sql
            return Result()

    client = Client()
    block = MarketStatePackageBuilder(client)._opening_auction_core_stocks(
        date.fromisoformat("2026-09-04"), 2
    )

    assert block["data_context"] == "OPEN_AUCTION"
    assert block["current_schema"] == OPEN_AUCTION_CURRENT_SCHEMA
    assert block["trajectory_schema"] == OPEN_AUCTION_TRAJECTORY_SCHEMA
    assert len(block["current_rows"]) == 2
    assert len(block["trajectory_rows"]) == 22
    for stock_code in ("000001.SZ", "000002.SZ"):
        assert [
            row[1] for row in block["trajectory_rows"] if row[0] == stock_code
        ] == [f"09:{minute:02d}" for minute in range(15, 26)]
    second_0915 = next(
        row
        for row in block["trajectory_rows"]
        if row[0] == "000002.SZ" and row[1] == "09:15"
    )
    assert second_0915[3] is None
    first_current = next(
        row for row in block["current_rows"] if row[0] == "000001.SZ"
    )
    assert first_current[2] == 11.13
    assert first_current[-1] == -89
    assert "auction_volume_ratio" not in block["current_schema"]
    assert "node_seq BETWEEN 1 AND 11" in client.sql


def test_capital_top_lists_separate_cumulative_and_instant_share_changes() -> None:
    builder = MarketStatePackageBuilder(client=None)
    rows = [
        {
            "sector_type": "concept",
            "sector_code": "A",
            "turnover_market_share_delta_15m": 2.0,
            "turnover_1m_market_share_delta_15m": -1.0,
        },
        {
            "sector_type": "concept",
            "sector_code": "B",
            "turnover_market_share_delta_15m": -3.0,
            "turnover_1m_market_share_delta_15m": 4.0,
        },
        {
            "sector_type": "concept",
            "sector_code": "C",
            "turnover_market_share_delta_15m": 0.0,
            "turnover_1m_market_share_delta_15m": 0.0,
        },
    ]
    block = builder._capital_block(rows, 10)
    assert [
        row["sector_code"]
        for row in block["concept"]["cumulative_share_rising_top"]
    ] == ["A"]
    assert [
        row["sector_code"]
        for row in block["concept"]["cumulative_share_falling_top"]
    ] == ["B"]
    assert [
        row["sector_code"]
        for row in block["concept"]["instant_1m_share_rising_top"]
    ] == ["B"]
    assert [
        row["sector_code"]
        for row in block["concept"]["instant_1m_share_falling_top"]
    ] == ["A"]


def test_style_remains_in_capital_but_not_core_sector_competition() -> None:
    assert CAPITAL_SECTOR_TYPES == ("concept", "industry", "style")
    assert CORE_SECTOR_TYPES == ("concept", "industry")
    assert "for sector_type in CAPITAL_SECTOR_TYPES" in PACKAGE_SOURCE
    assert "for sector_type in CORE_SECTOR_TYPES" in PACKAGE_SOURCE
    assert PACKAGE_SOURCE.count("sector_type IN {core_sector_types:Array(String)}") == 4


def test_package_reader_is_wired_into_both_mcp_servers() -> None:
    gateway = Path("app/ai_gateway.py").read_text(encoding="utf-8")
    plugin = Path("app/clickhouse_plugin_server.py").read_text(encoding="utf-8")
    assert "def get_market_state_package(" in gateway
    assert '"name": "get_market_state_package"' in plugin


def test_example_package_id_matches_its_resolved_node() -> None:
    package = json.loads(
        Path("docs/market_state_package_20260828_1030.json").read_text(encoding="utf-8")
    )
    assert package["package_id"] == package["target"]["resolved_collection_id"]
    assert package["package_id"] == "20260828072"


def test_core_sector_trajectories_keep_candidate_and_state_semantics_separate() -> None:
    package = json.loads(
        Path("docs/market_state_package_20260828_1030.json").read_text(encoding="utf-8")
    )
    assert set(package["core_sectors"]) == {"concept", "industry"}
    sectors = [
        sector
        for sector_type in ("concept", "industry")
        for sector in package["core_sectors"][sector_type]
    ]
    assert sectors
    for sector in sectors:
        recent = sector["trajectory_recent_5_nodes"]
        key_nodes = sector["trajectory_15m_last_5"]
        assert 1 <= len(recent) <= 5
        assert 1 <= len(key_nodes) <= 5
        assert all(node["candidate_rank"] is None for node in recent)
        assert all(node["candidate_score_v1"] is None for node in recent)
        assert all(node["state_data_status"] == "CURRENT" for node in recent)
        assert all(node["state_source_age_seconds"] == 0 for node in recent)
        assert all(
            (node["candidate_rank"] is None)
            == (node["candidate_score_v1"] is None)
            for node in key_nodes
        )


def test_1030_trajectory_preserves_the_real_fallback_source() -> None:
    package = json.loads(
        Path("docs/market_state_package_20260828_1030.json").read_text(encoding="utf-8")
    )
    sector = package["core_sectors"]["concept"][0]
    recent_current = sector["trajectory_recent_5_nodes"][-1]
    key_current = sector["trajectory_15m_last_5"][-1]
    assert recent_current["scheduled_time"].startswith("2026-08-28T10:29:15")
    assert key_current["scheduled_time"].startswith("2026-08-28T10:30:15")
    assert key_current["state_data_status"] == "FALLBACK"
    assert key_current["state_source_scheduled_time"].startswith(
        "2026-08-28T10:29:15"
    )
    assert key_current["state_source_age_seconds"] == 60


def test_v2_market_intraday_path_uses_the_fixed_axis_without_future_nodes() -> None:
    package = json.loads(
        Path("docs/market_state_package_20260828_1030.json").read_text(encoding="utf-8")
    )
    assert package["package_version"] == "V2"
    nodes = package["market"]["intraday_trajectory"]
    assert [node["node_seq"] for node in nodes] == [11, 12, 27, 42, 57, 72]
    assert all(
        node["scheduled_time"] <= package["target"]["resolved_scheduled_time"]
        for node in nodes
    )
    current = nodes[-1]
    assert current["state_data_status"] == "FALLBACK"
    assert current["state_source_scheduled_time"].startswith("2026-08-28T10:29:15")
    assert current["state_source_age_seconds"] == 60


def test_emotion_intraday_keeps_business_fields_and_pool_source_status() -> None:
    columns = [
        "scheduled_time",
        *EMOTION_POOL_SOURCE_FIELDS,
        *EMOTION_BUSINESS_FIELDS,
    ]
    values = [
        datetime.fromisoformat("2026-09-01T10:30:15+08:00"),
        "CURRENT",
        datetime.fromisoformat("2026-09-01T10:30:15+08:00"),
        0,
        *range(19),
    ]

    class Result:
        def __init__(self) -> None:
            self.column_names = columns
            self.result_rows = [values]

    class Client:
        def __init__(self) -> None:
            self.sql = ""
            self.parameters = {}

        def query(self, sql, parameters, settings):
            self.sql = sql
            self.parameters = parameters
            return Result()

    client = Client()
    target = datetime.fromisoformat("2026-09-01T10:30:15+08:00")
    rows = MarketStatePackageBuilder(client)._emotion_intraday_trajectory(
        target.date(), target
    )

    assert len(rows) == 1
    assert set(rows[0]) == {
        "scheduled_time",
        *EMOTION_POOL_SOURCE_FIELDS,
        *EMOTION_BUSINESS_FIELDS,
    }
    assert "collection_id" not in rows[0]
    assert "pool_source_collection_id" not in rows[0]
    assert "state_data_status" not in rows[0]
    assert rows[0]["pool_data_status"] == "CURRENT"
    assert rows[0]["pool_source_age_seconds"] == 0
    assert "node_seq IN" in client.sql
    assert "scheduled_time<=" in client.sql
    assert client.parameters["node_sequences"] == sorted(
        MARKET_REVIEW_NODE_SEQUENCES
    )


def test_core_sector_rank_nulls_have_explicit_statuses() -> None:
    assert '"candidate_rank_status": "NOT_EVALUATED"' in PACKAGE_SOURCE
    assert 'else "OUTSIDE_TOP_N"' in PACKAGE_SOURCE


def test_current_package_uses_only_current_core_sector_intraday_trajectories() -> None:
    assert "core_sector_intraday = {" in PACKAGE_SOURCE
    assert '"current_core_sector_count": len(selected_core_sector_rows)' in PACKAGE_SOURCE
    assert '"trajectories": [' in PACKAGE_SOURCE
    assert "target_scheduled_time,\n            intraday_nodes," in PACKAGE_SOURCE
    assert "self._concept_intraday_block(" not in PACKAGE_SOURCE
    assert 'trajectory["key_nodes"][-5:]' in PACKAGE_SOURCE
