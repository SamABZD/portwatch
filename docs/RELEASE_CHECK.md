# Step13 release-readiness assessment

Completed locally on2026-10-13 (Asia/Beirut; evidence timestamps are2026-10-12 UTC).
The historical reproducibility and critical correctness gates pass. This is a local engineering
assessment, not evidence of a hosted release or passing GitHub Actions. Step14A publication
and public24/7 deployment remain pending. Step14 and17 are skipped; Step16 remains optional.

Step14A follow-up: the owner disabled Actions and authorized proceeding with local validation.
Health/hosting source passed258 tests and deployed to Vercel/Railway; this supersedes the
earlier hosted-check gate. [PR#3](https://github.com/SamABZD/portwatch/pull/3) contains the
project; [GITHUB_SETUP.md](GITHUB_SETUP.md) records actual publication evidence.
The audit-time remote/process statements below are historical snapshots.

## Corrections made

- Restrict neighboring-day inference context to the selected port. Previously, source-only
  selection could include another port's processed day. The midnight integration case now
  includes a rival port and verifies canonical lineage and separation.
- Reconstruct historical weather in an empty database from complete, matching, checksum-verified
  raw evidence. Preserve its original receipt and create no new HTTP attempt. Forecast/news
  freshness is unchanged; explicit refresh still uses provider requests and cooldowns.
- Align Compose with the existing CI requirement: PostgreSQL17/PostGIS3.6, using
  `postgis/postgis:17-3.6-alpine`. Add a regression gate for both configurations.
- Honor the configured default port in health queries. Prepared-only frontend sessions now
  select the snapshot's port/historical mode automatically and disable unavailable Live mode.
- Scope replay verification to immutable historical business data while live collection grows.
  Recovery cleanup verifies only resources created by the drill, preserving its parent database.

## Twelve audit areas

All pass locally within the stated scope; remote execution remains unverified.

| Area | Evidence and result |
| --- | --- |
| UTC/source vs receipt | [Timestamp tests](../tests/test_positions.py); read-only SQL (local `artifacts/step13/final-state.json`): UTC session, zero invalid historical time choices/ranges. Live remains receipt fallback with separate provider text. |
| Idempotency/uniqueness | [Fresh real-data verification](evidence/reproducibility.json), [cleaning tests](../tests/test_cleaning.py): both-port forced reruns preserve business, profile and per-port mart digests; duplicate lineage retained. |
| Coordinates/units | [Zone tests](../tests/test_zones.py), [storage tests](../tests/test_zone_storage.py), SQL: zero mismatched longitude/X, latitude/Y or SRID; WGS84 and metric-distance rules covered. |
| Join multiplication | [Mart integration tests](../tests/test_mart_storage.py): weather revisions/articles do not multiply vessel metrics; distinct primary-zone counts reconcile. |
| Gaps/unknown | [Visit tests](../tests/test_visits.py), [replay tests](../tests/test_replay.py), recovery drill (local `artifacts/step13/b10119e6ea8c4327ace9b807fca53ebe/recovery-verification.json`): report/connection gaps remain unknown and break inference/trails. |
| Source/port/future isolation | [Multiport tests](../tests/test_multiport.py), [midnight regression](../tests/test_pipeline_storage.py), [Replay verification](evidence/replay.json): source, port, profile availability and cutoff isolation. |
| Cache invalidation | [Replay tests](../tests/test_replay.py): revision/display parameters change cache keys; [mart tests](../tests/test_mart_storage.py) replace obsolete late-data partitions transactionally. |
| Commit/checkpoint order | [Pipeline integration tests](../tests/test_pipeline_storage.py): committed business data precedes checkpoints; interruption resumes, session locks release and abandoned audits recover. |
| Schema/unknown messages | [Parser tests](../tests/test_positions.py), [collector tests](../tests/test_live.py), [profile tests](../tests/test_profiles.py): invalid required shapes quarantine, unknown/control messages are explicit, optional unknowns stay missing. |
| Secrets/bulk data | [Scan](evidence/security.json): private AIS credentials absent from tracked content/history; the documented public localhost DB sample is deliberately tracked and currently used locally. Bulk/private paths ignored; migrations001–008 and both lockfiles unchanged. Python112-package and npm production audits found zero known vulnerabilities at audit time. |
| Claims/labels | README/contracts/runbook updated; pipeline drawing separates architecture from real health. Approximate zones, proxy metrics, incomplete coverage, portable limits and local hosting remain explicit. |
| Workflow drift | Shared [validator](../scripts/validate.py) passes; Compose/CI versions match. Existing [workflow](../.github/workflows/ci.yml) runs locked Python quality/integration gates. Next.js build/browser checks were local; there is no frontend Actions job and no current-commit hosted run. |

## Fresh historical reproduction

A new native PG17.11/PostGIS3.6.2 cluster and owned database were created, eight migrations
applied, repeat applied zero, and both ports processed using genuine preserved NOAA/ERA5
artifacts. No provider requests or live credentials were used. Source downloads are a separate
documented online prerequisite; this does not prove current upstream availability. The owned
database was removed and all three audit-attempt clusters were confirmed stopped.

| Measurement | Los Angeles | Oakland |
| --- | ---: | ---: |
| National NOAA2024-01-01 input rows (same shared file) | 7,293,408 | 7,293,408 |
| Regional extracted reports | 62,159 | 52,785 |
| Canonical positions | 62,157 | 52,783 |
| Distinct observed vessels | 141 | 172 |
| Duplicate records | 2 | 2 |
| Quarantined / skipped records | 0 / 0 | 0 / 0 |
| First/last observation UTC on January1 | 00:00:00–23:59:58 | 00:00:00–23:59:59 |
| Fresh pipeline seconds | 65.45 | 59.38 |
| Forced repeat seconds | 24.47 | 25.01 |

Duplicates are repeated normalized observations with separate original-record lineage;
there are no historical quarantine reasons because no records quarantined. The two regional
counts overlap geographically only as their configured bounds permit; do not sum national
input counts or distinct vessel counts into a coverage claim.

Fresh two-port processing totaled124.84s; the complete launch/migration/repeat/API/export/
recovery/cleanup verification took295.53s. A genuine uncached LA noon HTTP frame took0.457s,
cached0.045s, prepared-without-DB0.499s. The saved frame matched all vessels and metrics,
including99 fresh LA vessels. These are local samples, not throughput or latency guarantees.

Recovery repeated genuine historical analytics with the same digest, then used an isolated
synthetic database for deliberate failures: database-denial/backlog recovery0.285s; late-report
replacement0.121s; failed publication retained previous metrics; malformed input reconciled
1 accepted/1 duplicate/1 quarantine (`invalid_mmsi`); duplicate replay unchanged; corruption
detected. A loopback-only WebSocket reconnected once and labeled its0.297s missing interval
unknown. Synthetic drills are not provider coverage evidence.

Hardware: Windows10 build19045, Python3.11.9,8 logical CPUs,25,642,815,488 bytes RAM
(about23.88GiB). Node24.15.0; source files were already on local disk and live collection
continued during checks. Cache/disk/runtime conditions affect timings.

## Validation and browser evidence

- `uv sync --locked --extra dev` succeeded. The Step05A shared validator passed **185 unit +
  57 integration =242 tests**, Ruff/101 formatted Python files, wheel/sdist, installed-wheel
  CLI/resources and empty-database migrations. [Final log](evidence/validation.txt).
  One upstream Starlette/httpx TestClient deprecation warning is non-failing.
- Next.js type checking/production build passed for `/` and `/pipeline`.
  [Build log](evidence/web-build.txt).
- Existing replay12 and multiport6 browser checks pass. Prepared-only actual HTTP sessions
  pass3 checks for each port with blank AIS credentials/closed DB port9; LA noon99, Oakland75.
  [LA](evidence/browser-los-angeles.json),
  [Oakland](evidence/browser-oakland.json).
- The nine-component architecture drawing passes4 checks: selection/keyboard, four viewport
  sizes/no clipped labels, secondary-text contrast (minimum5.79:1), and disconnected-health
  operation. Desktop/mobile inspected; zero page errors. [Report](evidence/diagram.json).
  Design scan (local `artifacts/step13/design-scan.json`) found no deterministic issues.

## Failed, deferred and unverified items

No unresolved critical local correctness/reproducibility failure remains. Initial fresh-run
attempts exposed Windows launcher pipe inheritance and a cleanup assertion that mistook its
parent database for leaked test resources; both were corrected before the successful run.

- GitHub CI remains blocked by account billing; the owner authorized $0 local continuation.
  No new push or current-commit Actions run exists. Remote main was observed at
  `f784a9374942c704630bd412704a67c4896ead24`; old failed runs do not validate this code.
- Docker is unavailable here: the Compose image/configuration matches CI, but fresh Docker
  startup is unverified. Existing PG16 volumes require explicit migration; never mount them
  into PG17 or delete them to obtain a green check.
- At20:57UTC both real collectors were connected; forecasts available; both GDELT news
  requests were429. Overall health was degraded for unavailable news. Brief pending batches
  are visible separately. A connected socket is not complete AIS coverage or an SLA.
- No public hosting, HTTPS, production least-privilege DB role, supervised boot restart,
  off-device backup or sustained uptime verification exists. The local computer must stay
  awake/connected for live work. Replace the documented public localhost DB sample password
  before any public deployment. Publishing GitHub alone does not host the system.
- NOAA is provider-preprocessed; AISStream gaps may be unrecoverable; coarse weather is
  model context; news is incomplete metadata. Approximate zones/inferred waiting and dwell
  do not establish official calls, queues or causality. See [source audit](SOURCE_AUDIT.md)
  and [metric definitions](ANALYTICS.md).
- Code links in the website target GitHub `main`; they become current at Step14A publication.
  Raw files, snapshots, credentials and generated evidence stay ignored. Remaining local artifact identifiers
  require the audit checkout; [selected reports](evidence/README.md) and
  [screenshots](assets/README.md) are packaged for repository review.

Reproduction and launch/stop commands: [RUNBOOK.md](RUNBOOK.md),
[LIVE_MULTIPORT.md](LIVE_MULTIPORT.md), [REPLAY.md](REPLAY.md).
