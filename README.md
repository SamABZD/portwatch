# PortWatch

PortWatch is a $0, local-first data engineering portfolio project for the Port of Los Angeles. Steps 00–05 establish the Python CLI, source audit and frozen demo, durable raw storage, PostgreSQL/PostGIS bookkeeping, historical ingestion, a verified live collector, and reproducible position cleaning. Historical replay works without a live AIS key. No frontend or deployed site exists yet.

## Local setup

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

The reviewed collection bounding box and one-day UTC demo are in `configs/ports/los_angeles.yaml` and `configs/demo.yaml`. The analytical geofence remains pending. `doctor` reports database connectivity and PostGIS separately and exits nonzero if a required check fails. Raw storage and recovery rules are in [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md). Default tests run without a database; setting `PORTWATCH_TEST_DB=1` enables real PostgreSQL tests in newly created isolated test schemas.

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

PostgreSQL is the primary database; Railway is the preferred future deployment target. Deployment remains outside the current stage and subject to the $0 budget.

Project decisions and the current state are in [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/HANDOFF.md](docs/HANDOFF.md).
Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [GitHub workflow](docs/GITHUB_WORKFLOW.md). The verified publication details are in [docs/GITHUB_SETUP.md](docs/GITHUB_SETUP.md).
