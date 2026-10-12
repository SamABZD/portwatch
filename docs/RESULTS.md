# Measured results

## Step12: Next.js map/replay (2026-10-12)

- Implemented the user's Next.js preference on `feat/map-replay`, from Step11 checkpoint
  `da4c315ae9a381d2c9a3d45ffd353b18c91cafef`. Next16.4.0/React19.3.0/TypeScript frontend,
  MapLibre6.14.0 plus local vector fallback, and loopback FastAPI0.143/Uvicorn0.54 query API.
  Restrained map direction was confirmed; PRODUCT/DESIGN capture the scope and interface.
  No migration, provider acquisition, account/payment change, remote push or deployment.
- `npm ci`/install, `npm run typecheck` and `npm run build`: production app compiles and serves
  `/`, read-only query proxy and SVG icon. `npm audit --omit=dev --audit-level=high`:0 vulnerabilities.
  Existing Node24.15.0 satisfies the20.9+ requirement. MapLibre's default bundler worker URL
  failed during actual browser use; an explicitly served installed module worker/BSD license
  fixes loading. Failed tile rendering now disposes its worker/map and preserves local geometry.
- `python scripts/verify_replay.py`: PASS. Real event-time cutoffs00:00,12:30,20:40,20:45 and
  end-of-day have no future positions/trail times/weather/chart hours. Noon32,632 observations,
  124 vessels seen,99 fresh/harbor reporting vessels,6 completed dwell samples; full day62,157
  observations/14 completed dwell samples. At00:00 there are3 actual reports, not an empty prefix.
  Activity reconciles exactly to completed `read_prefix_hours` SQL output. Current snapshots at
  the cutoff differ deliberately from mart hour-start snapshots (noon12:30=99 vs12:00=98).
- A deliberately uncached fractional-second HTTP frame took **1.070s** and its cached repeat
  **0.049s**. Five other cached HTTP cutoffs remained under2s; first-request cache-hit flags are
  recorded rather than assuming every first request is cold. Current live query0.154s,26 saved
  positions stale, fresh occupancy/waiting unknown. These are local measurements, not production SLAs.
- Database/source business hash stays
  `355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`; mart hash stays
  `6f6ca8ecaa8e3824040aa481cd85d88cd0b52990ba2a0307daf14001ad223112`; SQL audit count unchanged.
  API uses prepared bounded data, prefix inference and revision-aware caches, never starts ingestion.
- `demo export --output data/demo/los-angeles-2024-01-01.json`:22,749,112-byte regional export
  plus SHA-256 sidecar, ignored/private. Round-trip noon vessels and metrics match. Actual HTTP
  server verification with database set to closed port9 and AIS key unset succeeded with
  `database=not_used`; live/current health explicitly unavailable. Temporary8077 server stopped.
- `python -X utf8 scripts/verify_demo_browser.py`: **12 interaction/state checks PASS**,
  Chromium151.0.7922.34, production app;0 page errors,0 external tiles fetched in automation.
  Actual map/list selection/search, overlays/trails/stale/basemap, keyboard slider/reset, complete
  day/no-completed-hour prefix, speed/play/stable pause, timezone/staleness, live empty fresh window,
  historical news exclusion, source429, disconnected/failed-frame retry, reduced motion and
  desktop1365x900/laptop1280x720/tablet834x1112/mobile390x844. One isolated response-status fixture
  verifies valid-empty news independently of provider failure; no invented project records.
  Eight actual screenshots saved and visually inspected for desktop/laptop/mobile layout.
- Unit/integration cases verify future qualification/association/completion exclusion, observed
  gap-broken trails, conflicting positions withheld, unknown intervals, cache revision/display
  parameters, read-only SQL/source isolation, missing preparation and checksum/error masking.
  Error middleware catches exceptions before Uvicorn can log original private exception text.
- A final concurrent validation exposed a Windows status-file reader/replacement collision.
  Reproduced `PermissionError`/WinError5 with an owned synthetic file held open. Publisher now
  retries only Windows5/32/33, bounded to5 attempts/150ms of joined-thread waits; persistent and
  other failures still raise and preserve previous status. Actual30ms reader recovered in0.037s.
  Five regression cases added; focused collector suite **31 passed in8.56s**. Real collector not started.
- Final shared validator: **174 unit18.26s +55 integration46.19s =229 passed**, no selected
  skips, lint/format93 files, wheel/sdist and installed-wheel CLI/resources, seven fresh migrations/
  repeat zero. One upstream Starlette TestClient/httpx deprecation warning remains; no test failures.
  Migrations001–007 and genuine source data remain unchanged.

