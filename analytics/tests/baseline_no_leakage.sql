select partition_id,hour_utc from {{ ref('activity_baselines') }}
where baseline_end_utc>hour_utc
    or (anomaly_label is not null and not baseline_eligible)
    or (baseline_eligible and (baseline_hours<minimum_history_hours
        or baseline_days<minimum_history_days))
    or (baseline_hours=0 and (observation_p50 is not null or baseline_end_utc is not null))
