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
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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

CREATE TABLE IF NOT EXISTS market.hithink_limit_break_pool
(
    trade_date Date,
    collection_id FixedString(11),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    batch_id String,
    source_timestamp UInt64,
    source_time DateTime64(3, 'Asia/Shanghai'),
    thscode String,
    ticker String,
    name String,
    last_price Nullable(Float64),
    price_change_ratio_pct Nullable(Float64),
    open_times Nullable(UInt16),
    turnover_ratio_pct Nullable(Float64),
    turnover Nullable(Float64),
    ingest_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, scheduled_time, thscode);

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

CREATE TABLE IF NOT EXISTS market.strategic_sector_watchlist
(
    sector_type LowCardinality(String),
    sector_code String,
    sector_name String,
    strategic_theme String,
    strategic_subtheme Nullable(String),
    watch_level LowCardinality(String),
    watch_reason String,
    is_active UInt8,
    effective_from Date,
    effective_to Nullable(Date),
    definition_source LowCardinality(String),
    definition_note String,
    created_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    updated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(6, 'Asia/Shanghai') DEFAULT now64(6),
    CONSTRAINT strategic_watch_sector_type CHECK sector_type IN ('concept', 'industry', 'style'),
    CONSTRAINT strategic_watch_level CHECK watch_level IN ('CORE', 'IMPORTANT', 'WATCH'),
    CONSTRAINT strategic_watch_active CHECK is_active IN (0, 1),
    CONSTRAINT strategic_watch_date_order CHECK effective_to IS NULL OR effective_to >= effective_from,
    CONSTRAINT strategic_watch_active_window CHECK (is_active = 1 AND effective_to IS NULL) OR (is_active = 0 AND effective_to IS NOT NULL),
    CONSTRAINT strategic_watch_update_order CHECK updated_at >= created_at
)
ENGINE = ReplacingMergeTree(version_time)
ORDER BY (sector_type, sector_code, strategic_theme);

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
    collection_id String DEFAULT concat(formatDateTime(trade_date, '%Y%m%d'), '254'),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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
    collection_id String DEFAULT concat(formatDateTime(ex_date, '%Y%m%d'), '254'),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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
    collection_id String DEFAULT concat(formatDateTime(trade_date, '%Y%m%d'), '254'),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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
ORDER BY (collection_id, thscode);

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
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    session LowCardinality(String),
    sequence_no UInt16,
    status LowCardinality(String),
    raw_status LowCardinality(String) DEFAULT 'PENDING',
    derivation_status LowCardinality(String) DEFAULT 'PENDING',
    all_a_snapshot_status LowCardinality(String) DEFAULT 'PENDING',
    limit_up_pool_status LowCardinality(String) DEFAULT 'PENDING',
    limit_down_pool_status LowCardinality(String) DEFAULT 'PENDING',
    raw_completed_at Nullable(DateTime64(3, 'Asia/Shanghai')),
    derivation_started_at Nullable(DateTime64(3, 'Asia/Shanghai')),
    derivation_completed_at Nullable(DateTime64(3, 'Asia/Shanghai')),
    derivation_duration_ms Nullable(UInt32),
    raw_error_code Nullable(String),
    raw_error_message Nullable(String),
    derivation_error_code Nullable(String),
    derivation_error_message Nullable(String),
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
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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
    prev_trade_day_same_time_turnover Nullable(UInt64),
    turnover_prev_trade_day_delta Nullable(Int64),
    turnover_prev_trade_day_pct Nullable(Float64),
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
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS prev_trade_day_same_time_turnover Nullable(UInt64) AFTER turnover;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS turnover_prev_trade_day_delta Nullable(Int64) AFTER prev_trade_day_same_time_turnover;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS turnover_prev_trade_day_pct Nullable(Float64) AFTER turnover_prev_trade_day_delta;

