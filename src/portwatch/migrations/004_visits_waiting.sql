-- Retrospective derived episodes. Original observations and versioned zones are unchanged.
CREATE TABLE visit_rule_sets (
    rule_id text PRIMARY KEY CHECK (rule_id ~ '^[0-9a-f]{64}$'),
    rule_version text NOT NULL,
    parameters jsonb NOT NULL
);

CREATE TABLE visit_inference_partitions (
    partition_id text PRIMARY KEY CHECK (partition_id ~ '^[0-9a-f]{64}$'),
    port_id text NOT NULL,
    source_id text NOT NULL,
    dataset_id text NOT NULL,
    clean_version text NOT NULL,
    geofence_version text NOT NULL,
    assignment_version text NOT NULL,
    rule_id text NOT NULL REFERENCES visit_rule_sets(rule_id),
    mmsi text NOT NULL CHECK (mmsi ~ '^[0-9]{9}$' AND mmsi <> '000000000'),
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    position_count integer NOT NULL CHECK (position_count > 0),
    distinct_event_times integer NOT NULL CHECK (distinct_event_times > 0),
    result_as_of_utc timestamptz NOT NULL,
    result_semantics text NOT NULL DEFAULT 'retrospective_full_history'
        CHECK (result_semantics = 'retrospective_full_history'),
    current_state text NOT NULL CHECK (current_state IN
        ('outside','harbor','terminal','candidate_waiting','unknown')),
    computed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (port_id,geofence_version) REFERENCES geofence_versions(port_id,version),
    UNIQUE (port_id,source_id,dataset_id,clean_version,geofence_version,assignment_version,rule_id,mmsi)
);
CREATE INDEX visit_inference_partitions_rule_idx ON visit_inference_partitions(rule_id);
CREATE INDEX visit_inference_partitions_geofence_idx ON visit_inference_partitions(port_id,geofence_version);

CREATE TABLE vessel_activity_states (
    partition_id text NOT NULL REFERENCES visit_inference_partitions(partition_id) ON DELETE CASCADE,
    position_id bigint NOT NULL REFERENCES vessel_positions(id),
    observed_at timestamptz NOT NULL,
    observed_state text NOT NULL CHECK (observed_state IN
        ('outside','harbor','terminal','candidate_waiting','unknown')),
    port_inside boolean,
    waiting_candidate boolean,
    gap_before_seconds double precision NOT NULL CHECK (gap_before_seconds >= 0),
    interval_state_before text NOT NULL CHECK (interval_state_before IN
        ('outside','harbor','terminal','candidate_waiting','unknown')),
    quality_flags text[] NOT NULL,
    PRIMARY KEY (partition_id,position_id),
    CHECK ((observed_state='unknown' AND port_inside IS NULL AND waiting_candidate IS NULL)
        OR (observed_state<>'unknown' AND port_inside IS NOT NULL))
);
CREATE INDEX vessel_activity_states_position_idx ON vessel_activity_states(position_id);
CREATE INDEX vessel_activity_states_time_idx ON vessel_activity_states(partition_id,observed_at);

