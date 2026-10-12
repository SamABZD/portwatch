# PortWatch runbook

## Step09 analytical tables

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch transform marts --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch transform marts --port los_angeles --demo --as-of 2024-01-01T12:30:00Z
.\.venv\Scripts\python.exe scripts/verify_marts.py
```

Use saved canonical positions and loaded geofences; no provider acquisition occurs. Weather is
optional: a missing revision produces null context. Recent news/forecast never enter this demo.
After cleaning late reports, run the full transform again; visits and affected hours are replaced
atomically, including obsolete rows. A failure leaves the earlier visit/mart snapshot available.
Configuration/rule/weather changes create a separate analytical version. Snapshot age is
`snapshot_staleness_minutes` in the public port config, default15. All previous migrations remain
immutable; apply migration006 before invoking the new command.

Stored hourly rows are retrospective completed-hour output. Read [ANALYTICS.md](ANALYTICS.md)
before interpreting counts, medians or source health. Every chart query must select one exact
source/dataset/cleaning/analytical version. During replay use the event-time prefix helper or
`--as-of`, which reconstructs visits without future reports and marks the current hour partial.
The stored query helper withholds rows until the entire input inference cutoff is reached.
Prefix mode writes only temporary objects and creates no persistent audit or derived rows.

`verify_marts.py` checks duplicate reruns, source and visit hashes, independent observation,
snapshot, weather, event/dwell and reject reconciliation, real prefix knowledge times, unchanged
stored/audit rows and the five SQL examples. Reports remain ignored under `artifacts/step09/`.
Dataset reject totals are not hourly timestamps; never fill their unknown allocation or a missing
source interval with zeros. Midpoint dwell and low-speed waiting remain proxies with sample sizes.

## Step08 context connectors

From the project directory, with the existing database running:

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch ingest weather --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch ingest weather --port los_angeles
.\.venv\Scripts\python.exe -m portwatch ingest news --port los_angeles
.\.venv\Scripts\python.exe scripts/verify_context.py
```

Historical default verifies/reuses the chosen saved response without HTTP. `--refresh` on either
connector explicitly selects a fresh successfully normalized response; old bytes/rows remain.
Current context has a15-minute cache for the same request key. News uses explicit recent windows,
configured in `configs/ports/los_angeles.yaml`. There is no historical news command or auto-poller.

`source_rate_limited`, `source_timeout`, `source_http_failure` and normalization errors exit1 and
leave failed attempts separately from any previous successes. `local_poll_cooldown` reports the
next allowed UTC time without connecting; changed windows and refresh do not bypass it. News
waits at least15minutes, weather at least60seconds, and Retry-After can extend either. Run the
historical weather/AIS replay independently; never fill unavailable articles or measurements with
synthetic analytical data. Both source products preserve original successful HTTP bodies as
`.json` in shared raw storage; `storage inspect/reconcile` handles them alongside AIS.

Use [CONTEXT.md](CONTEXT.md) and [DATA_CONTRACTS.md](DATA_CONTRACTS.md) before querying. Retain
one exact request/revision/product for weather joins and keep recent articles out of2024. A failed
fresh fetch does not erase prior selected weather. Prefix reading is retrospective reanalysis,
with wind valid instants and precipitation preceding-hour intervals. The weather issue time is
unknown in the actual responses, even when retrieval time is available.

Provider health can be inspected without treating an empty table as a successful empty fetch:

```sql
SELECT r.kind, r.product, r.start_utc, r.end_utc, a.started_at, a.retrieved_at,
       a.status, a.http_status, a.error_code, a.next_allowed_at
FROM context_attempts a JOIN context_requests r USING (request_key)
ORDER BY a.started_at DESC;
```

No new paid credentials or account changes are required. Source unavailability and the separate
GitHub billing lock remain explicit; only local checks currently pass.

