-- Descriptive trailing distributions only. Each current hour is excluded from its own sample.
-- Different sources/rules/geofences/cleaning versions never share a baseline.
with comparable as (
    select h.*, p.clean_version,p.geofence_version,p.rule_id,
        p.parameters - 'weather_batch_id' - 'context_dataset_ids' as metric_parameters
    from {{ ref('port_activity_hourly') }} h
    join {{ ref('stg_partitions') }} p using(partition_id)
), distributions as (
    select c.partition_id,c.hour_utc,count(t.observation_count) as baseline_hours,
        count(distinct (t.hour_utc at time zone 'UTC')::date) as baseline_days,
        min(t.hour_utc) as baseline_start_utc,max(t.hour_end_utc) as baseline_end_utc,
        percentile_cont(0.05) within group(order by t.observation_count) as observation_p05,
        percentile_cont(0.50) within group(order by t.observation_count) as observation_p50,
        percentile_cont(0.95) within group(order by t.observation_count) as observation_p95,
        array_agg(distinct t.dataset_id) filter(where t.dataset_id is not null) as baseline_datasets
    from comparable c left join comparable t
        on t.port_id=c.port_id and t.source_id=c.source_id
        and t.metric_parameters=c.metric_parameters
        and t.hour_end_utc<=c.hour_utc and t.hour_utc>=c.hour_utc-interval '30 days'
        and t.source_interval_available and t.observation_count is not null
        and not ('cleaning_pending_or_failed'=any(t.quality_flags))
    group by c.partition_id,c.hour_utc
), eligible as (
    select c.*,d.baseline_hours,d.baseline_days,d.baseline_start_utc,d.baseline_end_utc,
        d.observation_p05,d.observation_p50,d.observation_p95,d.baseline_datasets,
        d.baseline_hours>={{ var('baseline_min_hours') }}
            and d.baseline_days>={{ var('baseline_min_days') }}
            and c.source_interval_available and c.observation_count is not null
            and not ('cleaning_pending_or_failed'=any(c.quality_flags)) as baseline_eligible
    from comparable c join distributions d using(partition_id,hour_utc)
)
select *,
    {{ var('baseline_min_hours') }}::integer as minimum_history_hours,
    {{ var('baseline_min_days') }}::integer as minimum_history_days,
    case when baseline_eligible and observation_count>observation_p95
            then 'above_preliminary_p95'
         when baseline_eligible and observation_count<observation_p05
            then 'below_preliminary_p05' end as anomaly_label,
    'preliminary_trailing_observed_reports_not_seasonal_or_total_ais_coverage'::text
        as baseline_limitation
from eligible
