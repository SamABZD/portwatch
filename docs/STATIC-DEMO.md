# Static historical demo

The public deployment is a saved-data demonstration. It has no running Python service, Postgres connection, scheduler or API proxy. It displays genuine NOAA OCM historical observations for Los Angeles and Oakland from January 1 through January 2, 2024 UTC. Weather is saved retrospective ERA5 context. It does not show present-day conditions.

Both https://portwatch-demo.samabouzeidd.workers.dev and https://portwatch-iota.vercel.app serve the static build. The existing Vercel project uses a prebuilt static deployment to preserve its public address; automatic Git deployments are disabled. Cloudflare uses `web/wrangler.jsonc` with static assets only. Neither deployment uses server functions or database bindings.

## Reproduce the snapshot

Use the existing `portwatch demo export --output ...` command with the desired `PORTWATCH_PORT_CONFIG` and `PORTWATCH_DEMO_CONFIG` paths and database configuration to produce a prepared export and its SHA-256 sidecar for each port. Use `configs/ports/los_angeles.yaml` with `configs/demo.yaml`, or `configs/ports/oakland.yaml` with `configs/demos/oakland.yaml`. Keep credentials outside the repository. Alternatively, use the protected prepared exports from the project backup. Do not publish raw database or filesystem archives.

From the repository root, with the Python project installed:

```sh
python scripts/export-static-demo.py \
  --snapshot /private/los_angeles.json \
  --snapshot /private/oakland.json \
  --output web/public/snapshot
cd web
npm ci
npm run build
```

The output directory must be new. Prepared source files must have valid checksum sidecars; `load_prepared` verifies them. Generated snapshots and `web/out` are ignored by Git. A source checkout alone needs the private prepared exports or a database export before building a usable demo. Snapshot generation computes all frames offline and can take substantial CPU time.

The exporter calls the existing replay engine for each port, five-minute cutoff from 0 through 1440, and freshness threshold 5/15/30/60 minutes: 2,312 responses. Each minute packs the four variants together, including their exact vessel ordering, metrics and staleness flags. It reconstructs each response and requires equality before writing deterministic gzip assets. The browser only selects a saved response; it does not recalculate port analytics.

The October 23 export contains 583 data assets totaling 11,432,474 bytes. This includes 62,157 Los Angeles and 52,783 Oakland observations in the prepared datasets. A checksum index accompanies the static files. Attribution and dataset dates remain visible. Replay supports play/pause, search, vessel selection, trails, approximate zones, stale points and optional OpenStreetMap tiles. No current weather or news is fetched. Unknown measurements remain unknown.

Build and deploy with an authorized Cloudflare account using Wrangler from a trusted installation:

```sh
cd web
npx wrangler deploy --config wrangler.jsonc
```

The Worker name is `portwatch-demo`. Use Workers Free and static assets only. Do not enable paid bindings or upgrade the account. Keep deployment credentials in protected storage. The existing Vercel URL is maintained by a separate prebuilt static artifact; preserve its project linkage and static routing for `/` and `/pipeline` when replacing it.

## Validation and recovery

Local validation passed: 236 Python tests (integration tests excluded), 15 Node tests for snapshot queries, view scope and polling, Next static build/typecheck, and npm audit with zero reported vulnerabilities. All 2,312 packed responses were compared against the original engine responses. Published root and pipeline routes, gzip data and real 404 responses were checked. Browser verification covered both ports, freshness selection, vessel selection and replay advancing through saved cutoffs.

Protected backups retain both PortWatch PostgreSQL databases, all persistent application files, prepared exports, service configuration and source history. The final main-database restore matched all 66 frozen-source table counts; the history restore matched all 38. These archives contain private operational data and must remain outside Git and public hosting.

Railway app and database compute are retired after backup verification. Volumes remain for recovery and may still incur storage charges. Recovery requires redeploying the pipeline, restoring verified data and reconnecting appropriate source credentials; it is not an automatic fallback. Never connect the public static demo to an old endpoint as an error fallback. The seven-day SQL output is retained in the private file/database archive and SQL sources; it is not part of the public one-day map snapshot.