CREATE TABLE IF NOT EXISTS market.hithink_market_state
(
    trade_date Date,
    collection_id FixedString(11),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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

CREATE TABLE IF NOT EXISTS market.hithink_market_delta_15m
(
    trade_date Date,
    collection_id FixedString(11),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    session LowCardinality(String),
    state_data_status LowCardinality(String),
    state_source_collection_id Nullable(FixedString(11)),
    state_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    state_source_age_seconds Nullable(UInt32),
    state_is_fallback UInt8,
    base_collection_id Nullable(FixedString(11)),
    base_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    base_age_seconds Nullable(UInt32),
    delta_status LowCardinality(String),
    delta_type LowCardinality(String),
    up_count_delta_15m Nullable(Int32),
    down_count_delta_15m Nullable(Int32),
    flat_count_delta_15m Nullable(Int32),
    up_ratio_delta_15m Nullable(Float64),
    down_ratio_delta_15m Nullable(Float64),
    limit_up_count_delta_15m Nullable(Int32),
    up_5_to_limit_count_delta_15m Nullable(Int32),
    up_1_to_5_count_delta_15m Nullable(Int32),
    up_0_to_1_count_delta_15m Nullable(Int32),
    down_0_to_1_count_delta_15m Nullable(Int32),
    down_1_to_5_count_delta_15m Nullable(Int32),
    down_5_to_limit_count_delta_15m Nullable(Int32),
    limit_down_count_delta_15m Nullable(Int32),
    limit_break_count_delta_15m Nullable(Int32),
    turnover_increment_15m Nullable(Decimal(18, 2)),
    turnover_speed_current Nullable(Decimal(18, 2)),
    turnover_speed_base Nullable(Decimal(18, 2)),
    turnover_speed_delta_15m Nullable(Decimal(18, 2)),
    turnover_speed_change_pct Nullable(Float64),
    turnover_accel_count_delta_15m Nullable(Int32),
    turnover_decel_count_delta_15m Nullable(Int32),
    turnover_accel_50_count_delta_15m Nullable(Int32),
    turnover_accel_100_count_delta_15m Nullable(Int32),
    volume_expand_count_delta_15m Nullable(Int32),
    volume_contract_count_delta_15m Nullable(Int32),
    volume_ratio_1_5_count_delta_15m Nullable(Int32),
    volume_ratio_2_count_delta_15m Nullable(Int32),
    volume_ratio_3_count_delta_15m Nullable(Int32),
    new_high_count_delta_15m Nullable(Int32),
    new_low_count_delta_15m Nullable(Int32),
    price_up_1m_count_delta_15m Nullable(Int32),
    price_down_1m_count_delta_15m Nullable(Int32),
    price_flat_1m_count_delta_15m Nullable(Int32),
    volume_price_up_count_delta_15m Nullable(Int32),
    volume_price_down_count_delta_15m Nullable(Int32),
    contract_price_up_count_delta_15m Nullable(Int32),
    contract_price_down_count_delta_15m Nullable(Int32),
    calculated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(6, 'Asia/Shanghai') DEFAULT now64(6),
    CONSTRAINT ck_market_delta_collection_digits CHECK match(toString(collection_id), '^[0-9]{11}$'),
    CONSTRAINT ck_market_delta_collection_date CHECK substring(toString(collection_id), 1, 8) = formatDateTime(trade_date, '%Y%m%d'),
    CONSTRAINT ck_market_delta_node_seq CHECK node_seq BETWEEN 1 AND 254,
    CONSTRAINT ck_market_delta_scheduled_date CHECK toDate(scheduled_time) = trade_date,
    CONSTRAINT ck_market_delta_source_status CHECK
        (
            state_data_status = 'CURRENT'
            AND state_source_collection_id = collection_id
            AND state_source_scheduled_time = scheduled_time
            AND state_source_age_seconds = 0
            AND state_is_fallback = 0
        )
        OR
        (
            state_data_status = 'FALLBACK'
            AND state_source_collection_id IS NOT NULL
            AND state_source_scheduled_time IS NOT NULL
            AND toDate(state_source_scheduled_time) = trade_date
            AND state_source_scheduled_time < scheduled_time
            AND state_source_age_seconds = dateDiff('second', state_source_scheduled_time, scheduled_time)
            AND state_source_age_seconds > 0
            AND state_is_fallback = 1
        )
        OR
        (
            state_data_status = 'NO_SOURCE'
            AND state_source_collection_id IS NULL
            AND state_source_scheduled_time IS NULL
            AND state_source_age_seconds IS NULL
            AND state_is_fallback = 0
        ),
    CONSTRAINT ck_market_delta_status CHECK delta_status IN ('VALID', 'NO_BASE', 'NO_SOURCE'),
    CONSTRAINT ck_market_delta_type CHECK
        (
            node_seq IN (11, 133)
            AND delta_type = 'SESSION_BASE'
            AND delta_status = 'NO_BASE'
        )
        OR
        (
            node_seq = 12
            AND delta_type = 'AUCTION_TO_OPEN'
        )
        OR
        (
            node_seq NOT IN (11, 12, 133)
            AND delta_type = 'NORMAL_15M'
        ),
    CONSTRAINT ck_market_delta_base_shape CHECK
        (
            delta_status = 'VALID'
            AND base_collection_id IS NOT NULL
            AND base_scheduled_time IS NOT NULL
            AND base_age_seconds IS NOT NULL
            AND base_age_seconds > 0
        )
        OR
        (
            delta_status IN ('NO_BASE', 'NO_SOURCE')
            AND base_collection_id IS NULL
            AND base_scheduled_time IS NULL
            AND base_age_seconds IS NULL
        ),
    CONSTRAINT ck_market_delta_business_period CHECK
        (
            trade_date < toDate('2026-09-02')
            AND tuple(
                node_seq,
                toHour(scheduled_time) * 3600 + toMinute(scheduled_time) * 60 + toSecond(scheduled_time)
            ) IN (
                (11, 33915), (12, 34215), (27, 35115), (42, 36015), (57, 36915),
                (72, 37815), (87, 38715), (102, 39615), (117, 40515), (132, 41415),
                (133, 46815), (148, 47715), (163, 48615), (178, 49515),
                (193, 50415), (208, 51315), (223, 52215), (238, 53115), (254, 54000)
            )
        )
        OR
        (
            trade_date >= toDate('2026-09-02')
            AND tuple(
                node_seq,
                toHour(scheduled_time) * 3600 + toMinute(scheduled_time) * 60 + toSecond(scheduled_time)
            ) IN (
                (11, 33908), (12, 34208), (27, 35108), (42, 36008), (57, 36908),
                (72, 37808), (87, 38708), (102, 39608), (117, 40508), (132, 41408),
                (133, 46808), (148, 47708), (163, 48608), (178, 49508),
                (193, 50408), (208, 51308), (223, 52208), (238, 53108), (254, 54000)
            )
        ),
    CONSTRAINT ck_market_delta_base_time CHECK delta_status != 'VALID' OR
        (
            toDate(base_scheduled_time) = trade_date
            AND substring(toString(base_collection_id), 1, 8) = formatDateTime(trade_date, '%Y%m%d')
            AND base_scheduled_time < scheduled_time
            AND base_age_seconds = dateDiff('second', base_scheduled_time, scheduled_time)
            AND
            (
                (
                    node_seq IN (11, 12, 27, 42, 57, 72, 87, 102, 117, 132)
                    AND base_scheduled_time < toDateTime64(concat(toString(trade_date), ' 12:00:00'), 3, 'Asia/Shanghai')
                )
                OR
                (
                    node_seq IN (133, 148, 163, 178, 193, 208, 223, 238, 254)
                    AND base_scheduled_time >= toDateTime64(concat(toString(trade_date), ' 13:00:00'), 3, 'Asia/Shanghai')
                )
            )
        ),
    CONSTRAINT ck_market_delta_null_status CHECK delta_status = 'VALID' OR
        (
            up_count_delta_15m IS NULL
            AND down_count_delta_15m IS NULL
            AND flat_count_delta_15m IS NULL
            AND up_ratio_delta_15m IS NULL
            AND down_ratio_delta_15m IS NULL
            AND limit_up_count_delta_15m IS NULL
            AND up_5_to_limit_count_delta_15m IS NULL
            AND up_1_to_5_count_delta_15m IS NULL
            AND up_0_to_1_count_delta_15m IS NULL
            AND down_0_to_1_count_delta_15m IS NULL
            AND down_1_to_5_count_delta_15m IS NULL
            AND down_5_to_limit_count_delta_15m IS NULL
            AND limit_down_count_delta_15m IS NULL
            AND limit_break_count_delta_15m IS NULL
            AND turnover_increment_15m IS NULL
            AND turnover_speed_current IS NULL
            AND turnover_speed_base IS NULL
            AND turnover_speed_delta_15m IS NULL
            AND turnover_speed_change_pct IS NULL
            AND turnover_accel_count_delta_15m IS NULL
            AND turnover_decel_count_delta_15m IS NULL
            AND turnover_accel_50_count_delta_15m IS NULL
            AND turnover_accel_100_count_delta_15m IS NULL
            AND volume_expand_count_delta_15m IS NULL
            AND volume_contract_count_delta_15m IS NULL
            AND volume_ratio_1_5_count_delta_15m IS NULL
            AND volume_ratio_2_count_delta_15m IS NULL
            AND volume_ratio_3_count_delta_15m IS NULL
            AND new_high_count_delta_15m IS NULL
            AND new_low_count_delta_15m IS NULL
            AND price_up_1m_count_delta_15m IS NULL
            AND price_down_1m_count_delta_15m IS NULL
            AND price_flat_1m_count_delta_15m IS NULL
            AND volume_price_up_count_delta_15m IS NULL
            AND volume_price_down_count_delta_15m IS NULL
            AND contract_price_up_count_delta_15m IS NULL
            AND contract_price_down_count_delta_15m IS NULL
        ),
    CONSTRAINT ck_market_delta_closing_254 CHECK node_seq != 254 OR
        (
            toDate(scheduled_time) = trade_date
            AND toHour(scheduled_time) = 15
            AND toMinute(scheduled_time) = 0
            AND toSecond(scheduled_time) = 0
            AND delta_status = 'VALID'
            AND state_data_status = 'CURRENT'
            AND state_source_collection_id = collection_id
            AND state_source_scheduled_time = scheduled_time
            AND state_source_age_seconds = 0
            AND state_is_fallback = 0
            AND base_scheduled_time >= toDateTime64(concat(toString(trade_date), ' 13:00:00'), 3, 'Asia/Shanghai')
            AND base_scheduled_time < scheduled_time
            AND base_age_seconds > 0
            AND calculated_at >= scheduled_time
        )
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id);

-- Migrate the first all-node draft to the fixed 19-checkpoint trading-day layout.
ALTER TABLE market.hithink_market_delta_15m ADD COLUMN IF NOT EXISTS state_data_status LowCardinality(String) DEFAULT 'NO_SOURCE' AFTER session;
ALTER TABLE market.hithink_market_delta_15m ADD COLUMN IF NOT EXISTS state_source_collection_id Nullable(FixedString(11)) AFTER state_data_status;
ALTER TABLE market.hithink_market_delta_15m ADD COLUMN IF NOT EXISTS state_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER state_source_collection_id;
ALTER TABLE market.hithink_market_delta_15m ADD COLUMN IF NOT EXISTS state_source_age_seconds Nullable(UInt32) AFTER state_source_scheduled_time;
ALTER TABLE market.hithink_market_delta_15m ADD COLUMN IF NOT EXISTS state_is_fallback UInt8 AFTER state_source_age_seconds;
ALTER TABLE market.hithink_market_delta_15m ADD COLUMN IF NOT EXISTS delta_type LowCardinality(String) DEFAULT 'NORMAL_15M' AFTER delta_status;
ALTER TABLE market.hithink_market_delta_15m DROP CONSTRAINT IF EXISTS ck_market_delta_source_status;
ALTER TABLE market.hithink_market_delta_15m DROP CONSTRAINT IF EXISTS ck_market_delta_status;
ALTER TABLE market.hithink_market_delta_15m DROP CONSTRAINT IF EXISTS ck_market_delta_type;
ALTER TABLE market.hithink_market_delta_15m DROP CONSTRAINT IF EXISTS ck_market_delta_base_shape;
ALTER TABLE market.hithink_market_delta_15m DROP CONSTRAINT IF EXISTS ck_market_delta_business_period;
ALTER TABLE market.hithink_market_delta_15m DROP CONSTRAINT IF EXISTS ck_market_delta_base_time;
ALTER TABLE market.hithink_market_delta_15m DROP CONSTRAINT IF EXISTS ck_market_delta_closing_254;
ALTER TABLE market.hithink_market_delta_15m ADD CONSTRAINT IF NOT EXISTS ck_market_delta_source_status CHECK (state_data_status = 'CURRENT' AND state_source_collection_id = collection_id AND state_source_scheduled_time = scheduled_time AND state_source_age_seconds = 0 AND state_is_fallback = 0) OR (state_data_status = 'FALLBACK' AND state_source_collection_id IS NOT NULL AND state_source_scheduled_time IS NOT NULL AND toDate(state_source_scheduled_time) = trade_date AND state_source_scheduled_time < scheduled_time AND state_source_age_seconds = dateDiff('second', state_source_scheduled_time, scheduled_time) AND state_source_age_seconds > 0 AND state_is_fallback = 1) OR (state_data_status = 'NO_SOURCE' AND state_source_collection_id IS NULL AND state_source_scheduled_time IS NULL AND state_source_age_seconds IS NULL AND state_is_fallback = 0);
ALTER TABLE market.hithink_market_delta_15m ADD CONSTRAINT IF NOT EXISTS ck_market_delta_status CHECK delta_status IN ('VALID', 'NO_BASE', 'NO_SOURCE');
ALTER TABLE market.hithink_market_delta_15m ADD CONSTRAINT IF NOT EXISTS ck_market_delta_type CHECK (node_seq IN (11, 133) AND delta_type = 'SESSION_BASE' AND delta_status = 'NO_BASE') OR (node_seq = 12 AND delta_type = 'AUCTION_TO_OPEN') OR (node_seq NOT IN (11, 12, 133) AND delta_type = 'NORMAL_15M');
ALTER TABLE market.hithink_market_delta_15m ADD CONSTRAINT IF NOT EXISTS ck_market_delta_base_shape CHECK (delta_status = 'VALID' AND base_collection_id IS NOT NULL AND base_scheduled_time IS NOT NULL AND base_age_seconds IS NOT NULL AND base_age_seconds > 0) OR (delta_status IN ('NO_BASE', 'NO_SOURCE') AND base_collection_id IS NULL AND base_scheduled_time IS NULL AND base_age_seconds IS NULL);
ALTER TABLE market.hithink_market_delta_15m ADD CONSTRAINT IF NOT EXISTS ck_market_delta_business_period CHECK (trade_date < toDate('2026-09-02') AND tuple(node_seq, toHour(scheduled_time) * 3600 + toMinute(scheduled_time) * 60 + toSecond(scheduled_time)) IN ((11, 33915), (12, 34215), (27, 35115), (42, 36015), (57, 36915), (72, 37815), (87, 38715), (102, 39615), (117, 40515), (132, 41415), (133, 46815), (148, 47715), (163, 48615), (178, 49515), (193, 50415), (208, 51315), (223, 52215), (238, 53115), (254, 54000))) OR (trade_date >= toDate('2026-09-02') AND tuple(node_seq, toHour(scheduled_time) * 3600 + toMinute(scheduled_time) * 60 + toSecond(scheduled_time)) IN ((11, 33908), (12, 34208), (27, 35108), (42, 36008), (57, 36908), (72, 37808), (87, 38708), (102, 39608), (117, 40508), (132, 41408), (133, 46808), (148, 47708), (163, 48608), (178, 49508), (193, 50408), (208, 51308), (223, 52208), (238, 53108), (254, 54000)));
ALTER TABLE market.hithink_market_delta_15m ADD CONSTRAINT IF NOT EXISTS ck_market_delta_base_time CHECK delta_status != 'VALID' OR (toDate(base_scheduled_time) = trade_date AND substring(toString(base_collection_id), 1, 8) = formatDateTime(trade_date, '%Y%m%d') AND base_scheduled_time < scheduled_time AND base_age_seconds = dateDiff('second', base_scheduled_time, scheduled_time) AND ((node_seq IN (11, 12, 27, 42, 57, 72, 87, 102, 117, 132) AND base_scheduled_time < toDateTime64(concat(toString(trade_date), ' 12:00:00'), 3, 'Asia/Shanghai')) OR (node_seq IN (133, 148, 163, 178, 193, 208, 223, 238, 254) AND base_scheduled_time >= toDateTime64(concat(toString(trade_date), ' 13:00:00'), 3, 'Asia/Shanghai'))));
ALTER TABLE market.hithink_market_delta_15m ADD CONSTRAINT IF NOT EXISTS ck_market_delta_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND delta_status = 'VALID' AND state_data_status = 'CURRENT' AND state_source_collection_id = collection_id AND state_source_scheduled_time = scheduled_time AND state_source_age_seconds = 0 AND state_is_fallback = 0 AND base_scheduled_time >= toDateTime64(concat(toString(trade_date), ' 13:00:00'), 3, 'Asia/Shanghai') AND base_scheduled_time < scheduled_time AND base_age_seconds > 0 AND calculated_at >= scheduled_time);

