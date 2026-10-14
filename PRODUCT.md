# Product

## Register

product

## Platform

web

## Users

The project owner and portfolio reviewers exploring Los Angeles/Oakland live observations, replay and pipeline evidence on a laptop. The primary task is to replay a real historical day and inspect the evidence behind
vessel positions, waiting candidates and completed visits.

## Product Purpose

Turn Python/PostGIS pipelines into a readable live vessel map, reproducible replay and inspectable pipeline view. Success
means a real day can be replayed without future leakage or invented movement and live mode
reports its actual freshness and unavailable sources.

## Positioning

A maritime data engineering demo: trace vessel reports through ingestion, validation,
PostGIS analytics, historical replay and the map. Keep observation, inference and missing
evidence visible.

## Brand Personality

Restrained and readable, as specified in Step12 and confirmed by the user. Copy describes
observations and uncertainty directly. The frontend uses Next.js, as the user requested.

## Design Principles

- Keep the map and replay controls central to the task.
- Show the selected source, period, timezone and inference cutoff together.
- Make unknown, stale and partial evidence legible alongside useful results.
- Prefer prepared bounded queries to rendering side effects.
- Preserve the distinction between historical replay and present source health.

## Accessibility & Inclusion

Implementation choices: keyboard controls, labeled inputs, visible focus, readable contrast,
text equivalents for chart/map selection, touch targets and reduced-motion support. No separate
accessibility certification is claimed.
