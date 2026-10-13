# Visit and candidate-waiting inference

Step10 adds explicit contiguous daily context through `visit_partition_context`, preserving
canonical source dataset lineage while inferring across midnight. Each target day's episode
projection may contain neighboring-day points; metrics attribute only target-day confirmation
hours. Missing context remains partial rather than inventing arrival. The frozen one-day results
below are unchanged. See [ORCHESTRATION.md](ORCHESTRATION.md).

Step07 uses saved normalized observations and versioned analytical zones. It makes deterministic
experimental inferences, not a census of vessel calls or a measured port queue. Current results
are **retrospective** and describe the 2024-01-01 UTC historical dataset.

## Scope and rules

Each track contains one source, dataset, cleaning version and MMSI. A partition also identifies
the port, geofence, spatial-assignment algorithm and content-addressed inference rules. Historical
and live sources are separate. No current news, weather or live provider request is involved.

`visits-v1` is configured in `configs/ports/los_angeles.yaml`:

| Parameter | Experimental default |
| --- | ---: |
| Candidate speed maximum | 1 knot, inclusive |
| Candidate duration minimum | 30 minutes, inclusive |
| Maximum adjacent observation gap | 15 minutes, inclusive |
| Entry/exit persistence | 2 minutes |
| Minimum consistent observations | 2 distinct event times |

The rule ID is `0191a4a0f869f2648508ff47b157108e28147c89095670610554e68afa523d5e`.
It hashes the algorithm version and normalized parameters. Changing thresholds creates another
rule partition; changing inference logic requires a new algorithm version.

Observations sort by event time and stable observation identity. Simultaneous reports count as
one evidence time, so two records in the same second cannot confirm entry. Conflicting simultaneous
harbor/terminal classifications become unknown and break continuity. Source quality flags remain
visible in activity states; valid source positions are never changed or discarded by this stage.

## States and timing

The states are outside, harbor, terminal, candidate waiting and unknown. Harbor/terminal
membership establishes analytical port presence. Waiting eligibility requires a candidate-area
membership, known speed at/below the threshold, and no terminal membership. Waiting eligibility
can coexist with harbor membership. Missing speed is unknown for waiting, never numeric zero.

Each activity row preserves its observed state, port presence, waiting eligibility, preceding
gap, preceding interval state and quality flags. Long gaps and category changes have unknown
intervals. A trailing absence longer than the gap threshold makes the partition's current state
unknown without inventing a departure.

Entry and exit require repeated consistent port presence/absence across the persistence period.
A brief crossing inside cannot create a visit. A brief outside excursion within an active visit
is recorded as boundary jitter; its adjacent intervals are excluded from supported inside time.
This policy can suppress a real brief exit. The jitter count exposes that disagreement.

Arrival is bracketed by the last preceding outside observation and first inside observation;
departure by the last inside observation and first sustained outside observation. Confirmation
times are stored separately from those intervals. The bounds use provider observation timestamps;
they do not claim second-accurate actual arrivals or departures.

A vessel first seen inside has unknown arrival bounds. A vessel ending inside has unknown
departure bounds. Reporting gaps split visits into incomplete fragments; time across a gap is
never counted as supported inside time or candidate waiting. Reasons include slice context,
reporting gaps, conflicting timestamps and insufficient exit persistence.

For a first-seen-inside partial, `arrival_confirmed_at` marks confirmation of persistent port
presence only. It does not establish an arrival event; the arrival bounds remain null.

Only visits with observed entry and exit intervals are completed under these rules. Their duration
range is `[departure_lower-arrival_upper, departure_upper-arrival_lower]`, with semantics
`completed_transition_interval`. Incomplete visits have null duration bounds and semantics
`partial_observation_span`. Their observed span and supported inside intervals are separate
fields, not completed-duration statistics or guaranteed physical continuous dwell. These are
sample-supported observations, and even adjacent reports permit unobserved movement.

## Waiting proxy and association

Candidate episodes require a continuous sequence of eligible observations meeting the minimum
duration, with no adjacent gap over the limit. Common candidate-zone membership is retained.
Unknown speed, a terminal point, a zone change, a disqualifying speed or a long gap ends the
supported sequence conservatively. Known transition intervals and missing-boundary reasons are
stored. Candidate duration is the eligible observed span, labeled
`low_speed_candidate_interval_proxy`; it is not actual queue or anchorage duration.

Offshore episodes remain region scoped unless the same track and uninterrupted segment contains
a persistent analytical port visit overlapping the episode or beginning within the gap limit
after it. Association time is no earlier than waiting qualification and the visit's confirmation.
The link supports an observed sequence toward this approximate port, not proof of berth use or
cargo congestion. A completed candidate refers to observed predicate boundaries only.

