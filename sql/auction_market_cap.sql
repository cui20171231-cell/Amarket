ALTER TABLE market.hithink_auction_snapshot
    ADD COLUMN IF NOT EXISTS total_market_cap Nullable(Float64)
    AFTER last_price;