CREATE TABLE IF NOT EXISTS market.hithink_sector_capital_migration
(
    trade_date Date,
    collection_id FixedString(11),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    sector_type LowCardinality(String),
    sector_code String,
    sector_name String,
    state_data_status LowCardinality(String),
    state_source_collection_id Nullable(FixedString(11)),
    state_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    state_source_age_seconds Nullable(UInt32),
    state_is_fallback UInt8,
    base_collection_id Nullable(FixedString(11)),
    base_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    base_age_seconds Nullable(UInt32),
    delta_type LowCardinality(String),
    turnover_total Nullable(Float64),
    turnover_delta_1m_total Nullable(Float64),
    turnover_market_share_pct Nullable(Float64),
    turnover_1m_market_share_pct Nullable(Float64),
    turnover_market_share_delta_15m Nullable(Float64),
    turnover_1m_market_share_delta_15m Nullable(Float64),
    turnover_share_rank Nullable(UInt16),
    turnover_1m_share_rank Nullable(UInt16),
    turnover_share_rank_delta Nullable(Int16),
    turnover_1m_share_rank_delta Nullable(Int16),
    turnover_increment_15m Nullable(Float64),
    turnover_increment_market_share_pct Nullable(Float64),
    up_ratio Nullable(Float64),
    down_ratio Nullable(Float64),
    limit_up_count Nullable(UInt32),
    limit_break_count Nullable(UInt32),
    new_high_ratio Nullable(Float64),
    new_low_ratio Nullable(Float64),
    up_ratio_delta_15m Nullable(Float64),
    down_ratio_delta_15m Nullable(Float64),
    limit_up_count_delta_15m Nullable(Int32),
    limit_break_count_delta_15m Nullable(Int32),
    new_high_ratio_delta_15m Nullable(Float64),
    new_low_ratio_delta_15m Nullable(Float64),
    turnover_1m_top1_share_pct Nullable(Float64),
    turnover_1m_top3_share_pct Nullable(Float64),
    turnover_1m_top5_share_pct Nullable(Float64),
    top1_share_delta_15m Nullable(Float64),
    top3_share_delta_15m Nullable(Float64),
    top5_share_delta_15m Nullable(Float64),
    calculated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(6, 'Asia/Shanghai') DEFAULT now64(6),
    CONSTRAINT ck_capital_migration_collection_digits CHECK match(toString(collection_id), '^[0-9]{11}$'),
    CONSTRAINT ck_capital_migration_collection_date CHECK substring(toString(collection_id), 1, 8) = formatDateTime(trade_date, '%Y%m%d'),
    CONSTRAINT ck_capital_migration_node_seq CHECK node_seq BETWEEN 1 AND 254,
    CONSTRAINT ck_capital_migration_scheduled_date CHECK toDate(scheduled_time) = trade_date,
    CONSTRAINT ck_capital_migration_sector_type CHECK sector_type IN ('concept', 'industry', 'style'),
    CONSTRAINT ck_capital_migration_delta_type CHECK
        (node_seq IN (11, 133) AND delta_type = 'SESSION_BASE')
        OR (node_seq = 12 AND delta_type = 'AUCTION_TO_OPEN')
        OR (node_seq NOT IN (11, 12, 133) AND delta_type = 'NORMAL_15M'),
    CONSTRAINT ck_capital_migration_source_status CHECK
        (
            state_data_status = 'CURRENT'
            AND state_source_collection_id = collection_id
            AND state_source_scheduled_time = scheduled_time
            AND state_source_age_seconds = 0
            AND state_is_fallback = 0
        )
        OR
        (
            state_data_status = 'FALLBACK'
            AND state_source_collection_id IS NOT NULL
            AND state_source_scheduled_time IS NOT NULL
            AND toDate(state_source_scheduled_time) = trade_date
            AND state_source_scheduled_time < scheduled_time
            AND state_source_age_seconds = dateDiff('second', state_source_scheduled_time, scheduled_time)
            AND state_source_age_seconds > 0
            AND state_is_fallback = 1
        )
        OR
        (
            state_data_status = 'NO_SOURCE'
            AND state_source_collection_id IS NULL
            AND state_source_scheduled_time IS NULL
            AND state_source_age_seconds IS NULL
            AND state_is_fallback = 0
        ),
    CONSTRAINT ck_capital_migration_base_shape CHECK
        (
            base_collection_id IS NULL
            AND base_scheduled_time IS NULL
            AND base_age_seconds IS NULL
        )
        OR
        (
            base_collection_id IS NOT NULL
            AND base_scheduled_time IS NOT NULL
            AND base_age_seconds IS NOT NULL
            AND toDate(base_scheduled_time) = trade_date
            AND base_scheduled_time < scheduled_time
            AND base_age_seconds = dateDiff('second', base_scheduled_time, scheduled_time)
            AND base_age_seconds > 0
        ),
    CONSTRAINT ck_capital_migration_session_base CHECK node_seq NOT IN (11, 133) OR
        (base_collection_id IS NULL AND base_scheduled_time IS NULL AND base_age_seconds IS NULL),
    CONSTRAINT ck_capital_migration_rank_positive CHECK
        (turnover_share_rank IS NULL OR turnover_share_rank > 0)
        AND (turnover_1m_share_rank IS NULL OR turnover_1m_share_rank > 0),
    CONSTRAINT ck_capital_migration_closing_254 CHECK node_seq != 254 OR
        (
            state_data_status = 'CURRENT'
            AND state_source_collection_id = collection_id
            AND state_source_scheduled_time = scheduled_time
            AND state_source_age_seconds = 0
            AND state_is_fallback = 0
            AND calculated_at >= scheduled_time
        )
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id, sector_type, sector_code);

