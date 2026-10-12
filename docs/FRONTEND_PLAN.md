# Frontend choice: Next.js

The user requested Next.js on2026-10-12. This replaces the original Streamlit/PyDeck choice
for Step12. Steps00–11 are completed locally; Step12 map/replay is now next. This records
the implementation direction; no frontend or API has been implemented yet.

## Architecture for Step12

- Build the frontend in `web/` with Next.js App Router, React and TypeScript.
- Use client components for the interactive vessel map, replay slider/playback and selections.
  Browser/WebGL map code must use the client-only boundary required by its installed version.
- Use a thin read-only Python HTTP API to expose bounded prepared positions, replay prefixes,
  metrics, matching context and pipeline health. Reuse existing query/inference functions and
  tested event-time rules. Do not duplicate visit inference in JavaScript or run ingestion from
  page rendering. Select and pin the API dependency when implementing the stage.
- Keep PostgreSQL/PostGIS, the Python ingestion/transformation package and Prefect workflows
  as the data-engineering system. Database credentials stay on the server.
- deck.gl's React integration is a candidate for points, observed trails and zone overlays.
  Preserve visible attribution, an appropriate free basemap and point/polygon fallback.

Checked [Next.js server/client components](https://nextjs.org/docs/app/getting-started/server-and-client-components)
and [deck.gl React/SSR integration](https://deck.gl/docs/get-started/using-with-react).
Choose supported runtime/package versions and verify them at implementation time.

## Acceptance remains the same

Retain every Step12 map/replay/data-quality requirement from the original prompt: historical/live
switch, explicit source/version/date/timezone, real positions at or before the cutoff, staleness,
trails breaking at reporting gaps, no invented movement or future waiting/visit knowledge,
completed-hour chart boundaries, correct-period weather, mode-appropriate news, unknown/partial
states and accurate pipeline health. Query/cache bounded subsets with revision/version-aware keys;
measure cached frames against the under-two-second target. Test actual browser interactions and
known future-leakage cases, save real screenshots when supported, and document exact launch commands.

The Next.js UI must not run migrations or acquire provider data. Local operation requires the
query API/database; the collector and finite processing remain separate processes.
For a possible $0 public historical demo, design a prepared-data mode that does not depend on
the laptop database. Any export must preserve provenance and prefix/replay semantics and verify
redistribution conditions. A full hosted live system separately needs collector/processing,
database and storage. No hosting account, resource, payment or publication is authorized here.

This choice adds a frontend build toolchain and an API boundary compared with the original
Streamlit plan. Implement the same single-page demonstration and measure it; additional product
features remain outside the stage. Stop after Step12 when that stage is requested.
