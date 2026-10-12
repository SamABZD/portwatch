# Next.js map and replay

Current live/multiport operation, vessel profiles and pipeline page: [LIVE_MULTIPORT.md](LIVE_MULTIPORT.md). Original Step12 results below precede this extension; live queries now contain the current and6 prior UTC hours.

Step12 implements the user's Next.js choice, replacing the original Streamlit/PyDeck launch.
The single-page UI lives in `web/`; `src/portwatch/demo` provides the read-only FastAPI query
service. The frozen January1,2024 NOAA day is the default. No render starts collection,
migrations, downloads, Prefect jobs or durable transformations.

## Open and develop

The current local app is at **http://127.0.0.1:3000**, with query API at127.0.0.1:8000.
For a fresh start, use two terminals. Existing PostgreSQL must be running for database mode;
the API does not depend on Prefect or a live AIS key.

Terminal1:

```powershell
Set-Location -LiteralPath 'C:\Users\samab\dataEngineering\portwatch'
.\.bootstrap-venv\Scripts\uv.exe sync --locked --extra dev
.\.venv\Scripts\python.exe -m portwatch demo serve --listen-port 8000
```

Terminal2:

```powershell
Set-Location -LiteralPath 'C:\Users\samab\dataEngineering\portwatch\web'
npm ci
npm run typecheck
npm run build
npm run start
```

Node20.9+ is required; this machine uses24.15.0. Locked versions include Next16.4.0,
React19.3.0, MapLibre6.14.0, FastAPI0.143.0 and Uvicorn0.54.0. `npm ci` also prepares the
installed MapLibre module worker and its BSD license under ignored `public/vendor/maplibre`.
The worker has an explicit same-origin URL; it is not an assumed bundler-relative asset.
`web/.env.example` documents the server-only `PORTWATCH_QUERY_URL` override. Database/AIS
credentials remain in the Python process and private root `.env`.

While the saved production demo is already running on3000, edit `web/src` and use a separate
development port to avoid a collision:

```powershell
npm run dev -- --port 3001
```

Open http://127.0.0.1:3001 for hot reload; it uses the same query API. Stop each process with
Ctrl+C in its owning terminal. Do not stop unrelated services or reset databases. The runbook
and final process-state evidence identify which project processes are running. These are local
processes, not boot services or a published site.

## Inspect the demo

- Switch Historical replay / Live. Source, period, timezone and optional provenance remain explicit.
- Move the5-minute slider with mouse or keyboard; Home/End jump to day boundaries. Play/pause,
  relative speed, reset and UTC/Los Angeles timezone controls do not change source timestamps.
- Select an actual point or search/select an observed MMSI. Inspect type when present in that
  report, speed, report time/age, zone, current waiting and prefix-only waiting/visit history.
- Toggle approximate zones, observed trails, stale reports and basemap. The default15-minute
  display cutoff can be5/15/30/60 minutes. Stale points are hidden by default with a legend.
- Read fresh reporting/harbor counts, currently qualified waiting candidates and completed dwell
  midpoint median/sample size. The chart uses completed UTC-hour bins only. Missing values stay unknown.
- Expand pipeline health for current collector/source/data-age/processing states. Historical
  readiness is separate from a stopped collector or stale/rate-limited current context.

At noon12:30UTC, the real prefix contains32,632 observations,124 vessels seen,99 fresh/harbor
reporting vessels and6 completed dwell samples. The full day contains62,157 observations and14
completed dwell samples. There are3 actual reports exactly at midnight; reset does not invent an
empty starting frame. Saved live reports are currently stale: fresh occupancy/waiting remain
unknown, and an empty fresh-data window does not establish zero traffic.

## Query and replay contracts

The API has three GET endpoints: `/api/catalog`, `/api/frame` and `/api/health`. The Next.js
server proxies only these named queries. It returns a helpful disconnected/unavailable state on
failure. Python binds loopback, uses parameterized source/version/date queries, and loads only
cleaned port subsets, with at most150,000 positions and a fixed historical day/two-day live window.
Persistent reads use a read-only repeatable-read transaction with a15-second statement timeout.
No national raw observations are loaded into browser data. Operational health may hash saved
raw files as part of its existing integrity check; it never exposes or parses national raw rows.

Dataset descriptors are rechecked at most every10 seconds. Their revision includes clean input
hashes/status/commit times, source/dataset, geofence content, analytical rules, period and selected
context revisions. A maximum128-frame cache keys this revision, exact UTC cutoff, display
staleness and trail limit. Publication/late input changes cannot reuse an earlier revision's frame.
The browser aborts obsolete requests and withholds results whose cutoff/mode differs from the
selected controls. One playback interval is cleaned up on pause/mode change/unmount; live polling
and health polling are separate, bounded intervals.

