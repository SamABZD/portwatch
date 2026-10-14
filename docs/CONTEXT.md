# Weather and news context

Step08 introduced independent public GET connectors. Current port configuration owns the weather
coordinate, news query, seven-day lookback, 100-result cap and optional title relevance keywords.
Completion phase 2 changes news parsing/selection and its existing panel without adding a service,
dependency, paid account, schema migration or acquisition schedule.

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
boundary. Both ports use a 168-hour scope: LA matches `"Port of Los Angeles"` or
`"Los Angeles Harbor"`; Oakland matches `"Port of Oakland"`. Disruption words are optional title
tags, not acquisition requirements. This is recent port context; there is no historical news
acquisition command. Hourly schedules and their existing per-port staggering remain unchanged.

Only returned article metadata is normalized: title, unchanged original URL, conservative URL key,
domain (provider value or URL-host fallback), language, optional explicitly supplied `published_at`,
`seendate`, title keyword tags and quality flags. Standard DOC results do not supply a verified
publication timestamp; missing publication time remains null. `seendate` is stored separately as
first-seen time. It is not publication time or the time a disruption occurred.

No publisher page, article body, snippet, image or embedded content is fetched. Tags use configured
case-insensitive title substrings; a query can match the indexed body without a matching title tag,
which is flagged. The label is **port news and context**, with optional disruption tags. Headlines
cannot establish a confirmed port event or explain vessel behavior. Publisher attribution comes
from the original URL/domain.

Canonical URLs remove `utm_*`, fbclid, gclid, dclid, msclkid, mc_cid and mc_eid parameters only.
Article identifiers, other parameters, their order/encoding, fragments, path case, scheme, www,
trailing slash and AMP variants remain distinct. Within a response the first occurrence of a
canonical URL wins; duplicate counts are recorded and all original metadata remains in raw bytes.
Separate responses retain separate evidence for the same URL. Normalization scans all bounded
candidates, applies the date window and canonical deduplication, then enforces the hard result cap.
Known publication time controls window eligibility; otherwise known first-seen time is used.
GDELT records with both times unknown remain explicitly query scoped, without an invented time.

At or above the configured provider result cap, mark the response truncated. This small connector
does not split capped windows. Even an uncapped response is not exhaustive news coverage. A valid
`{}` or `{"articles":[]}` is explicit successful empty data; invalid JSON, a null/missing articles
field in another object, malformed article metadata and HTTP errors are failures.

On recognized primary transport, rate-limit or JSON/metadata failures, the scheduled pipeline can
use the official [Oakland RSS](https://www.portofoakland.com/feed/) or
[LA release archive](https://portoflosangeles.org/news/news-release-archive). These sources provide
limited authority-release coverage. A successful empty primary response completes the request;
it does not trigger a supplemental official fetch or promise daily nonempty news.

Oakland publication timestamps retain their supplied offset and normalize to UTC. LA calendar
dates retain `publication_date` plus `America/Los_Angeles` day-overlap semantics, including DST;
publication/first-seen instants stay null. Official undated items are excluded and counted; an
entirely undated or unrecognized document fails. Quality records full parsed, eligible,
excluded-by-date, unknown-date, deduplicated, stored and capped counts, response bytes/format and
incomplete coverage. The news parser is `news-v2`; weather remains `context-v1`, so old weather
identities stay intact and revised news semantics acquire a distinct request identity.

## Storage, selection and source health

Migration005 adds `context_requests`, `context_responses`, `context_attempts`, `weather_values`
and `news_mentions`. Existing migrations and AIS tables are unchanged. The request SHA256 includes
kind, port, product, endpoint, complete public parameters, bounds, keywords and parser version.
Parameters are durably stored before acquisition. Shared immutable raw storage saves the **exact
successful HTTP body**, including malformed bodies that subsequently fail normalization. Live
Primary API responses use `.json`, Oakland fallback `.xml`, LA fallback `.html`; AIS envelopes
continue using `.jsonl`.

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
Matching verified news raw evidence can rebuild a missing selection within its original
15-minute cache eligibility, with the saved receipt unchanged and no network request.

Each kind has one session advisory lock across ports and a durable polling cooldown. News attempts
are at least15minutes apart; weather acquisition attempts at least60seconds apart. Changed windows
and `--refresh` cannot bypass cooldown. HTTP429 is not immediately retried. Retry-After extends the
cooldown when supplied. GETs have15-second socket timeouts, a checked35-second elapsed budget,
at most2MiB. News makes one HTTP attempt per provider, at most primary plus official fallback
per eligible invocation. Weather retains at most two attempts and a five-second transient retry
delay. The broader fallback allowlist therefore stays inside the former maximum HTTP budget.
The deadline is checked between reads; a final blocking read can last up to the socket timeout.
Declared Content-Length is checked against the completed transfer; partial bytes fail even if
they happen to parse as valid JSON.
Scheduled backend flows own collection; page render, navigation and refresh only read saved data.
Read the durable attempt/schedule for the current retry time; a historical audit timestamp is not
permission to bypass a later throttle.

`context_attempts` distinguishes running, valid_nonempty, valid_empty and failed, with HTTP status,
safe error code, run, timestamps and raw batch if available. Last failure and last success are
separate facts. The 15-minute cache lifetime controls acquisition reuse; display freshness uses
`health_context_stale_seconds` (default two hours), or a later failed refresh of the selected
provider. A cached read is not a new retrieval or a new provider success. HTTP error bodies are not
treated as data. Original shared run summaries still persist only exception classes.

`portwatch.news` supplies one read-only selection contract to health and replay. It selects the
latest successful receipt joined by both batch and request identity, retains latest attempts per
provider (selected, primary and global latest), and retains the latest scheduler disposition.
Both primary and fallback failing leaves last-good data stale, even when the global latest
attempt is the other provider. States are `available`, `valid_empty`, `fallback`, `stale`,
`deferred` and `unavailable`; `data_status` independently preserves empty/nonempty and `freshness`
preserves receipt age. A deferred invocation cannot create a receipt or overwrite article dates.

Live `/api/frame` carries rows and `news_context` from the same saved selection; `/api/health`
uses the same resolver at its own check time. Frame metadata matches its top-level state.
Revision material includes receipt, attempt and schedule identities but excludes ticking age.
Identical-byte refetches therefore invalidate stale metadata without duplicating normalized rows.
Rows sort by known publication calendar day (instant or source date), otherwise first-seen day,
then known publication instant, first-seen instant and canonical URL. Unknowns sort last; the
ten-row display limit applies after ordering. Date-only sorting never produces a midnight instant.

The Next.js proxy passes this read-only contract to `NewsPanel`. Provider, actual UTC window,
receipt age, primary outage and date precision come from the frame, so separately timed health
polls cannot relabel its articles. Historic mode excludes current rows and offers an explicit
switch to current context when live data is connected. No upstream acquisition runs in that flow.

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
Step08's original GDELT verification returned429. Completion phase 2 source/SQL/browser evidence
is recorded separately in [SOURCE_AUDIT](SOURCE_AUDIT.md) and
[phase2-news.json](evidence/phase2-news.json). Genuine saved official responses are kept private;
synthetic failure fixtures never enter analytical storage.
