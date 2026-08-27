CREATE DATABASE IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.trading_calendar
(
    trade_date Date,
    is_trading_day UInt8,
    source LowCardinality(String),
    fetched_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(fetched_at)
ORDER BY trade_date;

CREATE TABLE IF NOT EXISTS market.hithink_limit_up_pool
(
    trade_date Date,
    collection_id Nullable(FixedString(11)),
    scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    batch_id Nullable(String),
    source_timestamp UInt64,
    source_time DateTime64(3, 'Asia/Shanghai'),
    thscode String,
    ticker String,
    name String,
    is_st UInt8,
    is_new UInt8,
    last_price Float64,
    price_change_ratio_pct Float64,
    limit_up_time Nullable(String),
    limit_up_reason Nullable(String),
    continue_day_text Nullable(String),
    continue_day_cnt Nullable(UInt16),
    seal_money Nullable(Float64),
    max_seal_money Nullable(Float64),
    ingest_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, assumeNotNull(collection_id), thscode, source_time);

CREATE TABLE IF NOT EXISTS market.hithink_limit_down_pool
(
    trade_date Date,
    collection_id Nullable(FixedString(11)),
    scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    batch_id Nullable(String),
    source_timestamp UInt64,
    source_time DateTime64(3, 'Asia/Shanghai'),
    thscode String,
    ticker String,
    name String,
    last_price Float64,
    price_change_ratio_pct Float64,
    first_limit_time Nullable(String),
    last_limit_time Nullable(String),
    turnover_ratio_pct Nullable(Float64),
    ingest_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, assumeNotNull(collection_id), thscode, source_time);

CREATE TABLE IF NOT EXISTS market.sector_catalog
(
    sector_code String,
    sector_name String,
    sector_type LowCardinality(String),
    source_tag LowCardinality(String),
    is_active UInt8,
    first_seen_at DateTime64(3, 'Asia/Shanghai'),
    last_seen_at DateTime64(3, 'Asia/Shanghai'),
    source_timestamp Nullable(UInt64),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
ORDER BY (sector_type, sector_code);

CREATE TABLE IF NOT EXISTS market.sector_membership_history
(
    sector_code String,
    sector_type LowCardinality(String),
    thscode String,
    ticker String,
    stock_name String,
    observed_from Date,
    observed_to Nullable(Date),
    is_active UInt8,
    first_seen_at DateTime64(3, 'Asia/Shanghai'),
    last_confirmed_at DateTime64(3, 'Asia/Shanghai'),
    source_timestamp Nullable(UInt64),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
ORDER BY (sector_type, sector_code, thscode, observed_from);

CREATE TABLE IF NOT EXISTS market.sector_mapping_sync_status
(
    sync_date Date,
    sync_run_id String,
    sector_type LowCardinality(String),
    source_tag LowCardinality(String),
    status LowCardinality(String),
    sector_count UInt32,
    sector_success_count UInt32,
    sector_failed_count UInt32,
    membership_count UInt32,
    added_count UInt32,
    removed_count UInt32,
    unchanged_count UInt32,
    source_timestamp Nullable(UInt64),
    start_time DateTime64(3, 'Asia/Shanghai'),
    end_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    duration_ms Nullable(UInt64),
    error_code Nullable(String),
    error_message Nullable(String),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
ORDER BY (sync_date, sync_run_id, sector_type);

CREATE TABLE IF NOT EXISTS market.hithink_daily_k_raw
(
    trade_date Date,
    date_ms UInt64,
    thscode LowCardinality(String),
    ticker FixedString(6),
    currency LowCardinality(String),
    interval LowCardinality(String),
    adjusted LowCardinality(String),
    open_price Float64,
    high_price Float64,
    low_price Float64,
    close_price Float64,
    volume Float64,
    turnover Float64,
    source LowCardinality(String) DEFAULT 'hithink',
    ingest_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (thscode, trade_date);

CREATE TABLE IF NOT EXISTS market.hithink_adjustment_events
(
    thscode LowCardinality(String),
    ticker FixedString(6),
    ex_date Date,
    ex_date_ms UInt64,
    dividend_per_share Float64,
    per_share_bonus Float64,
    allotment_ratio Float64,
    allotment_price Float64,
    currency LowCardinality(String),
    event_fingerprint FixedString(64),
    source LowCardinality(String) DEFAULT 'hithink',
    ingest_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(ex_date)
ORDER BY (
    thscode,
    ex_date,
    dividend_per_share,
    per_share_bonus,
    allotment_ratio,
    allotment_price
);

CREATE TABLE IF NOT EXISTS market.hithink_daily_k_forward
(
    trade_date Date,
    thscode LowCardinality(String),
    ticker FixedString(6),
    open_price Float64,
    high_price Float64,
    low_price Float64,
    close_price Float64,
    volume Float64,
    turnover Float64,
    adjust_factor Float64,
    build_mode LowCardinality(String),
    source_raw_date Date,
    raw_version_time DateTime64(3, 'Asia/Shanghai'),
    event_set_hash FixedString(64),
    calculated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (thscode, trade_date);

CREATE TABLE IF NOT EXISTS market.hithink_daily_sync_status
(
    trade_date Date,
    task_name LowCardinality(String),
    status LowCardinality(String),
    request_start_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    request_end_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    source_file_date Nullable(Date),
    source_latest_trade_date Nullable(Date),
    download_rows Nullable(UInt64),
    insert_rows Nullable(UInt64),
    updated_rows Nullable(UInt64),
    affected_stock_count Nullable(UInt32),
    full_rebuild_stock_count Nullable(UInt32),
    incremental_stock_count Nullable(UInt32),
    retry_count UInt8 DEFAULT 0,
    duration_ms Nullable(UInt64),
    error_code Nullable(String),
    error_message Nullable(String),
    updated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, task_name);

CREATE TABLE IF NOT EXISTS market.hithink_snapshot_schedule
(
    trade_date Date,
    collection_id FixedString(11),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    session LowCardinality(String),
    sequence_no UInt16,
    status LowCardinality(String),
    batch_id Nullable(String),
    request_start_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    request_end_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    source_timestamp Nullable(UInt64),
    source_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    api_code Nullable(Int32),
    api_total Nullable(UInt32),
    received_count Nullable(UInt32),
    api_duration_ms Nullable(UInt32),
    raw_insert_count Nullable(UInt32),
    raw_insert_ms Nullable(UInt32),
    derived_insert_count Nullable(UInt32),
    derive_ms Nullable(UInt32),
    total_duration_ms Nullable(UInt32),
    retry_count UInt8 DEFAULT 0,
    limit_pool_collected UInt8 DEFAULT 0,
    limit_up_source_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    limit_down_source_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    limit_up_received_count Nullable(UInt32),
    limit_down_received_count Nullable(UInt32),
    limit_up_api_duration_ms Nullable(UInt32),
    limit_down_api_duration_ms Nullable(UInt32),
    error_code Nullable(String),
    error_message Nullable(String),
    updated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, scheduled_time);

CREATE TABLE IF NOT EXISTS market.hithink_snapshot_raw
(
    trade_date Date,
    collection_id FixedString(11),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    source_timestamp UInt64,
    source_time DateTime64(3, 'Asia/Shanghai'),
    session LowCardinality(String),
    batch_id String,
    thscode LowCardinality(String),
    ticker FixedString(6),
    last_price Nullable(Float64),
    price_change Nullable(Float64),
    price_change_ratio_pct Nullable(Float64),
    open_price Nullable(Float64),
    high_price Nullable(Float64),
    low_price Nullable(Float64),
    prev_price Nullable(Float64),
    volume Nullable(UInt64),
    turnover Nullable(UInt64),
    ingest_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, scheduled_time, thscode);

CREATE TABLE IF NOT EXISTS market.hithink_snapshot_derived
(
    trade_date Date,
    collection_id FixedString(11),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    source_timestamp UInt64,
    source_time DateTime64(3, 'Asia/Shanghai'),
    session LowCardinality(String),
    batch_id String,
    thscode LowCardinality(String),
    ticker FixedString(6),
    last_price Nullable(Float64),
    price_change Nullable(Float64),
    price_change_ratio_pct Nullable(Float64),
    open_price Nullable(Float64),
    high_price Nullable(Float64),
    low_price Nullable(Float64),
    prev_price Nullable(Float64),
    volume Nullable(UInt64),
    turnover Nullable(UInt64),
    turnover_delta_1m Nullable(Int64),
    volume_delta_1m Nullable(Int64),
    turnover_growth_1m Nullable(Float64),
    volume_ratio_1m Nullable(Float64),
    new_high_flag Nullable(UInt8),
    new_low_flag Nullable(UInt8),
    price_delta_1m Nullable(Float64),
    price_change_1m_pct Nullable(Float64),
    ingest_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, scheduled_time, thscode);

ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN last_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN price_change Nullable(Float64);
ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN price_change_ratio_pct Nullable(Float64);
ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN open_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN high_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN low_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN prev_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN volume Nullable(UInt64);
ALTER TABLE market.hithink_snapshot_raw MODIFY COLUMN turnover Nullable(UInt64);

ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN last_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN price_change Nullable(Float64);
ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN price_change_ratio_pct Nullable(Float64);
ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN open_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN high_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN low_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN prev_price Nullable(Float64);
ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN volume Nullable(UInt64);
ALTER TABLE market.hithink_snapshot_derived MODIFY COLUMN turnover Nullable(UInt64);

CREATE TABLE IF NOT EXISTS market.hithink_market_state
(
    trade_date Date,
    collection_id FixedString(11),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    source_time DateTime64(3, 'Asia/Shanghai'),
    session LowCardinality(String),
    stock_total UInt32,
    valid_stock_count UInt32,
    up_count UInt32,
    down_count UInt32,
    flat_count UInt32,
    up_ratio Float64,
    down_ratio Float64,
    limit_up_count Nullable(UInt32),
    up_5_to_limit_count Nullable(UInt32),
    up_1_to_5_count UInt32,
    up_0_to_1_count UInt32,
    down_0_to_1_count UInt32,
    down_1_to_5_count UInt32,
    down_5_to_limit_count Nullable(UInt32),
    limit_down_count Nullable(UInt32),
    turnover_total Decimal64(2),
    turnover_delta_1m_total Nullable(Decimal64(2)),
    prev_turnover_delta_1m_total Nullable(Decimal64(2)),
    turnover_growth_1m_market Nullable(Float64),
    volume_total UInt64,
    volume_delta_1m_total Nullable(Int64),
    yesterday_same_time_turnover Nullable(Decimal64(2)),
    turnover_prev_day_delta Nullable(Decimal64(2)),
    turnover_prev_day_pct Nullable(Float64),
    turnover_accel_count Nullable(UInt32),
    turnover_decel_count Nullable(UInt32),
    turnover_accel_50_count Nullable(UInt32),
    turnover_accel_100_count Nullable(UInt32),
    volume_expand_count Nullable(UInt32),
    volume_contract_count Nullable(UInt32),
    volume_ratio_1_5_count Nullable(UInt32),
    volume_ratio_2_count Nullable(UInt32),
    volume_ratio_3_count Nullable(UInt32),
    new_high_count Nullable(UInt32),
    new_low_count Nullable(UInt32),
    price_up_1m_count Nullable(UInt32),
    price_down_1m_count Nullable(UInt32),
    price_flat_1m_count Nullable(UInt32),
    volume_price_up_count Nullable(UInt32),
    volume_price_down_count Nullable(UInt32),
    contract_price_up_count Nullable(UInt32),
    contract_price_down_count Nullable(UInt32),
    up_count_delta_1m Nullable(Int32),
    down_count_delta_1m Nullable(Int32),
    new_high_count_delta_1m Nullable(Int32),
    new_low_count_delta_1m Nullable(Int32),
    volume_price_up_delta_1m Nullable(Int32),
    volume_price_down_delta_1m Nullable(Int32),
    calculated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, scheduled_time);

CREATE TABLE IF NOT EXISTS market.hithink_sector_index_snapshot
(
    trade_date Date,
    collection_id FixedString(11),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    source_timestamp UInt64,
    source_time DateTime64(3, 'Asia/Shanghai'),
    session LowCardinality(String),
    batch_id String,
    sector_code String,
    sector_name String,
    sector_type LowCardinality(String),
    source_tag LowCardinality(String),
    last_price Nullable(Float64),
    price_change Nullable(Float64),
    price_change_ratio_pct Nullable(Float64),
    open_price Nullable(Float64),
    high_price Nullable(Float64),
    low_price Nullable(Float64),
    prev_price Nullable(Float64),
    volume Nullable(UInt64),
    turnover Nullable(Float64),
    ingest_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id, sector_type, sector_code);

CREATE TABLE IF NOT EXISTS market.hithink_concept_state
(
    trade_date Date,
    collection_id FixedString(11),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    stock_source_time DateTime64(3, 'Asia/Shanghai'),
    index_source_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    session LowCardinality(String),
    sector_code String,
    sector_name String,
    member_total UInt32,
    valid_member_count UInt32,
    valid_member_ratio Float64,
    index_last_price Nullable(Float64),
    index_change_ratio_pct Nullable(Float64),
    index_change_1m_pct Nullable(Float64),
    up_count UInt32,
    down_count UInt32,
    flat_count UInt32,
    up_ratio Float64,
    down_ratio Float64,
    limit_up_count Nullable(UInt32),
    up_5_to_limit_count Nullable(UInt32),
    up_1_to_5_count UInt32,
    up_0_to_1_count UInt32,
    down_0_to_1_count UInt32,
    down_1_to_5_count UInt32,
    down_5_to_limit_count Nullable(UInt32),
    limit_down_count Nullable(UInt32),
    turnover_total Float64,
    turnover_delta_1m_total Nullable(Float64),
    prev_turnover_delta_1m_total Nullable(Float64),
    turnover_growth_1m Nullable(Float64),
    volume_total UInt64,
    volume_delta_1m_total Nullable(Int64),
    turnover_market_share_pct Nullable(Float64),
    turnover_1m_market_share_pct Nullable(Float64),
    prev_day_same_time_turnover Nullable(Float64),
    turnover_vs_prev_day_delta Nullable(Float64),
    turnover_vs_prev_day_pct Nullable(Float64),
    turnover_accel_count Nullable(UInt32),
    turnover_decel_count Nullable(UInt32),
    turnover_accel_50_count Nullable(UInt32),
    turnover_accel_100_count Nullable(UInt32),
    volume_expand_count Nullable(UInt32),
    volume_contract_count Nullable(UInt32),
    volume_ratio_1_5_count Nullable(UInt32),
    volume_ratio_2_count Nullable(UInt32),
    volume_ratio_3_count Nullable(UInt32),
    new_high_count Nullable(UInt32),
    new_low_count Nullable(UInt32),
    new_high_ratio Nullable(Float64),
    new_low_ratio Nullable(Float64),
    price_up_1m_count Nullable(UInt32),
    price_down_1m_count Nullable(UInt32),
    price_flat_1m_count Nullable(UInt32),
    volume_price_up_count Nullable(UInt32),
    volume_price_down_count Nullable(UInt32),
    contract_price_up_count Nullable(UInt32),
    contract_price_down_count Nullable(UInt32),
    turnover_1m_top1_share_pct Nullable(Float64),
    turnover_1m_top3_share_pct Nullable(Float64),
    turnover_1m_top5_share_pct Nullable(Float64),
    up_count_delta_1m Nullable(Int32),
    down_count_delta_1m Nullable(Int32),
    new_high_count_delta_1m Nullable(Int32),
    new_low_count_delta_1m Nullable(Int32),
    volume_price_up_delta_1m Nullable(Int32),
    volume_price_down_delta_1m Nullable(Int32),
    turnover_market_share_delta_1m Nullable(Float64),
    turnover_1m_market_share_delta_1m Nullable(Float64),
    calculated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id, sector_code);

CREATE TABLE IF NOT EXISTS market.hithink_industry_state AS market.hithink_concept_state;
CREATE TABLE IF NOT EXISTS market.hithink_style_state AS market.hithink_concept_state;

ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_index_status LowCardinality(String) DEFAULT '' AFTER limit_down_api_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_index_received_count Nullable(UInt32) AFTER sector_index_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_index_api_duration_ms Nullable(UInt32) AFTER sector_index_received_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_state_status LowCardinality(String) DEFAULT '' AFTER sector_index_api_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_state_row_count Nullable(UInt32) AFTER sector_state_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_state_duration_ms Nullable(UInt32) AFTER sector_state_row_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_error_code Nullable(String) AFTER sector_state_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_error_message Nullable(String) AFTER sector_error_code;

ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN last_price Nullable(Float64);
ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN price_change Nullable(Float64);
ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN price_change_ratio_pct Nullable(Float64);
ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN open_price Nullable(Float64);
ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN high_price Nullable(Float64);
ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN low_price Nullable(Float64);
ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN prev_price Nullable(Float64);
ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN volume Nullable(UInt64);
ALTER TABLE market.hithink_sector_index_snapshot MODIFY COLUMN turnover Nullable(Float64);

ALTER TABLE market.hithink_limit_up_pool ADD COLUMN IF NOT EXISTS collection_id Nullable(FixedString(11)) AFTER trade_date;
ALTER TABLE market.hithink_limit_up_pool ADD COLUMN IF NOT EXISTS scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER collection_id;
ALTER TABLE market.hithink_limit_up_pool ADD COLUMN IF NOT EXISTS batch_id Nullable(String) AFTER scheduled_time;

ALTER TABLE market.hithink_limit_down_pool ADD COLUMN IF NOT EXISTS collection_id Nullable(FixedString(11)) AFTER trade_date;
ALTER TABLE market.hithink_limit_down_pool ADD COLUMN IF NOT EXISTS scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER collection_id;
ALTER TABLE market.hithink_limit_down_pool ADD COLUMN IF NOT EXISTS batch_id Nullable(String) AFTER scheduled_time;

ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS collection_id FixedString(11) AFTER trade_date;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_pool_collected UInt8 DEFAULT 0 AFTER retry_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_up_source_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER limit_pool_collected;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_down_source_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER limit_up_source_time;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_up_received_count Nullable(UInt32) AFTER limit_down_source_time;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_down_received_count Nullable(UInt32) AFTER limit_up_received_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_up_api_duration_ms Nullable(UInt32) AFTER limit_down_received_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_down_api_duration_ms Nullable(UInt32) AFTER limit_up_api_duration_ms;

ALTER TABLE market.hithink_snapshot_raw ADD COLUMN IF NOT EXISTS collection_id FixedString(11) AFTER trade_date;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS collection_id FixedString(11) AFTER trade_date;
ALTER TABLE market.hithink_market_state ADD COLUMN IF NOT EXISTS collection_id FixedString(11) AFTER trade_date;
ALTER TABLE market.hithink_market_state MODIFY COLUMN limit_up_count Nullable(UInt32);
ALTER TABLE market.hithink_market_state MODIFY COLUMN up_5_to_limit_count Nullable(UInt32);
ALTER TABLE market.hithink_market_state MODIFY COLUMN down_5_to_limit_count Nullable(UInt32);
ALTER TABLE market.hithink_market_state MODIFY COLUMN limit_down_count Nullable(UInt32);
ALTER TABLE market.hithink_market_state RENAME COLUMN IF EXISTS turnover_yoy_delta TO turnover_prev_day_delta;
ALTER TABLE market.hithink_market_state RENAME COLUMN IF EXISTS turnover_yoy_pct TO turnover_prev_day_pct;
