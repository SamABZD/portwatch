-- Timed reported properties; original evidence remains immutable in raw storage.
CREATE TABLE vessel_profile_reports (
    source_record_id text NOT NULL,
    processing_version text NOT NULL,
    source_id text NOT NULL,
    dataset_id text NOT NULL,
    mmsi text NOT NULL CHECK (mmsi ~ '^[0-9]{9}$'),
    available_at timestamptz NOT NULL,
    fields jsonb NOT NULL CHECK (jsonb_typeof(fields)='object'),
    PRIMARY KEY (source_record_id,processing_version),
    FOREIGN KEY (source_record_id,processing_version)
        REFERENCES clean_records(source_record_id,processing_version)
);
CREATE INDEX vessel_profile_reports_lookup_idx
    ON vessel_profile_reports(source_id,dataset_id,mmsi,available_at);
CREATE TABLE vessel_profile_inputs (
    input_id text NOT NULL,
    processing_version text NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    report_count bigint NOT NULL CHECK (report_count >= 0),
    PRIMARY KEY (input_id,processing_version),
    FOREIGN KEY (input_id,processing_version)
        REFERENCES clean_batches(input_id,processing_version)
);
