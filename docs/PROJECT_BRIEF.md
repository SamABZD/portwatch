# PortWatch project brief

## Scope and delivery constraint

PortWatch is an academy data engineering portfolio project focused first on the Port of Los Angeles (`los_angeles`). The implementation window is five days and the incremental budget is $0. The baseline demonstration must work with reproducible historical AIS data even if a live AIS provider is unavailable or credentials are missing. A live collector is conditional on a verified provider and coverage audit.

## Architecture

The `portwatch` Python package owns validation, CLI entry points, and future pipeline orchestration. Original provider files and responses are stored unchanged in `data/raw/` before any transformation; provider products can have prior provider-side cleaning. Quarantine is separate from raw, and derived datasets are separate from both. PostGIS runs locally through Compose with a persistent named volume. Future UI components may read curated outputs but will not own ingestion or migrations.

Historical and live AIS are distinct datasets with explicit source provenance. Their overlapping observations are never blended in a metric until a documented precedence policy exists. Weather and disruption-news context will be separate sources with their own collection and freshness states. Persist timestamps in UTC. Preserve source event time, local receipt time, and timestamp quality when available; use `America/Los_Angeles` only for display. Do not imply source precision or total maritime coverage from a local data freshness measure.

Reruns should be idempotent for the same input and processing version; audit run IDs and timestamps may differ. Source failure, empty response, stale data, and a valid zero are different states. Geofences and waiting thresholds are versioned analytical assumptions. Synthetic records belong only in clearly labeled test fixtures.

## Five-day milestones

| Day | Intended result |
| --- | --- |
| 1 | Foundation and GitHub setup, then source feasibility and first demo specification. |
| 2 | Raw historical ingestion, contracts, and local spatial schema. |
| 3 | Vessel-position cleaning, geofence and waiting logic, and repeatable orchestration. |
| 4 | Context feeds, reliability checks, and a local demonstration view. |
| 5 | Verification, documentation, screenshots, and submission packaging. |

Milestones are planning targets; stage prompts govern exact scope and order. GitHub Step 00A follows this foundation step.
