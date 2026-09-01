SET materialize_ttl_after_modify = 0;

ALTER TABLE system.query_log MODIFY TTL event_date + INTERVAL 30 DAY DELETE;
ALTER TABLE system.part_log MODIFY TTL event_date + INTERVAL 30 DAY DELETE;
ALTER TABLE system.background_schedule_pool_log MODIFY TTL event_date + INTERVAL 30 DAY DELETE;
ALTER TABLE system.text_log MODIFY TTL event_date + INTERVAL 30 DAY DELETE;
ALTER TABLE system.trace_log MODIFY TTL event_date + INTERVAL 7 DAY DELETE;
ALTER TABLE system.trace_log_0 MODIFY TTL event_date + INTERVAL 7 DAY DELETE;
ALTER TABLE system.processors_profile_log MODIFY TTL event_date + INTERVAL 7 DAY DELETE;
ALTER TABLE system.metric_log MODIFY TTL event_date + INTERVAL 7 DAY DELETE;
ALTER TABLE system.asynchronous_metric_log MODIFY TTL event_date + INTERVAL 7 DAY DELETE;
ALTER TABLE system.query_metric_log MODIFY TTL event_date + INTERVAL 7 DAY DELETE;
ALTER TABLE system.error_log MODIFY TTL event_date + INTERVAL 90 DAY DELETE;