## Visit inference and read-only prefixes

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch transform visits --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch transform visits --port los_angeles --demo --as-of 2024-01-01T12:00:00Z
.\.venv\Scripts\python.exe scripts/verify_visits.py
```

Use the current native database and saved historical inputs. Default inference refreshes missing
zone assignments and replaces complete selected-dataset derived histories atomically. If a run
fails, the prior inference snapshot remains; fix the reported prerequisite and rerun. Missing
zone versions/checksum mismatch require reviewed `geo load`. `--version` selects an older loaded
geofence explicitly. Changed inference parameters create a separate rule ID; old contexts remain.

`--as-of` requires explicit UTC and performs no writes. It cannot use future events to confirm a
visit or waiting association. It reconstructs an event-time prefix, not actual historical provider
delivery. Stored full-history episodes must remain labeled retrospective. [VISITS.md](VISITS.md)
defines completed/partial timing, gaps and waiting-proxy limits, including the reported pilot-vessel
candidate. No actual queue is established. Do not average partial spans as completed dwell.

## Local geofence and zone checks

From the project root `C:\Users\samab\dataEngineering\portwatch`, use the running native
PostGIS cluster and saved historical inputs:

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch geo load --port los_angeles
.\.venv\Scripts\python.exe -m portwatch transform zones --port los_angeles --demo
.\.venv\Scripts\python.exe scripts/verify_zones.py
```

Changed-content errors require a new reviewed geofence version. Invalid geometry is rejected;
inspect the named ring/zone rather than applying an unrecorded repair. `--version <loaded-version>`
reproduces earlier stored polygons. A failed assignment rolls back all rows for that transaction;
rerunning inserts missing assignments. If large-load FK probes are slow, inspect table statistics
and plans before changing integrity constraints. [GEO_ZONES.md](GEO_ZONES.md) records the resolved
initial statistics issue and limitations. CLI classification does not acquire provider data.

## Step 04: live collector

The collector runs independently of the dashboard and analytical jobs. PostgreSQL bookkeeping must be migrated and running; PostGIS is not used in this stage. The existing native cluster can be restarted using [README.md](../README.md). Historical replay remains available when live credentials or service access are unavailable.

### Configure and capture

