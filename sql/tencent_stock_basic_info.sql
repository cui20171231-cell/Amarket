CREATE TABLE IF NOT EXISTS market.tencent_stock_basic_info
(
    `snapshot_date` Date,
    `scheduled_time` DateTime64(3, 'Asia/Shanghai'),
    `source_time` Nullable(DateTime64(3, 'Asia/Shanghai')),
    `batch_id` String,
    `thscode` LowCardinality(String),
    `ticker` FixedString(6),
    `stock_name` String,
    `total_shares` Nullable(UInt64),
    `float_shares` Nullable(UInt64),
    `source_tag` LowCardinality(String) DEFAULT 'tencent',
    `ingest_time` DateTime64(3, 'Asia/Shanghai') DEFAULT now64(3),
    `version_time` DateTime64(6, 'Asia/Shanghai') DEFAULT now64(6),
    CONSTRAINT ck_tencent_stock_basic_scheduled_date
        CHECK toDate(scheduled_time) = snapshot_date,
    CONSTRAINT ck_tencent_stock_basic_ticker
        CHECK match(toString(ticker), '^[0-9]{6}$'),
    CONSTRAINT ck_tencent_stock_basic_thscode
        CHECK match(toString(thscode), '^[0-9]{6}\\.(SH|SZ|BJ)$')
)
ENGINE = ReplacingMergeTree(version_time)
ORDER BY thscode
COMMENT '个股基础信息表';
