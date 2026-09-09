from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketIndexDefinition:
    index_code: str
    index_name: str
    index_group: str
    source_tag: str


MARKET_INDICES = (
    MarketIndexDefinition("000001.SH", "上证指数", "BROAD_MARKET", "HITHINK_INDEX"),
    MarketIndexDefinition("000300.SH", "沪深300", "BROAD_MARKET", "HITHINK_INDEX"),
    MarketIndexDefinition("000852.SH", "中证1000", "BROAD_MARKET", "HITHINK_INDEX"),
    MarketIndexDefinition("000905.SH", "中证500", "BROAD_MARKET", "HITHINK_INDEX"),
    MarketIndexDefinition("399006.SZ", "创业板指", "BROAD_MARKET", "HITHINK_INDEX"),
    MarketIndexDefinition("000688.SH", "科创50", "BROAD_MARKET", "HITHINK_INDEX"),
    MarketIndexDefinition("000016.SH", "上证50", "BROAD_MARKET", "HITHINK_INDEX"),
    MarketIndexDefinition(
        "883957.TI", "同花顺全A（沪深京）", "BROAD_MARKET", "HITHINK_INDEX"
    ),
)

HITHINK_MARKET_INDEX_CODES = tuple(
    definition.index_code
    for definition in MARKET_INDICES
    if definition.source_tag == "HITHINK_INDEX"
)
MARKET_INDEX_BY_CODE = {
    definition.index_code: definition for definition in MARKET_INDICES
}