CREATE TABLE IF NOT EXISTS market.hithink_core_sector_candidate
(
    trade_date Date,
    collection_id FixedString(11),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    sector_type LowCardinality(String),
    sector_code String,
    sector_name String,
    candidate_model_version LowCardinality(String),
    state_data_status LowCardinality(String),
    state_source_collection_id Nullable(FixedString(11)),
    state_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    state_source_age_seconds Nullable(UInt32),
    state_is_fallback UInt8,
    delta_type LowCardinality(String),
    candidate_rank UInt16,
    candidate_hit_count UInt8,
    candidate_reason_mask UInt16,
    candidate_score_v1 Float64,
    hit_price_strength UInt8,
    hit_breadth UInt8,
    hit_capital_share UInt8,
    hit_capital_acceleration UInt8,
    hit_limit_strength UInt8,
    hit_new_high UInt8,
    hit_persistence UInt8,
    index_change_ratio_pct Nullable(Float64),
    index_change_1m_pct Nullable(Float64),
    index_change_rank UInt16,
    index_change_1m_rank UInt16,
    up_ratio Nullable(Float64),
    down_ratio Nullable(Float64),
    up_ratio_delta_15m Nullable(Float64),
    down_ratio_delta_15m Nullable(Float64),
    turnover_market_share_pct Nullable(Float64),
    turnover_1m_market_share_pct Nullable(Float64),
    turnover_market_share_delta_15m Nullable(Float64),
    turnover_1m_market_share_delta_15m Nullable(Float64),
    turnover_increment_15m Nullable(Float64),
    turnover_increment_market_share_pct Nullable(Float64),
    turnover_share_rank Nullable(UInt16),
    turnover_1m_share_rank Nullable(UInt16),
    turnover_share_rank_delta Nullable(Int16),
    turnover_1m_share_rank_delta Nullable(Int16),
    limit_up_count Nullable(UInt32),
    limit_break_count Nullable(UInt32),
    limit_up_count_delta_15m Nullable(Int32),
    limit_break_count_delta_15m Nullable(Int32),
    new_high_ratio Nullable(Float64),
    new_low_ratio Nullable(Float64),
    new_high_ratio_delta_15m Nullable(Float64),
    new_low_ratio_delta_15m Nullable(Float64),
    turnover_1m_top1_share_pct Nullable(Float64),
    turnover_1m_top3_share_pct Nullable(Float64),
    turnover_1m_top5_share_pct Nullable(Float64),
    top1_share_delta_15m Nullable(Float64),
    top3_share_delta_15m Nullable(Float64),
    top5_share_delta_15m Nullable(Float64),
    candidate_hit_count_3 UInt8,
    candidate_hit_count_5 UInt8,
    turnover_share_positive_count_3 UInt8,
    up_ratio_positive_count_3 UInt8,
    best_candidate_rank_3 UInt16,
    best_candidate_rank_5 UInt16,
    calculated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(6, 'Asia/Shanghai') DEFAULT now64(6),
    CONSTRAINT ck_core_sector_model CHECK candidate_model_version = 'V1',
    CONSTRAINT ck_core_sector_type CHECK sector_type IN ('concept', 'industry'),
    CONSTRAINT ck_core_sector_closing_254 CHECK node_seq != 254 OR (state_data_status = 'CURRENT' AND state_source_collection_id = collection_id AND state_source_age_seconds = 0 AND state_is_fallback = 0 AND calculated_at >= scheduled_time)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id, sector_type, sector_code);