Evidence: ignored `artifacts/step12` replay/browser/prepared verification JSON, build/validation
logs and screenshots; public copies in chat `outputs`. Current frontend3000 and query API8000
remain running for the user, alongside preexisting PostgreSQL/Prefect API. Prefect deployments
paused; runner/real collector stopped. Historical replay remains ready despite stale forecast and
news429. Stop at Step12; next Step13 final audit. No online publication or automatic boot service.

## Step11: monitoring/recovery (2026-10-12)

- Branch `feat/monitoring-recovery` starts at Next.js preference checkpoint
  `a140c4baa5b4e638595609d283f20f4c36889126` (Step10 code `71d36b2`). Added read-only
  human/JSON health, safe structured events, backup/restore verification and owned recovery drills.
  No schema, dependency, provider request, payment, remote push or deployment was added.
- `python -m portwatch --log-json db backup --output artifacts/step11/portwatch-2026-10-12.dump`:
  **verified**,11,219,941 bytes; dump10.59s and restore16.34s. Actual separate owned DB restore
  matched27 application tables/321,972 rows and column contracts; PostGIS3.6.2 verified and DB
  removed. SHA-256 `c84c3d6647682b167831e36cca952cee0215c179cd8c191c780e40cf676b3e60`.
  Archive/manifest remain private/ignored on the same local disk. Raw/config/Prefect metadata
  and global roles are outside the archive; off-device disaster recovery remains unverified.
- `python scripts/verify_recovery.py`: **PASS in39.19s**. Genuine frozen NOAA mart reruns
  took8.18s and6.93s, each24 hours/62,157 observations with identical business rows.
  Source hash remains `355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`;
  mart hash remains `6f6ca8ecaa8e3824040aa481cd85d88cd0b52990ba2a0307daf14001ad223112`.
  Five genuine raw files remain verified; source bytes and real analytical values were preserved.
- Owned synthetic DB/file drills persisted a raw batch before registration, denied only that
  DB's connections, detected failure with unknown analytical counters, restored connections and
  processed5 positions in0.377s with unchanged raw bytes. A failed mart publication retained
  earlier valid metrics and produced a visible failed audit. A late point changed the one visit:
  5→6 observations, obsolete visit removed and hours replaced in0.114s. Malformed input retained
  original bytes/lineage with1 accepted,1 duplicate and1 quarantined `invalid_mmsi` record.
  Duplicate replay left business hashes unchanged in0.031s. Truncation was detected and the
  owned fixture restored to its original checksum. Final scoped totals7 accepted/1 duplicate/
  1 quarantined; final health healthy; owned DB removed. No synthetic data entered the main DB.
- Loopback WebSocket drill:2 connections,2 synthetic observations,1 reconnect in2.015s;
  visible disconnected/backoff/connecting/healthy/stopped snapshots and a0.310s missing
  connection interval. That interval means upstream reports unknown, not zero traffic.
  No AIS provider request or continuous real collector was started.
- The actual disconnect exercise found a shutdown hang caused by a Python3.11 Pong/cancellation
  race in `asyncio.wait_for`. Replaced the Pong wait with `asyncio.timeout` and added a
  deterministic simultaneous-completion/cancellation test. Collector-only recheck passed in
  2.012s. Synthetic timing tolerances remain bounded; production20-second ping timing unchanged.
- `uv run --locked --extra dev python scripts/validate.py`: **156 unit17.31s +53 integration
  42.55s =209 passed**, zero selected skips; lint/format81 files, wheel/sdist and installed-wheel
  CLI/resources, seven fresh migrations/repeat zero. Migrations001–007 remain unchanged.
  Tests also verify health is read-only, secret masking, active PID identity versus silence,
  disk warning, unregistered backlog, corrupted raw and archive/ownership guards.
- Real `health --port los_angeles --json` at17:19:35 UTC /20:19:35 Asia/Beirut returned exit1,
  **degraded** because current forecast is stale and the saved news429 attempt remains failed.
  Its consecutive `news_ingest` failure count is1; all other listed stages are0, running0.
  Historical `ready_historical`:62,157 accepted/canonical,2 duplicates,0 quarantined,0 backlog.
  Saved live26 positions are stale with0 backlog. Collector explicitly stopped with last genuine
  receipt2026-10-11T19:26:03.039308Z; old snapshot has no PID identity. Historical reanalysis
  remains available. `doctor` allPASS; raw inspection5 verified/0 issues; PostgreSQL PID18824
  remains running at localhost55432. Final read-only check found the existing Prefect API server
  running at127.0.0.1:4200 (PIDs8568/24468), correcting the older stopped-server record. API health
  passes and all three deployments have `paused=true`; runner/collector stopped. Server left running.

