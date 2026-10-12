# Analytical metrics

Step 09 adds `marts-v1` SQL output over the existing positions, zones, visits, waiting episodes,
raw/cleaning ledger and weather contracts. It does not introduce a congestion score, a ground
truth port-call table or an estimate of total AIS coverage. All vessel types remain included.

## Grain, version and publication

`port_activity_hourly` has one row per **port + source + dataset + analytical version + UTC hour**.
`hour_utc` starts a half-open `[hour_utc,hour_end_utc)` interval. The primary key enforces this grain.
`mart_partitions` records the cleaning, geofence, assignment and visit-rule versions, input period,
input hash, last inference cutoff, source health, selected weather request/batch and computation
time. `mart_rule_sets` hashes the full metric configuration, including a weather batch ID or null.
Snapshot staleness is configurable; Los Angeles uses 15 minutes. No NOAA/live series are blended.

Snapshot counts are taken **at the start of the hour**, not at the end. One latest report at or
before that instant is eligible per vessel when its age is at most the staleness limit. All latest
same-time reports are folded into one vessel. If they disagree on primary zone, the vessel is
counted once in `unknown`. Otherwise the existing terminal/harbor/candidate priority and lexical
zone tie-break apply. A primary zone is a geometric observation, not a confirmed berth or queue.

Processed raw input time ranges are unioned to identify available source intervals. This means a
successfully processed input exists for the period; it does not establish complete real-world AIS
coverage. An empty, available interval permits a measured zero observed-report count. An empty
unavailable interval gives null. Positive observed counts remain measured, with an unknown-source
flag when needed. Snapshot zero requires its full lookback interval to be available; positive
fresh-report counts can be shown as a partial observed lower bound with a lookback flag.

Each build appends any missing spatial assignments, then recomputes the full selected series'
visit histories and replaces its hour partition in one repeatable-read transaction. This removes
obsolete visit IDs/hours after late data or a changed display period. A failure rolls back both
visit and mart publication; previously valid rows remain available. Other sources/datasets/rules/
analytical versions remain separate. Run the transform again after cleaning late reports; scheduled
recomputation belongs to Step 10. Version changes retain the prior version's saved output.

## Metric dictionary

All rows use the grain above. Counts measure records, distinct reporting vessels or inferred
episodes as specified below. They do not measure all ships present. Unless stated otherwise, an
hour with no observations and no available source interval has null counts; an available empty
hour has zero counts. Means/medians with no samples are always null. Examples are synthetic unless
explicitly labelled real. All timestamps are UTC.

