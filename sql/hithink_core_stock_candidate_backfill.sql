INSERT INTO market.hithink_core_stock_candidate
(
trade_date,collection_id,scheduled_time,thscode,ticker,stock_name,candidate_model_version,
state_data_status,state_source_collection_id,state_source_scheduled_time,state_source_age_seconds,state_is_fallback,delta_type,
candidate_rank,candidate_hit_count,candidate_reason_mask,candidate_score_v1,
hit_price_strength,hit_turnover_absolute,hit_turnover_acceleration,hit_new_high,hit_limit_strength,hit_core_sector,hit_sector_leader,hit_persistence,
last_price,price_change_ratio_pct,price_change_1m_pct,price_delta_1m,new_high_flag,new_low_flag,price_change_rank_market,price_change_1m_rank_market,
turnover,turnover_delta_1m,turnover_growth_1m,turnover_prev_trade_day_pct,turnover_rank_market,turnover_delta_1m_rank_market,turnover_top_pct_market,turnover_delta_1m_top_pct_market,
is_limit_up,is_limit_down,is_limit_break,limit_break_open_times,continue_day_cnt,seal_money,max_seal_money,limit_up_time,
core_sector_count,core_concept_count,core_industry_count,core_style_count,best_core_sector_type,best_core_sector_code,best_core_sector_name,best_core_sector_candidate_rank,
best_sector_turnover_rank,best_sector_turnover_1m_rank,best_sector_price_rank,best_sector_turnover_share_pct,
candidate_hit_count_3,candidate_hit_count_5,turnover_top_hit_count_3,new_high_hit_count_3,core_sector_hit_count_3
)
WITH
source_nodes AS
(
 SELECT trade_date,collection_id,scheduled_time,
  if(scheduled_time<toDateTime64(concat(toString(trade_date),' 12:00:00'),3,'Asia/Shanghai'),'AM','PM') business_period
 FROM market.hithink_snapshot_derived
 WHERE trade_date={trade_date:Date}
 GROUP BY trade_date,collection_id,scheduled_time
 ORDER BY trade_date,business_period,scheduled_time
),
targets AS
(
 SELECT trade_date,collection_id,node_seq target_node_seq,scheduled_time,
  if(node_seq<=132,'AM','PM') business_period
 FROM market.hithink_snapshot_schedule FINAL WHERE trade_date={trade_date:Date}
 AND node_seq IN (11,12,27,42,57,72,87,102,117,132,133,148,163,178,193,208,223,238,254)
 ORDER BY trade_date,business_period,scheduled_time
),
selected_sources AS
(
 SELECT t.*,s.collection_id selected_source_collection_id,s.scheduled_time selected_source_scheduled_time
 FROM targets t ASOF LEFT JOIN source_nodes s ON t.trade_date=s.trade_date AND t.business_period=s.business_period AND t.scheduled_time>=s.scheduled_time
),
resolved_targets AS
(
 SELECT *,
  if(target_node_seq=254 AND selected_source_collection_id!=collection_id,CAST(NULL,'Nullable(FixedString(11))'),selected_source_collection_id) state_source_collection_id,
  if(target_node_seq=254 AND selected_source_collection_id!=collection_id,CAST(NULL AS Nullable(DateTime64(3,'Asia/Shanghai'))),selected_source_scheduled_time) state_source_scheduled_time
 FROM selected_sources
),
snapshots AS
(
 SELECT t.trade_date,t.collection_id,t.target_node_seq,t.scheduled_time,t.business_period,t.state_source_collection_id,t.state_source_scheduled_time,
  d.thscode,d.ticker,d.last_price,d.price_change_ratio_pct,d.price_change_1m_pct,d.price_delta_1m,d.new_high_flag,d.new_low_flag,
  d.turnover,d.turnover_delta_1m,d.turnover_growth_1m,d.turnover_prev_trade_day_pct,d.is_limit_up,d.is_limit_down,d.is_limit_break,d.limit_break_open_times
 FROM resolved_targets t INNER JOIN market.hithink_snapshot_derived d ON t.trade_date=d.trade_date AND t.state_source_collection_id=d.collection_id
),
ranked AS
(
 SELECT *,count() OVER (PARTITION BY collection_id) market_count,
  if(price_change_ratio_pct IS NULL,NULL,toUInt16(row_number() OVER (PARTITION BY collection_id ORDER BY price_change_ratio_pct DESC NULLS LAST,thscode))) price_change_rank_market,
  if(price_change_1m_pct IS NULL,NULL,toUInt16(row_number() OVER (PARTITION BY collection_id ORDER BY price_change_1m_pct DESC NULLS LAST,thscode))) price_change_1m_rank_market,
  if(turnover IS NULL,NULL,toUInt16(row_number() OVER (PARTITION BY collection_id ORDER BY turnover DESC NULLS LAST,thscode))) turnover_rank_market,
  if(turnover_delta_1m IS NULL,NULL,toUInt16(row_number() OVER (PARTITION BY collection_id ORDER BY turnover_delta_1m DESC NULLS LAST,thscode))) turnover_delta_1m_rank_market
 FROM snapshots
),
core_summary AS
(
 SELECT * FROM tmp_core_stock_summary
),
names AS
(
 SELECT thscode AS name_thscode,argMax(stock_name,last_confirmed_at) stock_name FROM market.sector_membership_history FINAL
 WHERE observed_from<={trade_date:Date} AND (observed_to IS NULL OR observed_to>={trade_date:Date}) GROUP BY thscode
),
up_pool AS
(
 SELECT collection_id AS pool_collection_id,thscode AS pool_thscode,continue_day_cnt,seal_money,max_seal_money,limit_up_time FROM market.hithink_limit_up_pool FINAL WHERE trade_date={trade_date:Date}
),
evidence AS
(
 SELECT r.*,
  r.trade_date AS trade_date,r.collection_id AS collection_id,r.target_node_seq AS target_node_seq,r.scheduled_time AS scheduled_time,r.business_period AS business_period,
  r.state_source_collection_id AS state_source_collection_id,r.state_source_scheduled_time AS state_source_scheduled_time,
  r.thscode AS thscode,r.ticker AS ticker,r.last_price AS last_price,r.price_change_ratio_pct AS price_change_ratio_pct,r.price_change_1m_pct AS price_change_1m_pct,r.price_delta_1m AS price_delta_1m,
  r.new_high_flag AS new_high_flag,r.new_low_flag AS new_low_flag,r.turnover AS turnover,r.turnover_delta_1m AS turnover_delta_1m,r.turnover_growth_1m AS turnover_growth_1m,r.turnover_prev_trade_day_pct AS turnover_prev_trade_day_pct,
  r.is_limit_up AS is_limit_up,r.is_limit_down AS is_limit_down,r.is_limit_break AS is_limit_break,r.limit_break_open_times AS limit_break_open_times,
  r.market_count AS market_count,r.price_change_rank_market AS price_change_rank_market,r.price_change_1m_rank_market AS price_change_1m_rank_market,r.turnover_rank_market AS turnover_rank_market,r.turnover_delta_1m_rank_market AS turnover_delta_1m_rank_market,
  n.stock_name AS stock_name,u.continue_day_cnt AS continue_day_cnt,u.seal_money AS seal_money,u.max_seal_money AS max_seal_money,u.limit_up_time AS limit_up_time,
  toUInt16(ifNull(cs.core_sector_count,0)) core_sector_count,toUInt16(ifNull(cs.core_concept_count,0)) core_concept_count,toUInt16(ifNull(cs.core_industry_count,0)) core_industry_count,toUInt16(ifNull(cs.core_style_count,0)) core_style_count,
  cs.best_core_sector_type AS best_core_sector_type,
  cs.best_core_sector_code AS best_core_sector_code,
  cs.best_core_sector_name AS best_core_sector_name,
  cs.best_core_sector_candidate_rank AS best_core_sector_candidate_rank,
  toUInt16(cs.best_sector_turnover_rank) best_sector_turnover_rank,toUInt16(cs.best_sector_turnover_1m_rank) best_sector_turnover_1m_rank,toUInt16(cs.best_sector_price_rank) best_sector_price_rank,
  cs.best_sector_turnover_share_pct AS best_sector_turnover_share_pct
 FROM ranked r LEFT JOIN names n ON r.thscode=n.name_thscode LEFT JOIN up_pool u ON r.state_source_collection_id=u.pool_collection_id AND r.thscode=u.pool_thscode
 LEFT JOIN core_summary cs ON r.collection_id=cs.collection_id AND r.thscode=cs.thscode
),
signals AS
(
 SELECT *,
  toUInt8((price_change_ratio_pct>=3 AND price_change_rank_market<=ceil(market_count*0.10)) OR (price_change_1m_pct>=1 AND price_change_1m_rank_market<=ceil(market_count*0.05))) hit_price_strength,
  toUInt8(turnover_rank_market<=ceil(market_count*0.03)) hit_turnover_absolute,
  toUInt8((turnover_delta_1m>0 AND turnover_delta_1m_rank_market<=ceil(market_count*0.03)) OR turnover_growth_1m>=0.50) hit_turnover_acceleration,
  toUInt8(new_high_flag=1) hit_new_high,toUInt8(is_limit_up=1 OR is_limit_break=1) hit_limit_strength,
  toUInt8(core_sector_count>=1) hit_core_sector,toUInt8(best_sector_turnover_rank<=5 OR best_sector_turnover_1m_rank<=5 OR best_sector_price_rank<=5) hit_sector_leader
 FROM evidence
),
preliminary AS
(
 SELECT *,hit_price_strength+hit_turnover_absolute+hit_turnover_acceleration+hit_new_high+hit_limit_strength+hit_core_sector+hit_sector_leader base_hit_count,
  hit_price_strength*20+hit_turnover_absolute*20+hit_turnover_acceleration*15+hit_new_high*10+hit_limit_strength*15+hit_core_sector*10+hit_sector_leader*5 base_score,
  toUInt8(base_score>=30 AND base_hit_count>=2 AND (hit_price_strength+hit_turnover_absolute+hit_turnover_acceleration+hit_new_high+hit_limit_strength)>=1) pre_candidate
 FROM signals
),
persistent AS
(
 SELECT *,toUInt8(sum(pre_candidate) OVER (PARTITION BY trade_date,business_period,thscode ORDER BY scheduled_time ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)) candidate_hit_count_3,
  toUInt8(sum(pre_candidate) OVER (PARTITION BY trade_date,business_period,thscode ORDER BY scheduled_time ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)) candidate_hit_count_5,
  toUInt8(countIf(hit_turnover_absolute=1) OVER (PARTITION BY trade_date,business_period,thscode ORDER BY scheduled_time ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)) turnover_top_hit_count_3,
  toUInt8(countIf(hit_new_high=1) OVER (PARTITION BY trade_date,business_period,thscode ORDER BY scheduled_time ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)) new_high_hit_count_3,
  toUInt8(countIf(hit_core_sector=1) OVER (PARTITION BY trade_date,business_period,thscode ORDER BY scheduled_time ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)) core_sector_hit_count_3
 FROM preliminary
),
scored AS
(
 SELECT *,toUInt8(candidate_hit_count_3>=2) hit_persistence,base_hit_count+hit_persistence candidate_hit_count,base_score+hit_persistence*5 candidate_score_v1,
  toUInt16(hit_price_strength+hit_turnover_absolute*2+hit_turnover_acceleration*4+hit_new_high*8+hit_limit_strength*16+hit_core_sector*32+hit_sector_leader*64+hit_persistence*128) candidate_reason_mask,
  toUInt8(candidate_score_v1>=35 AND candidate_hit_count>=2 AND (hit_price_strength+hit_turnover_absolute+hit_turnover_acceleration+hit_new_high+hit_limit_strength)>=1) is_eligible
 FROM persistent
),
final_ranked AS
(
 SELECT *,toUInt16(sum(is_eligible) OVER (PARTITION BY collection_id ORDER BY candidate_score_v1 DESC,turnover DESC NULLS LAST,thscode ROWS UNBOUNDED PRECEDING)) candidate_rank
 FROM scored
)
SELECT trade_date,collection_id,scheduled_time,thscode,ticker,stock_name,'V1',
 multiIf(state_source_collection_id=collection_id,'CURRENT','FALLBACK'),state_source_collection_id,state_source_scheduled_time,toUInt32(dateDiff('second',state_source_scheduled_time,scheduled_time)),toUInt8(state_source_collection_id!=collection_id),
 multiIf(target_node_seq IN (11,133),'SESSION_BASE',target_node_seq=12,'AUCTION_TO_OPEN','NORMAL_15M'),
 candidate_rank,candidate_hit_count,candidate_reason_mask,candidate_score_v1,
 hit_price_strength,hit_turnover_absolute,hit_turnover_acceleration,hit_new_high,hit_limit_strength,hit_core_sector,hit_sector_leader,hit_persistence,
 last_price,price_change_ratio_pct,price_change_1m_pct,price_delta_1m,new_high_flag,new_low_flag,price_change_rank_market,price_change_1m_rank_market,
 turnover,turnover_delta_1m,turnover_growth_1m,turnover_prev_trade_day_pct,turnover_rank_market,turnover_delta_1m_rank_market,toFloat64(turnover_rank_market)/market_count,toFloat64(turnover_delta_1m_rank_market)/market_count,
 is_limit_up,is_limit_down,is_limit_break,limit_break_open_times,continue_day_cnt,seal_money,max_seal_money,limit_up_time,
 core_sector_count,core_concept_count,core_industry_count,core_style_count,best_core_sector_type,best_core_sector_code,best_core_sector_name,best_core_sector_candidate_rank,
 best_sector_turnover_rank,best_sector_turnover_1m_rank,best_sector_price_rank,best_sector_turnover_share_pct,
 candidate_hit_count_3,candidate_hit_count_5,turnover_top_hit_count_3,new_high_hit_count_3,core_sector_hit_count_3
FROM final_ranked WHERE is_eligible=1 AND candidate_rank<=50;