Evidence: ignored `artifacts/step11/backup.log`, archive/manifest, `recovery-verification.json`,
`recovery.log`, `collector-check.json`, `collector-check.log`, `validation.log`, `health.json` and
`health-events.log`. Public copies live in this chat's `outputs`. Recovery target covers already
durable demonstrated inputs; volatile/unreceived inputs and production recovery objectives are
excluded. Step11 complete locally; next Step12 map/replay uses the saved Next.js preference.

## Step10: orchestration/backfills (2026-10-12)

- Branch `feat/orchestration-backfills` starts from Step09 checkpoint
  `5caeeb8badd0e3010f15bb6f39457417a7b638ed`. Local/$0/CI-deferred authorization persists.
  Added migration007, finite Python workflows, local Prefect server/serving commands,
  durable step checkpoints, safe audits, bounded retries, owned locks and missing-source gaps.
- Prefect3.8.8 installed after a transient DNS download failure. Resolved its WebSocket requirement
  with locked16.1.1. Real scheduling initially failed with SQLAlchemy2.1.4 bulk hybrid setters;
  pinned2.0.x, locked2.0.54, and verified real execution after restart. Metadata uses separate local
  SQLite; no Cloud, payment or account change. A service interruption required restarting the
  existing native PostgreSQL cluster without resetting data; subsequent hashes/doctor passed.
- Complete genuine frozen NOAA/ERA5 workflow succeeds through Prefect. A controlled stop after
  raw persistence creates an interrupted audit, then resumes and validates24 hours/62,157 positions,
  56 entry/35 exit confirmations and14 completed dwell samples. Two exact backfills for
  `[2024-01-01,2024-01-02)` succeed with reused clean/publication checkpoints.
  Source business hash remains `355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`;
  historical mart hash remains `6f6ca8ecaa8e3824040aa481cd85d88cd0b52990ba2a0307daf14001ad223112`.
  Five reconciliation groups have zero violations. Real verifier elapsed78.84s.
- A competing real request is rejected while its workflow is owned, without checkpoint writes.
  Synthetic tests cover rollback/checkpoint resumption, raw-only interruption before any analytics,
  attempts/durations, session-loss lock recovery, failed-date isolation/source404 gap, and midnight
  context. The synthetic cross-day visit retains its prior-day entry, has no day2 arrival, and
  one day2 exit/completed sample; canonical dataset lineage stays unchanged.
- Live workflow processes two saved batches/26 positions, with0 new positions and explicit stale
  event lag. It publishes20 elapsed-hour rows under a separate forecast product; most earlier
  unknown empty hours remain null. It never joins historical NOAA or recent articles.
  Continuous collection was not started, so successful jobs do not imply current AIS coverage.
- Actual automatically scheduled flow `01a130c2-3367-7c7d-9daa-9860b7ea2012` and its processing task
  both completed. Started `2026-10-12T15:49:32.882542Z`, ended `15:49:33.825234Z` (UTC), corresponding
  to18:49:32–18:49:33 in Asia/Beirut. `auto_scheduled=true` is confirmed through the API.
  Minute executions repeatedly completed; schedule registration alone was not accepted as proof.
  Independent hourly weather/news deployments are configured; they were paused before their next
  tick. Their real scheduled success is not claimed. Last real news acquisition remains429.
- Final shared validator: **143 unit26.71s +51 integration40.25s =194 passed**, no selected skips,
  lint/format72 Python files, wheel/sdist, actual wheel CLI/resource import and all seven fresh
  migrations; repeat migration applies zero. A prior30ms heartbeat fixture assumption was widened
  for the reconnect/backoff test while preserving its connection/session/count assertions.
  Pipeline locks now include schema to isolate fixture-owned tables from real workflow tables.
- Prefect schedules paused and temporary runner/server stopped after verification. PostgreSQL stays
  running; doctor allPASS and five genuine raw batches verify with zero issues. No reset, provider
  redownload, push, merge, hosted deployment or payment. Ignored evidence:
  `artifacts/step10/verification.json`, `schedule-verification.json`, `validation.log`.
  [ORCHESTRATION.md](ORCHESTRATION.md) and [RUNBOOK.md](RUNBOOK.md) give exact recovery/process commands.
  Next is Step11 monitoring/recovery; stop at Step10 here.

## Step09: analytical tables (2026-10-12)

- Published24 UTC hourly rows for the frozen January1,2024 NOAA series, on local branch
  `feat/analytics-marts` from Step08 checkpoint `8dd89ba51922cf972504a7bc188e67e558072886`.
  Partition `44c6625e37b5c9dc4c61bfc54d4183c3bc1d9f7dfe86f6ef7e43596f15c49081`, analytical
  version `ec3a3ebb7379e176f87f343e69474da992b3aa069bea9816e44f58f064f8a016`.