| Field | Unit / inclusion | Timestamp convention | Missing-data behavior / example |
| --- | --- | --- | --- |
| `observation_count` | Canonical valid position records in the selected source/dataset/clean version; duplicates excluded. | Effective event/receipt-fallback time in the half-open hour. | Null for empty unavailable hours. Five canonical records means 5, even if one vessel sent all five. Real daily sum: 62,157. |
| `distinct_vessels_observed` | Distinct MMSIs with a canonical record during the hour; all vessel types. | Same event-hour interval. | Null for empty unavailable hours. Two vessels reporting at different times means 2; it does not imply simultaneous presence. |
| `snapshot_vessel_count` | One fresh latest-report group per MMSI across the collection region. | Hour start; report time <= snapshot; age <= configured limit. | Null if no fresh reports and lookback is unavailable. At 01:00, only one of two hourly reporting vessels may have a fresh report. |
| `snapshot_port_vessel_count` | Fresh snapshot vessels whose unambiguous primary zone is terminal or harbor. | Same start snapshot. | Same snapshot missing rule. Unknown-zone vessels are excluded and exposed separately; zero is a measured count of classified reports. |
| `snapshot_zone_counts` | JSON map of disjoint primary-zone counts, including `outside` and `unknown`; sum equals total snapshot count. | Same start snapshot. | All values null for an unknown snapshot. A vessel covered by harbor and terminal polygons contributes once to the terminal vicinity. |
| `snapshot_stale_vessel_count` | Previously seen vessels whose latest report at the snapshot is older than the limit. Never includes a vessel first seen later. | Hour start. | Same snapshot missing rule. Last report at 00:32 is stale at 01:00 with a 15-minute limit. |
| `snapshot_conflicting_vessel_count` | Fresh vessels with different primary zones at the same latest timestamp. | Hour start. | Same snapshot missing rule. Harbor/outside reports both at 01:00 produce one unknown vessel, not two. |
| `confirmed_arrival_count` | Inferred visit episodes with a known outside-to-inside arrival interval and persistent entry confirmation; unknown initial arrival excluded. | Entry confirmation hour, not first inside point. | Base count rule. Outside at 00:00, inside at 00:10/12 produces one confirmation at 00:12. |
| `confirmed_departure_count` | Episodes with persistent exit confirmation, including partial episodes with unknown arrival. | Exit confirmation hour. | Base count rule. Outside at 00:20/22 gives one confirmation at 00:22. Neither count establishes an official port call. |
| `waiting_candidate_count` | Distinct vessel partitions with a qualified low-speed episode intersecting the hour and qualification known by period end. | Episode observation span intersects the hour; qualification must occur before its exclusive end. | Base count rule. Qualification at 00:31 is absent from a 00:30 replay prefix. A candidate spanning two hours can contribute to both. |
| `waiting_episode_count` | Number of qualified episodes represented by the candidate count; can exceed vessel count. | Same intersection/qualification rule. | Base count rule. Two eligible episodes for one vessel yield one candidate and two episodes. |
| `port_associated_waiting_count` | Candidate vessels with at least one same-segment association to persistent selected-port visit evidence known by period end. | Association knowledge time must precede period end. | Base count rule. Association at 20:43:01 is absent at 20:40. Port association takes precedence if a vessel also has a regional episode. |
| `regional_waiting_count` | Candidate vessels without a known selected-port association by period end. | Same rule. | Port + regional counts equal candidate count. Geographic vicinity alone never attributes offshore candidates to LA. |
| `waiting_duration_sample_count` | Qualified episode observation spans attributed once to their last observed hour; completed and partial candidates included. | Last observed timestamp, with qualification known by period end. | Zero with no eligible spans; null when interval unavailable. A 00:01–00:31 episode contributes one sample in hour 00. |
| `waiting_partial_sample_count` | Duration samples whose start/end boundary completeness is partial. | Same last-observed hour. | Same count rule. Exposed separately; partial duration is an observation proxy, not completed operational waiting. |
| `waiting_duration_median_seconds` | Median candidate low-speed observation span, with sample size and partial count. | Same last-observed-hour attribution. | Null with no samples. A single 30-minute observed span gives 1,800 seconds; no unobserved gap is added. |
| `completed_dwell_sample_count` | Completed visit episodes only, with both arrival/departure intervals known under the rules. | Departure **confirmation** hour. | Zero with no completed episodes, null for unavailable interval. Partial arrivals/departures do not enter dwell medians. |
| `completed_dwell_median_seconds` | Median of each completed interval's midpoint `(duration_min+duration_max)/2`; an estimate, not exact elapsed dwell. | Departure confirmation hour. | Null without completed samples. Synthetic [120,1,200] seconds gives a midpoint sample of 660 seconds. |
| `completed_dwell_lower_median_seconds` / `completed_dwell_upper_median_seconds` | Separate medians of completed duration lower/upper bounds; uncertainty context, not statistical confidence intervals. | Same departure confirmation hour. | Null without samples. Synthetic lower/upper medians: 120/1,200 seconds. Never average hourly medians into a daily median. |
| `incomplete_visit_count` | Partial visit episodes whose observed span intersects the hour and entry confirmation is known by period end. Counts episodes, not vessels. | Observed span and confirmation knowledge. | Base count rule. First sighting already inside can produce one partial episode with unknown arrival and null completed duration. |
| `rejected_record_count` | Quarantined regional-cleaning records only where hourly attribution is defensible. | No trustworthy reject event time is stored in the current ledger. | Zero is defensible when all selected inputs are processed and dataset rejects are zero. If any reject exists, hourly values are null; no raw receipt timestamp is invented as event time. |
| Partition `unattributed_rejected_record_count` | Dataset-level quarantined record count, reported once per partition; duplicates/skipped records excluded. | Regional input ledger, not an event hour. | Null if cleaning is unfinished/failed. One bad-coordinate record gives 1 here and unknown hourly rejects. Do not sum this parent field across 24 hours. |
| `receipt_fallback_observation_count` | Records using local receipt time because provider event time is absent. | Effective-time hour. | Base count rule. Exposed as timestamp quality, not silently treated as source event time. Real NOAA day: zero. |
| `flagged_observation_count` | Canonical records with at least one cleaning quality flag; each record counted once. | Effective-time hour. | Base count rule. A missing heading can flag a valid position; this count is not a rejection count or coverage percentage. |
| `latest_observation_utc` | Latest canonical effective timestamp within the measured hour/prefix. | Within the interval only. | Null without records. Future-hour observations cannot set this value. |
| `observation_age_seconds` | Period end/cutoff minus latest in-period effective timestamp. | Hour end for stored rows; replay cutoff for a partial row. | Null without records. Last record at 20:59:56 gives 4 seconds at 21:00. This is not freshness relative to today's clock. |
| `collector_state` | Historical file mode or unknown historical live-collector state. | Input mode; no current collector state projected onto 2024. | NOAA uses `not_applicable_historical`. A live historical interval without a collector log uses `unknown_historical_collector_state`. |
| `source_interval_available` | Boolean: union of successfully processed declared raw periods covers the whole hour. | Full hour, even when a replay prefix is partial; complete_hour distinguishes that case. | False for absent/unprocessed intervals. It describes input availability, not universal AIS coverage. |
| `snapshot_interval_available` | Boolean: processed periods cover the entire inclusive snapshot lookback. | `[snapshot-age_limit,snapshot]`, including the snapshot instant. | False at the day's start when the preceding day is absent. Real 00:00 has 3 fresh reports but incomplete lookback; do not call it total occupancy. |
| `weather_product` / `weather_status` | Historical reanalysis product and available/unavailable normalized hourly response. | Single pinned batch + exact UTC valid hour. | Missing source/hour gives unavailable; never numeric zeros. Recent forecast and articles are excluded. |
| `weather_wind_speed_mps` | Hourly model wind speed at 10m, m/s. | Instant at hour start. | Missing variable is null. Real 19:00 value: 2.33m/s; coarse regional ERA5 cell, not a harbor sensor. |
| `weather_gusts_mps` | Model gusts at 10m, m/s, where supplied. | Product-dependent valid interval. | ERA5 archive gusts were not requested/supported here, so all 24 are null with flags. |
| `weather_preceding_hour_precipitation_mm` | Model precipitation sum, mm, for the preceding hour ending at hour_utc. | At 19:00, represents 18:00–19:00, not 19:00–20:00. | Missing variable is null; a returned zero remains zero. Do not label the sum of these 24 values a calendar-day rainfall total. |
| `weather_quality_flags` / `quality_flags` | Named provenance/completeness cautions; no fabricated numeric confidence score. | Row input and inference context. | Flags include approximate zones, observed-only coverage, low-speed proxy, midpoint estimate, unknown source interval, incomplete lookback, timestamp conflicts/fallbacks, pending cleaning, unknown reject hour, partial waiting, regional attribution and missing weather. |

