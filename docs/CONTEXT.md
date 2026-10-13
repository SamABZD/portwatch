# Weather and news context

Step08 adds two independent public GET connectors. No new dependencies, paid accounts, scheduler,
frontend, causality model or analytical mart was introduced. Port configuration owns the weather
coordinate, news query, 24-hour lookback, 100-result cap and title relevance keywords.

## Weather semantics

`ingest weather --port los_angeles --demo` requests the frozen weather interval and refuses a
configuration that differs from the AIS demo. It uses Open-Meteo archive ERA5 and requests wind
speed and precipitation. Gusts are not requested because the audited ERA5 matrix does not supply
them. `ingest weather --port los_angeles` requests this UTC day and the next day from the forecast
endpoint with `best_match`, wind speed, gusts and precipitation.

Both requests explicitly select UTC, Unix timestamps, wind **m/s**, precipitation **mm**, and sea
cell preference. Complete, increasing hourly timestamps must exactly cover the requested half-open
UTC period. Missing variables and null values remain null with quality flags; measured zero remains
zero. Unexpected units, malformed numbers, wrong UTC offset or incomplete hours fail normalization.
The response is still preserved, and previous valid selections remain available.

The requested coordinate is latitude33.71466 / longitude−118.24239. Returned grid coordinates,
elevation and units are preserved separately. Actual ERA5 cell33.5 / −118.25 is well south of the
harbor; it is coarse regional context. The forecast cell33.72767 / −118.24222 is different. Neither
is an on-site sensor observation or evidence that weather caused a vessel delay.

Wind speed is an instantaneous hourly model value. Precipitation is a sum for the preceding hour
ending at its valid timestamp. Therefore `00:00` precipitation includes the preceding UTC day's
last hour. A plain sum of the 24 returned precipitation values must not be labelled a calendar-day
rainfall total without adjusting the interval. Forecast gusts represent a preceding-hour maximum.
Historical reanalysis is retrospective, retrieved in2026 for2024; it does not claim knowledge
available in2024. Current forecast output can include earlier hours of the current UTC day.

The requested model selector is recorded as a request parameter. Available provider model/run/
issue metadata is preserved if returned. Both actual responses supplied no issue/model-run time;
`issue_time_utc` is null. `generationtime_ms` is server computation duration. **Retrieval time is
never substituted for model issue time.**

## News semantics

`ingest news --port los_angeles` uses GDELT DOC ArticleList JSON. Request start/end are explicit
UTC seconds, covering the configured lookback and ending at the latest completed 15-minute
boundary. The narrow query combines named port phrases and disruption words. This is recent
context only; there is no historical news acquisition command.

Only returned article metadata is normalized: title, unchanged original URL, conservative URL key,
domain (provider value or URL-host fallback), language, optional explicitly supplied `published_at`,
`seendate`, title keyword tags and quality flags. Standard DOC results do not supply a verified
publication timestamp; missing publication time remains null. `seendate` is stored separately as
first-seen time. It is not publication time or the time a disruption occurred.

No publisher page, article body, snippet, image or embedded content is fetched. Tags use configured
case-insensitive title substrings; a query can match the indexed body without a matching title tag,
which is flagged. The label is **potential disruption mention**, not a confirmed port event or
causal explanation. Publisher attribution comes from the original URL/domain.

Canonical URLs remove `utm_*`, fbclid, gclid, dclid, msclkid, mc_cid and mc_eid parameters only.
Article identifiers, other parameters, their order/encoding, fragments, path case, scheme, www,
trailing slash and AMP variants remain distinct. Within a response the first occurrence of a
canonical URL wins; duplicate counts are recorded and all original metadata remains in raw bytes.
Separate responses retain separate evidence for the same URL.

At or above the configured provider result cap, mark the response truncated. This small connector
does not split capped windows. Even an uncapped response is not exhaustive news coverage. A valid
`{}` or `{"articles":[]}` is explicit successful empty data; invalid JSON, a null/missing articles
field in another object, malformed article metadata and HTTP errors are failures.

