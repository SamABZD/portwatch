# Data contracts

## Versioned geofences and memberships (Step06)

GeoJSON polygons are approximate analytical vicinity zones, in 2D longitude/latitude EPSG:4326.
`geofence_versions` owns the canonical document SHA-256, reference, review date, uncertainty and
validity metadata. `port_zones` owns valid nonempty versioned MultiPolygons. Updates/deletes are
refused; changing geometry or metadata requires a new version. Earlier versions remain queryable.
Operational effective dates are unknown, and the frozen-day application is an approximation.

`position_zone_assignments` has grain `(position_id, port_id, geofence_version, assignment_version)`.
Position ID identifies the unchanged source observation/cleaning version. `position_zone_memberships`
adds zone ID to that key and preserves every matching polygon. `covers-v1` uses boundary-inclusive
ST_Covers. Outside observations have no primary zone and zero memberships. Primary priority is
terminal (10), harbor (20), regional waiting candidate (30); equal priorities sort zone ID.
Overlap is preserved independently from primary display choice. Candidate membership is neither
waiting nor an LA visit. Source/dataset/cleaning version/UTC filters scope the demo; live stays separate.

Assignments insert missing keys transactionally; failure exposes no partial summary/membership
rows. The CLI default requires a configured/loaded checksum match; `--version` explicitly selects
a previously loaded version. Geographic areas and distances use PostGIS geography units, never
degrees as meters. [GEO_ZONES.md](GEO_ZONES.md) records approximation evidence, reference checks,
schema, observed results and limitations. The collection filter is not an analytical port boundary.

## Raw batch and manifest v1 (Step 02)

Raw payload bytes are immutable provider output. Storage does not parse or rewrite them. Synthetic data is used only in tests and is identified by `synthetic_*` source and dataset IDs. Each completed payload has a sidecar `<payload>.manifest.json` with `schema_version: 1`. Sidecars describe acquisition; they are not a copy of current database processing status.

| Field | Contract |
| --- | --- |
| `batch_id` | SHA-256 of compact JSON array `[source_id, dataset_id, public_source_url, content_sha256]`. Stable across retries and processing versions. |
| `source_id`, `dataset_id` | Safe identifiers, beginning with an alphanumeric character; no path separators. Historical and live IDs stay separate. |
| `source_url` | Public HTTP(S) or WSS provenance URL, excluding userinfo, query parameters, and fragment. Never put a secret in a URL path or identifier. Query-dependent public request parameters must be defined in source/dataset configuration separately. |
| `mode`, `source_date` | `historical` requires a source date; `live` uses the UTC receipt hour for storage. |
| `local_path`, `file_suffix` | Path relative to the configured raw root, derived from metadata and batch identity. No traversal outside that root. Historical suffix may include compression, such as `.csv.zst`; live suffix is `.jsonl`. |
| `content_sha256`, `byte_count` | SHA-256 and exact length of unchanged stored bytes, including compression if present. Both must verify before registration. Zero-byte files are allowed but mean empty source bytes, not zero vessels. |
| `record_count` | Nonnegative count if genuinely known; otherwise null. Storage does not invent a row count. |
| `received_at` | Timezone-aware acquisition time normalized to UTC. First receipt is retained on identical retries. |
| `source_start_utc`, `source_end_utc` | Both null when unknown; otherwise timezone-aware UTC range with end at or after start. Source-specific inclusivity/precision belongs in dataset configuration and provenance. |
| `processing_version` | Acquisition/storage version initially `raw-v1`; changing processing logic does not create different raw bytes or a new raw identity. |
| `state` | Sidecar intent is `complete`; a batch is usable only when the referenced file exists and its checksum/size verify. |
| `remote_etag`, `remote_last_modified`, `expected_byte_count` | Optional additive v1 acquisition fields. Retain server identity headers and declared transfer length when available; expected length must match stored bytes before publication. ETag is not a SHA-256 substitute. |