The MVP includes all observed vessel types. Its sole candidate reports AIS type **50** on all
57 eligible positions; the [USCG type table](https://navcen.uscg.gov/ais-class-a-reports) maps that
code to pilot vessel. This self-reported code and low speed do not establish why it stopped.
The candidate must not be presented as a cargo vessel in a port queue. Vessel population,
geofence approximation, speed threshold, reporting-gap limit and persistence are the most
consequential assumptions for any future waiting or congestion metric.

## Recompute and replay

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch transform visits --port los_angeles --demo
.\.venv\Scripts\python.exe -m portwatch transform visits --port los_angeles --demo --as-of 2024-01-01T20:40:00Z
.\.venv\Scripts\python.exe scripts/verify_visits.py
```

Default inference reads the selected dataset's complete available vessel histories, refreshes
missing spatial assignments, and transactionally replaces its derived partitions. It deletes
obsolete visit/waiting IDs and states before inserting the replacement. Failure restores the
previous result. Other sources, datasets, cleaning versions, rules and geofences remain separate.
Only one vessel history is buffered at a time; activity rows are staged with COPY before bulk
publication. Statement and lock timeouts bound database work.

Observations across midnight are included when present in the same selected dataset. The actual
saved demo has one UTC day and no adjacent-day context. Separate daily dataset identities are not
automatically blended. Missing context is preserved as partial episodes, rather than downloading
additional days or claiming continuity across unobserved boundaries.

`--as-of` reconstructs a **read-only event-time prefix**. Future event timestamps are filtered
before inference. It writes no pipeline run or derived row. For replay, call this prefix path or
`infer_track(..., as_of_utc=...)`; do not filter retrospective completed rows by their start time
and treat them as contemporaneous knowledge. Confirmation/association timestamps are event-time
model evidence, not historical provider delivery times. Late-report receipt chronology is not
reconstructed from this retrospective NOAA product.

On real data, the sole candidate is region scoped at 20:40:00 UTC and becomes visit associated
after persistent entry confirmation at 20:43:01 UTC. The verified 20:43:02 prefix has that link;
the earlier prefix does not use the future confirmation.

## Schema and results

Migration004 adds `visit_rule_sets`, `visit_inference_partitions`, `vessel_activity_states`,
`vessel_visits` and `waiting_episodes`. Partitions own source/dataset/MMSI/version context and input
hashes. Activity rows have grain `(partition_id, position_id)`. A visit or candidate episode has
one stable ID derived from its partition, episode kind and first evidence observation. Candidate
links reference a visit in the same partition. Referential indexes cover source positions,
partition parents, rule/geofence lookups and linked visits. Earlier migrations are unchanged.

Measured on 62,157 historical positions and 141 tracks:

| Result | Count |
| --- | ---: |
| Inferred visit episodes | 230 |
| Completed under the rules | 14 |
| Partial fragments | 216 |
| Qualified waiting candidates | 1 |
| Region-only candidates after full-history inference | 0 |
| Visit-associated candidates | 1 |
| Tracks with unknown state at dataset cutoff | 44 |

The candidate has 57 reports from 19:29:31 through 20:31:35 UTC, a **3,724-second observed proxy**.
Speeds range 0–0.8 knots; its largest adjacent gap is 78 seconds. Qualification becomes known at
19:59:58. Association becomes supported at 20:43:01, following harbor entry within the 15-minute
association limit. No physical queue is confirmed.

Two complete replays have identical business hash
`76f96085b195058b60af35ad72b4b33de761dcbf61a3069baa1b67f73cb525da`.
The original clean hash remains `355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`.
Eight independent SQL invariant groups report zero failures. The noon prefix sees 31,316 positions,
124 tracks, six completed and 158 partial visits, and no waiting candidate. It preserves stored
full-history results.

## Manual real-track review

Reviewed the state plot and timestamped episode JSON for four real MMSIs. Agreement below means
agreement with saved observations and declared rules; no independent port-call ground truth exists.

| Track | Agreement with observations | Disagreement or limitation |
| --- | --- | --- |
| 338093719 | The 57 low-speed candidate reports support the 62-minute proxy. Persistent harbor entry later supports association. A 1,305-second gap splits subsequent visits and leaves departure unknown. | Reported type50 is pilot vessel; no evidence establishes a cargo queue, berth use or purpose of stopping. Retrospective association must not appear at 19:59 qualification time. |
| 368164540 | Two bounded episodes have repeated outside/inside evidence. One lasts within 1,168–1,293 seconds under the interval rules. A 42,493-second gap is excluded from dwell. | First sighting inside gives unknown arrival. Short crossings through approximate polygons may be local movement rather than operational port calls. |
| 367657260 | The 30,193-second gap splits the track; neither side invents a departure/arrival. Unknown boundaries keep all three episodes partial. | Two brief outside observations are suppressed as jitter in the final fragment. They may represent real short exits; supported inside time excludes their adjacent intervals. |
| 368084540 | The 3,770-second gap separates two incomplete episodes; last sighting at23:58:52 leaves departure unknown. | Two brief outside observations remain within the second inferred visit. The 80,441-second observed span is not guaranteed continuous dwell; supported inside proxy is 80,243 seconds. |

Evidence is in ignored `artifacts/step07/verification.json`, `track-review.png` and four
`track-<mmsi>.json` files. The figure uses discrete observations and explicit reporting gaps,
without interpolated motion. Synthetic fixtures independently exercise normal entry/exit,
jitter, stationary distinct timestamps, long gaps, slice truncation, midnight, reordered input,
late-ID replacement, unknown speed, simultaneous conflicts, threshold boundaries, prefix causality,
rule separation, source isolation and transactional rollback.
