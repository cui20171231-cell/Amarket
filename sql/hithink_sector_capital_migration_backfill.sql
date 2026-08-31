INSERT INTO market.hithink_sector_capital_migration
(
    trade_date, collection_id, scheduled_time,
    sector_type, sector_code, sector_name,
    state_data_status, state_source_collection_id, state_source_scheduled_time,
    state_source_age_seconds, state_is_fallback,
    base_collection_id, base_scheduled_time, base_age_seconds, delta_type,
    turnover_total, turnover_delta_1m_total,
    turnover_market_share_pct, turnover_1m_market_share_pct,
    turnover_market_share_delta_15m, turnover_1m_market_share_delta_15m,
    turnover_share_rank, turnover_1m_share_rank,
    turnover_share_rank_delta, turnover_1m_share_rank_delta,
    turnover_increment_15m, turnover_increment_market_share_pct,
    up_ratio, down_ratio, limit_up_count, limit_break_count,
    new_high_ratio, new_low_ratio,
    up_ratio_delta_15m, down_ratio_delta_15m,
    limit_up_count_delta_15m, limit_break_count_delta_15m,
    new_high_ratio_delta_15m, new_low_ratio_delta_15m,
    turnover_1m_top1_share_pct, turnover_1m_top3_share_pct,
    turnover_1m_top5_share_pct,
    top1_share_delta_15m, top3_share_delta_15m, top5_share_delta_15m
)
WITH
sector_states AS
(
    SELECT
        trade_date, collection_id, scheduled_time,
        'concept' AS sector_type, sector_code, sector_name,
        turnover_total, turnover_delta_1m_total,
        turnover_market_share_pct, turnover_1m_market_share_pct,
        up_ratio, down_ratio, limit_up_count, limit_break_count,
        new_high_ratio, new_low_ratio,
        turnover_1m_top1_share_pct, turnover_1m_top3_share_pct,
        turnover_1m_top5_share_pct
    FROM market.hithink_concept_state FINAL
    WHERE trade_date = {trade_date:Date}
    UNION ALL
    SELECT
        trade_date, collection_id, scheduled_time,
        'industry' AS sector_type, sector_code, sector_name,
        turnover_total, turnover_delta_1m_total,
        turnover_market_share_pct, turnover_1m_market_share_pct,
        up_ratio, down_ratio, limit_up_count, limit_break_count,
        new_high_ratio, new_low_ratio,
        turnover_1m_top1_share_pct, turnover_1m_top3_share_pct,
        turnover_1m_top5_share_pct
    FROM market.hithink_industry_state FINAL
    WHERE trade_date = {trade_date:Date}
    UNION ALL
    SELECT
        trade_date, collection_id, scheduled_time,
        'style' AS sector_type, sector_code, sector_name,
        turnover_total, turnover_delta_1m_total,
        turnover_market_share_pct, turnover_1m_market_share_pct,
        up_ratio, down_ratio, limit_up_count, limit_break_count,
        new_high_ratio, new_low_ratio,
        turnover_1m_top1_share_pct, turnover_1m_top3_share_pct,
        turnover_1m_top5_share_pct
    FROM market.hithink_style_state FINAL
    WHERE trade_date = {trade_date:Date}
),
ranked_states AS
(
    SELECT
        *,
        if(
            turnover_market_share_pct IS NULL,
            CAST(NULL, 'Nullable(UInt16)'),
            toUInt16(row_number() OVER (
                PARTITION BY trade_date, collection_id, sector_type
                ORDER BY turnover_market_share_pct DESC NULLS LAST, sector_code
            ))
        ) AS turnover_share_rank,
        if(
            turnover_1m_market_share_pct IS NULL,
            CAST(NULL, 'Nullable(UInt16)'),
            toUInt16(row_number() OVER (
                PARTITION BY trade_date, collection_id, sector_type
                ORDER BY turnover_1m_market_share_pct DESC NULLS LAST, sector_code
            ))
        ) AS turnover_1m_share_rank
    FROM sector_states
),
expected_sector_counts AS
(
    SELECT trade_date, sector_type, uniqExact(sector_code) AS expected_sector_count
    FROM sector_states
    GROUP BY trade_date, sector_type
),
source_nodes AS
(
    SELECT
        state.trade_date AS trade_date,
        state.sector_type AS sector_type,
        state.collection_id AS collection_id,
        state.scheduled_time AS scheduled_time,
        if(
            state.scheduled_time < toDateTime64(concat(toString(state.trade_date), ' 12:00:00'), 3, 'Asia/Shanghai'),
            'AM', 'PM'
        ) AS business_period
    FROM sector_states AS state
    INNER JOIN expected_sector_counts AS expected
        ON state.trade_date = expected.trade_date
        AND state.sector_type = expected.sector_type
    WHERE state.scheduled_time < toDateTime64(concat(toString(state.trade_date), ' 12:00:00'), 3, 'Asia/Shanghai')
       OR state.scheduled_time >= toDateTime64(concat(toString(state.trade_date), ' 13:00:00'), 3, 'Asia/Shanghai')
    GROUP BY
        state.trade_date, state.sector_type, state.collection_id, state.scheduled_time
    HAVING uniqExact(state.sector_code) = max(expected.expected_sector_count)
    ORDER BY trade_date, sector_type, business_period, scheduled_time
),
targets AS
(
    SELECT
        trade_date, collection_id, node_seq AS target_node_seq, scheduled_time,
        arrayJoin(['concept', 'industry', 'style']) AS sector_type,
        if(node_seq <= 132, 'AM', 'PM') AS business_period
    FROM market.hithink_snapshot_schedule FINAL
    WHERE trade_date = {trade_date:Date}
      AND node_seq IN (
          11, 12, 27, 42, 57, 72, 87, 102, 117, 132,
          133, 148, 163, 178, 193, 208, 223, 238, 254
      )
    ORDER BY trade_date, sector_type, business_period, scheduled_time
),
selected_nodes AS
(
    SELECT
        target.*,
        source.collection_id AS selected_source_collection_id,
        source.scheduled_time AS selected_source_scheduled_time
    FROM targets AS target
    ASOF LEFT JOIN source_nodes AS source
        ON target.trade_date = source.trade_date
        AND target.sector_type = source.sector_type
        AND target.business_period = source.business_period
        AND target.scheduled_time >= source.scheduled_time
),
resolved_nodes AS
(
    SELECT
        *,
        if(
            target_node_seq = 254 AND selected_source_collection_id != collection_id,
            CAST(NULL, 'Nullable(FixedString(11))'),
            selected_source_collection_id
        ) AS state_source_collection_id,
        if(
            target_node_seq = 254 AND selected_source_collection_id != collection_id,
            CAST(NULL AS Nullable(DateTime64(3, 'Asia/Shanghai'))),
            selected_source_scheduled_time
        ) AS state_source_scheduled_time
    FROM selected_nodes
),
current_targets AS
(
    SELECT *, scheduled_time - INTERVAL 1 MILLISECOND AS previous_lookup_time
    FROM resolved_nodes
    ORDER BY trade_date, sector_type, business_period, previous_lookup_time
),
base_targets AS
(
    SELECT *
    FROM resolved_nodes
    ORDER BY trade_date, sector_type, business_period, scheduled_time
),
paired_nodes AS
(
    SELECT
        current.*,
        base.state_source_collection_id AS selected_base_collection_id,
        base.state_source_scheduled_time AS selected_base_scheduled_time
    FROM current_targets AS current
    ASOF LEFT JOIN base_targets AS base
        ON current.trade_date = base.trade_date
        AND current.sector_type = base.sector_type
        AND current.business_period = base.business_period
        AND current.previous_lookup_time >= base.scheduled_time
),
sector_universe AS
(
    SELECT
        trade_date, sector_type, sector_code,
        argMax(sector_name, scheduled_time) AS sector_name
    FROM sector_states
    GROUP BY trade_date, sector_type, sector_code
),
current_rows AS
(
    SELECT
        node.trade_date AS trade_date,
        node.collection_id AS collection_id,
        node.target_node_seq AS target_node_seq,
        node.scheduled_time AS scheduled_time,
        node.sector_type AS sector_type,
        node.business_period AS business_period,
        node.state_source_collection_id AS state_source_collection_id,
        node.state_source_scheduled_time AS state_source_scheduled_time,
        node.selected_base_collection_id AS selected_base_collection_id,
        node.selected_base_scheduled_time AS selected_base_scheduled_time,
        universe.sector_code AS sector_code,
        universe.sector_name AS universe_sector_name,
        current.sector_code AS current_sector_code,
        current.sector_name AS current_sector_name,
        current.turnover_total AS current_turnover_total,
        current.turnover_delta_1m_total AS current_turnover_delta_1m_total,
        current.turnover_market_share_pct AS current_turnover_market_share_pct,
        current.turnover_1m_market_share_pct AS current_turnover_1m_market_share_pct,
        current.turnover_share_rank AS current_turnover_share_rank,
        current.turnover_1m_share_rank AS current_turnover_1m_share_rank,
        current.up_ratio AS current_up_ratio,
        current.down_ratio AS current_down_ratio,
        current.limit_up_count AS current_limit_up_count,
        current.limit_break_count AS current_limit_break_count,
        current.new_high_ratio AS current_new_high_ratio,
        current.new_low_ratio AS current_new_low_ratio,
        current.turnover_1m_top1_share_pct AS current_top1_share_pct,
        current.turnover_1m_top3_share_pct AS current_top3_share_pct,
        current.turnover_1m_top5_share_pct AS current_top5_share_pct
    FROM paired_nodes AS node
    INNER JOIN sector_universe AS universe
        ON node.trade_date = universe.trade_date
        AND node.sector_type = universe.sector_type
    LEFT JOIN ranked_states AS current
        ON node.trade_date = current.trade_date
        AND node.sector_type = current.sector_type
        AND node.state_source_collection_id = current.collection_id
        AND universe.sector_code = current.sector_code
),
paired_rows AS
(
    SELECT
        current.trade_date AS trade_date,
        current.collection_id AS collection_id,
        current.target_node_seq AS target_node_seq,
        current.scheduled_time AS scheduled_time,
        current.sector_type AS sector_type,
        current.business_period AS business_period,
        current.state_source_collection_id AS state_source_collection_id,
        current.state_source_scheduled_time AS state_source_scheduled_time,
        current.selected_base_collection_id AS selected_base_collection_id,
        current.selected_base_scheduled_time AS selected_base_scheduled_time,
        current.sector_code AS sector_code,
        current.universe_sector_name AS universe_sector_name,
        current.current_sector_code AS current_sector_code,
        current.current_sector_name AS current_sector_name,
        current.current_turnover_total AS current_turnover_total,
        current.current_turnover_delta_1m_total AS current_turnover_delta_1m_total,
        current.current_turnover_market_share_pct AS current_turnover_market_share_pct,
        current.current_turnover_1m_market_share_pct AS current_turnover_1m_market_share_pct,
        current.current_turnover_share_rank AS current_turnover_share_rank,
        current.current_turnover_1m_share_rank AS current_turnover_1m_share_rank,
        current.current_up_ratio AS current_up_ratio,
        current.current_down_ratio AS current_down_ratio,
        current.current_limit_up_count AS current_limit_up_count,
        current.current_limit_break_count AS current_limit_break_count,
        current.current_new_high_ratio AS current_new_high_ratio,
        current.current_new_low_ratio AS current_new_low_ratio,
        current.current_top1_share_pct AS current_top1_share_pct,
        current.current_top3_share_pct AS current_top3_share_pct,
        current.current_top5_share_pct AS current_top5_share_pct,
        base.sector_code AS base_sector_code,
        base.turnover_total AS base_turnover_total,
        base.turnover_delta_1m_total AS base_turnover_delta_1m_total,
        base.turnover_market_share_pct AS base_turnover_market_share_pct,
        base.turnover_1m_market_share_pct AS base_turnover_1m_market_share_pct,
        base.turnover_share_rank AS base_turnover_share_rank,
        base.turnover_1m_share_rank AS base_turnover_1m_share_rank,
        base.up_ratio AS base_up_ratio,
        base.down_ratio AS base_down_ratio,
        base.limit_up_count AS base_limit_up_count,
        base.limit_break_count AS base_limit_break_count,
        base.new_high_ratio AS base_new_high_ratio,
        base.new_low_ratio AS base_new_low_ratio,
        base.turnover_1m_top1_share_pct AS base_top1_share_pct,
        base.turnover_1m_top3_share_pct AS base_top3_share_pct,
        base.turnover_1m_top5_share_pct AS base_top5_share_pct,
        current.current_sector_code IS NOT NULL AS has_current,
        (
            current.target_node_seq NOT IN (11, 133)
            AND current.current_sector_code IS NOT NULL
            AND base.sector_code IS NOT NULL
        ) AS can_calculate
    FROM current_rows AS current
    LEFT JOIN ranked_states AS base
        ON current.trade_date = base.trade_date
        AND current.sector_type = base.sector_type
        AND current.selected_base_collection_id = base.collection_id
        AND current.sector_code = base.sector_code
)
SELECT
    trade_date,
    collection_id,
    scheduled_time,
    sector_type,
    sector_code,
    if(has_current, current_sector_name, universe_sector_name),
    multiIf(
        NOT has_current, 'NO_SOURCE',
        state_source_collection_id = collection_id, 'CURRENT',
        'FALLBACK'
    ),
    if(has_current, state_source_collection_id, CAST(NULL, 'Nullable(FixedString(11))')),
    if(has_current, state_source_scheduled_time,
        CAST(NULL AS Nullable(DateTime64(3, 'Asia/Shanghai')))),
    if(has_current,
        toUInt32(dateDiff('second', state_source_scheduled_time, scheduled_time)),
        CAST(NULL, 'Nullable(UInt32)')),
    toUInt8(has_current AND state_source_collection_id != collection_id),
    if(can_calculate, selected_base_collection_id, CAST(NULL, 'Nullable(FixedString(11))')),
    if(can_calculate, selected_base_scheduled_time,
        CAST(NULL AS Nullable(DateTime64(3, 'Asia/Shanghai')))),
    if(can_calculate,
        toUInt32(dateDiff('second', selected_base_scheduled_time, scheduled_time)),
        CAST(NULL, 'Nullable(UInt32)')),
    multiIf(
        target_node_seq IN (11, 133), 'SESSION_BASE',
        target_node_seq = 12, 'AUCTION_TO_OPEN',
        'NORMAL_15M'
    ),
    current_turnover_total,
    current_turnover_delta_1m_total,
    current_turnover_market_share_pct,
    current_turnover_1m_market_share_pct,
    if(can_calculate,
        current_turnover_market_share_pct - base_turnover_market_share_pct, NULL),
    if(can_calculate,
        current_turnover_1m_market_share_pct - base_turnover_1m_market_share_pct, NULL),
    current_turnover_share_rank,
    current_turnover_1m_share_rank,
    if(can_calculate,
        toInt16(base_turnover_share_rank) - toInt16(current_turnover_share_rank), NULL),
    if(can_calculate,
        toInt16(base_turnover_1m_share_rank) - toInt16(current_turnover_1m_share_rank), NULL),
    if(can_calculate, current_turnover_total - base_turnover_total, NULL),
    if(
        can_calculate
        AND current_turnover_market_share_pct IS NOT NULL
        AND base_turnover_market_share_pct IS NOT NULL
        AND current_turnover_market_share_pct != 0
        AND base_turnover_market_share_pct != 0
        AND
        (
            current_turnover_total * 100 / current_turnover_market_share_pct
            - base_turnover_total * 100 / base_turnover_market_share_pct
        ) != 0,
        (current_turnover_total - base_turnover_total) * 100
            /
            (
                current_turnover_total * 100 / current_turnover_market_share_pct
                - base_turnover_total * 100 / base_turnover_market_share_pct
            ),
        NULL
    ),
    current_up_ratio,
    current_down_ratio,
    current_limit_up_count,
    current_limit_break_count,
    current_new_high_ratio,
    current_new_low_ratio,
    if(can_calculate, current_up_ratio - base_up_ratio, NULL),
    if(can_calculate, current_down_ratio - base_down_ratio, NULL),
    if(can_calculate, toInt32(current_limit_up_count) - toInt32(base_limit_up_count), NULL),
    if(can_calculate, toInt32(current_limit_break_count) - toInt32(base_limit_break_count), NULL),
    if(can_calculate, current_new_high_ratio - base_new_high_ratio, NULL),
    if(can_calculate, current_new_low_ratio - base_new_low_ratio, NULL),
    current_top1_share_pct,
    current_top3_share_pct,
    current_top5_share_pct,
    if(can_calculate, current_top1_share_pct - base_top1_share_pct, NULL),
    if(can_calculate, current_top3_share_pct - base_top3_share_pct, NULL),
    if(can_calculate, current_top5_share_pct - base_top5_share_pct, NULL)
FROM paired_rows
SETTINGS join_use_nulls = 1;