1. Sign in at [AISStream account](https://aisstream.io/account), create a key, and add `PORTWATCH_AIS_API_KEY=<your key>` to the existing ignored local `.env`. Keep the configured native database port. Do not paste the key into commands, screenshots, logs, or Git.
2. From `C:\Users\samab\dataEngineering\portwatch`, run a finite capture:

   ```powershell
   .\.bootstrap-venv\Scripts\uv.exe run python -m portwatch ingest live --port los_angeles --duration 60
   ```

3. Read the summary and `artifacts/live/los_angeles-status.json`. Record actual elapsed time, observations, distinct valid MMSI values, persistence failures and returned batch paths before claiming any coverage. A confirmed connection with no observed positions is an empty observation window, not proof of no vessels.
4. For continuous capture, omit `--duration`. Press Ctrl+C once to stop socket reading, drain queued records and finalize the active batch. Allow shutdown to finish; a forced termination can lose volatile records. Finite duration limits collection time; startup, recovery and draining can add to total command elapsed time.

Missing key exits with code 2 before connecting to AISStream or PostgreSQL and gives the exact configuration action. Other collector failures exit 1 with a trusted reason code; original exceptions, subscription contents and provider error bodies are not logged. The console prints status changes, approximately ten-second progress, and a final summary. WebSocket payload/debug logging is disabled for the private connection.

### Protocol and defaults

The [current AISStream documentation](https://aisstream.io/documentation) was checked on 2026-10-11. PortWatch uses one WSS connection, sends one complete subscription within two seconds of opening, requests `permessage-deflate`, decodes binary UTF-8 JSON, and accepts text JSON too. It requires the documented `SubscriptionConfirmation` and its `CompressionEnabled=true` within ten seconds. Bounds are converted explicitly from `[west, south, east, north]` to `[[south, west], [north, east]]`. Filters request `PositionReport`, `StandardClassBPositionReport`, `ExtendedClassBPositionReport`, `LongRangeAisBroadcastMessage`, `ShipStaticData` and `StaticDataReport`.

Provider limits are three subscribed connections per account, three open connections per originating IP, an initial subscription within three seconds, and at most one subscription update per second. This collector makes no updates and closes the old socket before reconnecting; a local dataset lock rejects a second owner. Other applications using the same account/IP still count toward provider limits. Compression confirmation is required because the provider documents bandwidth restrictions on uncompressed traffic.

| Setting | Default / bound |
| --- | --- |
| `PORTWATCH_LIVE_QUEUE_RECORDS` | 128 records; configurable 1–1,024 |
| Decompressed message size | 65,536 bytes maximum; oversized messages fail |
| WebSocket receive queue | 8 frames; separate from application queue |
| `PORTWATCH_LIVE_FLUSH_SECONDS` | 1 second; configurable greater than 0 through 5 |
| `PORTWATCH_LIVE_ROTATE_SECONDS` | 30 seconds; configurable 1–300 |
| `PORTWATCH_LIVE_ROTATE_BYTES` | 1 MiB target; configurable 256 KiB–16 MiB |
| `PORTWATCH_LIVE_SPOOL_DIR` | `data/live-spool` |
| `PORTWATCH_LIVE_STATUS_DIR` | `artifacts/live` |
| Keepalive | Client Ping every 20 seconds; wait at most 20 seconds for Pong |
| Reconnect | Exponential delay starting at 1 second, 0.8–1.2 jitter, capped at 60 seconds; reset after a confirmed connection lasts 60 seconds |

The [websockets client](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html) reassembles and decompresses incoming messages with a bounded size. PortWatch sends the subscription as one unfragmented JSON message. The client also answers server Ping frames. `last_socket_heartbeat` records a successfully received Pong to the client's Ping, not an AIS observation. A heartbeat can advance while vessel traffic remains absent.

### Raw records, durability and recovery

Each received frame becomes one JSONL envelope containing source/dataset, connection UUID, UTC receipt time, session-local sequence, available provider `MetaData.time_utc` text, classification and lossless base64 original payload. See [DATA_CONTRACTS.md](DATA_CONTRACTS.md). Position observations, static metadata, protocol controls, unknown messages and parse errors remain distinguishable. No position cleaning, timestamp rounding, visit inference or historical/live merging occurs. Provider timestamp text is retained without assuming it is a fully validated observation time; AIS UTC-second fields alone do not establish a date.

The reader only handles bounded protocol/envelope work and enqueues records. A separate writer performs disk and database work outside the event loop. If the application queue fills, capture stops with `buffer_pressure`, drains existing records and the one held overflow record, and reports the event. It never silently discards at that boundary. A disk/database failure stops capture, retains recoverable active files, and reports how many received records were not finalized.

Active `.active` files and non-secret `.meta` sidecars live under `data/live-spool/<dataset_id>/`. Writes receive periodic flush plus `fsync`; completed batches rotate by interval, size or UTC receipt hour and finalize through `RawStore`. Database registration follows checksum-verified publication. Final immutable JSONL and acquisition manifests live under `data/raw/aisstream_v0/live/YYYY/MM/DD/HH/<dataset_id>/`. Active data and status outputs are ignored by Git.

On restart, under the exclusive dataset lock, recovery reads active files in bounded lines and publishes readable complete envelopes. A valid final JSON record without a newline is retained. An incomplete tail is preserved separately as `.tail` with `.tail.meta`, its byte count appears in status and the final summary, and it is not counted as an observation. Invalid complete records, missing metadata or source mismatches fail recovery while preserving the files for investigation. A crash after raw publication but before active cleanup safely reuses the same raw batch identity. Never delete the spool to suppress a recovery error. For already published but unregistered batches, use `storage inspect` and `storage reconcile` from [README.md](../README.md).

The flush interval is a target, not a guaranteed loss bound. Queued records, in-flight socket frames and writes since the last completed `fsync` are volatile. Slow storage can extend that window. A hard kill or power failure can lose them, even though complete readable active records are recoverable. Windows file flushing is supported; directory flushing has the Step 02 platform limits. AISStream documents no durable replay, so events missed during outages or provider/client pressure may be unrecoverable. A connected socket does not establish complete port coverage.

### Status and failure actions

Status includes connection and confirmation state, compression, receipt/Pong/observation times, received/observation/static/control/unknown/parse counts, reconnects, current queue occupancy and high-water mark, pressure events, written/finalized records, raw persistence failures, recovered records/tail bytes, bounded recent batch paths and distinct valid MMSI count. The distinct counter is capped at 50,000 values; exceeding it stops explicitly. Only the latest 20 batch paths are retained in status; immutable raw manifests remain the full batch ledger. Startup recovery output counts can exceed new socket input counts in a pipeline run.

| Failure | Next action |
| --- | --- |
| Missing/rejected key or unconfirmed clean close | Configure/rotate key; verify account/IP limits and subscription. No endless authentication retry. |
| Confirmation timeout or three repeated unconfirmed disconnects | Check key and provider/network availability, then retry deliberately. |
| Compression not confirmed | Check negotiated WebSocket compression; capture stops rather than assume full delivery. |
| Protocol/policy/size failure | Check provider protocol/limits; do not enlarge buffers without investigating. |
| Buffer pressure | Check local storage speed and workload; existing queue and held record drain before exit. |
| Raw persistence failure | Check disk and PostgreSQL; preserve spool, then restart/reconcile after fixing the cause. |
| Recovery failure | Inspect the identified local active files/metadata; do not fabricate provenance or overwrite source bytes. |
| Collector already running | Stop the other local owner cleanly; OS releases its lock when the process ends. |

### Verification state

Step04 offline checks and the Step05 real capture passed. Key is now saved in ignored `.env`. The finite LA capture lasted 60.843 seconds and finalized 38 envelopes: 26 position messages, 11 static messages and one confirmation; no parse errors/reconnects/unfinalized received records. Collector is stopped. This short window does not establish complete harbor coverage. Synthetic tests remain isolated and exercise failure/recovery paths. [HANDOFF.md](HANDOFF.md) records checks and files.

## Step05 saved-input cleaning

PostgreSQL/PostGIS must be running and migrations applied. This machine now uses private `pg-runtime/` with the same native data directory and port; after reboot use README's hidden-window start and bounded `WaitForExit`, not `Start-Process -Wait` (which can wait for server descendants). No Docker is required. Preparation is already done; `scripts/prepare_postgis.py` reproduces the private overlay without touching running servers or reinitializing data.

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch doctor
.\.venv\Scripts\python.exe -m portwatch transform positions --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch transform positions --port los_angeles --pending-live
.\.venv\Scripts\python.exe scripts/verify_positions.py
```

Use exactly one transform mode. Demo needs saved NOAA raw/derived v2 input; missing input requires the historical ingestion command first. Live mode reads finalized raw envelopes only, processes pending inputs and reuses counts for committed ones. It needs no live connection or API key. Static/control messages have explicit skips; useful static JSON is retained separately. Successful statuses and analytical rows appear together after transaction commit; failed ledger records keep only safe exception classes. Quarantine payloads retained before a failed commit are recoverable evidence; do not delete raw data or reset the database.

Inspect rejects through `clean_records` where `outcome='quarantined'`; its `reason_code`, `raw_batch_id`, `record_offset`, `offset_unit` and `quarantine_path` identify evidence under ignored `data/quarantine/positions-v1/`. Sidecar includes source/version lineage. Specific invalid records quarantine; optional missing or suspicious motion only flags valid positions. Fix storage/migration/configuration issues, then rerun the same command. Completed-input replay preserves business rows and reconciliation counts.

Verifier writes ignored `artifacts/step05/verification.json`: two passes, identical business hashes/counts, PostGIS point checks, canonical lineage and timestamp-choice checks. Actual NOAA uses documented observation UTC; actual live uses receipt fallback with separate provider metadata time. Full event-time semantics and provider coverage limitations remain explicit.
