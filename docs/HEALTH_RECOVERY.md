# Health and recovery contracts

Step11 adds read-only operational health, opt-in structured events, a consistent database
archive with an actual restore check, and repeatable recovery exercises. No monitoring service
or new schema is required. Exact operator commands are in [RUNBOOK.md](RUNBOOK.md); measured
evidence is in [RESULTS.md](RESULTS.md).

## Health output

`python -m portwatch health --port los_angeles` prints a human report. Add `--json` for a
schema-versioned JSON report. Exit codes are0 healthy,1 degraded and2 invalid configuration or
an unavailable report. A degraded report remains usable. `--log-json` belongs before the
command; events go to stderr while report JSON goes to stdout.

Each command checks local status, available disk and raw manifests/checksums, then queries
PostgreSQL under a read-only repeatable-read transaction with a10-second statement timeout.
It does not fetch providers, register batches, transform data or append SQL audits. Raw hashing
reads all saved bytes, so elapsed time grows with storage size. Database failure retains the
filesystem/process/disk checks; analytical counters remain unknown rather than becoming zero.
Connection strings, credentials, provider payloads and stored exception text are never returned.

Collector snapshots now include process ID, process creation time and snapshot update time.
Matching creation time protects against a reused PID. A current process with a recent snapshot,
confirmed subscription and recent Pong is `healthy_connected`. Observation freshness is a
separate receipt-time signal: a connected collector may have `no_recent_observations`.
That does not establish zero traffic or complete AIS coverage. A nonexistent matching process
is `dead_process`; stale updates are `unresponsive_process`; missing process identity in an old
active snapshot is `unverified_process`. Explicit stopped/failed states remain visible.
Missing optional credentials with a stopped/absent collector are `not_configured`.

The frozen NOAA demo and stable AISStream dataset have separate source IDs and counters.
An old successfully cleaned/published NOAA snapshot is `ready_historical`; its event age is
informational. Live report age remains explicit. Completed raw batches are awaiting processing
until their current clean-version ledger is committed; registration backlog is counted separately.
Pending publications, unmanifested files and checksum failures remain separate storage issues.
Counts come from committed clean ledgers; canonical positions and skipped records are also shown.

Latency is **raw batch receipt to transactional clean publication**, with sample count, median
and maximum. It includes local queues, operator waiting and retries. Negative intervals are
excluded. This is neither upstream event latency nor time to the first hourly mart, and old
saved live receipts can yield a large legitimate latency. Mart publication time is shown separately.

Historical reanalysis, current forecast and recent news are separate products. Health reports
the latest selected successful retrieval and latest attempt independently, including HTTP status,
cooldown and saved valid-time range. A valid empty response differs from source failure; a later
failure can coexist with a usable older revision. Old reanalysis remains valid historical context.
Run health returns the latest20 root/manual attempts and up to20 attempts per stage for consecutive
failure counts; an unbroken count of20 is bounded by retained query history. Child attempts are
not double-counted. Legacy manual audits are labeled project-wide. A busy competing request is
excluded from processing-failure counts; running attempts are counted separately.

Default configurable warning thresholds:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `PORTWATCH_HEALTH_STATUS_STALE_SECONDS` |60 | Collector snapshot/Pong age |
| `PORTWATCH_HEALTH_OBSERVATION_STALE_SECONDS` |300 | Last observed report age |
| `PORTWATCH_HEALTH_BACKLOG_WARN_SECONDS` |120 | Oldest complete unprocessed receipt age |
| `PORTWATCH_HEALTH_CONTEXT_STALE_SECONDS` |7200 | Current forecast/news successful retrieval age |
| `PORTWATCH_STORAGE_MIN_FREE_BYTES` |1073741824 | Raw volume free-space warning |

Disk space is a warning, not a reservation or an automatic deletion policy. Overall degradation
reflects database/disk/raw problems, failed/dead/unresponsive/reconnecting collectors, overdue
backlog, failed/stale context and consecutive processing failures. Optional stopped collection
and historical event age alone do not fail historical readiness. Read the source-specific states
even when the overall status is healthy; it does not assert current live coverage.

## Events and SQL audits

`python -m portwatch --log-json ...` emits JSON events for workflow attempts, audit transitions,
collector connection/counters, health and backup verification. Fields contain safe IDs, counts,
states, UTC times and error classes rather than exception bodies. Logging is opt-in and handlers
are removed when the command closes. SQL audits remain the durable authority.
An event emitted inside an outer transaction is labeled `audit_visibility=pending_transaction`:
it may be rolled back with a failed publication. `committed` means the audit statement itself
has committed. The event stream is not a replacement transaction journal.

The disconnect drill exposed a Python3.11 cancellation race when a Pong completed as heartbeat
shutdown began. The heartbeat now uses `asyncio.timeout` around the Pong future; a deterministic
same-tick cancellation/Pong regression test verifies termination. Production ping timing remains20s.

## Database backups

`db backup --output NEW.dump` uses matching local `pg_dump`/`pg_restore` tools and a shared
exported repeatable-read snapshot for the custom archive and verification inventory. It refuses
an existing archive/manifest. Passwords are passed privately in the subprocess environment, never
command arguments or stderr output. A neighboring checksum manifest records every application
table's row count, primary-key-ordered row hash and column contract.

Verification is enabled by default. It creates a random `pw_step11_<32 hex characters>` database
from `template0`, restores there with exit-on-error and without owner/ACL requirements, verifies
PostGIS and compares the complete table inventory, then removes only that database. The configured
analytical database is never replaced or stopped. This needs local database-creation privileges
and sufficient disk; tables without primary keys cause a clear verification refusal. `--no-verify`
creates an explicitly unverified archive. `db verify-backup --file ...` verifies a saved archive.
An abrupt operating-system kill can bypass cleanup; inspect ownership before removing leftovers.

The check verifies application rows and column contracts, not every role/ACL/index/sequence or
production failover behavior. Custom archives do not replace original raw files/manifests,
private `.env`, runtime extensions, project configuration, Git history or Prefect's SQLite metadata.
Preserve these separately and keep private material out of Git. The demonstrated archive is a
local copy on the same disk; off-device disaster recovery has not been demonstrated. No archive
is called verified merely because its file exists.

References: PostgreSQL17 [pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html) and
[ALTER DATABASE](https://www.postgresql.org/docs/17/sql-alterdatabase.html).

## Demonstrated promise

The target is no loss of **already durably persisted** inputs during downstream recovery.
Actual exercises compare source bytes, outcome lineage and analytical business hashes. Unflushed
buffers, incomplete spool tails and unreceived upstream events are outside that guarantee.
Disconnected intervals remain unknown, never zero traffic. Receipt metadata does not establish
upstream completeness. Measured recovery times apply only to the small local fixtures and this
machine, not a production recovery objective. Failed mart publication retains the earlier valid
visit/hour outputs; recovery recomputes and removes obsolete derived rows.