- Canonical source count equals hourly observation sum: **62,157**. **56 known-boundary entry
  confirmations**, **35 exit confirmations**, **14 completed dwell samples** reconcile exactly
  to the selected visit rules. These are inferred observation transitions, not official port calls.
  Overall completed midpoint median from raw completed episodes: **4,495.5 seconds**; do not
  average hourly medians. The216 partial episodes stay outside completed-duration medians.
- Hour00 reports111 distinct MMSIs during the hour versus3 fresh reporting vessels at00:00; snapshot
  lookback is incomplete without preceding-day input. Hour01 reports104 distinct MMSIs versus102
  fresh reporting vessels,97 within primary port zones. Thus hourly activity and simultaneous
  observed occupancy are measurably different, and the first snapshot is a partial lower bound.
- One pilot-vessel candidate contributes **two candidate-vessel hours**,19 and20, with one
  last-observed-hour duration sample at20:00: **3,724 seconds**, completed under candidate boundary
  rules. Hour19 remains regional; by hour20's end the association is known. This is no cargo queue
  or delay-causality evidence. Actual replay at20:40 is regional; at20:43:02 association appears.
- All24 hours join the exact saved ERA5 batch from Step08, with unsupported gusts null. Recent
  forecast/news are excluded. Dataset quarantined-record count is0; hourly reject zeros reconcile.
  Five independent reconciliation groups report zero failures: observations, distinct snapshot,
  weather revision/values, visit events/dwell samples and dataset rejects.
- Two complete reruns have identical business hash
  `6f6ca8ecaa8e3824040aa481cd85d88cd0b52990ba2a0307daf14001ad223112`.
  Read-only12:30 prefix:13 rows/12 completed hours,32,632 observations; partial hour12 has1,317
  reports. Prefix20:40 has53,436 reports;20:43:02 has53,566. Source, visits, stored marts and run
  audit counts remain unchanged by prefix reads. Full stored data is withheld at noon replay.
- Shared validator **PASS**:139 unit tests31.97s and44 database integration tests34.07s,
  zero selected skips/failures. Lint/format passed65 Python files. Wheel/sdist, actual isolated-wheel
  CLI import (which loads the packaged hourly SQL resource), six migrations and fresh/repeated
  migration checks passed. Focused final mart/visit/migration subset:28 passed in18.78s.

Real evidence is retained in ignored `artifacts/step09/verification.json`; validation transcript
in `artifacts/step09/validation.log`. Original source hash is still
`355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`, visit hash still
`76f96085b195058b60af35ad72b4b33de761dcbf61a3069baa1b67f73cb525da`; five raw batches remain
verified. [ANALYTICS.md](ANALYTICS.md) defines every field and provides five executable SQL examples.
No provider requests, dependency additions, frontend, deployment, paid resources, push or merge.

The final verifier also executes all five documented SQL examples:24 activity,2 waiting,
24 dwell,24 freshness and1 reject-summary rows, with exact source/version filters. Latest full
real verification took37.40s. Final lint/format passed66 files, doctor allPASS and repeat migration
applied zero; original raw storage still has five complete batches/zero issues.

## Step08: weather/news context (2026-10-12)

- Historical Open-Meteo ERA5 GET200 at12:25:00.890960UTC: **24 hourly points**, January1,2024
  00:00–23:00UTC, matching the frozen AIS interval. Wind values0.85–3.92m/s; all gusts null/not
  requested for this product; missing issue time stays null. Actual coarse cell33.5 / −118.25.
  Raw batch `0a5dd14b907ec067c4e16a4780ef17101bc4e61d78f4c95de03ee38db00287e2`.
- Current forecast GET200 at12:26:18.591898UTC: **48 hourly points**, October12–13,2026 UTC,
  speed/gusts/precipitation, requested best_match; actual cell33.72767 / −118.24222. No issue/run
  timestamp returned. Raw batch `0629b82d62047dc355289145fb2282776f5fa5475576e6f083a1a5d5c396c127`.
- GDELT's narrow 24-hour query for October11–12,2026 returned **429**. Attempt status failed,
  error `source_rate_limited`; no saved article response and no successful-empty claim. Local next
  attempt no earlier than12:40:00.277470UTC. Successful nonempty/empty news handling is covered
  synthetically; real2026 article retrieval remains unverified. No immediate retries or paid service.
- Both historical cached replay passes reused the same batch/24 rows with no HTTP and unchanged
  weather business hash `5a2005506deaa95b4c5d053ebef1a31c2b1d79616557d8ed7b843724916e519f`.
  Read-only12:30UTC prefix returns13 hours and no future valid timestamp. Recent news is always
  `excluded_recent_source`; current forecast cannot enter the historical partition.