CREATE TABLE IF NOT EXISTS market.hithink_core_stock_candidate
(
    trade_date Date,
    collection_id FixedString(11),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    thscode LowCardinality(String),
    ticker FixedString(6),
    stock_name Nullable(String),
    candidate_model_version LowCardinality(String),
    state_data_status LowCardinality(String),
    state_source_collection_id Nullable(FixedString(11)),
    state_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    state_source_age_seconds Nullable(UInt32),
    state_is_fallback UInt8,
    delta_type LowCardinality(String),
    candidate_rank UInt16,
    candidate_hit_count UInt8,
    candidate_reason_mask UInt16,
    candidate_score_v1 Float64,
    hit_price_strength UInt8,
    hit_turnover_absolute UInt8,
    hit_turnover_acceleration UInt8,
    hit_new_high UInt8,
    hit_limit_strength UInt8,
    hit_core_sector UInt8,
    hit_sector_leader UInt8,
    hit_persistence UInt8,
    last_price Nullable(Float64),
    price_change_ratio_pct Nullable(Float64),
    price_change_1m_pct Nullable(Float64),
    price_delta_1m Nullable(Float64),
    new_high_flag Nullable(UInt8),
    new_low_flag Nullable(UInt8),
    price_change_rank_market Nullable(UInt16),
    price_change_1m_rank_market Nullable(UInt16),
    turnover Nullable(UInt64),
    turnover_delta_1m Nullable(Int64),
    turnover_growth_1m Nullable(Float64),
    turnover_prev_trade_day_pct Nullable(Float64),
    turnover_rank_market Nullable(UInt16),
    turnover_delta_1m_rank_market Nullable(UInt16),
    turnover_top_pct_market Nullable(Float64),
    turnover_delta_1m_top_pct_market Nullable(Float64),
    is_limit_up Nullable(UInt8),
    is_limit_down Nullable(UInt8),
    is_limit_break Nullable(UInt8),
    limit_break_open_times Nullable(UInt16),
    continue_day_cnt Nullable(UInt16),
    seal_money Nullable(Float64),
    max_seal_money Nullable(Float64),
    limit_up_time Nullable(String),
    core_sector_count UInt16,
    core_concept_count UInt16,
    core_industry_count UInt16,
    core_style_count UInt16 COMMENT 'COMPATIBILITY ONLY: V1 fixed at 0; prohibited from scoring, ranking, and core-sector links',
    best_core_sector_type Nullable(String),
    best_core_sector_code Nullable(String),
    best_core_sector_name Nullable(String),
    best_core_sector_candidate_rank Nullable(UInt16),
    best_sector_turnover_rank Nullable(UInt16),
    best_sector_turnover_1m_rank Nullable(UInt16),
    best_sector_price_rank Nullable(UInt16),
    best_sector_turnover_share_pct Nullable(Float64),
    candidate_hit_count_3 UInt8,
    candidate_hit_count_5 UInt8,
    turnover_top_hit_count_3 UInt8,
    new_high_hit_count_3 UInt8,
    core_sector_hit_count_3 UInt8,
    calculated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(6, 'Asia/Shanghai') DEFAULT now64(6),
    CONSTRAINT ck_core_stock_model CHECK candidate_model_version = 'V1',
    CONSTRAINT ck_core_stock_style_compat CHECK core_style_count = 0,
    CONSTRAINT ck_core_stock_closing_254 CHECK node_seq != 254 OR (state_data_status = 'CURRENT' AND state_source_collection_id = collection_id AND state_source_age_seconds = 0 AND state_is_fallback = 0 AND calculated_at >= scheduled_time)
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id, thscode);

ALTER TABLE market.hithink_core_sector_candidate ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_core_sector_candidate ADD CONSTRAINT IF NOT EXISTS ck_core_sector_closing_254 CHECK node_seq != 254 OR (state_data_status = 'CURRENT' AND state_source_collection_id = collection_id AND state_source_age_seconds = 0 AND state_is_fallback = 0 AND calculated_at >= scheduled_time);
ALTER TABLE market.hithink_core_stock_candidate ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_core_stock_candidate ADD CONSTRAINT IF NOT EXISTS ck_core_stock_closing_254 CHECK node_seq != 254 OR (state_data_status = 'CURRENT' AND state_source_collection_id = collection_id AND state_source_age_seconds = 0 AND state_is_fallback = 0 AND calculated_at >= scheduled_time);
ALTER TABLE market.hithink_core_sector_candidate DROP CONSTRAINT IF EXISTS ck_core_sector_type;
ALTER TABLE market.hithink_core_sector_candidate ADD CONSTRAINT IF NOT EXISTS ck_core_sector_type CHECK sector_type IN ('concept', 'industry');
ALTER TABLE market.hithink_core_stock_candidate MODIFY COLUMN core_style_count UInt16 COMMENT 'COMPATIBILITY ONLY: V1 fixed at 0; prohibited from scoring, ranking, and core-sector links';
ALTER TABLE market.hithink_core_stock_candidate ADD CONSTRAINT IF NOT EXISTS ck_core_stock_style_compat CHECK core_style_count = 0;

