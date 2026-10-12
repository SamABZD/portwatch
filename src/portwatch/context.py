"""Bounded context requests and pure normalization; no causal interpretation."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

import yaml

from portwatch.settings import Settings, for_port, load_port_config

ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
NEWS_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
PARSER_VERSION = "context-v1"
TRACKING_PARAMETERS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}


class ContextError(ValueError):
    def __init__(self, code: str, *, http_status=None, next_allowed_at=None):
        self.code = code
        self.http_status = http_status
        self.next_allowed_at = next_allowed_at
        super().__init__(code)


def utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ContextError("explicit_utc_required")
    return value.astimezone(UTC)


def iso_time(value: str) -> datetime:
    return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


@dataclass(frozen=True)
class ContextRequest:
    kind: str
    port_id: str
    product: str
    source_id: str
    endpoint: str
    parameters: dict
    start_utc: datetime
    end_utc: datetime
    keywords: tuple[str, ...] = ()

    @property
    def request_key(self) -> str:
        body = dict(
            kind=self.kind,
            port=self.port_id,
            product=self.product,
            source=self.source_id,
            endpoint=self.endpoint,
            parameters=self.parameters,
            start=utc(self.start_utc).isoformat(),
            end=utc(self.end_utc).isoformat(),
            keywords=self.keywords,
            parser=PARSER_VERSION,
        )
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    @property
    def url(self) -> str:
        return self.endpoint + "?" + urlencode(self.parameters)


def plan_context(
    settings: Settings, port_id: str, kind: str, *, demo=False, now: datetime | None = None
) -> ContextRequest:
    settings = for_port(settings, port_id)
    now = utc(now or datetime.now(UTC))
    port = load_port_config(settings.project_path(settings.port_config))
    if port.port_id != port_id:
        raise ContextError("unconfigured_port")
    if kind == "news":
        if demo:
            raise ContextError("recent_news_excluded_from_historical_demo")
        # Stable 15-minute windows make repeated invocations reproducible/cacheable.
        end = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        start = end - timedelta(hours=port.news_lookback_hours)
        return ContextRequest(
            kind,
            port_id,
            "recent_news",
            "gdelt_doc_v2",
            NEWS_ENDPOINT,
            dict(
                query=port.news_query,
                mode="artlist",
                format="json",
                sort="datedesc",
                maxrecords=port.news_max_records,
                startdatetime=start.strftime("%Y%m%d%H%M%S"),
                enddatetime=end.strftime("%Y%m%d%H%M%S"),
            ),
            start,
            end,
            tuple(port.news_keywords),
        )
    if kind != "weather":
        raise ContextError("unknown_context_kind")
    if demo:
        config = yaml.safe_load(settings.project_path(settings.demo_config).read_text("utf-8"))
        start = iso_time(config["weather_period"]["start_utc"])
        end = iso_time(config["weather_period"]["end_utc"])
        if (
            config["port_id"] != port_id
            or start != iso_time(config["start_utc"])
            or end != iso_time(config["end_utc"])
            or start.hour
            or start.minute
            or start.second
            or start.microsecond
            or end - start != timedelta(days=1)
        ):
            raise ContextError("weather_demo_period_must_match_ais")
        product, endpoint, model = "historical_reanalysis", ARCHIVE_ENDPOINT, "era5"
        # ERA5 does not supply gusts in the audited archive variable matrix.
        variables = "wind_speed_10m,precipitation"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=2)
        product, endpoint, model = "current_forecast", FORECAST_ENDPOINT, "best_match"
        variables = "wind_speed_10m,wind_gusts_10m,precipitation"
    return ContextRequest(
        kind,
        port_id,
        product,
        "open_meteo_archive" if demo else "open_meteo_forecast",
        endpoint,
        dict(
            latitude=port.weather_latitude,
            longitude=port.weather_longitude,
            hourly=variables,
            models=model,
            start_date=start.date().isoformat(),
            end_date=(end - timedelta(days=1)).date().isoformat(),
            timezone="UTC",
            timeformat="unixtime",
            wind_speed_unit="ms",
            precipitation_unit="mm",
            cell_selection="sea",
        ),
        start,
        end,
    )


def decode_response(payload: bytes) -> dict:
    def refuse_constant(value):
        raise ContextError("invalid_json_number")

    try:
        body = json.loads(payload, parse_constant=refuse_constant)
    except (UnicodeError, ValueError) as error:
        raise ContextError("invalid_json_response") from error
    if not isinstance(body, dict) or body.get("error"):
        raise ContextError("provider_error_or_invalid_object")
    return body


def finite_number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ContextError("invalid_numeric_value")
    try:
        if not math.isfinite(value):
            raise ContextError("invalid_numeric_value")
    except OverflowError as error:
        raise ContextError("invalid_numeric_value") from error
    return value


@dataclass(frozen=True)
class NormalizedContext:
    rows: list[dict]
    quality: dict
    metadata: dict


def normalize_weather(payload: bytes, request: ContextRequest) -> NormalizedContext:
    body = decode_response(payload)
    hourly, units = body.get("hourly"), body.get("hourly_units")
    if body.get("utc_offset_seconds") != 0 or not isinstance(hourly, dict):
        raise ContextError("weather_utc_or_hourly_missing")
    if not isinstance(units, dict) or units.get("time") != "unixtime":
        raise ContextError("weather_units_missing")
    lat, lon = finite_number(body.get("latitude")), finite_number(body.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ContextError("weather_grid_location_missing")
    times = hourly.get("time")
    if not isinstance(times, list) or any(type(value) is not int for value in times):
        raise ContextError("weather_times_invalid")
    expected = list(
        range(int(request.start_utc.timestamp()), int(request.end_utc.timestamp()), 3600)
    )
    if times != expected:
        raise ContextError("weather_coverage_mismatch")
    requested = set(request.parameters["hourly"].split(","))
    flags, arrays = [], {}
    for variable, unit in (
        ("wind_speed_10m", "m/s"),
        ("wind_gusts_10m", "m/s"),
        ("precipitation", "mm"),
    ):
        values = hourly.get(variable)
        if values is None:
            flags.append(
                f"{variable}_missing" if variable in requested else f"{variable}_not_requested"
            )
            arrays[variable] = [None] * len(times)
            continue
        if not isinstance(values, list) or len(values) != len(times) or units.get(variable) != unit:
            raise ContextError("weather_variable_shape_or_units_invalid")
        arrays[variable] = [finite_number(value) for value in values]
        if any(value is not None and value < 0 for value in arrays[variable]):
            raise ContextError("negative_weather_value")
    rows = []
    for index, stamp in enumerate(times):
        values = {name: array[index] for name, array in arrays.items()}
        null_flags = [f"{name}_null" for name, value in values.items() if value is None]
        rows.append(
            dict(
                valid_time_utc=datetime.fromtimestamp(stamp, UTC),
                **values,
                quality_flags=sorted(set(flags + null_flags)),
            )
        )
    metadata = {
        name: body[name]
        for name in (
            "latitude",
            "longitude",
            "elevation",
            "hourly_units",
            "timezone",
            "utc_offset_seconds",
            "generationtime_ms",
            "model",
            "models",
            "model_run",
            "issue_time",
        )
        if name in body
    }
    # Preserve supplied metadata; absence is never filled from retrieval time.
    metadata["issue_time_utc"] = None
    if isinstance(body.get("issue_time"), str):
        try:
            metadata["issue_time_utc"] = iso_time(body["issue_time"]).isoformat()
        except ValueError:
            flags.append("unparseable_issue_time")
    return NormalizedContext(rows, dict(missing_variables=flags, truncated=False), metadata)


def canonical_url(value: str) -> str:
    """Drop only known tracking keys; preserve order, encoding, IDs and fragments."""
    parts = urlsplit(value)
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
    ):
        raise ContextError("invalid_article_url")
    kept = []
    for item in parts.query.split("&"):
        key = unquote_plus(item.split("=", 1)[0]).casefold()
        if key not in TRACKING_PARAMETERS and not key.startswith("utm_"):
            kept.append(item)
    # Do not collapse HTTP/HTTPS, path case, www, trailing slash, AMP or URL fragments.
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, "&".join(kept), parts.fragment)
    )


def optional_news_time(value, field: str, flags: list[str]) -> datetime | None:
    if value is None or value == "":
        flags.append(field + "_missing")
        return None
    try:
        if field == "first_seen" and isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            except ValueError:
                pass
        return iso_time(value)
    except (TypeError, AttributeError, ValueError):
        flags.append(field + "_invalid")
        return None


def normalize_news(payload: bytes, request: ContextRequest) -> NormalizedContext:
    body = decode_response(payload)
    # The prior audited provider returned {} for a successful empty search.
    articles = [] if body == {} else body.get("articles")
    if not isinstance(articles, list):
        raise ContextError("news_articles_missing_or_invalid")
    rows = {}
    for article in articles:
        if (
            not isinstance(article, dict)
            or not isinstance(article.get("url"), str)
            or not isinstance(article.get("title"), str)
            or not article["title"].strip()
        ):
            raise ContextError("malformed_article_metadata")
        url = canonical_url(article["url"])
        flags = []
        published = optional_news_time(article.get("published_at"), "publication", flags)
        first_seen = optional_news_time(article.get("seendate"), "first_seen", flags)
        tags = [word for word in request.keywords if word in article["title"].casefold()]
        if not tags:
            flags.append("query_match_without_title_keyword")
        domain = article.get("domain")
        if not isinstance(domain, str) or not domain:
            domain = urlsplit(article["url"]).hostname
            flags.append("domain_from_url")
        language = article.get("language")
        if language is not None and not isinstance(language, str):
            raise ContextError("invalid_article_language")
        rows.setdefault(
            url,
            dict(
                canonical_url=url,
                original_url=article["url"],
                title=article["title"],
                source_domain=domain,
                language=language,
                publication_time_utc=published,
                first_seen_time_utc=first_seen,
                relevance_tags=tags,
                quality_flags=sorted(flags),
            ),
        )
    cap = int(request.parameters["maxrecords"])
    return NormalizedContext(
        list(rows.values()),
        dict(
            provider_article_count=len(articles),
            duplicate_urls=len(articles) - len(rows),
            truncated=len(articles) >= cap,
            label="potential disruption mention",
            exhaustive=False,
        ),
        dict(
            publication_field="published_at if supplied; otherwise unknown",
            first_seen_field="seendate",
            relevance_scope="title keyword substring",
        ),
    )
