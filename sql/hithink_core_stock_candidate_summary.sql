INSERT INTO tmp_core_stock_summary
WITH links AS
(
 SELECT c.collection_id AS collection_id,c.sector_type AS sector_type,c.sector_code AS sector_code,c.sector_name AS sector_name,c.candidate_rank AS sector_candidate_rank,
  h.thscode AS thscode,d.turnover AS turnover,d.turnover_delta_1m AS turnover_delta_1m,d.price_change_ratio_pct AS price_change_ratio_pct,
  row_number() OVER (PARTITION BY c.collection_id,c.sector_type,c.sector_code ORDER BY d.turnover DESC NULLS LAST,h.thscode) sector_turnover_rank,
  row_number() OVER (PARTITION BY c.collection_id,c.sector_type,c.sector_code ORDER BY d.turnover_delta_1m DESC NULLS LAST,h.thscode) sector_turnover_1m_rank,
  row_number() OVER (PARTITION BY c.collection_id,c.sector_type,c.sector_code ORDER BY d.price_change_ratio_pct DESC NULLS LAST,h.thscode) sector_price_rank,
  d.turnover*100/sum(d.turnover) OVER (PARTITION BY c.collection_id,c.sector_type,c.sector_code) sector_turnover_share_pct
 FROM
 (
  SELECT * FROM market.hithink_core_sector_candidate FINAL
  WHERE trade_date={trade_date:Date}
    AND sector_type IN ('concept','industry')
 ) c
 INNER JOIN (SELECT * FROM market.sector_membership_history FINAL) h
  ON h.sector_type=c.sector_type AND h.sector_code=c.sector_code AND h.observed_from<=c.trade_date AND (h.observed_to IS NULL OR h.observed_to>=c.trade_date)
 INNER JOIN market.hithink_snapshot_derived d ON d.trade_date=c.trade_date AND d.collection_id=c.state_source_collection_id AND d.thscode=h.thscode
)
SELECT collection_id,thscode,toUInt16(count()),toUInt16(countIf(sector_type='concept')),toUInt16(countIf(sector_type='industry')),toUInt16(0),
 argMin(sector_type,tuple(sector_candidate_rank,sector_type,sector_code)),argMin(sector_code,tuple(sector_candidate_rank,sector_type,sector_code)),argMin(sector_name,tuple(sector_candidate_rank,sector_type,sector_code)),toUInt16(min(sector_candidate_rank)),
 toUInt16(argMin(sector_turnover_rank,tuple(sector_candidate_rank,sector_type,sector_code))),toUInt16(argMin(sector_turnover_1m_rank,tuple(sector_candidate_rank,sector_type,sector_code))),toUInt16(argMin(sector_price_rank,tuple(sector_candidate_rank,sector_type,sector_code))),
 argMin(sector_turnover_share_pct,tuple(sector_candidate_rank,sector_type,sector_code))
FROM links GROUP BY collection_id,thscode;