Arbitrary extra manifest fields are rejected. API keys and connection passwords are never accepted as metadata fields. The writer removes URL credentials and all query/fragment content before manifest construction. CLI output and database error summaries use exception **class names**, not exception messages, SQL parameter dumps, keys, or response bodies. Raw records remain original bytes and can contain private source data; their directories are ignored by Git.

## Paths and revision behavior

Historical: `data/raw/<source_id>/historical/YYYY/MM/DD/<dataset_id>/<batch_id><suffix>`. Date is the source date, not the date of download.

Live: `data/raw/<source_id>/live/YYYY/MM/DD/HH/<dataset_id>/<batch_id>.jsonl`. Date/hour is UTC receipt time. Original source event timestamps stay inside records; late events do not move an old raw file. Identical content received again in another hour reuses the first manifest and path.

Database uniqueness is both `UNIQUE(batch_id)` and `UNIQUE(source_id, dataset_id, source_url, content_sha256)`. An identical file under the same source/dataset/public URL is a no-op. Changed bytes from the same URL have a different checksum and batch identity, preserving both revisions. Different source/dataset identities remain separate even when bytes match. This identity applies to raw acquisition; later processing versions need their own run bookkeeping. An existing file whose bytes no longer match its manifest is corrupt and must be investigated; the writer refuses to overwrite it.

## Publication and recovery boundary

1. Write chunks to a `.part` file inside the raw root; calculate SHA-256, flush, `fsync`, and close it.
2. Hold the raw-root publication lock. Create parent directories, then atomically publish a flushed recovery sidecar on the same filesystem **before** renaming the payload.
3. Rename the closed payload to its immutable final path. Flush parent directories where the OS supports directory `fsync`.
4. Register only the complete, verified payload and sidecar in PostgreSQL. An acquisition caller uses `RawStore.write(...)` followed by `register_batch(...)`; database failure leaves the original bytes and recovery metadata intact.

There is no atomic transaction across the filesystem and PostgreSQL. `storage reconcile` scans sidecars and verifies bytes before inserting missing database rows with `ON CONFLICT DO NOTHING`. It does not download, transform, or replace data. Registration never resets an existing batch's status, receipt time, or attempts.

| Observed state | Discovery and recovery |
| --- | --- |
| `.part` file only | Excluded from completed inputs. A failed/terminated download can leave it behind; investigate and remove only the identified temporary file when appropriate. |
| Sidecar published, payload absent | `pending`; never registered. Retry acquisition of the exact same bytes can finish that intent. Reconciliation cannot recover bytes that were never published. |
| Sidecar and verified payload, no DB row | `complete` orphan; reconciliation registers it without another download. |
| Sidecar and verified payload, existing DB row | Idempotent no-op after checking immutable metadata. |
| Bad sidecar, checksum mismatch, or payload without sidecar | `invalid` or `unmanifested`; inspection/reconciliation exits 1. Do not register, guess provenance, or silently repair bytes. |

The OS lock is released on process death, avoiding a stale-lock deletion protocol. A scanner can observe a brief pending intent during an active publication; a later scan resolves it. Verified process-interruption recovery does not establish protection against every power-loss/filesystem scenario. Windows supports file `fsync` but this implementation cannot `fsync` directories through the standard library; POSIX directory flushing is included. Keep raw storage on a local filesystem that supports atomic same-filesystem rename.

## Database status and runs

`ingestion_batches` starts as `registered` with `attempt_count=0`. `begin_batch_processing` permits `registered` or `failed` to become `processing`, increments attempts, and clears a prior error. `finish_batch_processing` permits `processing` to become `processed` or `failed`; a failure stores only its exception class. Duplicate registration is unrelated to processing attempts. An interrupted processing job can leave `processing`; a later orchestration step must establish that the job ended before marking it failed and retrying. Reconciliation itself does not infer processing completion.

`pipeline_runs` has a unique UUID run ID, stage, UTC start/end, status, nullable nonnegative input/output counts, and non-secret error summary. Storage reconciliation records `running` then `succeeded` or `failed`. Counts refer to scanned manifests/files and newly registered batches, not AIS records. Hard termination can leave a run `running`; do not treat it as success. The schema reserves `interrupted` for explicit later recovery handling. Ordinary runs that end must have an end time; failed/interrupted runs require an error summary.

