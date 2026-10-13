# Live vessels, multiple ports and pipeline evidence

Current hosting follow-up (2026-10-13): [PortWatch](https://portwatch-iota.vercel.app) runs
with a Vercel frontend and continuous Railway backend/private PostGIS in the owner's paid
workspace. Both ports were verified receiving reports and processing minute jobs. The local
runtime is stopped; leave it stopped while cloud collection runs. See [deployment operations](../deploy/README.md)
for actual endpoints, storage and safe handover. The local implementation record below
describes its earlier stage, before that deployment. Actions are disabled by the owner.

Implemented locally on2026-10-12 before final audit and portfolio publication. User priorities
skip Step14 academy submission and Step17 scale study, and move Step15 port generalization
and these additions before Step14A. Step13 final audit is complete locally; see
[RELEASE_CHECK.md](RELEASE_CHECK.md). Next is Step14A publication and remaining public hosting. Step16 history/dbt remains
an optional, unimplemented extension. No GitHub push, release or public hosting happened here.

## Open and operate

- Map: http://127.0.0.1:3000 — Live by default in DB mode; saved positions refresh every30 seconds. Prepared sessions select Historical automatically.
- Pipeline: http://127.0.0.1:3000/pipeline — current measurements and scoped workflow runs.
- Project: `.`.
- Ports: Los Angeles and Oakland. Frozen NOAA replay:2024-01-01 through2024-01-02 UTC.

From the project directory, with private `.env` configured and PostgreSQL running:

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
# Terminal1: read-only query API; keep running.
.\.venv\Scripts\python.exe -m portwatch demo serve
```

```powershell
# Terminal2, project directory: collectors and existing Prefect processing.
.\.venv\Scripts\python.exe -m portwatch.runtime
# Optional single port: append --ports oakland
```

```powershell
# Terminal3: frontend.
Set-Location -LiteralPath './web'
npm ci
npm run build
npm run start
```

One runtime owns a lock, at most3 selected port collectors, and a Prefect runner with3 worker
slots. It reuses the existing local Prefect API4200 or starts an owned local API. Each collector
uses one compressed AISStream connection. Processing runs every minute; weather/news hourly.
Weather anchors differ by2 minutes per port and news by20 minutes to respect global cooldowns
without systematically starving later ports. Provider errors and Retry-After still apply.
A30-second UI poll does not guarantee30-second source latency.

Stop from another terminal, or Ctrl+C in the runtime terminal:

```powershell
.\.venv\Scripts\python.exe -m portwatch.runtime --stop
```

This drains/finalizes collectors, pauses project deployments, stops owned children and records
stopped state. Preexisting PostgreSQL/Prefect servers stay running. Abrupt termination uses
existing spool recovery. Logs/status are ignored under `artifacts/live`; raw data remains in
ignored `data`. A disk reserve check runs before startup and during operation, stopping below
the configured1GiB reserve. Source evidence is never silently deleted. Storage growth and
off-device backups still require operation.

## What is live

These are genuine received AIS observations within selected port collection regions, including
offshore/bay approaches. Coverage is incomplete and source-dependent; this is not every ship
across all oceans. No interpolated movement or invented arrivals are shown. Live timestamps
remain explicitly local receipt fallback; provider metadata timestamps are preserved separately.
Display queries contain the current hour and6 prior UTC hours, with bounded position limits.

The computer must remain awake and connected. Sleeping, closing the runtime or losing
connectivity interrupts collection. A Next.js deployment alone cannot collect/process AIS.
Public24/7 operation still requires an always-on host, collector/processing, PostgreSQL/PostGIS,
raw storage, API and frontend, with private networking, HTTPS and backups. No hosting account
or payment was created; permanently free public24/7 operation is not promised. AISStream
documents no SLA or durable replay guarantee.

## Vessel evidence and port isolation

The inspector adds course/heading, five-decimal coordinates and timed reported profiles:
name, IMO/call sign, dimensions, draught, destination and ETA when received. Missing fields stay
missing. AIS ETA has month/day/time and no year; no year or confirmed destination port is
inferred. ClassB parts update only when valid. Historical properties come from genuine NOAA
rows. Profiles join only at or before the replay cutoff, preventing future voyage leakage.

Migration008 adds separate profile evidence and enrichment checkpoints;001–007 stay unchanged.
Only accepted/duplicate source records or preserved valid static messages become profiles.
Historical position counts and inference rules remain unchanged by enrichment.

Port YAML/demo/GeoJSON controls bounds, names, timezone, weather/news and zones. Queries and
cache revisions scope source/dataset/port/version. The same MMSI may occur at multiple ports.
The national NOAA file has distinct regional batch identities/manifests with the same checksum,
physically sharing verified immutable bytes by hard link. Regional extractions and analytics
remain separate. Source refresh creates a new immutable revision.

Oakland uses broad manually authored harbor-vicinity and regional-approach polygons, referenced
to the official2024 Port properties map. They include land/adjacent water, omit exact terminal
polygons, and are neither surveyed nor official operational boundaries. Its visits/dwell and
low-speed candidates are experimental vicinity metrics, not verified calls or queues.

## Public pipeline view

The page explains collect → preserve → validate → transform → serve with code/documentation
links. Its selectable architecture drawing connects NOAA, AISStream and context connectors to
raw files, Python validation, PostgreSQL/PostGIS, FastAPI and Next.js. Dashed lines distinguish
Prefect scheduling from saved-data flow; mobile uses a vertical drawing. Component explanations
remain available when current health is disconnected. It shows collector/context status, historical/live canonical counts, duplicates,
quarantine, backlog, commit times, measured receipt-to-clean latency and recent workflows.
Only workflows scoped to that port appear under its heading; unscoped manual audits are excluded.
Source failures and unavailable context stay visible. The view exposes no mutation controls,
raw messages, private paths, keys or stored exception bodies. Code links point to `main` and
become current with the pending portfolio publication.

## Verification

```powershell
.\.venv\Scripts\python.exe scripts/validate.py
.\.venv\Scripts\python.exe -X utf8 scripts/verify_demo_browser.py
.\.venv\Scripts\python.exe -X utf8 -m scripts.verify_multiport_browser
```

The third command requires genuine active live reports from both ports; it makes no provider
requests itself. Browser automation blocks external tiles and checks the local map fallback.
Synthetic records stay in owned temporary fixtures, never project data. See `RESULTS.md` and
ignored `artifacts/multiport*` for measured evidence and limitations.

Step13 adds owned fresh-DB historical reproduction and portable browser verification; see
[RUNBOOK.md](RUNBOOK.md#step13-audit-reproduction). Its242-test result supersedes earlier
stage counts. Hosted CI and Docker startup remain unverified; the native setup was exercised.