CREATE TABLE IF NOT EXISTS market.hithink_emotion_state
(
    trade_date Date,
    collection_id FixedString(11),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
    scheduled_time DateTime64(3, 'Asia/Shanghai'),
    session LowCardinality(String),
    pool_data_status LowCardinality(String) DEFAULT 'NO_SOURCE',
    pool_source_collection_id Nullable(FixedString(11)),
    pool_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    pool_source_age_seconds Nullable(UInt32),
    pool_is_fallback UInt8,
    limit_up_source_collection_id Nullable(FixedString(11)),
    limit_up_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    limit_up_is_fallback UInt8,
    limit_down_source_collection_id Nullable(FixedString(11)),
    limit_down_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    limit_down_is_fallback UInt8,
    limit_break_source_collection_id Nullable(FixedString(11)),
    limit_break_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    limit_break_is_fallback UInt8,
    limit_up_count Nullable(UInt32),
    limit_down_count Nullable(UInt32),
    limit_break_count Nullable(UInt32),
    limit_attempt_count Nullable(UInt32),
    limit_success_rate Nullable(Float64),
    limit_break_rate Nullable(Float64),
    first_board_count Nullable(UInt32),
    second_board_count Nullable(UInt32),
    third_board_count Nullable(UInt32),
    fourth_board_count Nullable(UInt32),
    fifth_plus_board_count Nullable(UInt32),
    max_board_height Nullable(UInt16),
    promotion_base_count Nullable(UInt32),
    promotion_success_count Nullable(UInt32),
    promotion_fail_count Nullable(UInt32),
    promotion_rate Nullable(Float64),
    high_board_count Nullable(UInt32),
    high_board_break_count Nullable(UInt32),
    high_board_fail_count Nullable(UInt32),
    calculated_at DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    version_time DateTime64(6, 'Asia/Shanghai') DEFAULT now64(6),
    CONSTRAINT ck_emotion_collection_digits CHECK match(toString(collection_id), '^[0-9]{11}$'),
    CONSTRAINT ck_emotion_collection_date CHECK substring(toString(collection_id), 1, 8) = formatDateTime(trade_date, '%Y%m%d'),
    CONSTRAINT ck_emotion_node_seq CHECK node_seq BETWEEN 1 AND 254,
    CONSTRAINT ck_emotion_pool_data_status CHECK pool_data_status IN ('CURRENT', 'FALLBACK', 'NOT_APPLICABLE', 'NO_SOURCE'),
    CONSTRAINT ck_emotion_closing_current CHECK node_seq != 254 OR
        (
            pool_data_status = 'CURRENT'
            AND pool_source_collection_id = collection_id
            AND pool_source_scheduled_time = scheduled_time
            AND pool_source_age_seconds = 0
            AND pool_is_fallback = 0
            AND limit_up_source_collection_id = collection_id
            AND limit_down_source_collection_id = collection_id
            AND limit_break_source_collection_id = collection_id
            AND limit_up_is_fallback = 0
            AND limit_down_is_fallback = 0
            AND limit_break_is_fallback = 0
        ),
    CONSTRAINT ck_emotion_scheduled_date CHECK toDate(scheduled_time) = trade_date
)
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id);

ALTER TABLE market.hithink_emotion_state ADD COLUMN IF NOT EXISTS pool_data_status LowCardinality(String) DEFAULT 'NO_SOURCE' AFTER session;
ALTER TABLE market.hithink_emotion_state ADD COLUMN IF NOT EXISTS pool_source_collection_id Nullable(FixedString(11)) AFTER session;
ALTER TABLE market.hithink_emotion_state ADD COLUMN IF NOT EXISTS pool_source_scheduled_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER pool_source_collection_id;
ALTER TABLE market.hithink_emotion_state ADD COLUMN IF NOT EXISTS pool_source_age_seconds Nullable(UInt32) AFTER pool_source_scheduled_time;
ALTER TABLE market.hithink_emotion_state ADD COLUMN IF NOT EXISTS pool_is_fallback UInt8 AFTER pool_source_age_seconds;
ALTER TABLE market.hithink_emotion_state ADD CONSTRAINT IF NOT EXISTS ck_emotion_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_emotion_state ADD CONSTRAINT IF NOT EXISTS ck_emotion_pool_data_status CHECK pool_data_status IN ('CURRENT', 'FALLBACK', 'NOT_APPLICABLE', 'NO_SOURCE');
ALTER TABLE market.hithink_emotion_state ADD CONSTRAINT IF NOT EXISTS ck_emotion_closing_current CHECK node_seq != 254 OR (pool_data_status = 'CURRENT' AND pool_source_collection_id = collection_id AND pool_source_scheduled_time = scheduled_time AND pool_source_age_seconds = 0 AND pool_is_fallback = 0 AND limit_up_source_collection_id = collection_id AND limit_down_source_collection_id = collection_id AND limit_break_source_collection_id = collection_id AND limit_up_is_fallback = 0 AND limit_down_is_fallback = 0 AND limit_break_is_fallback = 0);

CREATE TABLE IF NOT EXISTS market.hithink_sector_index_snapshot
(
    trade_date Date,
    collection_id FixedString(11),
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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
    node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)),
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

-- Every table that carries YYYYMMDD001..YYYYMMDD254 exposes the same immutable suffix.
-- The original collection_id and all existing sorting keys remain unchanged.
ALTER TABLE market.hithink_limit_up_pool ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_limit_down_pool ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_limit_break_pool ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_daily_k_raw ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_adjustment_events ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_daily_k_forward ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_snapshot_raw ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_market_state ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_market_delta_15m ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_emotion_state ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_sector_index_snapshot ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_concept_state ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_industry_state ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_style_state ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;

ALTER TABLE market.hithink_limit_up_pool ADD CONSTRAINT IF NOT EXISTS ck_limit_up_pool_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_limit_down_pool ADD CONSTRAINT IF NOT EXISTS ck_limit_down_pool_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_limit_break_pool ADD CONSTRAINT IF NOT EXISTS ck_limit_break_pool_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_daily_k_raw ADD CONSTRAINT IF NOT EXISTS ck_daily_k_raw_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_adjustment_events ADD CONSTRAINT IF NOT EXISTS ck_adjustment_events_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_daily_k_forward ADD CONSTRAINT IF NOT EXISTS ck_daily_k_forward_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_snapshot_schedule_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_snapshot_schedule_seq_match CHECK node_seq = sequence_no;
ALTER TABLE market.hithink_snapshot_raw ADD CONSTRAINT IF NOT EXISTS ck_snapshot_raw_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_snapshot_derived ADD CONSTRAINT IF NOT EXISTS ck_snapshot_derived_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_market_state ADD CONSTRAINT IF NOT EXISTS ck_market_state_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_sector_index_snapshot ADD CONSTRAINT IF NOT EXISTS ck_sector_index_snapshot_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_concept_state ADD CONSTRAINT IF NOT EXISTS ck_concept_state_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_industry_state ADD CONSTRAINT IF NOT EXISTS ck_industry_state_node_seq CHECK node_seq BETWEEN 1 AND 254;
ALTER TABLE market.hithink_style_state ADD CONSTRAINT IF NOT EXISTS ck_style_state_node_seq CHECK node_seq BETWEEN 1 AND 254;

