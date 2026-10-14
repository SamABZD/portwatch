# PortWatch release notes

## October 14, 2026 — completion phases 4–6

[PR #9](https://github.com/SamABZD/portwatch/pull/9) merged at `fb89c3c` and deployed to
the existing [Vercel production site](https://portwatch-iota.vercel.app/). The original
v0.1.0 tag is unchanged. Backend code remains at the earlier tested `d39d9bd` checkpoint;
this frontend release did not redeploy Railway or restart collectors.

The shared map/pipeline header preserves port, mode and cutoff. Maps fit both ports,
keep camera position across polls, distinguish loading and fallback states, and balance
the inspector on desktop and mobile. The architecture uses actual component bounds,
separates continuous collectors from finite Prefect work, and connects dbt only to its
frozen historical branch. Public source links and documentation explain the real stack,
reproduction commands, genuine data, analytical limits and current license status.

Validation: **329 Python tests**, lint/format/package/migrations, frontend install/typecheck/
build and **14 Node tests**; a batched browser run and one complete confirmation passed
**63 checks**, including four widths and native200% zoom. Separate production verification
passed **12 HTTPS responses, four API bounds cases and eight browser checks** with real API
data, plus post-navigation news/collector ownership checks. Actions remain disabled;
these are local tests and observed deployment checks.

Both ports currently render one official fallback article in a seven-day saved window.
GDELT returned HTTP429 in the final sample; Los Angeles also had an earlier timeout.
Normal hourly LA progress was observed without manual source acquisition. Replay excludes
current news. Frozen history remains443,296 positions/168hours/11models/36tests, with
unchanged standalone lineage HTML checksum. Coverage and visit/waiting inferences remain
incomplete and experimental.

[Sam's profile](https://github.com/SamABZD) now lists `03 / PortWatch` at
[profile commit99ba386](https://github.com/SamABZD/SamABZD/commit/99ba386936d39aa497b0c738c641b0cb9fbdad13).
Anonymous rendering, all three links and the pipeline image were checked. Flux and Retro
content/order are preserved; PortWatch was pinned without removing either.

No paid resource, replica, volume or source cadence was added. Expected recurring frontend
delta is $0 under unchanged traffic/backend workload; actual billing delta is unavailable.
Existing unmatched compute samples increased and establish no release savings. No hard
compute spending cap or raw-data retention horizon is claimed. Code has no selected license;
older reachable history retains disclosed non-secret operational context.

[Complete release evidence](evidence/phase6-release.json) ·
[Validator transcript](evidence/phase6-validation.txt) · [Handoff](HANDOFF.md)

## Original v0.1.0 notes

Prepared October 13, 2026 (Asia/Beirut). These notes describe the candidate; publication
status is recorded in GITHUB_SETUP. GitHub Actions are disabled at the owner's request.

## Implemented capabilities

- Historical NOAA processing and continuous local AISStream reception for Los Angeles and
  Oakland, with source/port isolation and timed vessel profiles.
- Immutable files, checksum manifests, record lineage, deterministic deduplication, quarantine,
  transactional publication and resumable Prefect workflows.
- PostgreSQL 17/PostGIS 3.6 zones, experimental visits/waiting and UTC hourly marts.
- Read-only FastAPI queries and Next.js map, event-time replay, selectable pipeline drawing,
  scoped measurements, mobile/keyboard controls and local map fallback.
- Historical exports usable without a database/live key, health diagnostics, verified backup
  restore and isolated recovery drills.
- Context schedules distinguish deferral from failure; Oakland can use its clearly attributed
  official RSS fallback while preserving GDELT failures. Concurrent raw publication no longer
  appears as an unmanifested file during inspection.
- Container backend entry point with migrations, genuine historical bootstrap, safe readiness
  and optional continuous runtime; Next.js standalone container support and Vercel deployment.
- Hosted frontend at [PortWatch](https://portwatch-iota.vercel.app), with continuous collectors,
  Prefect processing and private PostGIS on the owner's existing paid Railway workspace.
  Both ports' live reports, source health and genuine historical catalogs were verified.

## Dataset and measured results

Frozen period: January 1, 2024 UTC, exclusive end January 2. The shared NOAA file contains
7,293,408 provider-preprocessed rows and is excluded from Git. ERA5 has 24 hourly values per port.

| Measurement | Los Angeles | Oakland |
| --- | ---: | ---: |
| Regional reports | 62,159 | 52,785 |
| Canonical positions | 62,157 | 52,783 |
| Observed vessels | 141 | 172 |
| Duplicate records | 2 | 2 |
| Historical quarantines | 0 | 0 |
| Fresh processing from saved inputs | 65.45 s | 59.38 s |
| Forced repeat | 24.47 s | 25.01 s |

Both forced repeats preserved business and metric digests. Complete fresh-database/export/
recovery verification took 295.53 seconds. LA uncached/cached query samples were 0.457/0.045
seconds. Measurements exclude downloading and are not service guarantees.

## Validation and recovery

Step 13 passed 185 unit plus 57 integration tests, Ruff/format, package/wheel checks, eight
fresh migrations/repeat zero and the Next.js production build. Browser evidence covers
12 replay, six multiport, three prepared-mode checks per port and four architecture checks.
The health/hosting follow-up passed194 unit +64 integration =258 tests and the same full
validator; Next.js also built successfully on Vercel. No hosted GitHub CI pass is claimed.

Genuine historical recomputation preserved digests. Isolated synthetic drills verified database
denial/recovery, retained publication on failure, late-report replacement, quarantine, duplicate
replay and corruption detection. A loopback reconnect retained an explicit unknown interval.
These drills do not establish provider coverage. See the [audit](RELEASE_CHECK.md),
[reviewed evidence](evidence/README.md) and [recovery contracts](HEALTH_RECOVERY.md).

## Replay instructions

Follow the [README quick start](../README.md#run-the-historical-demo) and [demo guide](DEMO.md).
The first run downloads NOAA/weather. Saved artifacts reproduce the pipeline without provider
calls. After processing, `demo export --output data/demo/los-angeles.json` and
`demo serve --prepared data/demo/los-angeles.json` provide database-free historical queries;
keep the separate Next.js frontend running. Exports remain ignored and are not release attachments.
Live collection requires a private AISStream key and an awake, connected computer.

## Known limits and publication gates

- Previous GitHub Actions attempts were billing-blocked. The owner subsequently disabled
  Actions and authorized proceeding with local validation; earlier hosted gates are superseded.
- Repository visibility was observed private and is preserved. Reviewers need access.
- Vercel frontend and Railway backend/database deployment status and HTTPS endpoints are
  recorded in [deployment operations](../deploy/README.md). Off-device backup remains pending.
- After abrupt local downtime, stale RUNNING Prefect states and obsolete scheduled entries
  can delay processing. Operator recovery was demonstrated; automatic recovery is unimplemented.
- Local Docker startup is unverified; fresh reproduction used native PostgreSQL/PostGIS.
- Coverage is incomplete; live timestamps use receipt fallback. Weather is model context.
  GDELT can return429; Oakland's official RSS covers releases only. Recent news is excluded
  from replay. Successful empty results are not evidence of no traffic or disruption.
- Approximate zones, inferred calls, waiting and dwell are experimental. Missing data and
  incomplete boundaries stay unknown. No confirmed queue or disruption cause is claimed.
- Historical expansion/dbt is optional and unimplemented; academy submission and the scale
  study were skipped. These are not delivered features.

## Attribution and included artifacts

NOAA OCM/MarineCadastre historical data is CC0; attribution is retained. Weather credits are
Open-Meteo and Copernicus/ERA5 under applicable CC BY 4.0 terms. Original publishers retain
their content attribution. See [source audit](SOURCE_AUDIT.md) and [image provenance](assets/README.md).
Images show historical NOAA/ERA5 views and the independently drawn pipeline. No raw AISStream
payloads, bulk datasets, database files, private credentials or snapshots are included.
