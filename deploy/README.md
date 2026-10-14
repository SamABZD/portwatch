# Deployment and operations

The owner selected Vercel for the Next.js frontend and their existing paid Railway Hobby
workspace for the continuous Python backend and PostgreSQL/PostGIS. Local operation remains
available at $0; cloud resource consumption is billed by Railway under that existing plan.

**October14,2026 Phase6 release:** [PR#9](https://github.com/SamABZD/portwatch/pull/9)
merged at `fb89c3c`; the existing Vercel integration deployed that revision to
[production](https://portwatch-iota.vercel.app/). Twelve HTTPS checks, four API bounds
checks and eight real-response browser checks passed for both ports/modes and pipeline/
history/lineage. Railway deployment identities, one replica per service and resource
limits remain unchanged; both collectors are connected and local collectors remain stopped.
The previous frontend at `b6e4009` is saved as rollback, with detailed configuration private.
Expected recurring frontend delta0 assumes unchanged traffic/backend workload; actual
billing delta is unavailable. See [release evidence](../docs/evidence/phase6-release.json).
Older observations below retain their original scope.

| Component | Endpoint / storage |
| --- | --- |
| Frontend | [PortWatch](https://portwatch-iota.vercel.app), Vercel project `portwatch` |
| Read-only API | [Readiness](https://backend-production-895d.up.railway.app/readyz), Railway `backend` |
| Database | Private Railway networking only; `postgis/postgis:17-3.6-alpine` |
| Database volume | `/var/lib/postgresql/data`, `PGDATA=/var/lib/postgresql/data/pgdata` |
| Backend volume | `/app/data`: raw, derived, quarantine, active spools, Prefect metadata and status |

## Verification status

The final recheck later caught a genuine Los Angeles GDELT429 during its hourly refresh.
The follow-up adds [LA's official release archive](https://portoflosangeles.org/news/news-release-archive)
as a separately attributed fallback, analogous to Oakland's RSS. Publication dates retain
day precision and an explicit Pacific date-overlap assumption; no exact UTC publication
instant or first-seen time is invented. The original v0.1.0 tag remains unchanged.

Verified October13,2026 around11:30UTC: frontend and backend HTTPS endpoints return200;
Vercel's server-side proxy loads both ports. Both collectors report `healthy_connected` and
recent observations, and finite minute jobs succeed. Forecasts are available; LA GDELT and
Oakland official RSS are valid empty results, with Oakland's primary failure still audited.
Cloud historical catalogs contain62,157 LA and52,783 Oakland canonical NOAA observations.
The browser renders live observations and operational health; missing basemap tiles use the
existing local geographic fallback. These checks establish operation at that time, not an
uptime or source-coverage guarantee. An initial frontend-only deployment was superseded.

Backend and database deployments both reached SUCCESS. The Next.js production deployment
`portwatch-fhxc3doq2-abzd.vercel.app` was built from reviewed source checkpoint `2197e72`.
The local runtime stopped cleanly at11:25:00.742834UTC before cloud live was enabled.
Release/publication evidence is maintained in [GITHUB_SETUP](../docs/GITHUB_SETUP.md).

## Runtime configuration

Vercel's Git root directory is `web`, with Node24, the Next.js preset and `npm run build`.
`PORTWATCH_QUERY_URL` is a server-only variable pointing to the HTTPS Railway API. No DB
password or AIS key belongs in Vercel or a `NEXT_PUBLIC_*` variable. `.vercelignore` excludes
environment files, build output and dependencies; installation regenerates the map worker.

Railway builds the repository root with `Dockerfile.backend`. `python -m portwatch.cloud`
migrates the database and bootstraps missing genuine one-day NOAA partitions before serving
on `0.0.0.0:$PORT`. `/readyz` checks schema/database access without exposing exception text.
The HTTP query endpoints are read-only; request handlers never start ingestion.

Backend variables:

```text
PORT=8080
PORTWATCH_DB_HOST=${{database.RAILWAY_PRIVATE_DOMAIN}}
PORTWATCH_DB_PORT=5432
PORTWATCH_DB_NAME=portwatch
PORTWATCH_DB_USER=portwatch
PORTWATCH_DB_PASSWORD=${{database.POSTGRES_PASSWORD}}
PORTWATCH_DB_SSLMODE=disable
PORTWATCH_CLOUD_BOOTSTRAP=1
PORTWATCH_CLOUD_LIVE=1
PORTWATCH_LIVE_STATUS_DIR=data/status
```

Database transport uses the project's private network; only the read-only API has a public
domain. `POSTGRES_PASSWORD` is generated privately and referenced across services. Live
mode also needs the owner's private `PORTWATCH_AIS_API_KEY`, set through secret stdin.

Use one replica per service and disable sleeping. Both services run in Railway's US East
region (`us-east4-eqdc4a`), matching Vercel's `iad1` vicinity. Configured limits: backend1vCPU/2GB,
database0.5vCPU/1GB; these are ceilings, not measured usage or a quoted monthly price.
Both volumes are5000MB. The collector preserves the default1GB free-space reserve and
stops explicitly before exhausting disk. Retention, volume growth and off-device backups
need continuing operation; no raw-file deletion is automatic.

Railway health check: `/readyz`,600s startup allowance; restart policy `ALWAYS`;
overlap0s and45s drain for backend replacement. Volume-backed updates incur brief downtime.
Service settings were applied through the current API. Legacy `railway.json` configuration
was rejected as deprecated and is not shipped; see [Railway IaC](https://docs.railway.com/infrastructure-as-code)
before adopting declarative infrastructure.

## Safe handover and updates

1. Deploy with cloud live disabled. Verify readiness and both genuine historical catalogs.
2. Gracefully stop the existing local runtime with `python -m portwatch.runtime --stop`.
   Confirm the two owned collectors exit before enabling cloud collection. The provider
   limits simultaneous connections; running both pairs is unsafe for continuity.
3. Set the private AIS key and `PORTWATCH_CLOUD_LIVE=1`, then redeploy the backend. Its owned
   runtime starts independent collectors, local Prefect and finite scheduled processing.
4. Check both API health views, collector heartbeats, saved batches, completed minute jobs,
   weather/news freshness and explicit fallback labels. No observations during handover
   downtime are invented. A successful HTTP readiness check alone does not establish this.
5. Point Vercel production/preview `PORTWATCH_QUERY_URL` at the verified backend and redeploy.
   Verify both ports and replay through the browser. Preserve GitHub Actions disabled.

Inspect `railway status`, bounded `railway logs --service backend --lines 50` and the public
read-only pipeline page. Never print all cloud variables or expose Prefect/Postgres ports.
For abrupt failures, follow [RUNBOOK](../docs/RUNBOOK.md) and the recovery behavior below.
Back up PostgreSQL and the immutable backend volume together;
the existing verified restore procedure is in [HEALTH_RECOVERY](../docs/HEALTH_RECOVERY.md).

`Dockerfile.frontend` provides an optional standalone Node container. Its production Next.js
build was checked locally; the deployed frontend uses Vercel's Next.js integration instead.

## Budgeted history and continuous operation

The owner approved merging Step16 and deploying its seven-day history, with no additional
spending and no 30-day expansion. A separate owner-designated project had three deployments
stopped and two GitHub auto-deploy settings disabled without deleting its project, sources
or PostgreSQL volume. Its retained storage and prior usage still bill. Private account and
other-project cost details are excluded; this action alone does not prove a nonpositive
PortWatch cost delta.

Keep PortWatch's existing two services, CPU/RAM limits and 5000MB volumes. The frozen
`portwatch_history_7d` database shares the existing private PostGIS service. Import a verified
custom-format snapshot of Step16's public schema, excluding disposable `vessel_activity_states`
rows. Original canonical positions, record lineage, profiles, visits and hourly reference
outputs remain. Original NOAA files and derivations remain preserved locally; this is a
database snapshot deployment, not a claim that those national files were downloaded again
on Railway. Initialize PostGIS in the new database before restoring a schema-filtered dump.
Never restore over the live database. Run `pipeline models --port los_angeles --start 2024-01-01
--end 2024-01-08 --local` with the history database selected (the end is exclusive).
Publish only a successful 168-row
report to `data/status/history-models.json`. Copy `target/static_index.html`, which embeds
the manifest/catalog, to `data/history/index.html`; `target/index.html` needs external JSON
files and is unsuitable for this single-document route.
Set `PORTWATCH_HISTORY_DB_NAME=portwatch_history_7d`. The pipeline page reads these bounded
results; the map retains its original bounded replay. No dbt scheduler or service is added.
Cloud dbt logs, manifests and result files use `PORTWATCH_DBT_ARTIFACTS_DIR=data/models`
on the existing persistent volume; local runs keep `artifacts/models` by default.

The API's raw-byte checksum cache lasts at most five minutes and invalidates when file
identity, size or timestamps change. Manifests are inspected on each poll. Explicit CLI
audits and all writers hash bytes directly. Live cleaning scans only its source's live tree
and bulk-reuses committed counts only after profile enrichment completed; interrupted
enrichment still resumes. This prevents seven-day NOAA files from entering every minute job.

The runtime owns a single mounted-volume OS lock, starts its private Prefect server, then
recovers only its six exact deployments before starting its runner. It checks deployment
flow names and port parameters, takes the existing per-port SQL mutation lock, and re-reads
each state before cancelling obsolete schedules or marking pre-start active attempts crashed.
Current attempts and foreign deployments are excluded. An already running cloud Prefect
server fails closed. Server/runner loss exits the runtime; the API supervisor exits the
container with failure so Railway restarts the entire owned process tree. Deployment overlap
must stay zero. Local collectors must remain stopped while cloud collectors run.

Hourly maintenance truncates diagnostic logs larger than 5MiB, retaining one 1MiB tail,
and retains seven days
of terminal Prefect metadata for these deployments. Business audits and source bytes are not
deleted. It creates daily `pg_dump -Fc` copies on the existing backend volume, retaining two
per database, with SHA-256 receipts, a 512MiB/file abort threshold, ten-minute timeout and the existing
1GiB free-space reserve. Disposable activity-state projections are omitted; they can be
recomputed. Check `data/status/maintenance.json`; a missing/recently failed backup is not
backup success. Native Railway backups were rejected as Pro-only, so no plan upgrade or
additional storage service was created. A backup on the second Railway volume protects
against database-volume failure, but is not independent protection against account/provider
loss; keep an off-cloud copy. Test restores in an explicitly owned temporary database.

History and compressed copies add roughly $0.3–0.6/month of used storage at
[current Railway volume rates](https://docs.railway.com/pricing/plans); finite import/model
execution has a small one-time usage cost. Actual usage remains metered: this is not a hard spending
cap. Monitor the two fixed volumes, source growth and CPU/RAM trend. Source retention beyond
the available capacity still requires an archive policy; disk pressure stops collection
before losing preserved bytes. Do not silently delete original data to claim endless uptime.