The migration runner serializes concurrent runners with a transaction advisory lock. `schema_migrations` records numbered SQL filenames, checksums, and applied UTC timestamps. New migrations and their ledger entries commit together; failure rolls them back. Already applied SQL must remain unchanged. Missing, edited, duplicate-numbered, or out-of-order migrations fail; the runner never resets existing data. [PostgreSQL conflict handling](https://www.postgresql.org/docs/current/sql-insert.html) supplies database uniqueness, while sidecar reconciliation supplies filesystem recovery.

## Quarantine v1

`data/quarantine/<batch_id>/<zero-padded-record-offset>-<payload-sha256>.bin` preserves original rejected record bytes. Its sidecar records schema version, batch ID, zero-based **byte offset** within the original decoded input stream, a non-secret reason code, checksum, and byte count. A caller must retain compressed/source-file provenance so a decoded offset is interpretable. Do not pass exception text as a reason. Existing quarantined bytes are verified rather than overwritten. Quarantine has its own root and is not scanned as complete raw input.

## Historical acquisition and regional derivation (Step 03)

`ingest historical --port los_angeles --demo` reads the frozen source and UTC day from `configs/demo.yaml` and bounds from the port config. Explicit `--start YYYY-MM-DD --end YYYY-MM-DD` uses an exclusive end and supports 1–7 UTC days within the audited 2024 source. It creates one dataset per day and rejects unsupported ports, years, reversed ranges, missing dates, or dates combined with `--demo`.

Downloads use 1 MiB chunks, a 512 MiB maximum per daily file, 30-second network timeouts, a 600-second transfer deadline and at most three attempts with bounded exponential backoff. HEAD/GET lengths and ETags must agree when supplied. Strong ETags are sent as `If-Match`; a 412 response retries with a fresh HEAD within the same attempt limit. Transfers restart into a new temporary file rather than resume. Declared length, actual bytes and the local SHA-256 are checked before publication. Empty downloads, partial responses, unexpected content encoding and oversized files fail. Parquet footer validation precedes database registration. No independently published provider checksum was available; the local hash verifies the stored snapshot and later reads.

A default rerun verifies the cached raw snapshot and makes no HTTP request. `--refresh` checks remote identity and avoids a GET when ETag and length match. Changed bytes create a new immutable raw revision. A hidden, atomically written `.source-selection-<request-hash>.json` in the raw root records the currently selected batch, remote ETag, length and verification time; this mutable pointer is separate from immutable acquisition receipts and is ignored by raw scanning. It also handles provider rollback to a previously acquired hash without overwriting its first receipt. A missing pointer falls back to the most recently acquired matching batch; an invalid pointer target fails.

The extractor reads at most 65,536 records per batch with Parquet prefetch disabled. It decodes WKB Points into named `longitude` and `latitude`, rejects missing, invalid, non-point, non-finite or out-of-WGS84 coordinates, and applies the inclusive collection box before selecting the half-open UTC window. Explicit unknown or unsupported CRS fails; absent GeoParquet CRS follows its WGS84 default. Invalid-coordinate and missing-time counts are national input measurements. All 16 source fields remain unchanged alongside:

- `longitude`, `latitude`, `observed_at_utc`;
- `source_row_offset`: zero-based national Parquet **row index**, distinct from quarantine byte offsets;
- `source_batch_id`, `source_id`, `dataset_id`, `derivation_id`;
- `source_time_quality`: `provider_documented_utc`, preserving original second values without rounding or resampling.

No visits or waiting states are inferred. Distinct vessel counting retains only regional non-null MMSI values, with a 50,000-value ceiling to avoid unbounded growth. Preview logs contain at most five timestamps and coordinate pairs without vessel identifiers. Peak memory uses the OS lifetime peak working set/RSS when available and states its measurement scope; unavailable measurements stay null.

Derived output lives at `data/derived/<source_id>/<dataset_id>/<port_id>/<derivation_id>/positions.parquet`, with an adjacent manifest. The identity hashes the parent batch/hash, bounding box, UTC window, port, batch size, code version and relevant runtime versions. Changed parameters or versions get a new identity. The manifest records those inputs plus its own checksum/bytes, row counts, coordinate/time rejection counts, national/regional time spans, distinct vessel count, preview, maximum batch, elapsed time and process memory measurement.

Derived publication closes and flushes the temporary Parquet, computes its checksum, publishes the manifest intent under a lock, then renames the artifact. A matching rerun checks its checksum and reuses it. Corruption fails; an intent without an artifact can be regenerated from the same raw input and must match the intended checksum. The raw source remains separate. Historical `pipeline_runs` records source and selected record counts; a cached run reports the logical dataset counts while the CLI reports reuse. The raw batch remains `registered` for later cleaning, without inventing a cleaning attempt.

`scripts/verify_historical.py` resolves the current raw selection and extraction identity, checks every regional row in bounded batches and plots at most 5,000 sampled positions. `--rebuild` regenerates the extract from raw into ignored artifacts and compares checksums. Matplotlib is a development dependency; the ingestion runtime does not need it. Exact reconstruction assumes the recorded code and runtime versions.

## Live envelope and active spool v1 (Step 04)

Live source ID is `aisstream_v0`; Los Angeles dataset ID is `los_angeles_live_aisstream_v1`, separate from historical NOAA. The public provenance URL is the fixed provider WSS endpoint. The acquisition version is `live-envelope-v1`. Each raw JSONL record is a `LiveEnvelope` with closed schema version 1:

| Field | Contract |
| --- | --- |
| `source_id`, `dataset_id` | Safe identifiers matching the batch; synthetic fixtures use explicitly synthetic IDs. |
| `connection_session` | UUID, renewed on every socket connection. |
| `received_at` | Local timezone-aware UTC receipt time, separate from source time. |
| `record_sequence` | Positive integer starting at 1 per connection session. |
| `source_timestamp` | Available `MetaData.time_utc` text, otherwise null; no invented date or timestamp precision. Original provider values remain inside payload. |
| `kind` | `ais_observation`, `ais_static`, `protocol_control`, `parse_error` or `unknown`; controls are not vessel observations. |
| `frame_type` | `binary` or `text`, preserving transport message type. |
| `payload_encoding`, `payload_base64` | Base64 of exact received decompressed message bytes; text is represented by its original UTF-8 bytes. No reserialization of provider JSON. |

Wire compression/fragments are handled by the WebSocket library; envelopes preserve application messages rather than compressed network packets. At most 65,536 decompressed bytes per message and a bounded application queue are accepted. Source timestamp strings stay unparsed in this stage; raw batch source-start/end timestamps remain null. Manifest `record_count` counts all envelopes, including controls and parse errors, rather than only position observations.

The active spool has `.meta` acquisition intent before `.active` JSONL writes. A single local dataset owner holds an OS lock during recovery and capture. Frequent flush/fsync and interval/size/UTC-hour rotation publish through Step 02 raw storage, then register in PostgreSQL. Recovery validates each readable complete envelope/source in bounded lines, publishes unchanged complete bytes, preserves/report incomplete tails, and refuses invalid complete records. Original JSON without final LF is readable when its full envelope validates. An interrupted publication reuses the same raw identity; database reconciliation remains available for verified raw orphans.

Socket reading and the serial persistence worker are separate. Queue pressure stops capture and drains both buffered records and the held overflowing record. Persistence failures leave active bytes and metadata; received-but-unfinalized counts remain explicit. Cancellation joins filesystem workers before releasing ownership, and final status waits for serialized in-flight writes. Volatile queue/socket records and writes since the last successful flush can be lost on hard termination; provider events missed during an outage may have no replay. See [RUNBOOK.md](RUNBOOK.md) for default limits, statuses and recovery actions.

## Step 05 clean positions (`positions-v1`)

`src/portwatch/positions.py` defines the shared source-adapter contract. `vessel_positions` stores:

| Fields | Contract |
| --- | --- |
| `source_id`, `dataset_id` | Keep NOAA and AISStream separate; never collapse sources into an authoritative track. |
| `mmsi` | Nine ASCII digits as text; zero identifier invalid. Integer source IDs are zero-padded to nine digits; no claim of registry verification. |
| `observed_at` | Full source observation time only when its UTC semantics are documented. NOAA `base_date_time` is explicitly interpreted as UTC, never the Windows timezone. Live value remains null. |
| `received_at` | NOAA artifact acquisition start from raw manifest; live envelope receipt from local collector. UTC `timestamptz`. |
| `effective_at`, `timestamp_quality` | NOAA uses `observed_at` / `provider_documented_utc`; live uses `received_at` / `receipt_fallback`. All analysis uses this explicit choice. |
| `provider_reported_at`, `source_timestamp_raw` | Preserve live metadata timestamp separately, parsed only with explicit zone. PostgreSQL precision is microseconds; original nanosecond text remains lossless. This metadata time is not verified vessel event time. |
| `longitude`, `latitude`, `position` | Geographic decimal degrees and PostGIS `geometry(Point,4326)` with longitude first. Use typed live position body, never metadata's last-known position. |
| `speed_knots`, `course_degrees`, `heading_degrees` | Optional numeric measurements; missing, unavailable and invalid each have a distinct quality flag. Values are in knots/degrees already, without additional scaling. |
| `vessel_type`, `navigation_status` | Optional codes; unknown 0/15 retained with flags. Reserved/unrecognized nonnegative codes preserved, not rejected or relabeled. Optional malformed values become null with flags. |
| `source_batch_id`, `record_offset`, `offset_unit` | Raw parent identity; NOAA zero-based **national Parquet row** or live **JSONL byte offset**. These units are explicitly different from Step02 generic decoded-stream offsets. |
| `source_record_id`, `observation_id`, `processing_version` | Separate SHA-256 raw-record identity and normalized-observation fingerprint; current version `positions-v1`. |
| `connection_session`, `record_sequence`, `quality_flags` | Live session/sequence retained; flags record optional-value quality and suspicious motion. |

**Verified source rules.** [NOAA GeoParquet README](https://raw.githubusercontent.com/ocm-marinecadastre/ais-vessel-traffic/main/data/ais-broadcast-points-2024-readme.md) specifies UTC; [NOAA dictionary](https://coast.noaa.gov/data/marinecadastre/ais/data-dictionary.pdf) documents knots/degrees and text MMSI. NOAA's experimental GeoParquet is already cleaned; no exhaustive provider-specific sentinel map was found. Null optional motion values are normal. AIS sentinel checks below are defensive for NOAA and message-specific for live data, following [USCG Class A / long range](https://www.navcen.uscg.gov/ais-class-a-reports) and [Class B](https://www.navcen.uscg.gov/ais-class-b-reports). AISStream's actual JSON uses decoded decimal degrees/knots; its [published models](https://raw.githubusercontent.com/aisstream/ais-message-models/master/type-definition.yaml) do not establish `MetaData.time_utc` as vessel observation time. Do not complete the AIS UTC-second `Timestamp` with a guessed date/minute.

- Longitude 181 / latitude 91 quarantine as `unavailable_position`; other out-of-range/nonfinite coordinates as `invalid_coordinates`; missing coordinates as `missing_position`.
- Class A and Class B SOG 102.3, COG 360 and heading 511 become null with unavailable flags. SOG 102.2 retains its value with a lower-bound flag. Message27 uses SOG63 and COG511; heading is absent. Coarse long-range position is flagged.
- NOAA float32 motion values normalize to four decimal places before sentinel comparison, removing storage noise without adding a scaling factor. Coordinates retain exact source doubles. Optional negative/out-of-range/nonfinite values become null with invalid flags. Strict live JSON rejects NaN/Infinity, excessive nesting and malformed bytes as `invalid_payload_json`; huge numeric values cannot overflow validation.
- Reported speed above 60 knots is flagged. Adjacent positions separated by over 100 metres with implied speed above 80 knots are flagged `implausible_jump`; different positions over 100 metres apart at the same effective time get `conflicting_same_time_position`. These provisional screening thresholds retain observations. They do not classify waiting, reject maneuvers or claim physical certainty. Live jumps use receipt-time gaps and inherit network timing uncertainty. Adjacency flags are refreshed per source/dataset/version when new batches arrive.

**Identity and reconciliation.** Raw-record identity hashes `[source_id, raw_batch_id, offset_unit, record_offset]`. Observation fingerprint hashes source, dataset, text MMSI, reliable full observed time, exact longitude/latitude, message type, normalized speed/course/heading and navigation status. Without reliable observed time it instead uses original receipt, connection session and sequence; cross-delivery deduplication is intentionally limited. A stationary vessel at different observation times remains multiple observations. Identical historical observations across saved source records become one position with separate `clean_records` lineage for every duplicate. NOAA has no retained AIS message ID, so its message type is `noaa_position`.

Each source record has exactly one outcome: `valid`, `duplicate`, `quarantined` or `skipped`. Counts satisfy `input = valid + duplicate + quarantine + skip`; valid counts mean canonical accepted records, not new inserts on a replay. `vessel_static_messages` preserves useful typed static JSON linked through its source record and raw envelope. Static/control/unsupported messages have explicit skip reasons and never create positions. Malformed static records quarantine rather than become positions or fail otherwise valid batches.

`clean_batches` identifies the raw/derived input and processing version, storing frozen committed counts. A global transaction advisory lock serializes local cleaning; bounded 4,096-row Parquet batches and bounded JSONL lines stream into a PostgreSQL temporary COPY table. Positions, all outcomes, static messages, counts and processed status publish in one transaction. Failure rolls back analytical rows/status and records only a safe exception class in a failed ledger; retry remains possible. Success becomes visible only when the transaction commits. Completed input replay verifies saved input hashes, reuses the ledger and leaves business rows unchanged. Identity/version uniqueness, foreign keys, source/vessel/time, dataset/time and spatial GiST indexes enforce and support these contracts.

**Quarantine evidence.** Ignored `data/quarantine/positions-v1/<source>/<offset_unit>/<raw_batch>/` holds original live envelope bytes or a tagged logical Parquet row JSON. Tagged `$datetime`, `$bytes_base64` and `$float` preserve source values; the unchanged raw Parquet and national row offset remain authoritative. Generic payload sidecar stores hash/bytes/reason; additional `.lineage.json` stores source, dataset, batch, offset/unit, input ID, reason code and transformation version. `clean_records` links the retained file. Payload publication is fsynced before DB commit; files retained after a failed transaction are evidence, not successful processing, and replay verifies/reuses them. No filesystem/DB atomicity is claimed.

**Actual event/receipt examples.** A NOAA record observed at `2024-01-01T00:00:00Z` was acquired at `2026-10-11T18:20:04.839064Z`; analysis uses the 2024 event time. A live record had provider metadata `2026-10-11 19:25:04.152360282 +0000 UTC`, AIS UTC-second `3`, and local receipt `2026-10-11T19:25:07.311366Z`. Its full vessel event time is unverified, so `observed_at=null`, effective time is receipt, and provider text is preserved separately.

```powershell
.\.venv\Scripts\python.exe -m portwatch transform positions --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch transform positions --port los_angeles --pending-live
.\.venv\Scripts\python.exe scripts/verify_positions.py
```

Choose exactly one cleaning mode. Both read saved inputs only; historical mode resolves the current source pointer or the same latest verified receipt rule used by Step03 for older caches. Live mode scans finalized LA raw batches, processes pending ones and reports frozen counts for completed ones. Active spool is not analytical input. Verification hashes position/lineage/static business rows across two runs and checks geometry, UTC choice and reconciliation.

## Raw storage commands

```powershell
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch db migrate
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch storage inspect
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch storage reconcile
```

Storage commands accept `--root <path>` for a different raw root. `inspect` requires no database; `reconcile` requires successful migrations. Exit 0 means the requested operation completed with no integrity/pending issues; exit 1 means configuration, connection, migration, integrity, or pending-publication issues. An empty root is valid and reports zero completed files.
