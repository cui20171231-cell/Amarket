-- 用当前有效的AI战略观察名单，回看2026-08-28 15:00的完整状态。
-- 这是“当前名单的历史市场表现”查询；正式历史回测名单应再按effective_from/effective_to过滤。
WITH
watchlist AS
(
    SELECT * FROM market.strategic_sector_watchlist FINAL WHERE is_active=1
),
migration AS
(
    SELECT *,node_seq FROM market.hithink_sector_capital_migration FINAL WHERE trade_date={trade_date:Date}
),
current_migration AS
(
    SELECT * FROM migration WHERE node_seq=254
),
rank_history AS
(
    SELECT sector_type,sector_code,
        arraySlice(
            arraySort(x -> tupleElement(x,1),groupArray(tuple(scheduled_time,turnover_share_rank))),
            -5,5
        ) turnover_rank_last_5
    FROM migration
    GROUP BY sector_type,sector_code
),
candidates AS
(
    SELECT sector_type,sector_code,candidate_rank,node_seq
    FROM market.hithink_core_sector_candidate FINAL
    WHERE trade_date={trade_date:Date} AND node_seq=254
),
sector_states AS
(
    SELECT collection_id,'concept' sector_type,sector_code,index_change_ratio_pct
    FROM market.hithink_concept_state FINAL WHERE trade_date={trade_date:Date}
    UNION ALL
    SELECT collection_id,'industry',sector_code,index_change_ratio_pct
    FROM market.hithink_industry_state FINAL WHERE trade_date={trade_date:Date}
    UNION ALL
    SELECT collection_id,'style',sector_code,index_change_ratio_pct
    FROM market.hithink_style_state FINAL WHERE trade_date={trade_date:Date}
)
SELECT
    w.strategic_theme,w.strategic_subtheme,w.watch_level,
    w.sector_type AS sector_type,w.sector_code AS sector_code,w.sector_name AS sector_name,
    m.scheduled_time,m.state_data_status,m.state_source_scheduled_time,m.state_source_age_seconds,
    s.index_change_ratio_pct,
    m.turnover_share_rank current_class_rank,
    c.candidate_rank current_candidate_rank,
    m.turnover_market_share_pct,
    m.turnover_1m_market_share_pct,
    m.turnover_market_share_delta_15m,
    m.turnover_1m_market_share_delta_15m,
    m.up_ratio,m.new_high_ratio,
    m.limit_up_count,m.limit_break_count,
    m.turnover_1m_top1_share_pct,m.turnover_1m_top3_share_pct,m.turnover_1m_top5_share_pct,
    h.turnover_rank_last_5
FROM watchlist w
LEFT JOIN current_migration m USING (sector_type,sector_code)
LEFT JOIN candidates c USING (sector_type,sector_code)
LEFT JOIN rank_history h USING (sector_type,sector_code)
LEFT JOIN sector_states s ON s.sector_type=w.sector_type AND s.sector_code=w.sector_code
    AND s.collection_id=m.state_source_collection_id
ORDER BY w.strategic_theme,w.watch_level,w.sector_type,m.turnover_share_rank NULLS LAST,w.sector_code
SETTINGS join_use_nulls=1;
