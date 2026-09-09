ALTER TABLE market.hithink_snapshot_derived
    ADD COLUMN IF NOT EXISTS total_market_cap Nullable(Float64)
    AFTER limit_break_open_times;
ALTER TABLE market.hithink_snapshot_derived
    ADD COLUMN IF NOT EXISTS float_market_cap Nullable(Float64)
    AFTER total_market_cap;

ALTER TABLE market.hithink_concept_state
    ADD COLUMN IF NOT EXISTS total_market_cap Nullable(Float64)
    AFTER turnover_growth_1m;
ALTER TABLE market.hithink_concept_state
    ADD COLUMN IF NOT EXISTS float_market_cap Nullable(Float64)
    AFTER total_market_cap;

ALTER TABLE market.hithink_industry_state
    ADD COLUMN IF NOT EXISTS total_market_cap Nullable(Float64)
    AFTER turnover_growth_1m;
ALTER TABLE market.hithink_industry_state
    ADD COLUMN IF NOT EXISTS float_market_cap Nullable(Float64)
    AFTER total_market_cap;

ALTER TABLE market.hithink_style_state
    ADD COLUMN IF NOT EXISTS total_market_cap Nullable(Float64)
    AFTER turnover_growth_1m;
ALTER TABLE market.hithink_style_state
    ADD COLUMN IF NOT EXISTS float_market_cap Nullable(Float64)
    AFTER total_market_cap;
