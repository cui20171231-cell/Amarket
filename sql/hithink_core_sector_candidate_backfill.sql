INSERT INTO market.hithink_core_sector_candidate
(
trade_date,collection_id,scheduled_time,sector_type,sector_code,sector_name,candidate_model_version,
state_data_status,state_source_collection_id,state_source_scheduled_time,state_source_age_seconds,state_is_fallback,delta_type,
candidate_rank,candidate_hit_count,candidate_reason_mask,candidate_score_v1,
hit_price_strength,hit_breadth,hit_capital_share,hit_capital_acceleration,hit_limit_strength,hit_new_high,hit_persistence,
index_change_ratio_pct,index_change_1m_pct,index_change_rank,index_change_1m_rank,
up_ratio,down_ratio,up_ratio_delta_15m,down_ratio_delta_15m,
turnover_market_share_pct,turnover_1m_market_share_pct,turnover_market_share_delta_15m,turnover_1m_market_share_delta_15m,
turnover_increment_15m,turnover_increment_market_share_pct,turnover_share_rank,turnover_1m_share_rank,turnover_share_rank_delta,turnover_1m_share_rank_delta,
limit_up_count,limit_break_count,limit_up_count_delta_15m,limit_break_count_delta_15m,new_high_ratio,new_low_ratio,new_high_ratio_delta_15m,new_low_ratio_delta_15m,
turnover_1m_top1_share_pct,turnover_1m_top3_share_pct,turnover_1m_top5_share_pct,top1_share_delta_15m,top3_share_delta_15m,top5_share_delta_15m,
candidate_hit_count_3,candidate_hit_count_5,turnover_share_positive_count_3,up_ratio_positive_count_3,best_candidate_rank_3,best_candidate_rank_5
)
WITH indexes AS
(
    SELECT trade_date, collection_id, 'concept' sector_type, sector_code,
        index_change_ratio_pct, index_change_1m_pct FROM market.hithink_concept_state FINAL WHERE trade_date={trade_date:Date}
    UNION ALL SELECT trade_date, collection_id, 'industry', sector_code,
        index_change_ratio_pct, index_change_1m_pct FROM market.hithink_industry_state FINAL WHERE trade_date={trade_date:Date}
),
evidence AS
(
    SELECT m.*, i.index_change_ratio_pct, i.index_change_1m_pct,
        if(m.node_seq<=132,'AM','PM') business_period
    FROM
    (
        SELECT *, node_seq
        FROM market.hithink_sector_capital_migration FINAL
        WHERE trade_date={trade_date:Date}
          AND sector_type IN ('concept','industry')
    ) m
    LEFT JOIN indexes i ON m.trade_date=i.trade_date AND m.sector_type=i.sector_type
        AND m.sector_code=i.sector_code AND m.state_source_collection_id=i.collection_id
),
ranked AS
(
    SELECT *,
        toUInt16(row_number() OVER (PARTITION BY collection_id,sector_type ORDER BY index_change_ratio_pct DESC NULLS LAST,sector_code)) index_change_rank,
        toUInt16(row_number() OVER (PARTITION BY collection_id,sector_type ORDER BY index_change_1m_pct DESC NULLS LAST,sector_code)) index_change_1m_rank,
        count() OVER (PARTITION BY collection_id,sector_type) sector_count
    FROM evidence
),
signals AS
(
    SELECT *,
        toUInt8((index_change_ratio_pct>0 AND index_change_rank<=greatest(3,ceil(sector_count*0.15))) OR (index_change_1m_pct>0 AND index_change_1m_rank<=greatest(3,ceil(sector_count*0.10)))) hit_price_strength,
        toUInt8(up_ratio>=0.60 OR up_ratio_delta_15m>=0.10) hit_breadth,
        toUInt8(turnover_share_rank<=greatest(3,ceil(sector_count*0.10))) hit_capital_share,
        toUInt8((turnover_market_share_delta_15m>0 AND turnover_share_rank_delta>=0) OR (turnover_1m_market_share_delta_15m>0 AND turnover_1m_share_rank_delta>0)) hit_capital_acceleration,
        toUInt8(limit_up_count>=1 AND (limit_break_count IS NULL OR limit_up_count>=limit_break_count)) hit_limit_strength,
        toUInt8(new_high_ratio>=0.10 OR new_high_ratio_delta_15m>=0.05) hit_new_high
    FROM ranked
),
preliminary AS
(
    SELECT *,
        hit_price_strength+hit_breadth+hit_capital_share+hit_capital_acceleration+hit_limit_strength+hit_new_high base_hit_count,
        hit_price_strength*20+hit_breadth*15+hit_capital_share*20+hit_capital_acceleration*15+hit_limit_strength*10+hit_new_high*10 base_score,
        toUInt8(base_score>=30 AND base_hit_count>=2 AND (hit_price_strength+hit_capital_share+hit_limit_strength+hit_new_high)>=1) pre_candidate
    FROM signals
),
persistent AS
(
    SELECT *,
        toUInt8(sum(pre_candidate) OVER (PARTITION BY trade_date,business_period,sector_type,sector_code ORDER BY scheduled_time ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)) candidate_hit_count_3,
        toUInt8(sum(pre_candidate) OVER (PARTITION BY trade_date,business_period,sector_type,sector_code ORDER BY scheduled_time ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)) candidate_hit_count_5,
        toUInt8(countIf(turnover_market_share_delta_15m>0) OVER (PARTITION BY trade_date,business_period,sector_type,sector_code ORDER BY scheduled_time ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)) turnover_share_positive_count_3,
        toUInt8(countIf(up_ratio_delta_15m>0) OVER (PARTITION BY trade_date,business_period,sector_type,sector_code ORDER BY scheduled_time ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)) up_ratio_positive_count_3
    FROM preliminary
),
relative_strength AS
(
    SELECT *,
        if(index_change_ratio_pct IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY index_change_ratio_pct ASC NULLS FIRST)) price_total_strength,
        if(index_change_1m_pct IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY index_change_1m_pct ASC NULLS FIRST)) price_1m_strength,
        if(up_ratio IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY up_ratio ASC NULLS FIRST)) breadth_current_strength,
        if(up_ratio_delta_15m IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY up_ratio_delta_15m ASC NULLS FIRST)) breadth_delta_strength,
        if(turnover_market_share_pct IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY turnover_market_share_pct ASC NULLS FIRST)) capital_total_strength,
        if(turnover_1m_market_share_pct IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY turnover_1m_market_share_pct ASC NULLS FIRST)) capital_1m_strength,
        if(turnover_market_share_delta_15m IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY turnover_market_share_delta_15m ASC NULLS FIRST)) capital_delta_strength,
        if(turnover_1m_market_share_delta_15m IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY turnover_1m_market_share_delta_15m ASC NULLS FIRST)) capital_1m_delta_strength,
        if(limit_up_count IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY (ifNull(toInt64(limit_up_count),0)-ifNull(toInt64(limit_break_count),0)) ASC NULLS FIRST)) limit_structure_strength,
        if(new_high_ratio IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY new_high_ratio ASC NULLS FIRST)) new_high_current_strength,
        if(new_high_ratio_delta_15m IS NULL,0.,percent_rank() OVER (PARTITION BY collection_id,sector_type ORDER BY new_high_ratio_delta_15m ASC NULLS FIRST)) new_high_delta_strength
    FROM persistent
),
scored AS
(
    SELECT *, toUInt8(candidate_hit_count_3>=2) hit_persistence,
        base_hit_count+hit_persistence candidate_hit_count,
        base_score+hit_persistence*10 qualification_score,
        round(
            hit_price_strength*8 + greatest(if(index_change_ratio_pct>0,price_total_strength,0.),if(index_change_1m_pct>0,price_1m_strength,0.))*12
            + hit_breadth*6 + greatest(breadth_current_strength,if(up_ratio_delta_15m>0,breadth_delta_strength,0.))*9
            + hit_capital_share*8 + greatest(capital_total_strength,capital_1m_strength)*12
            + hit_capital_acceleration*6 + greatest(if(turnover_market_share_delta_15m>0,capital_delta_strength,0.),if(turnover_1m_market_share_delta_15m>0,capital_1m_delta_strength,0.))*9
            + hit_limit_strength*4 + if(limit_up_count>0,limit_structure_strength,0.)*6
            + hit_new_high*4 + greatest(if(new_high_ratio>0,new_high_current_strength,0.),if(new_high_ratio_delta_15m>0,new_high_delta_strength,0.))*6
            + hit_persistence*4 + greatest(toFloat64(candidate_hit_count_3)/3,toFloat64(candidate_hit_count_5)/5)*6,
            4
        ) candidate_score_v1,
        toUInt16(hit_price_strength+hit_breadth*2+hit_capital_share*4+hit_capital_acceleration*8+hit_limit_strength*16+hit_new_high*32+hit_persistence*64) candidate_reason_mask,
        toUInt8(qualification_score>=35 AND candidate_hit_count>=2 AND (hit_price_strength+hit_capital_share+hit_limit_strength+hit_new_high)>=1) is_eligible
    FROM relative_strength
),
all_ranks AS
(
    SELECT *,
        toUInt16(sum(is_eligible) OVER (PARTITION BY collection_id,sector_type ORDER BY candidate_score_v1 DESC,turnover_market_share_pct DESC NULLS LAST,sector_code ROWS UNBOUNDED PRECEDING)) candidate_rank_raw
    FROM scored
),
selected AS
(
    SELECT *, toUInt16(if(
            sector_type='concept',
            {concept_candidate_limit:UInt16},
            {industry_candidate_limit:UInt16}
        )) candidate_cap,
        if(is_eligible=1 AND candidate_rank_raw<=candidate_cap,candidate_rank_raw,CAST(NULL,'Nullable(UInt16)')) candidate_rank
    FROM all_ranks
),
with_best AS
(
    SELECT *,
        min(candidate_rank) OVER (PARTITION BY trade_date,business_period,sector_type,sector_code ORDER BY scheduled_time ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) best_candidate_rank_3,
        min(candidate_rank) OVER (PARTITION BY trade_date,business_period,sector_type,sector_code ORDER BY scheduled_time ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) best_candidate_rank_5
    FROM selected
)
SELECT
    trade_date,collection_id,scheduled_time,sector_type,sector_code,sector_name,'V1',
    state_data_status,state_source_collection_id,state_source_scheduled_time,state_source_age_seconds,state_is_fallback,delta_type,
    candidate_rank,candidate_hit_count,candidate_reason_mask,candidate_score_v1,
    hit_price_strength,hit_breadth,hit_capital_share,hit_capital_acceleration,hit_limit_strength,hit_new_high,hit_persistence,
    index_change_ratio_pct,index_change_1m_pct,index_change_rank,index_change_1m_rank,
    up_ratio,down_ratio,up_ratio_delta_15m,down_ratio_delta_15m,
    turnover_market_share_pct,turnover_1m_market_share_pct,turnover_market_share_delta_15m,turnover_1m_market_share_delta_15m,
    turnover_increment_15m,turnover_increment_market_share_pct,turnover_share_rank,turnover_1m_share_rank,turnover_share_rank_delta,turnover_1m_share_rank_delta,
    limit_up_count,limit_break_count,limit_up_count_delta_15m,limit_break_count_delta_15m,new_high_ratio,new_low_ratio,new_high_ratio_delta_15m,new_low_ratio_delta_15m,
    turnover_1m_top1_share_pct,turnover_1m_top3_share_pct,turnover_1m_top5_share_pct,top1_share_delta_15m,top3_share_delta_15m,top5_share_delta_15m,
    candidate_hit_count_3,candidate_hit_count_5,turnover_share_positive_count_3,up_ratio_positive_count_3,
    toUInt16(best_candidate_rank_3),toUInt16(best_candidate_rank_5)
FROM with_best WHERE candidate_rank IS NOT NULL;
