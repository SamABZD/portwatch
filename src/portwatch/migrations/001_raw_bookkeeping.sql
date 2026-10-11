CREATE TABLE ingestion_batches (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id text NOT NULL UNIQUE CHECK (batch_id ~ '^[0-9a-f]{64}$'),
    source_id text NOT NULL,
    dataset_id text NOT NULL,
    source_url text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('historical', 'live')),
    local_path text NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    byte_count bigint NOT NULL CHECK (byte_count >= 0),
    record_count bigint CHECK (record_count >= 0),
    received_at timestamptz NOT NULL,
    source_start_utc timestamptz,
    source_end_utc timestamptz,
    processing_version text NOT NULL,
    status text NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered', 'processing', 'processed', 'failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_summary text,
    registered_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, dataset_id, source_url, content_sha256),
    CHECK ((source_start_utc IS NULL) = (source_end_utc IS NULL)),
    CHECK (source_end_utc >= source_start_utc),
    CHECK ((status = 'failed') = (error_summary IS NOT NULL))
);

CREATE INDEX ingestion_batches_dataset_receipt_idx
    ON ingestion_batches (dataset_id, received_at);
CREATE INDEX ingestion_batches_pending_idx
    ON ingestion_batches (status) WHERE status IN ('registered', 'failed');

CREATE TABLE pipeline_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL UNIQUE,
    stage text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
    input_count bigint CHECK (input_count >= 0),
    output_count bigint CHECK (output_count >= 0),
    error_summary text,
    CHECK (ended_at >= started_at),
    CHECK ((status = 'running') = (ended_at IS NULL)),
    CHECK ((status IN ('failed', 'interrupted')) = (error_summary IS NOT NULL))
);

CREATE INDEX pipeline_runs_stage_start_idx ON pipeline_runs (stage, started_at);