Real candidate: one reported pilot vessel, 3,724 seconds of low-speed observations. Its two
candidate-vessel hours are not two vessels and do not establish a cargo queue. Experimental rule
confirmation, observation intervals, sparse reports and unknown port-call ground truth remain
material limits; see [VISITS.md](VISITS.md) and [CONTEXT.md](CONTEXT.md).

## Replay and query helpers

Stored rows are `retrospective_completed_hours`; they may use full-series visit outcomes.
`read_completed_hours(connection,scope,config,as_of_utc)` filters exact source/dataset/cleaning/
analytical version and display range, completed hours, and requires the partition's full input
inference cutoff <= as_of_utc. Therefore stored Jan1 rows are withheld for a Jan1 noon replay.

`read_prefix_hours(connection,scope,config,as_of_utc,completed_only=False)` reconstructs visit
episodes from positions **at or before** the cutoff and uses the same SQL metrics. It returns
series/version/weather identifiers, completed hours and an explicitly partial current hour.
`completed_only=True` omits the partial row. It writes temporary tables only, with no persistent
audit/derived changes. Reanalysis remains retrospective; historical provider receipt chronology
is not claimed. Use this helper during the demo, not an unrestricted stored-hour chart query.

`transform marts --port los_angeles --demo --as-of 2024-01-01T12:30:00Z` returns 13 rows, 12
completed hours, and 32,632 observations through the cutoff; partial hour12 has 1,317 records.
At 20:40 the candidate remains regional; at 20:43:02 its association is available. Later points
and the rest of the partial hour are never exposed. Full publication uses no `--as-of`.

## Five executable SQL examples

These use psycopg named parameters, not string interpolation. Supply `port_id`, `source_id`,
`dataset_id`, `version`, `clean_version`, `start_utc`, `end_utc`, `as_of`. For the saved demo,
use port `los_angeles`, source `noaa_ocm_ais_2024_geoparquet`, dataset
`los_angeles_2024_01_01_noaa_ocm_geoparquet_v1`, clean version `positions-v1`, the exact chosen
analytical version, Jan1–Jan2 UTC bounds and as_of Jan2 00:00UTC. Version identity must come from
the transform/configuration; do not combine all catalog versions or choose one by averaging.

Activity, snapshot and pinned weather:

```sql
-- query: activity
SELECT h.hour_utc,h.observation_count,h.distinct_vessels_observed,
       h.snapshot_vessel_count,h.snapshot_port_vessel_count,h.snapshot_zone_counts,
       h.weather_wind_speed_mps,h.weather_preceding_hour_precipitation_mm
FROM port_activity_hourly h JOIN mart_partitions p USING(partition_id)
WHERE h.port_id=%(port_id)s AND h.source_id=%(source_id)s AND h.dataset_id=%(dataset_id)s
  AND h.analytical_version=%(version)s AND p.clean_version=%(clean_version)s
  AND h.hour_utc>=%(start_utc)s AND h.hour_utc<%(end_utc)s
  AND h.hour_end_utc<=%(as_of)s AND p.input_as_of_utc<=%(as_of)s
ORDER BY h.hour_utc;
```

Qualified waiting candidates, attribution and duration sample size:

```sql
-- query: waiting
SELECT h.hour_utc,h.waiting_candidate_count,h.port_associated_waiting_count,
       h.regional_waiting_count,h.waiting_duration_sample_count,h.waiting_partial_sample_count,
       h.waiting_duration_median_seconds/60 AS observed_span_median_minutes,h.quality_flags
FROM port_activity_hourly h JOIN mart_partitions p USING(partition_id)
WHERE h.port_id=%(port_id)s AND h.source_id=%(source_id)s AND h.dataset_id=%(dataset_id)s
  AND h.analytical_version=%(version)s AND p.clean_version=%(clean_version)s
  AND h.hour_utc>=%(start_utc)s AND h.hour_utc<%(end_utc)s
  AND h.hour_end_utc<=%(as_of)s AND p.input_as_of_utc<=%(as_of)s
  AND (h.waiting_candidate_count>0 OR h.waiting_duration_sample_count>0)
ORDER BY h.hour_utc;
```

Completed dwell midpoint and bounds, with partial episodes separate:

```sql
-- query: dwell
SELECT h.hour_utc,h.completed_dwell_sample_count,
       h.completed_dwell_median_seconds/3600 AS midpoint_median_hours,
       h.completed_dwell_lower_median_seconds/3600 AS lower_median_hours,
       h.completed_dwell_upper_median_seconds/3600 AS upper_median_hours,h.incomplete_visit_count
FROM port_activity_hourly h JOIN mart_partitions p USING(partition_id)
WHERE h.port_id=%(port_id)s AND h.source_id=%(source_id)s AND h.dataset_id=%(dataset_id)s
  AND h.analytical_version=%(version)s AND p.clean_version=%(clean_version)s
  AND h.hour_utc>=%(start_utc)s AND h.hour_utc<%(end_utc)s
  AND h.hour_end_utc<=%(as_of)s AND p.input_as_of_utc<=%(as_of)s
ORDER BY h.hour_utc;
```

Reporting recency, source availability and timestamp/quality cautions:

```sql
-- query: freshness
SELECT h.hour_utc,h.latest_observation_utc,h.observation_age_seconds,
       h.source_interval_available,h.snapshot_interval_available,h.collector_state,
       h.receipt_fallback_observation_count,h.flagged_observation_count,h.quality_flags,
       p.source_health
FROM port_activity_hourly h JOIN mart_partitions p USING(partition_id)
WHERE h.port_id=%(port_id)s AND h.source_id=%(source_id)s AND h.dataset_id=%(dataset_id)s
  AND h.analytical_version=%(version)s AND p.clean_version=%(clean_version)s
  AND h.hour_utc>=%(start_utc)s AND h.hour_utc<%(end_utc)s
  AND h.hour_end_utc<=%(as_of)s AND p.input_as_of_utc<=%(as_of)s
ORDER BY h.hour_utc;
```

Rejected-record reconciliation without multiplying the dataset total:

```sql
-- query: rejected
SELECT p.partition_id,p.unattributed_rejected_record_count,
       count(*) FILTER (WHERE h.rejected_record_count IS NULL) AS hours_with_unknown_reject_count,
       CASE WHEN count(*) FILTER (WHERE h.rejected_record_count IS NULL)=0
            THEN sum(h.rejected_record_count) END AS attributable_hourly_total,p.source_health
FROM port_activity_hourly h JOIN mart_partitions p USING(partition_id)
WHERE h.port_id=%(port_id)s AND h.source_id=%(source_id)s AND h.dataset_id=%(dataset_id)s
  AND h.analytical_version=%(version)s AND p.clean_version=%(clean_version)s
  AND h.hour_utc>=%(start_utc)s AND h.hour_utc<%(end_utc)s
  AND h.hour_end_utc<=%(as_of)s AND p.input_as_of_utc<=%(as_of)s
GROUP BY p.partition_id;
```

News stays in `news_mentions`/context request tables and is excluded from this historical mart.
If future live views include articles, use a separate panel or pre-aggregate metadata; never join
raw articles or all weather revisions to positions. PostgreSQL's
[range aggregates and ordered-set aggregates](https://www.postgresql.org/docs/17/functions-aggregate.html)
support the source interval union and medians; aggregate operands separately before joining.
