# PortWatch runbook

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
