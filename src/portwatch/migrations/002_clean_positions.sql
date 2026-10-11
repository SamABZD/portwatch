-- PostGIS is installed once in public; application tables follow the active schema.
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;

CREATE TABLE clean_batches (
    input_id text NOT NULL,
    processing_version text NOT NULL,
    raw_batch_id text NOT NULL REFERENCES ingestion_batches(batch_id),
    input_sha256 text NOT NULL,
    status text NOT NULL DEFAULT 'registered' CHECK (status IN ('registered','processing','processed','failed')),
    started_at timestamptz,
    completed_at timestamptz,
    input_records bigint NOT NULL DEFAULT 0 CHECK (input_records >= 0),
    valid_positions bigint NOT NULL DEFAULT 0 CHECK (valid_positions >= 0),
    duplicate_records bigint NOT NULL DEFAULT 0 CHECK (duplicate_records >= 0),
    quarantined_records bigint NOT NULL DEFAULT 0 CHECK (quarantined_records >= 0),
    skipped_records bigint NOT NULL DEFAULT 0 CHECK (skipped_records >= 0),
    error_summary text,
    PRIMARY KEY (input_id, processing_version),
    CHECK (status <> 'processed' OR input_records = valid_positions + duplicate_records + quarantined_records + skipped_records)
);
CREATE INDEX clean_batches_raw_idx ON clean_batches(raw_batch_id);
CREATE INDEX clean_batches_pending_idx ON clean_batches(raw_batch_id, processing_version) WHERE status <> 'processed';

CREATE TABLE vessel_positions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    observation_id text NOT NULL,
    processing_version text NOT NULL,
    source_id text NOT NULL,
    dataset_id text NOT NULL,
    mmsi text NOT NULL CHECK (mmsi ~ '^[0-9]{9}$' AND mmsi <> '000000000'),
    observed_at timestamptz,
    received_at timestamptz NOT NULL,
    effective_at timestamptz NOT NULL,
    timestamp_quality text NOT NULL CHECK (timestamp_quality IN ('provider_documented_utc','receipt_fallback')),
    provider_reported_at timestamptz,
    source_timestamp_raw text,
    longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    speed_knots double precision CHECK (speed_knots BETWEEN 0 AND 102.2),
    course_degrees double precision CHECK (course_degrees >= 0 AND course_degrees < 360),
    heading_degrees double precision CHECK (heading_degrees >= 0 AND heading_degrees <= 359),
    vessel_type bigint,
    navigation_status bigint,
    message_type text NOT NULL,
    source_batch_id text NOT NULL REFERENCES ingestion_batches(batch_id),
    record_offset bigint NOT NULL CHECK (record_offset >= 0),
    offset_unit text NOT NULL CHECK (offset_unit IN ('national_row','jsonl_byte')),
    source_record_id text NOT NULL,
    connection_session uuid,
    record_sequence bigint,
    quality_flags text[] NOT NULL,
    position public.geometry(Point,4326) NOT NULL,
    UNIQUE (observation_id, processing_version),
    UNIQUE (source_record_id, processing_version),
    CHECK ((observed_at IS NOT NULL AND timestamp_quality = 'provider_documented_utc' AND effective_at = observed_at)
        OR (observed_at IS NULL AND timestamp_quality = 'receipt_fallback' AND effective_at = received_at))
);
CREATE INDEX vessel_positions_vessel_time_idx ON vessel_positions(source_id,dataset_id,mmsi,effective_at);
CREATE INDEX vessel_positions_dataset_time_idx ON vessel_positions(dataset_id,effective_at);
CREATE INDEX vessel_positions_batch_idx ON vessel_positions(source_batch_id);
CREATE INDEX vessel_positions_spatial_idx ON vessel_positions USING gist(position);

CREATE TABLE clean_records (
    source_record_id text NOT NULL,
    processing_version text NOT NULL,
    input_id text NOT NULL,
    raw_batch_id text NOT NULL REFERENCES ingestion_batches(batch_id),
    record_offset bigint NOT NULL CHECK (record_offset >= 0),
    offset_unit text NOT NULL CHECK (offset_unit IN ('national_row','jsonl_byte')),
    outcome text NOT NULL CHECK (outcome IN ('valid','duplicate','quarantined','skipped')),
    position_id bigint REFERENCES vessel_positions(id),
    reason_code text,
    quarantine_path text,
    PRIMARY KEY (source_record_id,processing_version),
    FOREIGN KEY (input_id,processing_version) REFERENCES clean_batches(input_id,processing_version),
    CHECK ((outcome IN ('valid','duplicate') AND position_id IS NOT NULL AND reason_code IS NULL)
        OR (outcome = 'quarantined' AND position_id IS NULL AND reason_code IS NOT NULL AND quarantine_path IS NOT NULL)
        OR (outcome = 'skipped' AND position_id IS NULL AND reason_code IS NOT NULL))
);
CREATE INDEX clean_records_input_idx ON clean_records(input_id,processing_version);
CREATE INDEX clean_records_raw_idx ON clean_records(raw_batch_id);
CREATE INDEX clean_records_position_idx ON clean_records(position_id) WHERE position_id IS NOT NULL;

CREATE TABLE vessel_static_messages (
    source_record_id text NOT NULL,
    processing_version text NOT NULL,
    source_id text NOT NULL,
    dataset_id text NOT NULL,
    mmsi text NOT NULL CHECK (mmsi ~ '^[0-9]{9}$' AND mmsi <> '000000000'),
    original_message jsonb NOT NULL,
    PRIMARY KEY (source_record_id,processing_version),
    FOREIGN KEY (source_record_id,processing_version) REFERENCES clean_records(source_record_id,processing_version)
);
CREATE INDEX vessel_static_messages_vessel_idx ON vessel_static_messages(source_id,dataset_id,mmsi);
