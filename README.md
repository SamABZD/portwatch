# PortWatch

PortWatch is a $0, local-first data engineering portfolio project for the Port of Los Angeles. Steps 00–10 establish the Python CLI, frozen historical demo, durable storage, PostgreSQL/PostGIS bookkeeping, historical/live AIS, reproducible cleaning, approximate zones, visit/waiting inference, weather/news connectors and versioned hourly analytical tables with resumable local workflows. Historical replay works without a live AIS key. Weather is saved; the latest real GDELT request was rate limited, with failure separate from empty results. No frontend or deployed site exists yet. GitHub CI Step05A remains blocked by an account billing lock; the user authorized continuing local stages at $0.

## Local setup

The planned Step12 frontend is **Next.js with React/TypeScript**, as requested by the user.
It will read the existing Python/PostGIS system through a query API. The UI is not built yet;
see [frontend direction and acceptance criteria](docs/FRONTEND_PLAN.md).

Step10 adds complete resumable workflows. With the existing PostgreSQL cluster running, apply
all seven migrations, then keep this local Prefect server terminal open:

```powershell
.\.bootstrap-venv\Scripts\uv.exe sync --locked --extra dev
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch orchestrate server
```

In another terminal in this project directory:

```powershell
.\.venv\Scripts\python.exe -m portwatch pipeline run --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch pipeline backfill --port los_angeles --start 2024-01-01 --end 2024-01-02
.\.venv\Scripts\python.exe -m portwatch orchestrate serve --port los_angeles
```

The backfill selects one frozen UTC day, with exclusive end. `--local` on either pipeline
command provides recovery without Prefect; matching checkpoints reuse committed work.
The API/UI is `http://127.0.0.1:4200`, with its own local SQLite metadata. The serving process
runs finite live processing every minute and weather/news hourly. Continuous AIS collection
needs the separate collector below. Sleep/shutdown stops execution; these are local processes.
See [orchestration contracts](docs/ORCHESTRATION.md) and [start/stop/recovery runbook](docs/RUNBOOK.md).

Use Python 3.11 (the version installed on this machine). `uv` was installed into an ignored project-local bootstrap environment. From this directory on the current machine:

```powershell
.\.bootstrap-venv\Scripts\uv.exe sync --locked --extra dev
docker compose up -d db
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch db migrate
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch doctor
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch storage inspect
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch storage reconcile
.\.bootstrap-venv\Scripts\uv.exe run ruff check .
$env:PORTWATCH_TEST_DB='1'
.\.bootstrap-venv\Scripts\uv.exe run pytest -q
```

On another machine without `uv`, create the same local bootstrap environment with `py -3.11 -m venv .bootstrap-venv` and `.\.bootstrap-venv\Scripts\python.exe -m pip install uv`. If `uv` is already on PATH, the equivalent commands are `uv sync --locked --extra dev` and `uv run ...`. Copy `.env.example` to `.env` only when local overrides are needed; the checked-in defaults work for the localhost demo.

The Compose option needs Docker Desktop. Docker publishes PostgreSQL only on `127.0.0.1:5432` by default; the database uses a named local volume. The sample password is strictly for this localhost demo and should be changed if the service is exposed elsewhere. Update `PORTWATCH_DB_PASSWORD` in `.env` before first Compose startup if you change it. Compose initialization variables do not reset a populated volume.

On this Windows machine, the isolated PostgreSQL 17.11 cluster uses a private runtime with PostGIS 3.6.2 in ignored `pg-runtime/`, prepared from installed PostgreSQL and the official PostGIS bundle. Existing services were untouched. Data stays in `pgdata/portwatch`; local `.env` selects `127.0.0.1:55432`. It is already initialized; restart it after reboot from this project directory with:

```powershell
$portwatchPg = Start-Process -FilePath "$PWD\pg-runtime\bin\pg_ctl.exe" -ArgumentList @('-D','pgdata\portwatch','-l','pgdata\portwatch-server.log','-o','"-h 127.0.0.1 -p 55432"','-w','start') -WindowStyle Hidden -PassThru
$portwatchPg.WaitForExit(15000) | Out-Null
```

Skip `docker compose up` when using this native cluster. Check status with `& .\pg-runtime\bin\pg_ctl.exe -D pgdata\portwatch status`; stop cleanly with `& .\pg-runtime\bin\pg_ctl.exe -D pgdata\portwatch -m fast -w stop`. These commands target only PortWatch's cluster. Use `$portwatchPg.WaitForExit(15000)` as above; `Start-Process -Wait` also waits for server descendants and can remain blocked. On this machine the runtime is already prepared. To reproduce preparation with installed PG17, run `.\.venv\Scripts\python.exe scripts/prepare_postgis.py` before starting the private runtime; it verifies the pinned official bundle hash and never restarts a server. Keep the private runtime with this cluster because PostGIS libraries resolve through it.

The reviewed collection box, approximate geofence and one-day UTC demo are in `configs/ports/los_angeles.yaml` and `configs/demo.yaml`. `doctor` reports database connectivity and PostGIS separately and exits nonzero if a required check fails. Raw storage and recovery rules are in [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md). Default tests run without a database; setting `PORTWATCH_TEST_DB=1` enables real PostgreSQL tests in newly created isolated test schemas.

## Historical demo

With the local database running and migrations applied:

```powershell
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch ingest historical --port los_angeles --demo
.\.bootstrap-venv\Scripts\uv.exe run python scripts/verify_historical.py --rebuild
```

The command downloads the frozen NOAA day once, preserves its unchanged raw artifact and manifest, then extracts the collection box in bounded batches into derived Parquet with parent lineage. A matching rerun verifies and reuses both artifacts. Add `--refresh` to check remote ETag/length explicitly; changed bytes retain a separate raw revision. Generated provider data and plots are ignored by Git.

