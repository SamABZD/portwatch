# Step 01 source audit

Step09 review (2026-10-12): no provider requests or source changes. The analytical mart uses the
saved NOAA series and one selected historical ERA5 revision; recent news/forecast are excluded.
Known-boundary confirmations and completed midpoint durations remain experimental inference,
not verified port calls/dwell. Counts include all vessel types and all reporting points in the
collection region. The sole candidate remains reported pilot-vessel type50, contributing two
candidate-vessel hours and one duration sample; it is not a demonstrated cargo queue. Coarse
weather, approximate zones, gaps and unknown boundaries remain explicit. Processed source
intervals and event-time recency do not establish total AIS coverage or today's collector health.
All original source/visit business hashes and raw checksums remain unchanged.

## Step08 recheck and real requests (2026-10-12)

Primary [archive](https://open-meteo.com/en/docs/historical-weather-api),
[forecast](https://open-meteo.com/en/docs), [terms](https://open-meteo.com/en/terms),
[licence](https://open-meteo.com/en/licence) and
[GDELT DOC](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) documentation were rechecked.
Free non-commercial weather terms still list fewer than600/minute,5,000/hour and10,000/day calls.
Local policies are much lower; no key/payment was used. Attribution and interpretation are
documented for future presentation in [CONTEXT.md](CONTEXT.md).

- Archive GET200 at12:25:00.890960UTC: latitude33.71466, longitude−118.24239; date2024-01-01,
  hourly wind_speed_10m/precipitation, models=era5, UTC/unixtime, ms/mm, sea preference.
  Saved24 points00:00–23:00UTC; actual cell33.5 / −118.25. ERA5 has no archive gusts in the audited
  matrix, so gusts were not requested. Wind model values0.85–3.92m/s. Returned precipitation
  values sum0.2mm over their preceding-hour intervals, not a claimed calendar-day rainfall total.
- Forecast GET200 at12:26:18.591898UTC: same requested location/units, dates2026-10-12 through
  2026-10-13, best_match and wind/gusts/precipitation. Saved48 points; actual cell33.72767 / −118.24222.
  Neither response provided model issue/run time; generationtime_ms remains computation duration.
- GDELT bounded ArticleList request: query `("Port of Los Angeles" OR "Los Angeles Harbor")
  (disruption OR closure OR strike OR congestion OR storm)`, maxrecords100, explicit interval
  2026-10-11T12:15:00Z to2026-10-12T12:15:00Z. **HTTP429**, recorded `source_rate_limited`; no
  immediate retry, no real articles and no empty-success claim. Earliest local retry12:40:00.277470UTC.
  Its docs allow explicit recent intervals and a maximum250-record cap. First-seen metadata is
  separate from unknown publication time. News is excluded from the2024 replay.

Exact public parameter maps, response metadata, original bytes, raw checksums and attempts are
retained locally. Real news nonempty/empty responses remain externally unverified in Step08;
synthetic tests cover both shapes and throttling. Existing Step01 `{}` evidence remains historical
probe evidence, not a fresh2026 feed success. Weather is model context, not on-site observations
or causal evidence for queueing.

Step07 review (2026-10-12): historical source and saved observations were preserved. The single
low-speed candidate reports vessel type50 on all 57 eligible records. Verified the
[USCG AIS type table](https://navcen.uscg.gov/ais-class-a-reports): code50 denotes pilot vessel.
This is reported AIS metadata, not independently verified identity or queue/berth evidence.
No new provider acquisition or additional day was required. [VISITS.md](VISITS.md) records
manual real-track review, sparse-context limits and retrospective/event-time-prefix semantics.

Checked 2026-10-11. Scope: $0, local-first Port of Los Angeles demo. Step 01 checks below were access probes. Step 03 downloaded the selected NOAA artifact and measured real regional observations, recorded separately below. No news articles were saved or observations invented. Provider responses and coverage can change.

## Frozen historical demo

Step06 update (2026-10-12): reviewed the official
[2023 Port of Los Angeles terminal schematic](https://kentico.portoflosangeles.org/getmedia/07e1377d-b452-4ecb-a629-9a0c69410805/pola_terminals_map)
visually and compared six manually authored vicinity polygons with saved 2024 AIS tracks.
`geo/los_angeles.geojson` records version, uncertainty, references and reviewed exclusion points.
The schematic does not supply surveyed coordinates or establish historical operational validity.
The shared offshore candidate is regional; no anchorage, waiting or visit evidence is claimed.
See [GEO_ZONES.md](GEO_ZONES.md) for the exact approximation and source limitations.

[`configs/demo.yaml`](../configs/demo.yaml) is the single source of truth for later commands: NOAA OCM 2024-01-01, from `2024-01-01T00:00:00Z` inclusive to `2024-01-02T00:00:00Z` exclusive, with the same UTC weather period and a 5-minute replay interval. The interval controls presentation; it does not create five-minute AIS observations. `America/Los_Angeles` is display time only. All stored event times remain UTC. The one UTC day spans portions of two Los Angeles calendar dates.

## Source status

| Source | Status and evidence | Access, credentials, cadence | Format and time | Terms, attribution, rate handling, fallback |
| --- | --- | --- | --- | --- |
| [NOAA OCM 2024 daily AIS](https://github.com/ocm-marinecadastre/ais-vessel-traffic) | **Verified.** Selected [2024-01-01 GeoParquet](https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/ais2024/ais-2024-01-01.parquet): HEAD `200`, 227,181,435 bytes; first 16 bytes ranged GET `206`, `Content-Range: bytes 0-15/227181435`, `PAR1`; remote Parquet footer parsed. | Public HTTPS, no key. Historical daily files; published retrospectively, not a live feed. Download one immutable raw file in Step 03 and retain source URL, ETag, byte count, and hash. | GeoParquet 1.0.0, Snappy, WKB Point in WGS84; `base_date_time` is a Parquet millisecond timestamp without an encoded zone. NOAA documents UTC and nearest-minute downsampling. | [NOAA readme](https://github.com/ocm-marinecadastre/ais-vessel-traffic/blob/main/data/ais-broadcast-points-2024-readme.md) specifies CC0 and a suggested NOAA OCM citation. Honor the citation even though CC0 does not require attribution. Retry bounded transfers with backoff and checksum; fail the historical run if no genuine source file is accessible. [Alternate CSV.zst listing](https://noaaocm.blob.core.windows.net/ais/csv2/csv2024/index.html) exists, but that actual file was not probed. |
| [AISStream](https://www.aisstream.io/documentation/) | **Verified for a bounded LA capture in Step05.** 60.843 seconds: 26 position messages, 11 static messages and one confirmation; 38 finalized envelopes, no parse errors/reconnects. No complete harbor-coverage claim. | WebSocket `wss://stream.aisstream.io/v0/stream`. Key now saved in ignored `.env`. Supply `APIKey` in subscription, never logs or Git. Events arrive when received; no guaranteed fixed cadence or replay. Subscribe to documented latitude/longitude corner pairs derived from the project longitude/latitude box. | Binary UTF-8 JSON; typed report coordinates are current, metadata may be last-known context. Full source event-time semantics remain unverified; Step05 preserves metadata timestamp separately and uses receipt fallback. | Provider [documentation](https://www.aisstream.io/documentation/) limits connections and subscription updates; it promises no SLA. Specific redistribution/license terms were not verified, so do not publish raw messages until checked. On absent key, timeout, or outage, continue genuine NOAA historical replay and label live status accordingly. Zero messages during a test window means zero observed messages, not zero vessels. |
| [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api) | **Verified for one bounded request.** HTTP `200` JSON for 2024-01-01 at `33.71466,-118.24239`, `timezone=UTC`, `cell_selection=sea`, hourly wind speed and precipitation; 24 hourly timestamps from `00:00` through `23:00`. | Public HTTPS, no key for [noncommercial free use](https://open-meteo.com/en/terms). Historical hourly modeled data. | JSON `hourly.time` ISO-like strings without `Z`; response `timezone=GMT`, `utc_offset_seconds=0`. Units returned: wind speed km/h, precipitation mm. Historical weather is reanalysis/model output, not an on-port sensor reading. | [Open-Meteo attribution](https://open-meteo.com/en/pricing) and applicable upstream weather-data attribution must be shown in any later UI. Free-tier limits are documented as 600/min, 5,000/hour, 10,000/day; use caching and backoff. If unavailable, preserve AIS replay and mark weather unavailable rather than fill values. |
| [Open-Meteo forecast weather](https://open-meteo.com/en/docs) | **Conditionally available / untested.** Documentation reviewed, but no forecast response was requested in this audit. | Public HTTPS under the same documented noncommercial free tier; refresh cadence and live availability require a later live-mode test. | Documented JSON forecast time series; actual response shape, timestamp offset, and values remain unverified here. | Same Open-Meteo attribution and rate handling. Forecast weather belongs to live mode, not the January 2024 replay. |
| [GDELT DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) | **Conditionally available / rate-limited.** A bounded `timespan=1d`, `maxrecords=5` query returned HTTP `200` with `{}`: valid empty result, no article timestamps observed. A second bounded one-week query returned HTTP `429`. | Public HTTPS, no key in tested request. DOC API searches recent rolling content; no historical 2024 article context was verified. Poll only in live mode, initially no more often than every 15 minutes, and obey 429/backoff. This interval is a PortWatch policy, not a provider guarantee. | JSON `artlist` response when matches exist; timestamps must be parsed and labeled in live mode. Empty `{}` is distinct from HTTP error. | Attribute [GDELT](https://www.gdeltproject.org/) and link original publishers. Keep only article metadata, URLs, and short permitted snippets after rights review; never store whole copyrighted articles. For 2024 replay, display no current news as if historical. If live request fails, preserve last successful fetch time and mark news unavailable/stale. |

The [old NOAA 2024 ZIP index](https://chs.coast.noaa.gov/htdata/CMSP/AISDataHandler/2024/index.html) lists `AIS_2024_01_01.zip`, but a HEAD request to its derived direct ZIP URL returned HTTP `404` on 2026-10-11. The index alone is not proof of file access. The selected GeoParquet URL is confirmed by the [NOAA daily filename list](https://github.com/ocm-marinecadastre/ais-vessel-traffic/blob/main/data/daily-2024-parquet-filenames.md) and the HTTP/metadata probes above.

### Inspected NOAA file metadata

Step 01 remote Parquet footer inspection with `pyarrow 26.0.0` over a 65,536-byte HTTP tail range reported 7,293,408 rows in seven row groups, 16 fields, and GeoParquet 1.0.0 metadata. This is the **whole national daily file**, not a count of Los Angeles positions. PyArrow became a locked application dependency in Step 03.

| Field | Physical/logical type or meaning |
| --- | --- |
| `mmsi` | int32 vessel identifier |
| `base_date_time` | int64 Parquet timestamp, milliseconds, no embedded timezone; interpret as UTC under NOAA documentation |
| `sog`, `cog` | float32 speed over ground and course over ground |
| `heading` | int32 |
| `vessel_name`, `imo`, `call_sign` | strings |
| `vessel_type`, `status` | int32 |
| `length`, `draft` | float32 |
| `width`, `cargo` | int32 |
| `transceiver` | string |
| `geometry` | binary WKB Point; coordinates are longitude, latitude in EPSG:4326 |

NOAA describes this product as **cleaned and downsampled** to the nearest whole minute, with sentinel/anomalous values filtered. Its [InPort record](https://www.fisheries.noaa.gov/inport/item/73064) describes vessel-attribute cross-checking against AVID and receiver-coverage bias. Preserve this provider-side processing in provenance. Do not call file contents raw radio broadcasts or infer complete maritime coverage. Parquet storage resolution does not imply original observation precision.

### Step 03 observed data (2026-10-11)

The selected full GeoParquet was acquired once: 227,181,435 unchanged bytes, ETag `0x8DDAFF746752C72`, SHA-256 `3124557f70bb216e3a4d5817cf019368a6ca47d86aec8d3d2aa8c8e6d1e92560`. Inspection of all 7,293,408 national rows found zero unusable coordinates and zero missing timestamps; the national span is 2024-01-01 00:00:00 through 23:59:59 UTC. The frozen collection box and time window selected 62,159 observations and 141 distinct non-null MMSI values, spanning 00:00:00 through 23:59:58 UTC. Full counts, lineage, memory measurement, preview, geographic plot, and reconstruction evidence are in [RESULTS.md](RESULTS.md).

The observed timestamps do **not** match a literal whole-minute quantization: 61,496 of the 62,159 regional rows have nonzero seconds, and none has fractional seconds. NOAA's documented processing remains source provenance, but PortWatch does not label the actual values as rounded to the minute or assume a fixed one-minute observation cadence. The exact provider sampling procedure is unverified. Version `noaa-la-subset-v2` preserves the original timestamps, adds UTC under NOAA's documented convention, and labels their quality `provider_documented_utc`. No resampling or timestamp rounding is performed. Receiver coverage, provider cleaning, and possible gaps still limit later vessel and waiting interpretations.

## Step 04 AISStream protocol and external verification (2026-10-11)

The [current provider documentation](https://aisstream.io/documentation) and its [Python example](https://github.com/aisstream/example/blob/main/python/main.py) were rechecked for Step 04. The collector uses the focused configured latitude/longitude subscription, supported position/static types, compressed binary UTF-8 JSON and explicit subscription confirmation. It respects the documented connection/subscription constraints and handles text frames defensively. Operational behavior and precise limits are in [RUNBOOK.md](RUNBOOK.md).

At initial Step04 completion, key was absent and external collection unverified. User supplied a key for Step05; it was saved privately in ignored `.env`. Real finite capture then accepted the compressed subscription and finalized 38 envelopes: 24 Class A reports, two Class B reports, eight ship-static messages, three static reports and one confirmation. Collector observed 33 distinct MMSIs across position/static context; 26 clean positions represent 24 distinct MMSIs. No parse errors, reconnects or received-but-unfinalized records occurred. Exact batches and time ranges are in [RESULTS.md](RESULTS.md). This is evidence of access and observed messages in one short window; it is not complete harbor coverage or an availability guarantee.

### Step05 source semantics

The [NOAA dictionary](https://coast.noaa.gov/data/marinecadastre/ais/data-dictionary.pdf), [GeoParquet README](https://raw.githubusercontent.com/ocm-marinecadastre/ais-vessel-traffic/main/data/ais-broadcast-points-2024-readme.md) and inspected schema establish UTC, decoded knots/degrees and MMSI identifier semantics. NOAA has already removed some sentinels/anomalies; an exhaustive GeoParquet sentinel map is unavailable. Optional nulls are preserved as missing. The [2024 metadata](https://www.fisheries.noaa.gov/inport/item/73064) also describes vessel-attribute processing, so unfamiliar optional type/status codes are retained and flagged. Dictionary domains for 2025+ are not assumed to describe the selected 2024 experimental file.

[USCG Class A / message27](https://www.navcen.uscg.gov/ais-class-a-reports) and [Class B](https://www.navcen.uscg.gov/ais-class-b-reports) establish unavailable coordinate and motion values. Message27 differs: speed63/course511 unavailable; no heading or full timestamp. AISStream [generated models](https://raw.githubusercontent.com/aisstream/ais-message-models/master/type-definition.yaml) and current documentation do not verify `MetaData.time_utc` as vessel event time. Position `Timestamp` is UTC-second only. The first actual position contained UTC-second3, provider metadata `2026-10-11 19:25:04.152360282 +0000 UTC`, and local receipt `2026-10-11T19:25:07.311366Z`; analysis uses receipt, preserving provider time separately. No missing date/minute is guessed and metadata's last-known coordinates never become new static-message positions.

## Los Angeles collection area

The [Port of Los Angeles terminal map](https://kentico.portoflosangeles.org/getmedia/07e1377d-b452-4ecb-a629-9a0c69410805/pola_terminals_map) identifies LA facilities and harbor geography. The approximate [Pier 400 station position](https://www.ndbc.noaa.gov/station_history.php?station=pfdc1) and [outer-harbor point](https://www.rpvca.gov/DocumentCenter/View/7986) anchor the eastern harbor and approaches. The configured WGS84 collection box is **`[-118.34, 33.66, -118.225, 33.80]`** in `[west longitude, south latitude, east longitude, north latitude]` order. It is a hand-reviewed **heuristic filter**, not an official boundary. The eastern edge stays west of the [Long Beach Pier F example position](https://www.maritime.dot.gov/sites/marad.dot.gov/files/2023-02/PID2022000326%20Port%20of%20Long%20Beach%20Middle%20Harbor%20Terminal%20Zero%20Emission%20Conversion%20Project-Long%20Beach%20California%20Application.pdf), keeping that neighboring core identifiable. Border positions can still overlap harbor approaches. A later analytical polygon must be separately sourced, versioned, and tested; `geofence_path` remains null. No metric may label all points in this box as inside Port of Los Angeles.

Initial waiting heuristics remain 1.0 knot, 30 continuous minutes, maximum 15-minute observation gap. They are analytical assumptions, not port rules or observed thresholds. Given the documented provider sampling and observed second-level timestamps, validate gap behavior on actual LA tracks before reporting waiting estimates.

## Failure-state rules and Step 03 fallback

- **Failure:** non-2xx, parse error, timeout, or authentication error. Keep source error and last success separately; do not turn failure into empty data.
- **Valid empty:** successful response with no matched records, including the observed GDELT `{}`. Record request bounds and response time.
- **Zero observed messages:** a bounded successful AISStream window with no messages, only after a real subscription is established. It says nothing about vessel absence or full coverage.
- **Valid numeric zero:** a measured `0` from a successful source, with units and context. Do not replace null or failure with zero.
- **Stale:** prior successful data past its source-specific freshness threshold, marked with last successful fetch time.

If AISStream or context feeds remain unavailable, complete the one-day **real NOAA historical replay**. Do not blend NOAA and live AIS without a precedence rule. Weather for the replay must come from the matching 2024 period. Current GDELT articles belong only to live mode and must not appear as 2024 historical events.

## Reproducible probe requests and observed result

- NOAA HEAD: `HEAD https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/ais2024/ais-2024-01-01.parquet` returned `200` and `Content-Length: 227181435`.
- NOAA first bytes: `GET` same URL with `Range: bytes=0-15` returned `206`, `Content-Range: bytes 0-15/227181435`, and `PAR1` magic. Absolute tail range was used for the Parquet footer; no full-file download.
- Open-Meteo: `GET https://archive-api.open-meteo.com/v1/archive?latitude=33.71466&longitude=-118.24239&start_date=2024-01-01&end_date=2024-01-01&hourly=wind_speed_10m%2Cprecipitation&timezone=UTC&cell_selection=sea` returned `200` with 24 hourly values per requested variable.
- GDELT: `GET https://api.gdeltproject.org/api/v2/doc/doc?query=%28%22Port+of+Los+Angeles%22+OR+%22Los+Angeles+Harbor%22%29&mode=artlist&maxrecords=5&timespan=1d&sort=datedesc&format=json` returned `200` and `{}`. A second one-week request returned `429`; no further immediate retries were made.
- Credential check: `Settings().ais_api_key` was absent, and `.env` did not exist. No live subscription was attempted.
