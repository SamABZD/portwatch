-- Request identities include endpoint, parameters, product, port and parser version.
-- Raw content stays immutable; explicit selections may move to another saved revision.
CREATE TABLE context_requests (
    request_key text PRIMARY KEY CHECK (request_key ~ '^[0-9a-f]{64}$'),
    kind text NOT NULL CHECK (kind IN ('weather', 'news')),
    port_id text NOT NULL,
    product text NOT NULL CHECK (product IN ('historical_reanalysis', 'current_forecast', 'recent_news')),
    source_id text NOT NULL,
    endpoint text NOT NULL,
    parameters jsonb NOT NULL,
    start_utc timestamptz NOT NULL,
    end_utc timestamptz NOT NULL CHECK (end_utc > start_utc),
    selected_batch_id text REFERENCES ingestion_batches (batch_id),
    selected_retrieved_at timestamptz,
    CHECK ((selected_batch_id IS NULL) = (selected_retrieved_at IS NULL)),
    CHECK ((kind = 'news') = (product = 'recent_news'))
);
CREATE INDEX context_requests_selected_idx ON context_requests (selected_batch_id);
CREATE INDEX context_requests_scope_idx ON context_requests (kind, port_id, product, start_utc);

CREATE TABLE context_responses (
    batch_id text PRIMARY KEY REFERENCES ingestion_batches (batch_id),
    request_key text NOT NULL REFERENCES context_requests (request_key),
    row_count integer NOT NULL CHECK (row_count >= 0),
    quality jsonb NOT NULL,
    source_metadata jsonb NOT NULL,
    normalized_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX context_responses_request_idx ON context_responses (request_key);

CREATE TABLE context_attempts (
    attempt_id uuid PRIMARY KEY,
    request_key text NOT NULL REFERENCES context_requests (request_key),
    run_id uuid NOT NULL REFERENCES pipeline_runs (run_id),
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    retrieved_at timestamptz,
    batch_id text REFERENCES ingestion_batches (batch_id),
    status text NOT NULL CHECK (status IN ('running', 'valid_empty', 'valid_nonempty', 'failed')),
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    error_code text,
    next_allowed_at timestamptz NOT NULL,
    CHECK ((status = 'running') = (ended_at IS NULL)),
    CHECK ((status = 'failed') = (error_code IS NOT NULL)),
    CHECK (ended_at >= started_at),
    CHECK (status NOT IN ('valid_empty', 'valid_nonempty') OR (batch_id IS NOT NULL AND retrieved_at IS NOT NULL))
);
CREATE INDEX context_attempts_request_idx ON context_attempts (request_key, started_at DESC);
CREATE INDEX context_attempts_batch_idx ON context_attempts (batch_id);
CREATE INDEX context_attempts_run_idx ON context_attempts (run_id);

CREATE TABLE weather_values (
    batch_id text NOT NULL REFERENCES context_responses (batch_id),
    valid_time_utc timestamptz NOT NULL,
    wind_speed_10m double precision CHECK (wind_speed_10m >= 0 AND wind_speed_10m < 'Infinity'),
    wind_gusts_10m double precision CHECK (wind_gusts_10m >= 0 AND wind_gusts_10m < 'Infinity'),
    precipitation double precision CHECK (precipitation >= 0 AND precipitation < 'Infinity'),
    quality_flags text[] NOT NULL,
    PRIMARY KEY (batch_id, valid_time_utc)
);

CREATE TABLE news_mentions (
    batch_id text NOT NULL REFERENCES context_responses (batch_id),
    canonical_url text NOT NULL,
    original_url text NOT NULL,
    title text NOT NULL,
    source_domain text NOT NULL,
    language text,
    publication_time_utc timestamptz,
    first_seen_time_utc timestamptz,
    relevance_tags text[] NOT NULL,
    quality_flags text[] NOT NULL,
    PRIMARY KEY (batch_id, canonical_url)
);