Explicit backfills support 1–7 UTC days within 2024, with an exclusive end date:

```powershell
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch ingest historical --port los_angeles --start 2024-01-01 --end 2024-01-02
```

The verified demo contains 62,159 observations and 141 distinct vessel identifiers. Counts, measured memory, timestamp limitations and the geographic preview are in [docs/RESULTS.md](docs/RESULTS.md). The verifier checks every regional row, regenerates a bounded scatter plot, and optionally rebuilds from the preserved raw file to compare checksums. No live key or Docker download is needed for this stage on the configured native database.

## Live capture

Create a key at [AISStream account](https://aisstream.io/account) and set `PORTWATCH_AIS_API_KEY=<your key>` in the existing ignored local `.env`, then:

```powershell
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch ingest live --port los_angeles --duration 60
```

Omit `--duration` for continuous capture; Ctrl+C drains and finalizes the batch. The collector preserves original messages in versioned envelopes, flushes active files frequently, rotates raw batches, reconnects with bounded backoff and reports queue/persistence failures. A real 60.84-second LA capture saved 26 positions, 11 static messages and one confirmation without parse errors or reconnects. A key is now saved in ignored `.env`. Setup, status definitions, loss windows and interrupted-file recovery are in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Clean saved positions

```powershell
.\.venv\Scripts\python.exe -m portwatch transform positions --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch transform positions --port los_angeles --pending-live
.\.venv\Scripts\python.exe scripts/verify_positions.py
```

These commands use saved inputs without provider downloads. Historical inputs reconcile to 62,157 clean positions and two duplicates; live inputs to 26 positions and 12 explicit skips, including 11 separately preserved static messages. Reruns reuse identical counts and business rows. Contracts, timestamp choices, source-record lineage and quarantine reasons are in [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md). The verifier checks two replays and writes ignored `artifacts/step05/verification.json`.

## Spatial demo

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch geo load --port los_angeles
.\.venv\Scripts\python.exe -m portwatch transform zones --port los_angeles --demo
.\.venv\Scripts\python.exe scripts/verify_zones.py
```

Six reviewed approximate zones classify all 62,157 historical positions: 61,712 inside and 445
outside, with all 72,237 memberships retained. Replays preserve original positions and existing
geofence versions. Offshore candidate membership is regional and does not establish waiting or
a Los Angeles visit. See [zone provenance and rules](docs/GEO_ZONES.md); the ignored
`artifacts/step06/los-angeles-zones.png` is a diagnostic plot, not a dashboard.

PostgreSQL is the primary database; Railway is the preferred future deployment target. Deployment remains outside the current stage and subject to the $0 budget.

## Visits and waiting candidates

```powershell
.\.venv\Scripts\python.exe -m portwatch transform visits --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch transform visits --port los_angeles --demo --as-of 2024-01-01T12:00:00Z
.\.venv\Scripts\python.exe scripts/verify_visits.py
```

The saved historical day yields 230 inferred episodes: 14 completed under experimental rules and
216 partial, plus one low-speed waiting candidate. Repeated observations confirm entry/exit;
reporting gaps split continuity, and missing arrival/departure remain unknown. Reprocessing replaces
affected histories transactionally, including obsolete IDs after late reports. `--as-of` is read-only
and excludes future event times. Full-history rows are retrospective. The sole candidate reports
pilot-vessel type50 and does not establish a cargo queue; see [inference contracts and track review](docs/VISITS.md).

## Weather and recent news

```powershell
.\.venv\Scripts\python.exe -m portwatch ingest weather --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch ingest weather --port los_angeles
.\.venv\Scripts\python.exe -m portwatch ingest news --port los_angeles
.\.venv\Scripts\python.exe scripts/verify_context.py
```

The real demo has24 ERA5 hourly values for2024-01-01; current forecast has48 hours over the
current/next UTC day. Both preserve exact raw responses, request parameters, units, actual grid
coordinates and immutable revisions. Historical default uses verified cache; explicit `--refresh`
selects another successful revision and respects rate limits. Retrieval time is not issue time.
News keeps article metadata only, separates first seen/publication, preserves URL IDs and marks
capped results truncated. Latest real GDELT request was429, so real articles remain unverified;
historical replay works independently and excludes recent news. See [CONTEXT.md](docs/CONTEXT.md)
for source health, attribution, retrospective interpretation and revision-safe queries.

## Hourly analytical tables

```powershell
.\.venv\Scripts\python.exe -m portwatch transform marts --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch transform marts --port los_angeles --demo --as-of 2024-01-01T12:30:00Z
.\.venv\Scripts\python.exe scripts/verify_marts.py
```

The saved demo produces24 hourly rows reconciling62,157 canonical observations,56 inferred entry/
35 exit confirmations and14 completed dwell samples. Hourly distinct activity is separate from
hour-start snapshots, which use one fresh latest report group per vessel with a15-minute age limit.
Completed dwell has midpoint/bound medians; partial episodes, waiting sample/completeness flags,
source gaps, reject allocation and model weather remain explicit. News never multiplies counts.

Visits and affected mart hours publish together, including replacement after late data; failures
retain the previous valid partition. `--as-of` reconstructs a prefix without future reports or
persistent writes and labels its partial hour. No congestion baseline or confirmed cargo queue is
claimed. [ANALYTICS.md](docs/ANALYTICS.md) defines every field and provides five executable SQL
queries. Next stage is orchestration/backfills; no scheduler has been introduced yet.

Project decisions and the current state are in [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/HANDOFF.md](docs/HANDOFF.md).
Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [GitHub workflow](docs/GITHUB_WORKFLOW.md). The verified publication details are in [docs/GITHUB_SETUP.md](docs/GITHUB_SETUP.md).
