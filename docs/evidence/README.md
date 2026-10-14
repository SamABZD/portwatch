# Reviewed evidence archive

## Current completion phases

- [Phase 6 release](phase6-release.json) and [validator transcript](phase6-validation.txt):
  329 current Python tests,14 Node tests,63 browser checks, actual hosted API contracts and
  scoped cost/rollback evidence. Production/profile status is recorded only after verification.

- [Phase 4 workspace](phase4-workspace.json): current frontend build/typecheck,14 Node tests
  and37 recorded browser checks, including preserved unchanged-code checks and narrow follow-ups.
- [Phase 5 public readiness](phase5-public-readiness.json):20 successful clean-clone commands,
  two-port genuine saved-input parity, exposure audit, actual cleanup limitations and release steps.
- [Phase 3 navigation](phase3-navigation.json), [Phase 2 news](phase2-news.json),
  [Phase 1 cost controls](phase1-cost-controls.json): original dated phase checkpoints.

The phases1–3 release at `d39d9bd` passed236 unit +93 integration =329 Python tests;
[handoff](../HANDOFF.md) records the exact validator. Backend code is unchanged in phases4–5.
Full release validation remains Phase6. GitHub now reports public; Actions remain disabled.
New feature-branch presentation and UI are not a production rollout.

## Earlier stage evidence

Small selected reports from the completed local audit, packaged for repository review.
The reports retain their measured values and original UTC timestamps. Absolute workstation
and temporary-directory prefixes in text transcripts were replaced with `<project>` and
`<temp>`; trailing display whitespace is removed. These are presentation changes, not
executable paths or altered measurements. No provider credentials,
raw payloads, database dump, prepared snapshot or bulk dataset is included.

| Evidence | Scope |
| --- | --- |
| [Cloud history/operations](cloud-history-operations.json), [validation](cloud-history-validation.txt) | Actual Railway models/parity, two-port automatic recovery, finished backup restore, unchanged limits and bounded cost samples;282 Python tests. |
| [History/dbt](history-dbt.json), [validation](history-dbt-validation.txt), [lineage](dbt-lineage.json) | Seven real LA dates,443,296 observations,168 matching hours,36 dbt data tests,45 typed columns and276 Python tests; resume, boundary and preliminary-baseline evidence. |
| [Fresh reproduction](reproducibility.json) | Both-port genuine saved inputs, repeated digests, API/prepared timings and owned-resource cleanup. |
| [Validation transcript](validation.txt) | 185 unit + 57 integration tests, lint/format, actual package checks and migrations. |
| [Health/hosting follow-up](health-hosting-validation.txt) | 194 unit +64 integration tests, lint/format, installed wheel/CLI and eight fresh migrations/repeat zero. Absolute prefixes redacted. |
| [LA fallback follow-up](la-news-fallback-validation.txt) | 201 unit +65 integration tests, strict official archive/date handling and preserved primary failures; full validator and repeated migrations pass. |
| [Frontend build](web-build.txt) | Successful Next.js production build for map and pipeline. |
| [Replay](replay.json) | Read-only historical prefix and integrity checks. |
| [LA browser](browser-los-angeles.json), [Oakland browser](browser-oakland.json) | Automatic prepared mode, real noon metrics, desktop/mobile and zero page errors. |
| [Drawing](diagram.json) | Selection/keyboard, viewport fit, contrast and unavailable-health checks. |
| [Security summary](security.json) | Point-in-time Step 13 dependency/content/history checks; not a future vulnerability guarantee. |

The fresh reproduction creates a new database from already downloaded genuine NOAA/ERA5
artifacts without provider calls. It is not proof that upstream downloads currently work.
Failure drills use isolated synthetic inputs; they do not prove provider coverage or production
recovery objectives. Full raw audit artifacts remain local and ignored.

See [release assessment](../RELEASE_CHECK.md), [source audit](../SOURCE_AUDIT.md) and
[screenshot manifest](../assets/manifest.json). Hosted CI and public deployment remain separate gates.
