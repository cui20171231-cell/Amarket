from __future__ import annotations

import argparse
import re
from datetime import date, datetime

from app.hithink.config import Settings
from app.hithink.schedule import SHANGHAI
from app.hithink.writer import ClickHouseWriter

TARGET_TABLES = (
    "hithink_snapshot_derived",
    "hithink_concept_state",
    "hithink_industry_state",
    "hithink_style_state",
    "hithink_auction_snapshot",
)
SECTOR_TABLES = {
    "concept": "hithink_concept_state",
    "industry": "hithink_industry_state",
    "style": "hithink_style_state",
}


def rebuild(trade_date: date) -> dict[str, tuple[int, int, int]]:
    date_suffix = trade_date.strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", date_suffix):
        raise ValueError("invalid trade date")
    shares_join = f"market.tmp_market_cap_shares_{date_suffix}"
    sector_joins = {
        sector_type: f"market.tmp_market_cap_{sector_type}_{date_suffix}"
        for sector_type in SECTOR_TABLES
    }
    temporary_tables = [shares_join, *sector_joins.values()]

    settings = Settings.load(require_api_key=False)
    writer = ClickHouseWriter(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        settings.clickhouse_username,
        settings.clickhouse_password,
    )
    client = writer.client
    try:
        existing_temporary = client.query(
            "SELECT name FROM system.tables WHERE database='market' "
            "AND startsWith(name, {prefix:String})",
            parameters={"prefix": "tmp_market_cap_"},
        ).result_rows
        if existing_temporary:
            raise RuntimeError(
                f"market-cap temporary tables already exist: {existing_temporary}"
            )

        client.command(
            f"""
            CREATE TABLE {shares_join}
            (
                thscode String,
                total_shares Nullable(UInt64),
                float_shares Nullable(UInt64)
            )
            ENGINE = Join(ANY, LEFT, thscode)
            """
        )
        client.command(
            f"""
            INSERT INTO {shares_join}
            SELECT thscode, total_shares, float_shares
            FROM market.tencent_stock_basic_info FINAL
            WHERE snapshot_date <= {{trade_date:Date}}
            """,
            parameters={"trade_date": trade_date},
        )
        share_count = client.query(f"SELECT count() FROM {shares_join}").result_rows[0][0]
        if int(share_count) == 0:
            raise RuntimeError("no share-count rows are available for market-cap rebuild")

        mutation_settings = {
            "mutations_sync": 2,
            "allow_nondeterministic_mutations": 1,
        }
        client.command(
            f"""
            ALTER TABLE market.hithink_snapshot_derived UPDATE
                total_market_cap = CAST(
                    last_price * joinGetOrNull(
                        '{shares_join}', 'total_shares', toString(thscode)
                    ),
                    'Nullable(Float64)'
                ),
                float_market_cap = CAST(
                    last_price * joinGetOrNull(
                        '{shares_join}', 'float_shares', toString(thscode)
                    ),
                    'Nullable(Float64)'
                )
            WHERE trade_date = {{trade_date:Date}}
            """,
            parameters={"trade_date": trade_date},
            settings=mutation_settings,
        )

        for sector_type, table_name in SECTOR_TABLES.items():
            sector_join = sector_joins[sector_type]
            client.command(
                f"""
                CREATE TABLE {sector_join}
                (
                    collection_id String,
                    sector_code String,
                    total_market_cap Nullable(Float64),
                    float_market_cap Nullable(Float64)
                )
                ENGINE = Join(ANY, LEFT, collection_id, sector_code)
                """
            )
            client.command(
                f"""
                INSERT INTO {sector_join}
                SELECT
                    toString(derived.collection_id),
                    membership.sector_code,
                    if(
                        countIf(derived.total_market_cap IS NOT NULL) = 0,
                        CAST(NULL, 'Nullable(Float64)'),
                        toFloat64(sum(derived.total_market_cap))
                    ),
                    if(
                        countIf(derived.float_market_cap IS NOT NULL) = 0,
                        CAST(NULL, 'Nullable(Float64)'),
                        toFloat64(sum(derived.float_market_cap))
                    )
                FROM market.hithink_snapshot_derived AS derived
                INNER JOIN
                (
                    SELECT * FROM market.sector_membership_history FINAL
                ) AS membership
                    ON membership.thscode = derived.thscode
                    AND membership.sector_type = {{sector_type:String}}
                    AND membership.observed_from <= {{trade_date:Date}}
                    AND (
                        membership.observed_to > {{trade_date:Date}}
                        OR membership.observed_to IS NULL
                    )
                WHERE derived.trade_date = {{trade_date:Date}}
                GROUP BY derived.collection_id, membership.sector_code
                """,
                parameters={"trade_date": trade_date, "sector_type": sector_type},
            )
            client.command(
                f"""
                ALTER TABLE market.{table_name} UPDATE
                    total_market_cap = joinGetOrNull(
                        '{sector_join}',
                        'total_market_cap',
                        toString(collection_id),
                        sector_code
                    ),
                    float_market_cap = joinGetOrNull(
                        '{sector_join}',
                        'float_market_cap',
                        toString(collection_id),
                        sector_code
                    )
                WHERE trade_date = {{trade_date:Date}}
                """,
                parameters={"trade_date": trade_date},
                settings=mutation_settings,
            )

        client.command(
            f"""
            ALTER TABLE market.hithink_auction_snapshot UPDATE
                total_market_cap = CAST(
                    auction_price * joinGetOrNull(
                        '{shares_join}', 'total_shares', toString(thscode)
                    ),
                    'Nullable(Float64)'
                ),
                float_market_cap = CAST(
                    auction_price * joinGetOrNull(
                        '{shares_join}', 'float_shares', toString(thscode)
                    ),
                    'Nullable(Float64)'
                )
            WHERE trade_date = {{trade_date:Date}}
            """,
            parameters={"trade_date": trade_date},
            settings=mutation_settings,
        )

        return {
            table: tuple(
                int(value)
                for value in client.query(
                    f"""
                    SELECT
                        count(),
                        countIf(total_market_cap IS NOT NULL),
                        countIf(float_market_cap IS NOT NULL)
                    FROM market.{table} FINAL
                    WHERE trade_date = {{trade_date:Date}}
                    """,
                    parameters={"trade_date": trade_date},
                ).result_rows[0]
            )
            for table in TARGET_TABLES
        }
    finally:
        for table in reversed(temporary_tables):
            client.command(f"DROP TABLE IF EXISTS {table} SYNC")
        writer.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--trade-date",
        type=date.fromisoformat,
        default=datetime.now(SHANGHAI).date(),
    )
    args = parser.parse_args()
    for table, counts in rebuild(args.trade_date).items():
        print(table, *counts)


if __name__ == "__main__":
    main()
