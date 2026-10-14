# PortWatch documentation

Start with the [project overview and local setup](../README.md), [hosted vessel map](https://portwatch-iota.vercel.app/) or [pipeline workspace](https://portwatch-iota.vercel.app/pipeline). GitHub now reports the repository public; Actions remain disabled. Phase 6 will validate and release the phases 4–5 feature branch; production currently serves the previous release.

## Understand and reproduce

| Guide | Use it for |
| --- | --- |
| [Five-minute demo](DEMO.md) | Genuine replay, vessel evidence, pipeline and SQL lineage. |
| [Data contracts](DATA_CONTRACTS.md) | Source identity, immutable inputs, validation, deduplication and lineage. |
| [Analytics](ANALYTICS.md) | Approximate zones, visits, denominator and coverage limits. |
| [Orchestration](ORCHESTRATION.md) | Finite jobs, checkpoints, idempotency, locks and recovery. |
| [Replay](REPLAY.md) | Event-time cutoffs, prepared exports and database-free operation. |
| [History and dbt](HISTORY_DBT.md) | Separate analytics environment, frozen seven-day LA results and SQL lineage. |
| [Live multiport operation](LIVE_MULTIPORT.md) | Collector ownership, schedules, source windows and gaps. |
| [Context](CONTEXT.md) | Weather/news products, attribution and failure semantics. |

## Operate and inspect

| Guide | Use it for |
| --- | --- |
| [Runbook](RUNBOOK.md) | Database/runtime commands, backup/restore and native Windows operation. |
| [Health and recovery](HEALTH_RECOVERY.md) | Freshness, backlog, structured events and failure drills. |
| [Deployment operations](../deploy/README.md) | Existing hosted resources, verified rollouts and rollback history. |
| [Source audit](SOURCE_AUDIT.md) | Provenance, redistribution, timestamps and attribution limits. |
| [Phase 4 workspace evidence](evidence/phase4-workspace.json) | Map/pipeline browser checks and limits. |
| [Phase 5 public readiness](evidence/phase5-public-readiness.json) | Clean-clone reproduction, exposure audit and remaining release steps. |
| [Evidence archive](evidence/README.md) | Earlier dated verification; this is not current hosted CI. |
| [Screenshots](assets/README.md) | Genuine capture scope, credits and checksums. |

## Engineering history

[Decisions](DECISIONS.md), [handoff](HANDOFF.md), [results](RESULTS.md), [release assessment](RELEASE_CHECK.md) and [release notes](RELEASE_NOTES.md) preserve stage history. Statements about missing hosting or earlier test totals describe their recorded stage; the root README and current phase evidence identify today's state. The owner's Actions-disabled setting overrides older instructions to enable hosted CI. No code license has been selected.

[Contribution guide](../CONTRIBUTING.md) · [Working agreement](WORKING_AGREEMENT.md) · [Product intent](../PRODUCT.md) · [Interface system](../DESIGN.md)