- Verifier checks: zero wrong-partition selections, zero weather timestamps outside request bounds,
  zero failed responses mislabelled empty, zero unfinished context attempts. All **five real raw
  batches** verify complete, including the original three AIS batches. Source hash remains
  `355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`; visit hash remains
  `76f96085b195058b60af35ad72b4b33de761dcbf61a3069baa1b67f73cb525da`.
- Final shared validator: **134 unit tests in28.60s and32 database integration tests in20.63s**,
  zero selected skips/failures. Ruff lint/format passed61 Python files. Wheel/sdist build, isolated
  wheel import/CLI, all five SQL resources and fresh/repeated migrations passed. Focused initial
  context suite31 passed; subsequent context/bookkeeping36 passed before recovery/corruption/
  transaction and incomplete-transfer regressions were added. Final connector unit subset25 passed.

Original responses remain ignored under `data/raw/`; request parameters, attempts, selection and
normalized rows reside in the native PostgreSQL. `artifacts/step08/verification.json` records the
exact acquisitions, periods, receipts, units, grid coordinates, values, hashes and invariants.
Interpretation, attribution, cache/cooldown and revision policies are in [CONTEXT.md](CONTEXT.md).
No causal delay claim, confirmed disruption, mart, frontend, deployment, paid spend, push or merge.

## Step 07: visits and candidate waiting (2026-10-12)

All 62,157 frozen historical positions were processed in 141 source-separated vessel histories.
Experimental `visits-v1` produces **230 inferred episodes: 14 completed, 216 partial**, and **one
qualified low-speed candidate**. 44 tracks are unknown at the dataset cutoff. Persistence is two
distinct observation times spanning two minutes. Long gaps split incomplete fragments; unknown
arrival/departure and partial observation proxies remain separate from completed duration ranges.

