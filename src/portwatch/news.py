"""One read-only news selection/provenance contract for frames and source health."""

from datetime import datetime

from psycopg.rows import dict_row

from portwatch.settings import for_port, load_port_config

PROVIDERS = {
    "gdelt_doc_v2": ("GDELT DOC 2.0", "https://www.gdeltproject.org/"),
    "port_oakland_rss": ("Port of Oakland", "https://www.portofoakland.com/feed/"),
    "port_los_angeles_releases": (
        "Port of Los Angeles",
        "https://portoflosangeles.org/news/news-release-archive",
    ),
}
FALLBACKS = set(PROVIDERS) - {"gdelt_doc_v2"}


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def news_state(snapshot, now, stale_seconds):
    """Compute freshness without changing saved fetch receipts or article dates."""
    result = dict(snapshot)
    fetched = result.get("last_successful_fetch_utc")
    fetched = _time(fetched)
    age = max(0, (now - fetched).total_seconds()) if fetched else None
    stale = age is not None and age > stale_seconds
    result.update(
        success_age_seconds=age,
        freshness="stale" if stale else "fresh" if fetched else "unavailable",
    )
    latest = result.get("latest_attempt")
    selected_attempt = result.get("selected_attempt") or latest
    failed_after_success = bool(
        selected_attempt
        and selected_attempt["status"] == "failed"
        and selected_attempt.get("source_id", result.get("source_id")) == result.get("source_id")
        and (not fetched or _time(selected_attempt["started_at"]) >= fetched)
    )
    schedule = result.get("schedule")
    deferred = bool(
        schedule
        and schedule["status"] == "deferred"
        and (not fetched or _time(schedule["started_at"]) >= fetched)
    )
    backoff = bool(
        latest
        and latest["http_status"] == 429
        and latest.get("next_allowed_at")
        and _time(latest["next_allowed_at"]) > now
    )
    if fetched and failed_after_success:
        result["freshness"] = "stale"
    result["status"] = (
        "stale"
        if stale or (fetched and failed_after_success)
        else "deferred"
        if deferred or (not fetched and backoff)
        else "fallback"
        if fetched and result["using_fallback"]
        else result["data_status"]
        if fetched
        else "unavailable"
    )
    return result