Each vessel's latest actual timestamp group at or before the cutoff supplies its location.
Conflicting coordinates are withheld rather than averaged. Trail segments break at actual gaps
over the existing15-minute rule before display-only downsampling to at most32 observed points
per segment. There is no interpolation of vessel movement. Analytical inference uses the intended
full cleaned prefix through the existing `infer_track` implementation.

Waiting qualification/association and visit completion are recomputed from that prefix. A later
completed episode is never shown at its first report. Current waiting counts mean fresh, currently
qualified low-speed candidates; historical episode/association counts are separate in the detail.
Neither is a confirmed terminal queue. Dwell pools completed prefix episodes and reports the
median of interval midpoints and sample size; partial episodes cannot enter it.

These map snapshots are **at the selected cutoff**, unlike persisted mart snapshots at hour start.
Thus12:30 can have99 fresh vessels while the12:00 mart has98. The saved analytical-version ID
references rule lineage; exact cutoff/display parameters remain explicit in the frame/cache key.
Activity counts are independently reconciled against `read_prefix_hours`; charts omit the partial
current hour. Historical weather matches the pinned reanalysis period and is labeled retrospective,
model-derived context. Current articles never appear as historical disruption evidence. Recent
news keeps valid-empty responses separate from failed source requests.

Vessel type comes from the selected report. Untimed static-message data is not joined into an
earlier replay; some live types remain unknown. Last successful processing is the later recorded
clean/mart publication for the selected source, not a claim of current collection.
Exception bodies/credentials are masked before errors reach Uvicorn; access-query logging is off.
Windows collector status replacement tolerates brief concurrent-reader sharing/access errors
with at most5 attempts and150ms of joined I/O-thread waits. Permanent failures still surface;
the previous published status is preserved. Real source files are not modified by this retry.

## Portable historical mode

The prepared export contains only cleaned regional observations, approximate zones, rules,
period/version provenance, declared input coverage and matching saved weather. It has a SHA-256
sidecar and refuses existing paths. It is generated/ignored, not committed or published.

```powershell
Set-Location -LiteralPath 'C:\Users\samab\dataEngineering\portwatch'
# This stage already created the first file; use a NEW path for another export.
.\.venv\Scripts\python.exe -m portwatch demo export --output data/demo/another-los-angeles-snapshot.json
.\.venv\Scripts\python.exe -m portwatch demo serve --listen-port 8000 --prepared data/demo/los-angeles-2024-01-01.json
```

Run one API instance on8000 at a time. The second command can replace database mode after its
owning process stops. Actual verification used an unset AIS key and database configured to closed
port9; historical requests succeeded with `database=not_used`. Live mode is unavailable in a
prepared snapshot and current pipeline health is explicitly unconnected. The export is22,749,112
bytes and round-trips to the same noon vessels/metrics. Publishing/redistribution and hosting are
later work; no hosting account or deployment was created.

## Basemap and verification

Standard OpenStreetMap browser tiles are optional and need internet. Visible attribution,
normal browser caching/referer and a configurable anonymous tile URL are preserved. No bulk
download, offline tile pack or automatic pan/prefetch is provided. Tile/WebGL failure switches to
the local Mercator polygon/point map and disposes the failed renderer. Basemap can be disabled.
The geometry fallback does not invent a coastline. See [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/)
and [MapLibre worker API](https://maplibre.org/maplibre-gl-js/docs/API/functions/setWorkerUrl/).
Next.js [client boundaries](https://nextjs.org/docs/app/getting-started/server-and-client-components)
and [route handlers](https://nextjs.org/docs/app/api-reference/file-conventions/route) inform the implementation.

```powershell
.\.venv\Scripts\python.exe scripts/verify_replay.py
.\.venv\Scripts\python.exe -X utf8 scripts/verify_demo_browser.py
.\.bootstrap-venv\Scripts\uv.exe run --locked --extra dev python scripts/validate.py
```

Run browser verification against the production app. It uses an existing local Chromium when
available; otherwise install it explicitly with `python -m playwright install chromium` or supply
`--browser-executable`. Automated tests block external tiles and exercise actual fallback,
controls, selection, empty fresh-data window, source failure/retry and responsive viewports.
They include an explicitly isolated response-status fixture for valid-empty news. No invented
records enter the project database. Screenshots/logs/results live in ignored `artifacts/step12`;
public copies are in this chat's `outputs`. Cached HTTP frame timings are measured on this local
machine, not production service objectives. Saved raw/business/mart hashes and audit counts are
checked unchanged. See [RESULTS.md](RESULTS.md) for observed measurements.