The candidate spans 3,724 seconds across 57 reports, with speeds0–0.8 knots and max adjacent gap78s.
It reports type50 (pilot vessel per the [USCG table](https://navcen.uscg.gov/ais-class-a-reports)),
so it does not establish cargo congestion or actual queue time. Waiting qualifies19:59:58 UTC;
association with the subsequent approximate harbor visit is supported20:43:01. At20:40 the
read-only prefix keeps it regional; after20:43:01 it may associate. Full results are retrospective.

Two recomputations have identical business SHA-256
`76f96085b195058b60af35ad72b4b33de761dcbf61a3069baa1b67f73cb525da`. Eight independent SQL
invariant groups have zero violations. Source hash remains
`355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`.
The noon prefix has 31,316 positions, 124 tracks, six completed and158 partial visits, no candidate,
and preserves stored rows. Visually reviewed four real tracks; exact evidence, disagreements and
no-ground-truth limitations are in [VISITS.md](VISITS.md) and ignored `artifacts/step07/`.

The shared full validator passed **109 unit tests (27.08s)** and **22 database tests (15.43s)**,
with no selected skips, lint/format, source/wheel packaging and actual wheel import, all four SQL
resources and fresh/repeated migration checks. Late-point fixtures verify obsolete IDs are removed;
rollback restores the previous snapshot. No dependency changes, provider downloads, remote CI
success, frontend, hosting or spending were added. Step08 is next.

## Step 06: versioned approximate geospatial zones (2026-10-12)

Local implementation completed with the user's authorization to continue at $0 while the
GitHub account lock remains unresolved. Six approximate polygons in `la-approx-v1` classify all
62,157 historical positions: **61,712 inside**, **445 outside**, **72,237 memberships**, and
**10,525 overlapping positions**. Candidate offshore memberships are regional. None of these
counts establish visits, berth occupancy or waiting time.

Two replays independently checked every membership with Shapely: zero mismatches and no new rows.
Assignment SHA-256: `be40f26d8b7786456f876e7b969c00201527888b1fc3417ddab34f5fab7740a4`.
Original clean business SHA-256 remains `355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`.
The original raw batches remain preserved. Report, plan and visually reviewed 4,782-point plot
are in ignored `artifacts/step06/`; detailed approximation/reference limits are in [GEO_ZONES.md](GEO_ZONES.md).

The first bulk load exposed stale-statistics FK probes and timed out; its transaction rolled back.
Refreshing bulk-table statistics resolved the measured bottleneck. The two-command load/reuse and
classification invocation then completed in9.70s. A representative spatial join used the existing
position GiST index and executed in83.753ms on the initial verified run. These are local timings.

Full shared validation passed: **96 unit tests in24.29s** and **16 integration tests in9.25s**, no
selected skips; lint/format (47 Python files), sdist/wheel import and all three migration-resource
checks, fresh schema migration and repeat no-op all passed. Live and historical data are separate.
No provider downloads, frontend, deployment, paid service or remote CI pass was added.

## Step 05A: deterministic local CI gates

On 2026-10-11, `.\.venv\Scripts\python.exe scripts/validate.py --group all` passed against the existing native PostgreSQL17/PostGIS3.6.2 installation:

- Ruff lint and format check passed (43 formatted Python files).
- 87 unit tests passed in 23.38 seconds; 12 integration tests passed in 6.30 seconds. Selected suites had zero skips. Six additional unit cases verify validation refuses empty/skipped/failed/errored suites and external Python socket networking.
- Built source distribution and wheel. An isolated Python process imported the built wheel (asserted wheel origin), loaded both SQL migrations from that wheel, imported CLI dependencies, and ran CLI help.
- Applied both immutable migrations in a fresh owned schema, verified ledger count, and repeated them with zero applications. Integration fixtures separately verify duplicate replay, quarantine bytes/lineage, transactional rollback, and rejection of modified/broken migrations. All fixture data and outputs are temporary and synthetic.
- Fixed a Windows test timing race by advancing a controlled clock, and bounded periodic collector fixture teardown. Twelve files were formatted; production collector logic and migration SQL remain unchanged.

The validator clears live credentials for its subprocesses and redirects outputs to temporary roots. This evidence is local. The documented `uv run --locked --extra dev python scripts/validate.py` command also passed (87 unit/12 integration tests in26.57s/6.29s). Eleven formatter-only files retain identical Python ASTs; migration SQL bytes are unchanged.

Completed prerequisite stages were separately published and merged in [PR#1](https://github.com/SamABZD/portwatch/pull/1). [CI PR#2](https://github.com/SamABZD/portwatch/pull/2) remains open. Its initial code SHA `97b8af116948088bf6730866b77cdffc71f0e16b` produced actual failing [PR](https://github.com/SamABZD/portwatch/actions/runs/38171159125) and [push](https://github.com/SamABZD/portwatch/actions/runs/38171158488) runs: neither job started, with GitHub's account billing-lock annotation. Remote checks, PostGIS service, merge/main verification, and proposed branch-rule application remain blocked; see [GITHUB_SETUP.md](GITHUB_SETUP.md) for owner action and exact status. No hosted UI or deployment was created in this stage.

## Step 05 — cleaning and real live verification (2026-10-11)

Real key is saved only in ignored `.env`; its value is omitted from every report. A finite `ingest live --port los_angeles --duration 60` completed in **60.843 seconds**, with compression/subscription accepted, **38** saved envelopes, **26** position messages (24 Class A, 2 Class B), **11** static messages (8 ship static, 3 static reports), and **1** confirmation. Collector observed 33 distinct MMSIs across position/static messages; clean positions represent 24 distinct MMSIs. These measure this short collection window, not complete harbor coverage. Parse errors, reconnects, unfinalized received records and recovered tail bytes were all zero; queue high-water was 2/128. Two genuine raw batches were finalized and registered:

- `428ae9eb66ce4035819f210c8855474055f656dfa28a54c717cacb82ffe6ab67`: 22 envelopes.
- `4c03dc8edda7117d6a107928a9602c8814056b73810d1f3a827bf9a71497e582`: 16 envelopes.

Native PostgreSQL 17.11 now runs with PostGIS 3.6.2 from private ignored `pg-runtime/`. Official bundle `postgis-bundle-pg17-3.6.2x64.zip`, 123,959,035 bytes, SHA-256 `7ba180ee2a352987b9a2f194673652c59483b55852295ccf401dceccd8765425`, came from [OSGeo's official Windows distribution](https://download.osgeo.org/postgis/windows/pg17/). Only PortWatch's existing cluster was restarted; `pgdata/portwatch`, previous data and unrelated installed services were preserved. Migration002 and `doctor` passed, including actual extension availability. No Docker, paid service or deployment was needed.

| Input | Records | Valid positions | Duplicates | Quarantined | Explicit skips |
| --- | ---: | ---: | ---: | ---: | ---: |
| NOAA frozen regional v2 | 62,159 | 62,157 | 2 | 0 | 0 |
| First live batch | 22 | 15 | 0 | 0 | 7 |
| Second live batch | 16 | 11 | 0 | 0 | 5 |
| Total | 62,197 | 62,183 | 2 | 0 | 12 |

The 11 useful static messages are stored separately; one confirmation accounts for the remaining skip. Every source record has outcome lineage; both historical duplicates point to their canonical position. Historical clean data retains 141 vessels and UTC extent `2024-01-01T00:00:00Z` through `23:59:58Z`. Live clean extent uses receipt time `2026-10-11T19:25:07.311366Z` through `19:26:03.039308Z`.

`scripts/verify_positions.py` replayed both modes twice. Business rows and all per-input counts matched; the two hashes were `355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`. Checks found zero bad geometries, missing canonical lineage, incorrect timestamp choices, unreconciled counts or unfinished batches. Artifact: ignored `artifacts/step05/verification.json`. Three immutable raw batches passed storage checks with zero issues.

Actual combined quality flags included 9,096 missing courses, 35,846 missing headings, 23 missing speeds, 30,080 missing navigation statuses, 34 missing vessel types, one unavailable course and 12 unavailable headings. These optional values did not quarantine otherwise valid positions. No actual row met the provisional jump/high-speed screen; deliberately suspicious synthetic records test the flag path without creating real analytical observations.

**Event time versus receipt:** NOAA's earliest clean observation was `2024-01-01T00:00:00Z`; its raw acquisition started `2026-10-11T18:20:04.839064Z`. The historical replay uses the event time. The first live position contains provider metadata `2026-10-11 19:25:04.152360282 +0000 UTC` and AIS UTC-second `3`, but arrived locally at `19:25:07.311366Z`. Provider metadata is retained separately; full vessel event time remains unverified, so live analysis uses receipt time with `receipt_fallback`.

Final full suite: **93 passed in 28.58 seconds**, with actual PostgreSQL/PostGIS tests enabled; Step05 unit/DB subset: **32 passed**. Ruff, whitespace checks, locked dependency sync, `doctor` and raw inspection passed. Final Cavecrew review reported **No issues**; nonignored-file scan found zero actual-key matches. Latest verifier rerun at `2026-10-11T19:51:54.664463Z` completed in 16.10 seconds with the same hashes/counts and zero failed checks.

Synthetic tests verify timezone conversion, all four position types, unavailable motion, optional missing/unknown values, invalid coordinates/MMSI/time/lineage, malformed/huge/exponent-overflow JSON numbers, deep JSON, stationary observations at distinct times, source separation, exact and cross-batch replay, specific quarantine evidence, PostGIS point SRID, suspicious jumps and rollback/retry. Review found numeric conversion and nonfinite JSON could fail a batch; guards and regression coverage were added. A test's implicit missing-key assumption and verifier report-path/replacement errors were corrected. Older live fixtures now allow Windows scheduling and wait for actual receipt before cancellation/stop. Exact commands are recorded in [HANDOFF.md](HANDOFF.md).

No Step05 blocker remains after verification. Frontend, port polygons, visits/waiting metrics, deployment and GitHub CI belong to later prompts. User's next required action is only restarting the private native cluster after a reboot, using README instructions; live collection is finite and currently stopped.

## Step 04 — live collector implementation (2026-10-11)

- **At initial Step04 completion, external capture was unverified.** No configured AISStream key existed then. The real Step05 capture above now closes this access/measurement gate; it does not establish complete coverage.
- **Offline verification: passed.** Full suite: 61 tests, including synthetic text/binary/compressed protocol handling, controls, malformed/deep JSON, heartbeat, reconnect/backoff reset, configuration failure, bounded pressure/draining, interrupted recovery and graceful shutdown. One synthetic local capture used actual PostgreSQL in an isolated schema and persisted two envelopes (confirmation and position), with matching run/batch counts.
- **Historical regression: passed.** Frozen NOAA command reused raw and extract in 2.05 seconds, retaining 62,159 observations and 141 distinct MMSI values. Raw storage inspection verified one complete real source artifact with zero issues; synthetic files were confined to test temporary roots.

Live setup, durability limits and recovery actions are in [RUNBOOK.md](RUNBOOK.md). Exact commands and changed files are in [HANDOFF.md](HANDOFF.md). Offline synthetic results must not be presented as real AISStream coverage.

## Step 03 — historical Los Angeles extract (2026-10-11)

The frozen demo uses the genuine [NOAA OCM 2024-01-01 GeoParquet](https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/ais2024/ais-2024-01-01.parquet), acquired once and retained unchanged. The UTC window is `[2024-01-01T00:00:00Z, 2024-01-02T00:00:00Z)`. The inclusive collection box is `[-118.34, 33.66, -118.225, 33.80]` in west/south/east/north order. No date or collection-bound change was necessary.

| Measurement | Observed result |
| --- | --- |
| Original provider artifact | 227,181,435 bytes |
| National rows inspected | 7,293,408 |
| Rows within collection box and UTC window | 62,159 |
| Missing/unusable coordinates in national input | 0 |
| Missing timestamps in national input | 0 |
| National time extent | 2024-01-01 00:00:00 through 23:59:59 UTC |
| Regional time extent | 2024-01-01 00:00:00 through 23:59:58 UTC |
| Distinct non-null regional MMSI values | 141 |
| Derived Parquet bytes | 4,089,031 |
| Maximum batch observed | 65,536 rows |
| Final v2 extraction time | 13.878 seconds |
| Final v2 CLI elapsed time, raw already cached | 15.25 seconds |
| Matching rerun CLI elapsed time | 2.36 seconds; raw and extract reused |
| Extraction process peak working set | 232,808,448 bytes (222.02 MiB), Windows OS lifetime peak |
| Regional rows with nonzero timestamp seconds | 61,496; no fractional seconds observed |
| Regional verification | All 62,159 rows passed bounds, WKB-coordinate equality, UTC range, source lineage and source-row-offset checks |
| Raw-only reconstruction | Identical derived SHA-256, using recorded parameters and the locked runtime |

The memory value is the whole extraction process's lifetime peak, not an incremental allocation estimate. National data is consumed in batches without Parquet prefetch or a full national table; only the bounded regional vessel set and a five-row preview accumulate. Counts describe receiver-observed provider data, not a census of ships. This stage performs no visit or waiting inference. The collection box remains a heuristic rather than an official port boundary.

### Artifact identity and provenance

- Source ID: `noaa_ocm_ais_2024_geoparquet`.
- Dataset ID: `los_angeles_2024_01_01_noaa_ocm_geoparquet_v1`.
- Remote ETag: `0x8DDAFF746752C72`.
- Raw batch ID: `4e1e85393cde6faee49ecd88a8c586b3aeb4cb4ed298c208063454b7f2ae9c70`.
- Raw SHA-256: `3124557f70bb216e3a4d5817cf019368a6ca47d86aec8d3d2aa8c8e6d1e92560`.
- Extraction code: `noaa-la-subset-v2`.
- Runtime identity: PyArrow 26.0.0, Shapely 2.2.0, NumPy 2.4.6.
- Derivation ID: `2a135f61d7ceb1f2ccdf531b15fe4f4d342fe7399fe8c9adeb202ecb7dec543f`.
- Derived SHA-256: `e4667c5c339809b0614e1998537308a5102c9c5523eee84f5d9650025a71d9c0`.

Raw file, relative to the project:

```text
data/raw/noaa_ocm_ais_2024_geoparquet/historical/2024/01/01/los_angeles_2024_01_01_noaa_ocm_geoparquet_v1/4e1e85393cde6faee49ecd88a8c586b3aeb4cb4ed298c208063454b7f2ae9c70.parquet
```

Final derived file, relative to the project:

```text
data/derived/noaa_ocm_ais_2024_geoparquet/los_angeles_2024_01_01_noaa_ocm_geoparquet_v1/los_angeles/2a135f61d7ceb1f2ccdf531b15fe4f4d342fe7399fe8c9adeb202ecb7dec543f/positions.parquet
```

Both files have adjacent `.manifest.json` sidecars. The derived manifest records the parent checksum, bounds, UTC window, code/runtime versions, counts, timing, memory measurement and preview. These generated files are local and Git-ignored. A preliminary v1 derivation is retained as an immutable prior result; v2 supersedes its incorrect nearest-minute quality label after inspecting actual seconds. Consumers must resolve the current derivation from its parameters and version, rather than choose an arbitrary folder.

### Preview and geography

| Observed UTC | Longitude | Latitude |
| --- | --- | --- |
| 2024-01-01 00:00:04 | -118.271050 | 33.735040 |
| 2024-01-01 00:00:08 | -118.279760 | 33.723690 |
| 2024-01-01 00:00:00 | -118.233530 | 33.718400 |
| 2024-01-01 00:00:02 | -118.261400 | 33.726090 |
| 2024-01-01 00:00:02 | -118.226350 | 33.732650 |

The [scatter plot](../artifacts/step03-los-angeles.png) contains a deterministic sample of 4,782 positions, the collection box, and geographic reference points from [SOURCE_AUDIT.md](SOURCE_AUDIT.md). Visual inspection found plausible harbor and approach clusters near the outer harbor and Pier 400; the Long Beach Pier F reference is outside the eastern edge. It is a coordinate plot without a basemap or official analytical polygon. The verifier also checks every regional coordinate, not just the plotted sample.

### Reproduce

```powershell
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch ingest historical --port los_angeles --demo
.\.bootstrap-venv\Scripts\uv.exe run python scripts/verify_historical.py --rebuild
```

The matching ingestion command verifies and reuses cached raw and derived artifacts. The verifier regenerates the ignored plot and `artifacts/step03-verification.json`; `--rebuild` reconstructs the extract in `artifacts/step03-rebuild` using only the preserved raw input, then compares checksums. It does not download again. Checksum equality assumes the recorded extraction code and runtime versions. Provider processing and the observed timestamp discrepancy are documented in [SOURCE_AUDIT.md](SOURCE_AUDIT.md).