## Storage, selection and source health

Migration005 adds `context_requests`, `context_responses`, `context_attempts`, `weather_values`
and `news_mentions`. Existing migrations and AIS tables are unchanged. The request SHA256 includes
kind, port, product, endpoint, complete public parameters, bounds, keywords and parser version.
Parameters are durably stored before acquisition. Shared immutable raw storage saves the **exact
successful HTTP body**, including malformed bodies that subsequently fail normalization. Live
API responses use `.json`; AIS envelopes continue using `.jsonl`.

Byte changes create another raw revision. Identical bytes reuse the first raw batch and first raw
receipt; a new actual fetch still has its own attempt and retrieval time. Normalized rows are keyed
by raw batch plus valid time or canonical URL. Raw lineage and normalized counts publish together
in a short transaction; selection and successful run/attempt completion publish together afterward.
A normalization failure rolls back rows and retains the raw batch as failed. Earlier valid
selections are retained. Interrupted context attempts are marked on the next invocation after the
connector lock establishes that no other writer remains active.

Historical default ingestion verifies and reuses its selected revision indefinitely, with no HTTP.
`--refresh` acquires a new response and selects that saved revision only after successful
normalization; identical bytes and provider rollback can select an older saved batch. Current
weather/news cache for15minutes at the identical request key. A changed recent-news window makes
another request partition. A failure never erases previously successful partitions.

Each kind has one session advisory lock across ports and a durable polling cooldown. News attempts
are at least15minutes apart; weather acquisition attempts at least60seconds apart. Changed windows
and `--refresh` cannot bypass cooldown. HTTP429 is not immediately retried. Retry-After extends the
cooldown when supplied. GETs have15-second socket timeouts, a checked35-second elapsed budget,
at most two attempts, at most2MiB, and at most a five-second retry delay for transient failures.
The deadline is checked between reads; a final blocking read can last up to the socket timeout.
Declared Content-Length is checked against the completed transfer; partial bytes fail even if
they happen to parse as valid JSON.
There is no background poller. On the current machine's real429, retry is allowed no earlier than
2026-10-12T12:40:00.277470Z, subject to any subsequent throttle.

`context_attempts` distinguishes running, valid_nonempty, valid_empty and failed, with HTTP status,
safe error code, run, timestamps and raw batch if available. Last failure and last success are
separate facts; old successful data must be labelled stale past the15-minute current-context cache
period. A cached read is not a new retrieval or a new provider success. HTTP error bodies are not
treated as data. Original shared run summaries still persist only exception classes.

`read_demo_context(settings, connection, port, as_of_utc, batch_id=None)` restricts weather to the
matching historical request/product and valid times no later than the replay cutoff. An explicit
saved batch pins a revision for reproducibility; a forecast batch is refused. News is always empty
with status `excluded_recent_source`, independent of provider success/failure. Missing historical
weather returns `unavailable`, not zeros. Weather prefix reconstruction remains retrospective.
Store the chosen batch ID with future analytical output; join only one revision/product.

## Attribution and references

Display weather attribution to [Open-Meteo](https://open-meteo.com/) and
[Copernicus Climate Change Service / ERA5](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=overview),
with [CC BY4.0](https://creativecommons.org/licenses/by/4.0/) and a note that these are transformed
UTC selections. Respect [Open-Meteo's non-commercial free API terms](https://open-meteo.com/en/terms).
For forecast, retain Open-Meteo attribution and its upstream
[data-source credits](https://open-meteo.com/en/docs). News attributes
[GDELT](https://www.gdeltproject.org/) and each publisher's original URL; publisher copyrights remain.

Audited primary documentation: [archive API](https://open-meteo.com/en/docs/historical-weather-api),
[forecast API](https://open-meteo.com/en/docs), and
[GDELT DOC2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/), checked2026-10-12.
The real news request was429; nonempty and empty normalization behavior is verified synthetically
in isolated schemas. No real2026 article feed success is claimed.