-- Node 254 is the formal 15:00 close. A tables must contain a new post-close
-- persistence, and B tables must be calculated from fresh closing-node sources.
-- The upstream pool timestamp may lead the planned close by at most 60 seconds.
ALTER TABLE market.hithink_snapshot_raw ADD CONSTRAINT IF NOT EXISTS ck_snapshot_raw_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND source_time >= scheduled_time - INTERVAL 60 SECOND AND ingest_time >= scheduled_time);
ALTER TABLE market.hithink_sector_index_snapshot ADD CONSTRAINT IF NOT EXISTS ck_sector_index_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND source_time >= scheduled_time - INTERVAL 60 SECOND AND ingest_time >= scheduled_time);
ALTER TABLE market.hithink_limit_up_pool ADD CONSTRAINT IF NOT EXISTS ck_limit_up_closing_254 CHECK node_seq != 254 OR (scheduled_time IS NOT NULL AND toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND source_time >= scheduled_time - INTERVAL 60 SECOND AND ingest_time >= scheduled_time);
ALTER TABLE market.hithink_limit_down_pool ADD CONSTRAINT IF NOT EXISTS ck_limit_down_closing_254 CHECK node_seq != 254 OR (scheduled_time IS NOT NULL AND toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND source_time >= scheduled_time - INTERVAL 60 SECOND AND ingest_time >= scheduled_time);
ALTER TABLE market.hithink_limit_break_pool ADD CONSTRAINT IF NOT EXISTS ck_limit_break_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND source_time >= scheduled_time - INTERVAL 60 SECOND AND ingest_time >= scheduled_time);

