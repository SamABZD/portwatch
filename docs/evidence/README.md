# Reviewed Step 13 evidence

Small selected reports from the completed local audit, packaged for repository review.
The reports retain their measured values and original UTC timestamps. Absolute workstation
and temporary-directory prefixes in text transcripts were replaced with `<project>` and
`<temp>`; trailing display whitespace is removed. These are presentation changes, not
executable paths or altered measurements. No provider credentials,
raw payloads, database dump, prepared snapshot or bulk dataset is included.

| Evidence | Scope |
| --- | --- |
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