def read_news_context(connection, port, now, settings):
    """Select matching receipt/batch and preserve primary failures independently."""
    from portwatch.pipeline import PIPELINE_VERSION

    selected_settings = for_port(settings, port)
    config = load_port_config(selected_settings.project_path(selected_settings.port_config))
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """SELECT r.request_key,r.source_id,r.endpoint,r.parameters,r.start_utc,r.end_utc,
            r.selected_batch_id,r.selected_retrieved_at,c.row_count,c.quality,c.source_metadata,
            b.byte_count
            FROM context_requests r JOIN context_responses c
              ON c.batch_id=r.selected_batch_id AND c.request_key=r.request_key
            JOIN ingestion_batches b ON b.batch_id=c.batch_id
            WHERE r.kind='news' AND r.port_id=%s AND r.product='recent_news'
            ORDER BY r.selected_retrieved_at DESC,r.request_key,c.batch_id LIMIT 1""",
            (port,),
        )
        success = cursor.fetchone()
        cursor.execute(
            """SELECT DISTINCT ON (r.source_id) r.source_id,a.status,a.started_at,
            a.retrieved_at,a.http_status,a.error_code,a.next_allowed_at,a.attempt_id,a.batch_id
            FROM context_attempts a JOIN context_requests r USING(request_key)
            WHERE r.kind='news' AND r.port_id=%s AND r.product='recent_news'
            ORDER BY r.source_id,a.started_at DESC,a.ended_at DESC NULLS FIRST,a.attempt_id DESC""",
            (port,),
        )
        attempts = cursor.fetchall()
        cursor.execute(
            """SELECT started_at,details,status,run_id FROM pipeline_runs
            WHERE stage='pipeline_news' AND workflow_key LIKE %s
            ORDER BY started_at DESC,id DESC LIMIT 1""",
            (f"{PIPELINE_VERSION}:{port}:news:%",),
        )
        schedule = cursor.fetchone()
    latest = max(
        attempts, key=lambda item: (item["started_at"], str(item["attempt_id"])), default=None
    )
    primary = next((item for item in attempts if item["source_id"] == "gdelt_doc_v2"), None)
    source = success["source_id"] if success else latest["source_id"] if latest else None
    provider, source_url = PROVIDERS.get(source, ("News source", None))
    fallback = source in FALLBACKS
    quality = success["quality"] if success else {}
    result = dict(
        product="recent_news",
        port_id=port,
        source_id=source,
        provider=provider,
        source_url=source_url,
        using_fallback=fallback,
        fallback_label=f"{provider} official releases (limited coverage)" if fallback else None,
        coverage=quality.get("coverage", "Query-matched article metadata; incomplete coverage"),
        data_status=("available" if success["row_count"] else "valid_empty")
        if success
        else "unavailable",
        last_successful_fetch_utc=success["selected_retrieved_at"] if success else None,
        saved_batch_id=success["selected_batch_id"] if success else None,
        request_key=success["request_key"] if success else None,
        saved_row_count=success["row_count"] if success else None,
        valid_start_utc=success["start_utc"] if success else None,
        valid_end_utc=success["end_utc"] if success else None,
        window_hours=(success["end_utc"] - success["start_utc"]).total_seconds() / 3600
        if success
        else None,
        configured_window_hours=config.news_lookback_hours,
        query=success["parameters"].get("api", {}).get("query") if success else None,
        response_bytes=success["byte_count"] if success else None,
        response_format=success["source_metadata"].get("format", "JSON") if success else None,
        quality=quality,
        latest_attempt=latest,
        selected_attempt=next((item for item in attempts if item["source_id"] == source), None),
        primary_attempt=primary,
        schedule=dict(
            started_at=schedule["started_at"],
            run_id=schedule["run_id"],
            status=schedule["details"].get("status", schedule["status"]),
            reason=schedule["details"].get("reason"),
            next_allowed_utc=schedule["details"].get("next_allowed_utc"),
        )
        if schedule
        else None,
        last_attempt_status=latest["status"] if latest else None,
        latest_attempt_source_id=latest["source_id"] if latest else None,
        last_http_status=latest["http_status"] if latest else None,
        latest_attempt_utc=latest["started_at"] if latest else None,
        next_allowed_utc=latest["next_allowed_at"] if latest else None,
        error_class="ContextSourceFailure" if latest and latest["status"] == "failed" else None,
    )
    return news_state(result, now, settings.health_context_stale_seconds)


def news_identity(context):
    """Age-free revision material; identical bytes with a new receipt must invalidate."""
    return {
        key: value
        for key, value in context.items()
        if key not in {"success_age_seconds", "freshness", "status"}
    }


def read_news_rows(connection, context, timezone):
    if not context.get("saved_batch_id"):
        return []
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """SELECT n.title,n.original_url AS url,n.source_domain AS domain,
            n.publication_time_utc,n.first_seen_time_utc,n.relevance_tags,n.quality_flags,
            c.source_metadata->'publication_dates'->>n.original_url AS publication_date,
            c.source_metadata->>'publication_date_timezone_assumption' AS publication_timezone
            FROM news_mentions n JOIN context_responses c ON c.batch_id=n.batch_id
            WHERE n.batch_id=%s AND c.request_key=%s
            ORDER BY COALESCE((n.publication_time_utc AT TIME ZONE %s)::date,
              (c.source_metadata->'publication_dates'->>n.original_url)::date,
              (n.first_seen_time_utc AT TIME ZONE %s)::date) DESC NULLS LAST,
              n.publication_time_utc DESC NULLS LAST,n.first_seen_time_utc DESC NULLS LAST,
              n.canonical_url LIMIT 10""",
            (context["saved_batch_id"], context["request_key"], timezone, timezone),
        )
        rows = cursor.fetchall()
    for row in rows:
        row["publication_precision"] = (
            "instant"
            if row["publication_time_utc"]
            else ("date" if row["publication_date"] else "unknown")
        )
    return rows
