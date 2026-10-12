-- Source-separated, versioned, retrospective completed-hour outputs.
CREATE TABLE mart_rule_sets (
    analytical_version text PRIMARY KEY CHECK (analytical_version ~ '^[0-9a-f]{64}$'),
    metric_version text NOT NULL,
    rule_id text NOT NULL REFERENCES visit_rule_sets(rule_id),
    parameters jsonb NOT NULL
);
CREATE INDEX mart_rule_sets_visit_rule_idx ON mart_rule_sets(rule_id);

CREATE TABLE mart_partitions (
    partition_id text PRIMARY KEY CHECK (partition_id ~ '^[0-9a-f]{64}$'),
    port_id text NOT NULL,
    source_id text NOT NULL,
    dataset_id text NOT NULL,
    clean_version text NOT NULL,
    geofence_version text NOT NULL,
    assignment_version text NOT NULL,
    rule_id text NOT NULL REFERENCES visit_rule_sets(rule_id),
    analytical_version text NOT NULL REFERENCES mart_rule_sets(analytical_version),
    start_utc timestamptz NOT NULL,
    end_utc timestamptz NOT NULL CHECK (end_utc > start_utc),
    input_as_of_utc timestamptz NOT NULL,
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    source_health jsonb NOT NULL,
    unattributed_rejected_record_count bigint CHECK (unattributed_rejected_record_count >= 0),
    weather_batch_id text REFERENCES context_responses(batch_id),
    weather_request_key text REFERENCES context_requests(request_key),
    computed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    result_semantics text NOT NULL DEFAULT 'retrospective_completed_hours'
        CHECK (result_semantics = 'retrospective_completed_hours'),
    FOREIGN KEY (port_id,geofence_version) REFERENCES geofence_versions(port_id,version),
    CHECK ((weather_batch_id IS NULL) = (weather_request_key IS NULL)),
    UNIQUE (port_id,source_id,dataset_id,analytical_version),
    UNIQUE (partition_id,port_id,source_id,dataset_id,analytical_version)
);
CREATE INDEX mart_partitions_rule_idx ON mart_partitions(rule_id);
CREATE INDEX mart_partitions_version_idx ON mart_partitions(analytical_version);
CREATE INDEX mart_partitions_geofence_idx ON mart_partitions(port_id,geofence_version);
CREATE INDEX mart_partitions_weather_idx ON mart_partitions(weather_batch_id);
CREATE INDEX mart_partitions_weather_request_idx ON mart_partitions(weather_request_key);

CREATE TABLE port_activity_hourly (
    partition_id text NOT NULL,
    port_id text NOT NULL,
    source_id text NOT NULL,
    dataset_id text NOT NULL,
    analytical_version text NOT NULL,
    hour_utc timestamptz NOT NULL,
    hour_end_utc timestamptz NOT NULL CHECK (hour_end_utc = hour_utc + interval '1 hour'),
    complete_hour boolean NOT NULL CHECK (complete_hour),
    metric_as_of_utc timestamptz NOT NULL CHECK (metric_as_of_utc = hour_end_utc),
    source_interval_available boolean NOT NULL,
    snapshot_interval_available boolean NOT NULL,
    observation_count bigint CHECK (observation_count >= 0),
    distinct_vessels_observed bigint CHECK (distinct_vessels_observed >= 0),
    snapshot_vessel_count bigint CHECK (snapshot_vessel_count >= 0),
    snapshot_port_vessel_count bigint CHECK (snapshot_port_vessel_count >= 0),
    snapshot_stale_vessel_count bigint CHECK (snapshot_stale_vessel_count >= 0),
    snapshot_conflicting_vessel_count bigint CHECK (snapshot_conflicting_vessel_count >= 0),
    snapshot_zone_counts jsonb NOT NULL,
    confirmed_arrival_count bigint CHECK (confirmed_arrival_count >= 0),
    confirmed_departure_count bigint CHECK (confirmed_departure_count >= 0),
    waiting_candidate_count bigint CHECK (waiting_candidate_count >= 0),
    waiting_episode_count bigint CHECK (waiting_episode_count >= 0),
    port_associated_waiting_count bigint CHECK (port_associated_waiting_count >= 0),
    regional_waiting_count bigint CHECK (regional_waiting_count >= 0),
    waiting_duration_sample_count bigint CHECK (waiting_duration_sample_count >= 0),
    waiting_partial_sample_count bigint CHECK (waiting_partial_sample_count >= 0),
    waiting_duration_median_seconds double precision CHECK (waiting_duration_median_seconds >= 0),
    completed_dwell_sample_count bigint CHECK (completed_dwell_sample_count >= 0),
    completed_dwell_median_seconds double precision CHECK (completed_dwell_median_seconds >= 0),
    completed_dwell_lower_median_seconds double precision CHECK (completed_dwell_lower_median_seconds >= 0),
    completed_dwell_upper_median_seconds double precision CHECK (completed_dwell_upper_median_seconds >= 0),
    incomplete_visit_count bigint CHECK (incomplete_visit_count >= 0),
    rejected_record_count bigint CHECK (rejected_record_count >= 0),
    receipt_fallback_observation_count bigint CHECK (receipt_fallback_observation_count >= 0),
    flagged_observation_count bigint CHECK (flagged_observation_count >= 0),
    latest_observation_utc timestamptz,
    observation_age_seconds double precision CHECK (observation_age_seconds >= 0),
    collector_state text NOT NULL CHECK (collector_state IN
        ('not_applicable_historical','unknown_historical_collector_state')),
    weather_product text NOT NULL CHECK (weather_product = 'historical_reanalysis'),
    weather_status text NOT NULL CHECK (weather_status IN ('available','unavailable')),
    weather_wind_speed_mps double precision,
    weather_gusts_mps double precision,
    weather_preceding_hour_precipitation_mm double precision,
    weather_quality_flags text[] NOT NULL,
    quality_flags text[] NOT NULL,
    PRIMARY KEY (port_id,source_id,dataset_id,analytical_version,hour_utc),
    FOREIGN KEY (partition_id,port_id,source_id,dataset_id,analytical_version)
        REFERENCES mart_partitions(partition_id,port_id,source_id,dataset_id,analytical_version)
        ON DELETE CASCADE,
    CHECK (hour_utc AT TIME ZONE 'UTC' = date_trunc('hour',hour_utc AT TIME ZONE 'UTC')),
    CHECK (distinct_vessels_observed <= observation_count),
    CHECK (snapshot_port_vessel_count <= snapshot_vessel_count),
    CHECK (port_associated_waiting_count + regional_waiting_count = waiting_candidate_count),
    CHECK (waiting_partial_sample_count <= waiting_duration_sample_count),
    CHECK ((waiting_duration_sample_count > 0) = (waiting_duration_median_seconds IS NOT NULL)),
    CHECK ((completed_dwell_sample_count > 0) = (completed_dwell_median_seconds IS NOT NULL)),
    CHECK ((latest_observation_utc IS NULL) = (observation_age_seconds IS NULL))
);
CREATE INDEX port_activity_hourly_partition_idx ON port_activity_hourly(partition_id,hour_utc);
