# Local orchestration and recovery

Step10 uses Prefect3.8.8 and ignored local SQLite metadata at `data/prefect/prefect.db`.
Analytics remain in PostgreSQL17/PostGIS3.6 on localhost55432. The API/UI binds localhost4200.
The wrapper sets process-local configuration, its own profiles path, no ephemeral server,
and disabled telemetry. Global profiles and paid services are not changed.

Checked the installed APIs and official guides for [local servers](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli),
[local deployments](https://docs.prefect.io/v3/how-to-guides/deployment_infra/run-flows-in-local-processes)
and [retries](https://docs.prefect.io/v3/how-to-guides/workflows/retries). Prefect requires
websockets below17: the lock uses16.1.1, with collector tests rerun. Its actual scheduler failed
with SQLAlchemy2.1.4 hybrid bulk-DML setters. Constrain SQLAlchemy to2.0.x, locked2.0.54;
the [2.1 migration notes](https://docs.sqlalchemy.org/en/21/changelog/migration_21.html) describe
the changed behavior. Scheduling was verified again with the compatible version.

| Workflow | Work | Schedule |
| --- | --- | --- |
| Historical demo | Raw verification/acquisition, regional extraction, cleaning, matching ERA5, zones/visits/marts, publication validation | Manual |
| Backfill | Same functions per explicit UTC source day, resumable | Manual, 1–7 days |
| dbt models | Historical SQL build, typed contract, data tests, reference parity, docs and preliminary baselines | Manual; same Prefect server, separate locked dbt environment |
| Live processing | Completed raw discovery, idempotent cleaning, affected visits/marts, observation lag | Every60 seconds |
| Current weather | Bounded forecast fetch/normalization | Hourly |
| Recent news | Bounded article metadata, separate failure/empty/cooldown state | Hourly |

Hourly schedules begin at the next UTC hour. Deployments each allow one run; the shared runner
allows three, keeping connectors independent. A news429 cannot stop live processing or replay.
Tasks call Python functions. Prefect uses subprocesses for whole finite flows; it does not spawn
a shell for each transformation. The long-lived WebSocket collector remains a separate process.

Migration007 extends `pipeline_runs` with workflow key, parent, attempt, safe details and generated
duration. Child steps record input/output counts and checkpoint reuse. Counts describe the selected
input, not necessarily new inserts; live explicitly reports new batches/positions and event lag.
`pipeline_checkpoints` records committed input fingerprints/results and their audit run.
Business functions commit first, then checkpoints commit. A crash between commits resumes safely
through immutable raw/extract verification, idempotent cleaning and atomic visit/hour replacement.
No filesystem/database distributed transaction is claimed.

Every invocation verifies raw and extracted bytes. Clean/publication checkpoints reuse matching
inputs; publication validation executes again. `--rebuild` bypasses analytical checkpoints.
Late reports/quality changes, geofence/rules, weather revision and context identities invalidate
publication. Fingerprints stream4,096 rows at a time. Historical analytical identity stays unchanged
when inputs/rules remain unchanged. Do not manually delete clean source ledgers to force recovery.

PostgreSQL session locks own one workflow; a separate port lock covers mutations. Keys include
the schema because isolated fixtures own different tables. Locks survive commits, release in
`finally`, or release when a terminated session closes. An overlap is audited busy/failed without
running stages or advancing checkpoints. Reacquiring an abandoned workflow marks its old running
attempt interrupted. Existing transaction locks still protect individual manual transformations.
Prefect retries transient connection/serialization/deadlock errors twice after1 and3 seconds.
Credentials, incompatible schema/SQL and validation failures are not transient. Transfer functions
keep their existing bounded network retries;429 does not receive an immediate extra Prefect retry.
Settings/credentials never enter task parameters or failure payloads.

Backfills accept UTC `[start,end)` in the audited2024 NOAA source. One source day at a time,
at most seven requested days and512MiB per daily download. Preparation commits available inputs
before publication; a failed day is isolated while other days publish. Successful steps are reused
on resumption. HTTP404 creates an unresolved `pipeline_source_gaps` row, not a successful empty
partition. Valid national input with no regional reports can publish a known empty interval.
A subsequent successful source day resolves its gap.

Step16 adds opt-in `--allow-missing-weather` for recognized historical provider/cooldown
failures only. Failed connector audits and absent checkpoints remain; successful AIS outputs
carry a saved matching revision or null/flagged weather. Strict mode remains default.
`pipeline models` uses the same orchestration/audit path and owned output schemas, with a
`--local` recovery option. It does not start collectors or register another scheduler.
See [history/dbt operations](HISTORY_DBT.md) for frozen dates, tests and baseline requirements.

Historical visits use at most seven contiguous processed neighboring days, preferring earlier
context then later context. Missing days break continuity. Original positions retain canonical
dataset IDs; `visit_partition_context` records input datasets. Context identities enter the metric
version. Each daily hourly output covers only its target date, attributing transitions/dwell to
confirmation hours. A prior-day entry cannot become a midnight arrival. If entry lies outside
saved context it remains unknown/partial. Contextual episodes are projections for each target
partition: do not sum them across projections as independent port calls.

The live source's stable dataset carries complete histories across midnight. Its publication
covers up to the last seven observed days and only elapsed UTC hours. Current-hour reports can
still update visits. Unknown empty intervals remain null, with explicit collector/coverage limits.
Unchanged source/weather fingerprints reuse publication while lag updates every run. Retained
full live history will grow; larger-scale incremental optimization remains future work.
Live uses a separate current-forecast product/revision. Forecast never enters NOAA/reanalysis;
news never multiplies AIS metrics or establishes a delay cause.

See [RUNBOOK.md](RUNBOOK.md) for exact commands. PostgreSQL, the local Prefect server and serving
process must remain running. Continuous collection additionally requires the separate collector.
These are laptop processes, not boot services. Sleep/shutdown stops work and collection; saved
raw/checkpoints survive. Queued missed ticks may run after restart, processing current saved
inputs idempotently. They cannot recover reports never collected during downtime.
Graceful runner shutdown pauses schedules; `orchestrate pause` pauses only these three deployments.
Step11 adds [read-only health and safe structured events](HEALTH_RECOVERY.md); committed SQL
audits remain authoritative and events inside outer transactions are marked provisional.
Stage verification stops its temporary server/runner, retaining evidence. Ready schedules are not
an always-on hosted service. No frontend or deployment was created.
