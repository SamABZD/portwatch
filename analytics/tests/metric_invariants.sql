select partition_id,hour_utc from {{ ref('port_activity_hourly') }}
where distinct_vessels_observed>observation_count
    or snapshot_port_vessel_count>snapshot_vessel_count
    or port_associated_waiting_count+regional_waiting_count<>waiting_candidate_count
    or (completed_dwell_sample_count>0)<>(completed_dwell_median_seconds is not null)
    or (waiting_duration_sample_count>0)<>(waiting_duration_median_seconds is not null)
    or snapshot_vessel_count<>(select sum(value::bigint)
        from jsonb_each_text(snapshot_zone_counts))
    or (snapshot_vessel_count is null and exists(select 1
        from jsonb_each_text(snapshot_zone_counts) where value is not null))
