-- Durable workflow state belongs with the existing audit, not Prefect metadata.
ALTER TABLE pipeline_runs ADD COLUMN workflow_key text;
ALTER TABLE pipeline_runs ADD COLUMN parent_run_id uuid REFERENCES pipeline_runs(run_id);
ALTER TABLE pipeline_runs ADD COLUMN attempt_number integer NOT NULL DEFAULT 1
    CHECK (attempt_number > 0);
ALTER TABLE pipeline_runs ADD COLUMN details jsonb NOT NULL DEFAULT '{}';
ALTER TABLE pipeline_runs ADD COLUMN duration_seconds double precision
    GENERATED ALWAYS AS (extract(epoch FROM ended_at-started_at)) STORED;
CREATE INDEX pipeline_runs_workflow_idx ON pipeline_runs(workflow_key,started_at DESC);
CREATE INDEX pipeline_runs_parent_idx ON pipeline_runs(parent_run_id);

CREATE TABLE pipeline_checkpoints (
    workflow_key text NOT NULL,
    step text NOT NULL,
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    run_id uuid NOT NULL REFERENCES pipeline_runs(run_id),
    result jsonb NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workflow_key,step)
);
CREATE INDEX pipeline_checkpoints_run_idx ON pipeline_checkpoints(run_id);

CREATE TABLE pipeline_source_gaps (
    port_id text NOT NULL,
    source_id text NOT NULL,
    dataset_id text NOT NULL,
    start_utc timestamptz NOT NULL,
    end_utc timestamptz NOT NULL CHECK (end_utc > start_utc),
    run_id uuid NOT NULL REFERENCES pipeline_runs(run_id),
    reason text NOT NULL,
    resolved_at timestamptz,
    PRIMARY KEY (port_id,source_id,dataset_id)
);
CREATE INDEX pipeline_source_gaps_run_idx ON pipeline_source_gaps(run_id);

-- Daily outputs retain the identities of the canonical input datasets in their context.
CREATE TABLE visit_partition_context (
    partition_id text NOT NULL REFERENCES visit_inference_partitions(partition_id)
        ON DELETE CASCADE,
    dataset_id text NOT NULL,
    PRIMARY KEY (partition_id,dataset_id)
);
INSERT INTO visit_partition_context SELECT partition_id,dataset_id
    FROM visit_inference_partitions;

ALTER TABLE port_activity_hourly DROP CONSTRAINT port_activity_hourly_weather_product_check;
ALTER TABLE port_activity_hourly ADD CHECK
    (weather_product IN ('historical_reanalysis','current_forecast'));