ALTER TABLE market.hithink_snapshot_derived ADD CONSTRAINT IF NOT EXISTS ck_snapshot_derived_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND source_time >= scheduled_time - INTERVAL 60 SECOND AND ingest_time >= scheduled_time);
ALTER TABLE market.hithink_market_state ADD CONSTRAINT IF NOT EXISTS ck_market_state_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND source_time >= scheduled_time - INTERVAL 60 SECOND AND calculated_at >= scheduled_time);
ALTER TABLE market.hithink_emotion_state ADD CONSTRAINT IF NOT EXISTS ck_emotion_closing_254_freshness CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND pool_data_status = 'CURRENT' AND pool_source_collection_id = collection_id AND pool_source_scheduled_time = scheduled_time AND pool_source_age_seconds = 0 AND pool_is_fallback = 0 AND calculated_at >= scheduled_time);
ALTER TABLE market.hithink_concept_state ADD CONSTRAINT IF NOT EXISTS ck_concept_state_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND stock_source_time >= scheduled_time - INTERVAL 60 SECOND AND index_source_time IS NOT NULL AND index_source_time >= scheduled_time - INTERVAL 60 SECOND AND calculated_at >= scheduled_time);
ALTER TABLE market.hithink_industry_state ADD CONSTRAINT IF NOT EXISTS ck_industry_state_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND stock_source_time >= scheduled_time - INTERVAL 60 SECOND AND index_source_time IS NOT NULL AND index_source_time >= scheduled_time - INTERVAL 60 SECOND AND calculated_at >= scheduled_time);
ALTER TABLE market.hithink_style_state ADD CONSTRAINT IF NOT EXISTS ck_style_state_closing_254 CHECK node_seq != 254 OR (toDate(scheduled_time) = trade_date AND toHour(scheduled_time) = 15 AND toMinute(scheduled_time) = 0 AND toSecond(scheduled_time) = 0 AND stock_source_time >= scheduled_time - INTERVAL 60 SECOND AND index_source_time IS NOT NULL AND index_source_time >= scheduled_time - INTERVAL 60 SECOND AND calculated_at >= scheduled_time);

ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_index_status LowCardinality(String) DEFAULT '' AFTER limit_down_api_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_index_received_count Nullable(UInt32) AFTER sector_index_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_index_api_duration_ms Nullable(UInt32) AFTER sector_index_received_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_state_status LowCardinality(String) DEFAULT '' AFTER sector_index_api_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_state_row_count Nullable(UInt32) AFTER sector_state_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_state_duration_ms Nullable(UInt32) AFTER sector_state_row_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_error_code Nullable(String) AFTER sector_state_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS sector_error_message Nullable(String) AFTER sector_error_code;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS emotion_state_status LowCardinality(String) DEFAULT '' AFTER derivation_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS emotion_state_row_count Nullable(UInt8) AFTER emotion_state_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS emotion_state_duration_ms Nullable(UInt32) AFTER emotion_state_row_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS emotion_error_code Nullable(String) AFTER emotion_state_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS emotion_error_message Nullable(String) AFTER emotion_error_code;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_delta_15m_status LowCardinality(String) DEFAULT '' AFTER emotion_error_message;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_delta_15m_row_count Nullable(UInt8) AFTER market_delta_15m_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_delta_15m_duration_ms Nullable(UInt32) AFTER market_delta_15m_row_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_delta_15m_error_code Nullable(String) AFTER market_delta_15m_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_delta_15m_error_message Nullable(String) AFTER market_delta_15m_error_code;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_schedule_market_delta_15m_status CHECK market_delta_15m_status IN ('', 'PENDING', 'SUCCESS', 'FAILED', 'BLOCKED', 'SKIPPED');
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS capital_migration_status LowCardinality(String) DEFAULT '' AFTER market_delta_15m_error_message;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS capital_migration_row_count Nullable(UInt32) AFTER capital_migration_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS capital_migration_duration_ms Nullable(UInt32) AFTER capital_migration_row_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS capital_migration_error_code Nullable(String) AFTER capital_migration_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS capital_migration_error_message Nullable(String) AFTER capital_migration_error_code;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_schedule_capital_migration_status CHECK capital_migration_status IN ('', 'PENDING', 'SUCCESS', 'FAILED', 'BLOCKED', 'SKIPPED');
ALTER TABLE market.hithink_snapshot_schedule DROP CONSTRAINT IF EXISTS ck_schedule_market_delta_15m_status;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_schedule_market_delta_15m_status CHECK market_delta_15m_status IN ('', 'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'BLOCKED', 'SKIPPED', 'FAILED_TIMEOUT');
ALTER TABLE market.hithink_snapshot_schedule DROP CONSTRAINT IF EXISTS ck_schedule_capital_migration_status;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_schedule_capital_migration_status CHECK capital_migration_status IN ('', 'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'BLOCKED', 'SKIPPED', 'FAILED_TIMEOUT');
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_sector_candidate_status LowCardinality(String) DEFAULT '' AFTER capital_migration_error_message;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_sector_candidate_row_count Nullable(UInt16) AFTER core_sector_candidate_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_sector_candidate_duration_ms Nullable(UInt32) AFTER core_sector_candidate_row_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_sector_candidate_error_code Nullable(String) AFTER core_sector_candidate_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_sector_candidate_error_message Nullable(String) AFTER core_sector_candidate_error_code;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_schedule_core_sector_candidate_status CHECK core_sector_candidate_status IN ('', 'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'BLOCKED', 'SKIPPED', 'FAILED_TIMEOUT');
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_stock_candidate_status LowCardinality(String) DEFAULT '' AFTER core_sector_candidate_error_message;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_stock_candidate_row_count Nullable(UInt16) AFTER core_stock_candidate_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_stock_candidate_duration_ms Nullable(UInt32) AFTER core_stock_candidate_row_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_stock_candidate_error_code Nullable(String) AFTER core_stock_candidate_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS core_stock_candidate_error_message Nullable(String) AFTER core_stock_candidate_error_code;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_schedule_core_stock_candidate_status CHECK core_stock_candidate_status IN ('', 'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'BLOCKED', 'SKIPPED', 'FAILED_TIMEOUT');
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_package_status LowCardinality(String) DEFAULT '' AFTER core_stock_candidate_error_message;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_package_path Nullable(String) AFTER market_package_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_package_bytes Nullable(UInt64) AFTER market_package_path;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_package_duration_ms Nullable(UInt32) AFTER market_package_bytes;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_package_error_code Nullable(String) AFTER market_package_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS market_package_error_message Nullable(String) AFTER market_package_error_code;
ALTER TABLE market.hithink_snapshot_schedule ADD CONSTRAINT IF NOT EXISTS ck_schedule_market_package_status CHECK market_package_status IN ('', 'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'BLOCKED', 'SKIPPED', 'FAILED_TIMEOUT');
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS raw_status LowCardinality(String) DEFAULT 'PENDING' AFTER status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS derivation_status LowCardinality(String) DEFAULT 'PENDING' AFTER raw_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS all_a_snapshot_status LowCardinality(String) DEFAULT 'PENDING' AFTER derivation_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_up_pool_status LowCardinality(String) DEFAULT 'PENDING' AFTER all_a_snapshot_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_down_pool_status LowCardinality(String) DEFAULT 'PENDING' AFTER limit_up_pool_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_break_pool_status LowCardinality(String) DEFAULT 'PENDING' AFTER limit_down_pool_status;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS is_limit_break Nullable(UInt8) AFTER turnover_prev_trade_day_pct;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS limit_break_open_times Nullable(UInt16) AFTER is_limit_break;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS is_limit_up Nullable(UInt8) AFTER turnover_prev_trade_day_pct;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS is_limit_down Nullable(UInt8) AFTER is_limit_up;
ALTER TABLE market.hithink_market_state ADD COLUMN IF NOT EXISTS limit_break_count Nullable(UInt32) AFTER limit_down_count;
ALTER TABLE market.hithink_market_state ADD COLUMN IF NOT EXISTS limit_break_count_delta_prev_available Nullable(Int32) AFTER limit_break_count;
ALTER TABLE market.hithink_concept_state ADD COLUMN IF NOT EXISTS limit_break_count Nullable(UInt32) AFTER limit_down_count;
ALTER TABLE market.hithink_concept_state ADD COLUMN IF NOT EXISTS limit_break_count_delta_prev_available Nullable(Int32) AFTER limit_break_count;
ALTER TABLE market.hithink_industry_state ADD COLUMN IF NOT EXISTS limit_break_count Nullable(UInt32) AFTER limit_down_count;
ALTER TABLE market.hithink_industry_state ADD COLUMN IF NOT EXISTS limit_break_count_delta_prev_available Nullable(Int32) AFTER limit_break_count;
ALTER TABLE market.hithink_style_state ADD COLUMN IF NOT EXISTS limit_break_count Nullable(UInt32) AFTER limit_down_count;
ALTER TABLE market.hithink_style_state ADD COLUMN IF NOT EXISTS limit_break_count_delta_prev_available Nullable(Int32) AFTER limit_break_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS raw_completed_at Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER derivation_status;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS derivation_started_at Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER raw_completed_at;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS derivation_completed_at Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER derivation_started_at;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS derivation_duration_ms Nullable(UInt32) AFTER derivation_completed_at;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS raw_error_code Nullable(String) AFTER derivation_completed_at;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS raw_error_message Nullable(String) AFTER raw_error_code;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS derivation_error_code Nullable(String) AFTER raw_error_message;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS derivation_error_message Nullable(String) AFTER derivation_error_code;

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
ALTER TABLE market.hithink_sector_capital_migration ADD COLUMN IF NOT EXISTS node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3)) AFTER collection_id;
ALTER TABLE market.hithink_sector_capital_migration ADD CONSTRAINT IF NOT EXISTS ck_capital_migration_closing_254 CHECK node_seq != 254 OR (state_data_status = 'CURRENT' AND state_source_collection_id = collection_id AND state_source_scheduled_time = scheduled_time AND state_source_age_seconds = 0 AND state_is_fallback = 0 AND calculated_at >= scheduled_time);
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_pool_collected UInt8 DEFAULT 0 AFTER retry_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_up_source_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER limit_pool_collected;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_down_source_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER limit_up_source_time;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_up_received_count Nullable(UInt32) AFTER limit_down_source_time;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_down_received_count Nullable(UInt32) AFTER limit_up_received_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_up_api_duration_ms Nullable(UInt32) AFTER limit_down_received_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_down_api_duration_ms Nullable(UInt32) AFTER limit_up_api_duration_ms;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_break_source_time Nullable(DateTime64(3, 'Asia/Shanghai')) AFTER limit_down_source_time;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_break_received_count Nullable(UInt32) AFTER limit_down_received_count;
ALTER TABLE market.hithink_snapshot_schedule ADD COLUMN IF NOT EXISTS limit_break_api_duration_ms Nullable(UInt32) AFTER limit_down_api_duration_ms;

ALTER TABLE market.hithink_snapshot_raw ADD COLUMN IF NOT EXISTS collection_id FixedString(11) AFTER trade_date;
ALTER TABLE market.hithink_snapshot_derived ADD COLUMN IF NOT EXISTS collection_id FixedString(11) AFTER trade_date;
ALTER TABLE market.hithink_market_state ADD COLUMN IF NOT EXISTS collection_id FixedString(11) AFTER trade_date;
ALTER TABLE market.hithink_market_state MODIFY COLUMN limit_up_count Nullable(UInt32);
ALTER TABLE market.hithink_market_state MODIFY COLUMN up_5_to_limit_count Nullable(UInt32);
ALTER TABLE market.hithink_market_state MODIFY COLUMN down_5_to_limit_count Nullable(UInt32);
ALTER TABLE market.hithink_market_state MODIFY COLUMN limit_down_count Nullable(UInt32);
ALTER TABLE market.hithink_market_state RENAME COLUMN IF EXISTS turnover_yoy_delta TO turnover_prev_day_delta;
ALTER TABLE market.hithink_market_state RENAME COLUMN IF EXISTS turnover_yoy_pct TO turnover_prev_day_pct;

ALTER TABLE market.hithink_daily_k_raw
    ADD COLUMN IF NOT EXISTS collection_id String
    DEFAULT concat(formatDateTime(trade_date, '%Y%m%d'), '254')
    AFTER trade_date;

ALTER TABLE market.hithink_adjustment_events
    ADD COLUMN IF NOT EXISTS collection_id String
    DEFAULT concat(formatDateTime(ex_date, '%Y%m%d'), '254')
    AFTER ex_date;

ALTER TABLE market.hithink_daily_k_forward
    ADD COLUMN IF NOT EXISTS collection_id String
    DEFAULT concat(formatDateTime(trade_date, '%Y%m%d'), '254')
    AFTER trade_date;
