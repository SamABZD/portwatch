# Deployment and operations

The owner selected Vercel for the Next.js frontend and their existing paid Railway Hobby
workspace for the continuous Python backend and PostgreSQL/PostGIS. Local operation remains
available at $0; cloud resource consumption is billed by Railway under that existing plan.

| Component | Endpoint / storage |
| --- | --- |
| Frontend | [PortWatch](https://portwatch-iota.vercel.app), Vercel `abzd/portwatch` |
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

Backend live deployment: `c7394e40-070e-4cd2-bde5-5df1820b9fa5`; database deployment:
`57033f2a-b959-4c82-8860-a4205d4115f9`, both SUCCESS. The Next.js production deployment
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

Railway health check: `/readyz`,600s startup allowance; restart on failure, at most3 retries;
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
For abrupt failures, follow [RUNBOOK](../docs/RUNBOOK.md); stale Prefect state/schedule cleanup
remains an operator task. Back up PostgreSQL and the immutable backend volume together;
the existing verified restore procedure is in [HEALTH_RECOVERY](../docs/HEALTH_RECOVERY.md).

`Dockerfile.frontend` provides an optional standalone Node container. Its production Next.js
build was checked locally; the deployed frontend uses Vercel's Next.js integration instead.
