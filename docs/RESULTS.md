# Measured results

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
