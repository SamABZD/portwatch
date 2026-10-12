-- All operands are source-scoped temp tables; aggregate separately before joining.
WITH options AS (
    SELECT %(start)s::timestamptz start_utc, %(end)s::timestamptz end_utc,
        %(cutoff)s::timestamptz cutoff, %(stale)s::integer stale_seconds,
        %(rejected)s::bigint rejected_total, %(collector)s::text collector_state,
        %(processing_complete)s::boolean processing_complete,
        %(weather)s::text weather_batch
), hours AS (
    SELECT t AS hour_utc, t + interval '1 hour' AS hour_end_utc,
        least(t + interval '1 hour', o.cutoff + interval '1 microsecond') AS exclusive_end,
        least(t + interval '1 hour', o.cutoff) AS metric_as_of_utc,
        t + interval '1 hour' <= o.cutoff AS complete_hour,
        coalesce(c.available @> tstzrange(t, t + interval '1 hour','[)'),false)
            AS source_interval_available,
        coalesce(c.available @> tstzrange(t - o.stale_seconds * interval '1 second',
            t + interval '1 microsecond','[)'),false) AS snapshot_interval_available
    FROM options o CROSS JOIN pw_mart_coverage c CROSS JOIN LATERAL
        generate_series(o.start_utc,o.end_utc - interval '1 hour',interval '1 hour') t
    WHERE t <= o.cutoff
), observations AS (
    SELECT h.hour_utc,count(p.id) AS observations,count(DISTINCT p.mmsi) AS vessels,
        count(p.id) FILTER (WHERE p.timestamp_quality='receipt_fallback') AS fallbacks,
        count(p.id) FILTER (WHERE cardinality(p.quality_flags)>0) AS flagged,
        max(p.effective_at) AS latest
    FROM hours h LEFT JOIN pw_mart_points p
        ON p.effective_at >= h.hour_utc AND p.effective_at < h.exclusive_end
    GROUP BY h.hour_utc
), latest_times AS (
    SELECT h.hour_utc,p.mmsi,max(p.effective_at) AS latest
    FROM hours h JOIN pw_mart_points p ON p.effective_at <= h.hour_utc
    GROUP BY h.hour_utc,p.mmsi
), latest_reports AS (
    -- Fold all reports at the latest timestamp: each vessel appears at most once.
    SELECT l.hour_utc,l.mmsi,l.latest,
        CASE WHEN count(DISTINCT p.primary_zone)>1 THEN 'unknown'
             ELSE min(p.primary_zone) END AS primary_zone,
        CASE WHEN count(DISTINCT p.primary_zone)>1 THEN NULL
             ELSE bool_or(p.zone_type IN ('harbor','terminal')) END AS port_inside,
        count(DISTINCT p.primary_zone)>1 AS conflicting
    FROM latest_times l JOIN pw_mart_points p
        ON p.mmsi=l.mmsi AND p.effective_at=l.latest
    GROUP BY l.hour_utc,l.mmsi,l.latest
), snapshots AS (
    SELECT h.hour_utc,
        count(l.mmsi) FILTER (WHERE l.latest >= h.hour_utc-o.stale_seconds*interval '1 second') AS fresh,
        count(l.mmsi) FILTER (WHERE l.latest >= h.hour_utc-o.stale_seconds*interval '1 second'
            AND l.port_inside IS TRUE) AS inside,
        count(l.mmsi) FILTER (WHERE l.latest < h.hour_utc-o.stale_seconds*interval '1 second') AS stale,
        count(l.mmsi) FILTER (WHERE l.latest >= h.hour_utc-o.stale_seconds*interval '1 second'
            AND l.conflicting) AS conflicting
    FROM hours h CROSS JOIN options o LEFT JOIN latest_reports l USING(hour_utc)
    GROUP BY h.hour_utc
), zones AS (
    SELECT h.hour_utc,jsonb_object_agg(z.zone_id,
        CASE WHEN s.fresh>0 OR h.snapshot_interval_available THEN
            (SELECT count(*) FROM latest_reports l WHERE l.hour_utc=h.hour_utc
                AND l.latest>=h.hour_utc-o.stale_seconds*interval '1 second'
                AND l.primary_zone=z.zone_id) ELSE NULL END ORDER BY z.zone_id) AS counts
    FROM hours h CROSS JOIN options o CROSS JOIN pw_mart_zone_ids z
    JOIN snapshots s USING(hour_utc)
    GROUP BY h.hour_utc
), visit_events AS (
    SELECT h.hour_utc,
        count(v.visit_id) FILTER (WHERE v.arrival_lower IS NOT NULL
            AND v.arrival_confirmed_at>=h.hour_utc AND v.arrival_confirmed_at<h.exclusive_end) AS arrivals,
        count(v.visit_id) FILTER (WHERE v.departure_confirmed_at>=h.hour_utc
            AND v.departure_confirmed_at<h.exclusive_end) AS departures,
        count(v.visit_id) FILTER (WHERE NOT v.completed AND v.observed_start<h.exclusive_end
            AND v.observed_end>=h.hour_utc AND v.arrival_confirmed_at<h.exclusive_end) AS incomplete,
        count(v.visit_id) FILTER (WHERE v.completed AND v.departure_confirmed_at>=h.hour_utc
            AND v.departure_confirmed_at<h.exclusive_end) AS dwell_samples,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY (v.duration_min_seconds+v.duration_max_seconds)/2)
            FILTER (WHERE v.completed AND v.departure_confirmed_at>=h.hour_utc
                AND v.departure_confirmed_at<h.exclusive_end) AS dwell_median,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY v.duration_min_seconds)
            FILTER (WHERE v.completed AND v.departure_confirmed_at>=h.hour_utc
                AND v.departure_confirmed_at<h.exclusive_end) AS dwell_lower,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY v.duration_max_seconds)
            FILTER (WHERE v.completed AND v.departure_confirmed_at>=h.hour_utc
                AND v.departure_confirmed_at<h.exclusive_end) AS dwell_upper
    FROM hours h LEFT JOIN pw_mart_visits v ON true GROUP BY h.hour_utc
), active_waits AS (
    SELECT h.hour_utc,w.partition_id,count(*) AS episodes,
        bool_or(w.associated_visit_id IS NOT NULL AND w.association_known_at<h.exclusive_end) AS port_associated
    FROM hours h JOIN pw_mart_waits w ON w.observed_start<h.exclusive_end
        AND w.observed_end>=h.hour_utc AND w.qualification_known_at<h.exclusive_end
    GROUP BY h.hour_utc,w.partition_id
), waiting AS (
    SELECT h.hour_utc,count(w.partition_id) AS candidates,coalesce(sum(w.episodes),0)::bigint AS episodes,
        count(w.partition_id) FILTER (WHERE w.port_associated) AS port_associated,
        count(w.partition_id) FILTER (WHERE NOT w.port_associated) AS regional
    FROM hours h LEFT JOIN active_waits w USING(hour_utc) GROUP BY h.hour_utc
), waiting_duration AS (
    SELECT h.hour_utc,count(w.episode_id) AS samples,
        count(w.episode_id) FILTER (WHERE NOT w.completed) AS partial_samples,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY w.candidate_duration_seconds) AS median_seconds
    FROM hours h LEFT JOIN pw_mart_waits w ON w.observed_end>=h.hour_utc
        AND w.observed_end<h.exclusive_end AND w.qualification_known_at<h.exclusive_end
    GROUP BY h.hour_utc
), combined AS (
    SELECT h.*,o.observations,o.vessels,o.fallbacks,o.flagged,o.latest,
        (h.source_interval_available OR o.observations>0) AS activity_known,
        (h.snapshot_interval_available OR s.fresh>0) AS snapshot_known,
        s.fresh,s.inside,s.stale,s.conflicting,z.counts,
        v.arrivals,v.departures,v.incomplete,v.dwell_samples,v.dwell_median,v.dwell_lower,v.dwell_upper,
        w.candidates,w.episodes,w.port_associated,w.regional,d.samples,d.partial_samples,d.median_seconds,
        x.rejected_total,x.collector_state,
        wx.valid_time_utc AS weather_valid,wx.wind_speed_10m,wx.wind_gusts_10m,wx.precipitation,
        wx.quality_flags AS weather_flags
    FROM hours h JOIN observations o USING(hour_utc) JOIN snapshots s USING(hour_utc)
    JOIN zones z USING(hour_utc) JOIN visit_events v USING(hour_utc)
    JOIN waiting w USING(hour_utc) JOIN waiting_duration d USING(hour_utc) CROSS JOIN options x
    LEFT JOIN weather_values wx ON wx.batch_id=x.weather_batch AND wx.valid_time_utc=h.hour_utc
)
SELECT hour_utc,hour_end_utc,complete_hour,metric_as_of_utc,
    source_interval_available,snapshot_interval_available,
    CASE WHEN activity_known THEN observations END AS observation_count,
    CASE WHEN activity_known THEN vessels END AS distinct_vessels_observed,
    CASE WHEN snapshot_known THEN fresh END AS snapshot_vessel_count,
    CASE WHEN snapshot_known THEN inside END AS snapshot_port_vessel_count,
    CASE WHEN snapshot_known THEN stale END AS snapshot_stale_vessel_count,
    CASE WHEN snapshot_known THEN conflicting END AS snapshot_conflicting_vessel_count,
    counts AS snapshot_zone_counts,
    CASE WHEN activity_known THEN arrivals END AS confirmed_arrival_count,
    CASE WHEN activity_known THEN departures END AS confirmed_departure_count,
    CASE WHEN activity_known THEN candidates END AS waiting_candidate_count,
    CASE WHEN activity_known THEN episodes END AS waiting_episode_count,
    CASE WHEN activity_known THEN port_associated END AS port_associated_waiting_count,
    CASE WHEN activity_known THEN regional END AS regional_waiting_count,
    CASE WHEN activity_known THEN samples END AS waiting_duration_sample_count,
    CASE WHEN activity_known THEN partial_samples END AS waiting_partial_sample_count,
    median_seconds AS waiting_duration_median_seconds,
    CASE WHEN activity_known THEN dwell_samples END AS completed_dwell_sample_count,
    dwell_median AS completed_dwell_median_seconds,
    dwell_lower AS completed_dwell_lower_median_seconds,
    dwell_upper AS completed_dwell_upper_median_seconds,
    CASE WHEN activity_known THEN incomplete END AS incomplete_visit_count,
    CASE WHEN activity_known AND rejected_total=0 THEN 0 END AS rejected_record_count,
    CASE WHEN activity_known THEN fallbacks END AS receipt_fallback_observation_count,
    CASE WHEN activity_known THEN flagged END AS flagged_observation_count,
    latest AS latest_observation_utc,extract(epoch FROM metric_as_of_utc-latest) AS observation_age_seconds,
    collector_state,'historical_reanalysis'::text AS weather_product,
    CASE WHEN weather_valid IS NOT NULL THEN 'available' ELSE 'unavailable' END AS weather_status,
    wind_speed_10m AS weather_wind_speed_mps,wind_gusts_10m AS weather_gusts_mps,
    precipitation AS weather_preceding_hour_precipitation_mm,
    coalesce(weather_flags,ARRAY['weather_unavailable']) AS weather_quality_flags,
    array_remove(ARRAY[
        'experimental_approximate_zones','observed_reports_not_total_ais_coverage',
        'waiting_low_speed_proxy','dwell_transition_midpoint_estimate',
        CASE WHEN NOT source_interval_available THEN 'source_interval_unknown' END,
        CASE WHEN NOT (SELECT processing_complete FROM options) THEN 'cleaning_pending_or_failed' END,
        CASE WHEN NOT snapshot_interval_available THEN 'snapshot_lookback_incomplete' END,
        CASE WHEN conflicting>0 THEN 'snapshot_timestamp_conflict' END,
        CASE WHEN fallbacks>0 THEN 'receipt_time_fallback' END,
        CASE WHEN rejected_total IS NULL OR rejected_total>0 THEN 'rejected_hour_unattributed' END,
        CASE WHEN partial_samples>0 THEN 'waiting_duration_includes_partial' END,
        CASE WHEN regional>0 THEN 'waiting_regional_attribution' END,
        CASE WHEN weather_valid IS NULL THEN 'weather_unavailable' END,
        CASE WHEN NOT complete_hour THEN 'partial_hour_event_time_prefix' END
    ],NULL) AS quality_flags
FROM combined ORDER BY hour_utc;