CREATE TABLE vessel_visits (
    visit_id text PRIMARY KEY,
    partition_id text NOT NULL REFERENCES visit_inference_partitions(partition_id) ON DELETE CASCADE,
    segment integer NOT NULL CHECK (segment >= 0),
    first_position_id bigint NOT NULL REFERENCES vessel_positions(id),
    last_position_id bigint NOT NULL REFERENCES vessel_positions(id),
    observed_start timestamptz NOT NULL,
    observed_end timestamptz NOT NULL CHECK (observed_end >= observed_start),
    arrival_lower timestamptz,
    arrival_upper timestamptz,
    arrival_confirmed_at timestamptz NOT NULL,
    departure_lower timestamptz,
    departure_upper timestamptz,
    departure_confirmed_at timestamptz,
    known_at timestamptz NOT NULL,
    completed boolean NOT NULL,
    completeness_reasons text[] NOT NULL,
    gap_indicators text[] NOT NULL,
    boundary_jitter_count integer NOT NULL CHECK (boundary_jitter_count >= 0),
    observation_count integer NOT NULL CHECK (observation_count >= 2),
    position_count integer NOT NULL CHECK (position_count >= observation_count),
    supported_inside_seconds double precision NOT NULL CHECK (supported_inside_seconds >= 0),
    observed_span_seconds double precision NOT NULL CHECK (observed_span_seconds >= 0),
    duration_min_seconds double precision,
    duration_max_seconds double precision,
    duration_semantics text NOT NULL CHECK (duration_semantics IN
        ('completed_transition_interval','partial_observation_span')),
    UNIQUE (visit_id,partition_id),
    CHECK ((arrival_lower IS NULL AND arrival_upper IS NULL)
        OR (arrival_lower IS NOT NULL AND arrival_upper IS NOT NULL AND arrival_lower <= arrival_upper)),
    CHECK ((departure_lower IS NULL AND departure_upper IS NULL AND departure_confirmed_at IS NULL)
        OR (departure_lower IS NOT NULL AND departure_upper IS NOT NULL
            AND departure_confirmed_at IS NOT NULL AND departure_lower <= departure_upper)),
    CHECK ((completed AND duration_semantics='completed_transition_interval'
            AND arrival_lower IS NOT NULL AND departure_lower IS NOT NULL
            AND cardinality(completeness_reasons)=0 AND duration_min_seconds IS NOT NULL
            AND duration_max_seconds IS NOT NULL AND duration_min_seconds >= 0
            AND duration_max_seconds >= duration_min_seconds)
        OR (NOT completed AND duration_semantics='partial_observation_span'
            AND cardinality(completeness_reasons)>0
            AND duration_min_seconds IS NULL AND duration_max_seconds IS NULL))
);
CREATE INDEX vessel_visits_partition_idx ON vessel_visits(partition_id);
CREATE INDEX vessel_visits_first_position_idx ON vessel_visits(first_position_id);
CREATE INDEX vessel_visits_last_position_idx ON vessel_visits(last_position_id);

CREATE TABLE waiting_episodes (
    episode_id text PRIMARY KEY,
    partition_id text NOT NULL REFERENCES visit_inference_partitions(partition_id) ON DELETE CASCADE,
    segment integer NOT NULL CHECK (segment >= 0),
    first_position_id bigint NOT NULL REFERENCES vessel_positions(id),
    last_position_id bigint NOT NULL REFERENCES vessel_positions(id),
    observed_start timestamptz NOT NULL,
    observed_end timestamptz NOT NULL CHECK (observed_end > observed_start),
    start_lower timestamptz,
    start_upper timestamptz,
    end_lower timestamptz,
    end_upper timestamptz,
    qualification_known_at timestamptz NOT NULL,
    known_at timestamptz NOT NULL,
    completed boolean NOT NULL,
    completeness_reasons text[] NOT NULL,
    observation_count integer NOT NULL CHECK (observation_count >= 2),
    position_count integer NOT NULL CHECK (position_count >= observation_count),
    candidate_zone_ids text[] NOT NULL CHECK (cardinality(candidate_zone_ids)>0),
    gap_indicators text[] NOT NULL,
    candidate_duration_seconds double precision NOT NULL CHECK (candidate_duration_seconds > 0),
    associated_visit_id text,
    association_known_at timestamptz,
    association_scope text NOT NULL CHECK (association_scope IN ('port','region')),
    duration_semantics text NOT NULL DEFAULT 'low_speed_candidate_interval_proxy'
        CHECK (duration_semantics='low_speed_candidate_interval_proxy'),
    FOREIGN KEY (associated_visit_id,partition_id) REFERENCES vessel_visits(visit_id,partition_id)
        ON DELETE CASCADE,
    CHECK ((association_scope='region' AND associated_visit_id IS NULL AND association_known_at IS NULL)
        OR (association_scope='port' AND associated_visit_id IS NOT NULL AND association_known_at IS NOT NULL)),
    CHECK ((completed AND start_lower IS NOT NULL AND end_upper IS NOT NULL
            AND cardinality(completeness_reasons)=0)
        OR (NOT completed AND cardinality(completeness_reasons)>0))
);
CREATE INDEX waiting_episodes_partition_idx ON waiting_episodes(partition_id);
CREATE INDEX waiting_episodes_visit_idx ON waiting_episodes(associated_visit_id,partition_id);
CREATE INDEX waiting_episodes_first_position_idx ON waiting_episodes(first_position_id);
CREATE INDEX waiting_episodes_last_position_idx ON waiting_episodes(last_position_id);
