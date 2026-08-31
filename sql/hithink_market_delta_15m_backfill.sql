INSERT INTO market.hithink_market_delta_15m
(
    trade_date, collection_id, scheduled_time, session,
    state_data_status, state_source_collection_id, state_source_scheduled_time,
    state_source_age_seconds, state_is_fallback,
    base_collection_id, base_scheduled_time, base_age_seconds, delta_status, delta_type,
    up_count_delta_15m, down_count_delta_15m, flat_count_delta_15m,
    up_ratio_delta_15m, down_ratio_delta_15m,
    limit_up_count_delta_15m, up_5_to_limit_count_delta_15m,
    up_1_to_5_count_delta_15m, up_0_to_1_count_delta_15m,
    down_0_to_1_count_delta_15m, down_1_to_5_count_delta_15m,
    down_5_to_limit_count_delta_15m, limit_down_count_delta_15m,
    limit_break_count_delta_15m, turnover_increment_15m,
    turnover_speed_current, turnover_speed_base, turnover_speed_delta_15m,
    turnover_speed_change_pct,
    turnover_accel_count_delta_15m, turnover_decel_count_delta_15m,
    turnover_accel_50_count_delta_15m, turnover_accel_100_count_delta_15m,
    volume_expand_count_delta_15m, volume_contract_count_delta_15m,
    volume_ratio_1_5_count_delta_15m, volume_ratio_2_count_delta_15m,
    volume_ratio_3_count_delta_15m,
    new_high_count_delta_15m, new_low_count_delta_15m,
    price_up_1m_count_delta_15m, price_down_1m_count_delta_15m,
    price_flat_1m_count_delta_15m,
    volume_price_up_count_delta_15m, volume_price_down_count_delta_15m,
    contract_price_up_count_delta_15m, contract_price_down_count_delta_15m
)
WITH
targets AS
(
    SELECT
        trade_date,
        collection_id,
        node_seq AS target_node_seq,
        scheduled_time,
        session,
        if(node_seq <= 132, 'AM', 'PM') AS business_period
    FROM market.hithink_snapshot_schedule FINAL
    WHERE trade_date = {trade_date:Date}
      AND node_seq IN (
          11, 12, 27, 42, 57, 72, 87, 102, 117, 132,
          133, 148, 163, 178, 193, 208, 223, 238, 254
      )
    ORDER BY trade_date, business_period, scheduled_time
),
market_states AS
(
    SELECT
        *,
        if(scheduled_time < toDateTime64(concat(toString(trade_date), ' 12:00:00'), 3, 'Asia/Shanghai'), 'AM', 'PM') AS business_period
    FROM market.hithink_market_state FINAL
    WHERE trade_date = {trade_date:Date}
      AND
      (
          scheduled_time < toDateTime64(concat(toString(trade_date), ' 12:00:00'), 3, 'Asia/Shanghai')
          OR scheduled_time >= toDateTime64(concat(toString(trade_date), ' 13:00:00'), 3, 'Asia/Shanghai')
      )
    ORDER BY trade_date, business_period, scheduled_time
),
resolved_targets AS
(
    SELECT
        target.*,
        state.collection_id AS state_source_collection_id,
        state.scheduled_time AS state_source_scheduled_time,
        if(
            state.collection_id IS NULL,
            'NO_SOURCE',
            if(state.collection_id = target.collection_id, 'CURRENT', 'FALLBACK')
        ) AS resolved_state_data_status,
        state.up_count AS up_count,
        state.down_count AS down_count,
        state.flat_count AS flat_count,
        state.up_ratio AS up_ratio,
        state.down_ratio AS down_ratio,
        state.limit_up_count AS limit_up_count,
        state.up_5_to_limit_count AS up_5_to_limit_count,
        state.up_1_to_5_count AS up_1_to_5_count,
        state.up_0_to_1_count AS up_0_to_1_count,
        state.down_0_to_1_count AS down_0_to_1_count,
        state.down_1_to_5_count AS down_1_to_5_count,
        state.down_5_to_limit_count AS down_5_to_limit_count,
        state.limit_down_count AS limit_down_count,
        state.limit_break_count AS limit_break_count,
        state.turnover_total AS turnover_total,
        state.turnover_delta_1m_total AS turnover_delta_1m_total,
        state.turnover_accel_count AS turnover_accel_count,
        state.turnover_decel_count AS turnover_decel_count,
        state.turnover_accel_50_count AS turnover_accel_50_count,
        state.turnover_accel_100_count AS turnover_accel_100_count,
        state.volume_expand_count AS volume_expand_count,
        state.volume_contract_count AS volume_contract_count,
        state.volume_ratio_1_5_count AS volume_ratio_1_5_count,
        state.volume_ratio_2_count AS volume_ratio_2_count,
        state.volume_ratio_3_count AS volume_ratio_3_count,
        state.new_high_count AS new_high_count,
        state.new_low_count AS new_low_count,
        state.price_up_1m_count AS price_up_1m_count,
        state.price_down_1m_count AS price_down_1m_count,
        state.price_flat_1m_count AS price_flat_1m_count,
        state.volume_price_up_count AS volume_price_up_count,
        state.volume_price_down_count AS volume_price_down_count,
        state.contract_price_up_count AS contract_price_up_count,
        state.contract_price_down_count AS contract_price_down_count
    FROM targets AS target
    ASOF LEFT JOIN market_states AS state
        ON target.trade_date = state.trade_date
        AND target.business_period = state.business_period
        AND target.scheduled_time >= state.scheduled_time
),
usable_targets AS
(
    SELECT *
    FROM resolved_targets
    WHERE target_node_seq != 254 OR state_source_collection_id = collection_id
),
current_targets AS
(
    SELECT *, scheduled_time - INTERVAL 1 MILLISECOND AS previous_lookup_time
    FROM usable_targets
    ORDER BY trade_date, business_period, previous_lookup_time
),
base_targets AS
(
    SELECT *
    FROM usable_targets
    ORDER BY trade_date, business_period, scheduled_time
),
paired AS
(
    SELECT
        current.*,
        base.state_source_collection_id AS selected_base_collection_id,
        base.state_source_scheduled_time AS selected_base_scheduled_time,
        base.up_count AS base_up_count,
        base.down_count AS base_down_count,
        base.flat_count AS base_flat_count,
        base.up_ratio AS base_up_ratio,
        base.down_ratio AS base_down_ratio,
        base.limit_up_count AS base_limit_up_count,
        base.up_5_to_limit_count AS base_up_5_to_limit_count,
        base.up_1_to_5_count AS base_up_1_to_5_count,
        base.up_0_to_1_count AS base_up_0_to_1_count,
        base.down_0_to_1_count AS base_down_0_to_1_count,
        base.down_1_to_5_count AS base_down_1_to_5_count,
        base.down_5_to_limit_count AS base_down_5_to_limit_count,
        base.limit_down_count AS base_limit_down_count,
        base.limit_break_count AS base_limit_break_count,
        base.turnover_total AS base_turnover_total,
        base.turnover_delta_1m_total AS base_turnover_delta_1m_total,
        base.turnover_accel_count AS base_turnover_accel_count,
        base.turnover_decel_count AS base_turnover_decel_count,
        base.turnover_accel_50_count AS base_turnover_accel_50_count,
        base.turnover_accel_100_count AS base_turnover_accel_100_count,
        base.volume_expand_count AS base_volume_expand_count,
        base.volume_contract_count AS base_volume_contract_count,
        base.volume_ratio_1_5_count AS base_volume_ratio_1_5_count,
        base.volume_ratio_2_count AS base_volume_ratio_2_count,
        base.volume_ratio_3_count AS base_volume_ratio_3_count,
        base.new_high_count AS base_new_high_count,
        base.new_low_count AS base_new_low_count,
        base.price_up_1m_count AS base_price_up_1m_count,
        base.price_down_1m_count AS base_price_down_1m_count,
        base.price_flat_1m_count AS base_price_flat_1m_count,
        base.volume_price_up_count AS base_volume_price_up_count,
        base.volume_price_down_count AS base_volume_price_down_count,
        base.contract_price_up_count AS base_contract_price_up_count,
        base.contract_price_down_count AS base_contract_price_down_count,
        if(
            current.target_node_seq IN (11, 133),
            'NO_BASE',
            if(
                current.state_source_collection_id IS NULL
                    OR base.state_source_collection_id IS NULL,
                'NO_SOURCE',
                'VALID'
            )
        ) AS resolved_delta_status
    FROM current_targets AS current
    ASOF LEFT JOIN base_targets AS base
        ON current.trade_date = base.trade_date
        AND current.business_period = base.business_period
        AND current.previous_lookup_time >= base.scheduled_time
)
SELECT
    trade_date,
    collection_id,
    scheduled_time,
    session,
    resolved_state_data_status,
    state_source_collection_id,
    state_source_scheduled_time,
    if(
        state_source_collection_id IS NULL,
        CAST(NULL, 'Nullable(UInt32)'),
        toUInt32(dateDiff('second', state_source_scheduled_time, scheduled_time))
    ),
    toUInt8(resolved_state_data_status = 'FALLBACK'),
    if(resolved_delta_status = 'VALID', selected_base_collection_id,
        CAST(NULL, 'Nullable(FixedString(11))')),
    if(resolved_delta_status = 'VALID', selected_base_scheduled_time,
        CAST(NULL AS Nullable(DateTime64(3, 'Asia/Shanghai')))),
    if(resolved_delta_status = 'VALID',
        toUInt32(dateDiff('second', selected_base_scheduled_time, scheduled_time)),
        CAST(NULL, 'Nullable(UInt32)')),
    resolved_delta_status,
    multiIf(
        target_node_seq IN (11, 133), 'SESSION_BASE',
        target_node_seq = 12, 'AUCTION_TO_OPEN',
        'NORMAL_15M'
    ),
    if(resolved_delta_status = 'VALID', toInt32(up_count) - toInt32(base_up_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(down_count) - toInt32(base_down_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(flat_count) - toInt32(base_flat_count), NULL),
    if(resolved_delta_status = 'VALID', up_ratio - base_up_ratio, NULL),
    if(resolved_delta_status = 'VALID', down_ratio - base_down_ratio, NULL),
    if(resolved_delta_status = 'VALID', toInt32(limit_up_count) - toInt32(base_limit_up_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(up_5_to_limit_count) - toInt32(base_up_5_to_limit_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(up_1_to_5_count) - toInt32(base_up_1_to_5_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(up_0_to_1_count) - toInt32(base_up_0_to_1_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(down_0_to_1_count) - toInt32(base_down_0_to_1_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(down_1_to_5_count) - toInt32(base_down_1_to_5_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(down_5_to_limit_count) - toInt32(base_down_5_to_limit_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(limit_down_count) - toInt32(base_limit_down_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(limit_break_count) - toInt32(base_limit_break_count), NULL),
    if(resolved_delta_status = 'VALID', turnover_total - base_turnover_total, NULL),
    if(resolved_delta_status = 'VALID', turnover_delta_1m_total, NULL),
    if(resolved_delta_status = 'VALID', base_turnover_delta_1m_total, NULL),
    if(resolved_delta_status = 'VALID', turnover_delta_1m_total - base_turnover_delta_1m_total, NULL),
    if(
        resolved_delta_status = 'VALID'
        AND turnover_delta_1m_total IS NOT NULL
        AND base_turnover_delta_1m_total IS NOT NULL
        AND base_turnover_delta_1m_total != 0,
        toFloat64(turnover_delta_1m_total) / toFloat64(base_turnover_delta_1m_total) - 1,
        NULL
    ),
    if(resolved_delta_status = 'VALID', toInt32(turnover_accel_count) - toInt32(base_turnover_accel_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(turnover_decel_count) - toInt32(base_turnover_decel_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(turnover_accel_50_count) - toInt32(base_turnover_accel_50_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(turnover_accel_100_count) - toInt32(base_turnover_accel_100_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(volume_expand_count) - toInt32(base_volume_expand_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(volume_contract_count) - toInt32(base_volume_contract_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(volume_ratio_1_5_count) - toInt32(base_volume_ratio_1_5_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(volume_ratio_2_count) - toInt32(base_volume_ratio_2_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(volume_ratio_3_count) - toInt32(base_volume_ratio_3_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(new_high_count) - toInt32(base_new_high_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(new_low_count) - toInt32(base_new_low_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(price_up_1m_count) - toInt32(base_price_up_1m_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(price_down_1m_count) - toInt32(base_price_down_1m_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(price_flat_1m_count) - toInt32(base_price_flat_1m_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(volume_price_up_count) - toInt32(base_volume_price_up_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(volume_price_down_count) - toInt32(base_volume_price_down_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(contract_price_up_count) - toInt32(base_contract_price_up_count), NULL),
    if(resolved_delta_status = 'VALID', toInt32(contract_price_down_count) - toInt32(base_contract_price_down_count), NULL)
FROM paired
SETTINGS join_use_nulls = 1;
