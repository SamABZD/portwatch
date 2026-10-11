# Decisions

| ID | Stage | Decision | Reason / revisit point |
| --- | --- | --- | --- |
| D-001 | 00 | Use a src-layout Python package and `python -m portwatch` Typer CLI. | Keeps pipeline entry points testable and separate from future presentation code. |
| D-002 | 00 | Use Python 3.11 locally, with project support through 3.14. | Python 3.12 is not installed on this machine; 3.11 is installed and mature for the selected dependencies. Recheck when a 3.12 interpreter is available. |
| D-003 | 00 | Use `postgis/postgis:16-3.5` in Compose with a localhost port and named volume. | Maintainer documentation lists PostgreSQL 16 with PostGIS 3.5 and the `/var/lib/postgresql/data` mount path. Image existence was verified from documentation, but this machine cannot run it until Docker is installed. [Maintainer reference](https://github.com/postgis/docker-postgis). |
| D-004 | 00 | Keep Los Angeles `bounding_box` and `geofence_path` null. | Step 01 must identify a defensible geographic source and version before these fields are populated. |
| D-005 | 00 | Start with provisional waiting thresholds of 1 knot, 30 minutes, and a 15-minute maximum observation gap. | These are explicit analytical assumptions, not authoritative port facts; revisit and version in Step 01. |
| D-006 | 00 | Treat live AIS credentials as optional. | Historical replay is the dependable $0 demo path. |
| D-007 | 00 | Store raw provider files under `data/raw/`, derived data under `data/derived/`, and rejected payloads under `data/quarantine/`. | Maintains source provenance and prevents synthetic or transformed records from replacing source evidence. |
| D-008 | 00A | Make the existing `portwatch` child folder the independent Git repository root and use `main`. | The parent `dataEngineering` folder is unrelated and has no Git repository; PortWatch is already isolated. |
| D-009 | 00A | Adapt Flux's text normalization and contributor workflow now; defer CI to Step 05A. | Python cleaning and database integration tests are introduced in later stages, so a meaningful Validate workflow belongs with them. |
