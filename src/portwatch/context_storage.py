"""Durable context lineage, failures, revisions, selection and replay separation."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from portwatch.bookkeeping import (
    begin_batch_processing,
    finish_batch_processing,
    finish_run,
    register_batch,
    start_run,
)
from portwatch.context import (
    PARSER_VERSION,
    ContextError,
    ContextRequest,
    normalize_news,
    normalize_weather,
    plan_context,
    utc,
)
from portwatch.context_http import HttpResponse, fetch_context
from portwatch.settings import Settings
from portwatch.storage import BatchSpec, Manifest, RawStore


@dataclass(frozen=True)
class ContextResult:
    request_key: str
    batch_id: str
    rows: int
    reused: bool
    retrieved_at: datetime
    quality: dict


def cached_response(connection, request: ContextRequest) -> ContextResult | None:
    row = connection.execute(
        """SELECT r.selected_batch_id, c.row_count, r.selected_retrieved_at, c.quality
        FROM context_requests r JOIN context_responses c ON c.batch_id = r.selected_batch_id
        WHERE r.request_key = %s AND c.request_key = r.request_key""",
        (request.request_key,),
    ).fetchone()
    return ContextResult(request.request_key, row[0], row[1], True, row[2], row[3]) if row else None


def save_response(connection, store, request, response) -> ContextResult:
    """Store exact bytes before parsing; failed normalization keeps raw evidence."""
    manifest = store.write(
        [response.payload],
        BatchSpec(
            source_id=request.source_id,
            dataset_id="ctx-" + request.request_key,
            source_url=request.endpoint,
            mode="historical" if request.product == "historical_reanalysis" else "live",
            source_date=request.start_utc.date()
            if request.product == "historical_reanalysis"
            else None,
            received_at=response.retrieved_at,
            source_start_utc=request.start_utc,
            source_end_utc=request.end_utc,
            processing_version=PARSER_VERSION,
            file_suffix=".json",
            remote_etag=response.etag,
            remote_last_modified=response.last_modified,
        ),
    )
    register_batch(connection, store, manifest)
    existing = connection.execute(
        "SELECT row_count, quality FROM context_responses WHERE batch_id = %s", (manifest.batch_id,)
    ).fetchone()
    if existing:
        return ContextResult(
            request.request_key,
            manifest.batch_id,
            existing[0],
            True,
            response.retrieved_at,
            existing[1],
        )
    # Recover a terminated normalization while holding the connector's session lock.
    connection.execute(
        """UPDATE ingestion_batches SET status = 'failed', error_summary = 'InterruptedContext'
        WHERE batch_id = %s AND status = 'processing'""",
        (manifest.batch_id,),
    )
    begin_batch_processing(connection, manifest.batch_id)
    try:
        normalize = normalize_weather if request.kind == "weather" else normalize_news
        normalized = normalize(response.payload, request)
        with connection.transaction():
            connection.execute(
                """INSERT INTO context_responses
                (batch_id, request_key, row_count, quality, source_metadata)
                VALUES (%s, %s, %s, %s, %s)""",
                (
                    manifest.batch_id,
                    request.request_key,
                    len(normalized.rows),
                    Jsonb(normalized.quality),
                    Jsonb(normalized.metadata),
                ),
            )
            if request.kind == "weather":
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO weather_values (batch_id, valid_time_utc, wind_speed_10m,
                        wind_gusts_10m, precipitation, quality_flags) VALUES (%s,%s,%s,%s,%s,%s)""",
                        [
                            (
                                manifest.batch_id,
                                row["valid_time_utc"],
                                row["wind_speed_10m"],
                                row["wind_gusts_10m"],
                                row["precipitation"],
                                row["quality_flags"],
                            )
                            for row in normalized.rows
                        ],
                    )
            else:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO news_mentions (batch_id, canonical_url, original_url,
                        title, source_domain, language, publication_time_utc, first_seen_time_utc,
                        relevance_tags, quality_flags) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [
                            (
                                manifest.batch_id,
                                row["canonical_url"],
                                row["original_url"],
                                row["title"],
                                row["source_domain"],
                                row["language"],
                                row["publication_time_utc"],
                                row["first_seen_time_utc"],
                                row["relevance_tags"],
                                row["quality_flags"],
                            )
                            for row in normalized.rows
                        ],
                    )
            finish_batch_processing(connection, manifest.batch_id)
        return ContextResult(
            request.request_key,
            manifest.batch_id,
            len(normalized.rows),
            False,
            response.retrieved_at,
            normalized.quality,
        )
    except BaseException as error:
        finish_batch_processing(connection, manifest.batch_id, error)
        raise


def ingest_context(
    settings: Settings,
    connection,
    request: ContextRequest,
    *,
    refresh=False,
    fetch=fetch_context,
    now: datetime | None = None,
) -> ContextResult:
    now = utc(now or datetime.now(UTC))
    # One writer per source kind across ports; no long database transaction during HTTP.
    lock = int.from_bytes(request.kind.encode().ljust(8, b"\0"), "big")
    if not connection.execute("SELECT pg_try_advisory_lock(%s)", (lock,)).fetchone()[0]:
        raise ContextError("context_connector_already_running")
    try:
        return _ingest_locked(settings, connection, request, refresh=refresh, fetch=fetch, now=now)
    finally:
        connection.execute("SELECT pg_advisory_unlock(%s)", (lock,))


def _ingest_locked(settings, connection, request, *, refresh, fetch, now):
    # The session lock proves any older running context attempt has lost its worker.
    with connection.transaction():
        interrupted = connection.execute(
            """UPDATE context_attempts a SET status = 'failed', error_code = 'source_interrupted',
            ended_at = clock_timestamp() FROM context_requests r
            WHERE a.request_key = r.request_key AND r.kind = %s AND a.status = 'running'
            RETURNING a.run_id""",
            (request.kind,),
        ).fetchall()
        for (run_id,) in interrupted:
            connection.execute(
                """UPDATE pipeline_runs SET status = 'interrupted', ended_at = clock_timestamp(),
                error_summary = 'InterruptedContext' WHERE run_id = %s AND status = 'running'""",
                (run_id,),
            )
    connection.execute(
        """INSERT INTO context_requests (request_key, kind, port_id, product, source_id,
        endpoint, parameters, start_utc, end_utc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (request_key) DO NOTHING""",
        (
            request.request_key,
            request.kind,
            request.port_id,
            request.product,
            request.source_id,
            request.endpoint,
            Jsonb(dict(api=request.parameters, keywords=request.keywords, parser=PARSER_VERSION)),
            request.start_utc,
            request.end_utc,
        ),
    )
    cached = cached_response(connection, request)
    if not cached and not refresh and request.product == "historical_reanalysis":
        # Rebuild an empty database from verified saved evidence without fabricating a fetch.
        store = RawStore(settings.project_path(settings.raw_dir))
        saved = [
            item.manifest
            for item in store.inspect()
            if item.state == "complete"
            and item.manifest.dataset_id == "ctx-" + request.request_key
            and item.manifest.source_id == request.source_id
            and item.manifest.source_url == request.endpoint
            and item.manifest.mode == "historical"
            and item.manifest.source_start_utc == request.start_utc
            and item.manifest.source_end_utc == request.end_utc
        ]
        if saved:
            manifest = max(saved, key=lambda item: item.received_at)
            result = save_response(
                connection,
                store,
                request,
                HttpResponse(
                    store._path(manifest.local_path).read_bytes(),
                    manifest.received_at,
                    200,
                    manifest.remote_etag,
                    manifest.remote_last_modified,
                ),
            )
            connection.execute(
                "UPDATE context_requests SET selected_batch_id=%s,selected_retrieved_at=%s "
                "WHERE request_key=%s",
                (result.batch_id, manifest.received_at, request.request_key),
            )
            return result
    if (
        cached
        and not refresh
        and (
            request.product == "historical_reanalysis"
            or now < cached.retrieved_at + timedelta(minutes=15)
        )
    ):
        store = RawStore(settings.project_path(settings.raw_dir))
        local = connection.execute(
            "SELECT local_path FROM ingestion_batches WHERE batch_id = %s", (cached.batch_id,)
        ).fetchone()[0]
        path = store._path(local)
        manifest = Manifest.model_validate_json(
            type(path)(str(path) + ".manifest.json").read_text("utf-8")
        )
        if manifest.batch_id != cached.batch_id:
            raise ContextError("cached_context_manifest_mismatch")
        register_batch(connection, store, manifest)
        return cached
    last = connection.execute(
        """SELECT max(a.next_allowed_at) FROM context_attempts a
        JOIN context_requests r USING (request_key) WHERE r.kind = %s""",
        (request.kind,),
    ).fetchone()[0]
    if last and now < last:
        raise ContextError("local_poll_cooldown", next_allowed_at=last)
    run_id = start_run(connection, request.kind + "_ingest")
    attempt_id = uuid4()
    cooldown = timedelta(minutes=15) if request.kind == "news" else timedelta(seconds=60)
    connection.execute(
        """INSERT INTO context_attempts
        (attempt_id, request_key, run_id, started_at, status, next_allowed_at)
        VALUES (%s,%s,%s,%s,'running',%s)""",
        (attempt_id, request.request_key, run_id, now, now + cooldown),
    )
    result, response = None, None
    try:
        response = fetch(request.url)
        store = RawStore(settings.project_path(settings.raw_dir))
        result = save_response(connection, store, request, response)
        with connection.transaction():
            connection.execute(
                """UPDATE context_requests SET selected_batch_id = %s, selected_retrieved_at = %s
                WHERE request_key = %s""",
                (result.batch_id, response.retrieved_at, request.request_key),
            )
            connection.execute(
                """UPDATE context_attempts SET ended_at = clock_timestamp(), retrieved_at = %s,
                batch_id = %s, http_status = %s, status = %s WHERE attempt_id = %s""",
                (
                    response.retrieved_at,
                    result.batch_id,
                    response.status,
                    "valid_nonempty" if result.rows else "valid_empty",
                    attempt_id,
                ),
            )
            finish_run(
                connection,
                run_id,
                input_count=result.rows,
                output_count=0 if result.reused else result.rows,
            )
        return result
    except BaseException as error:
        # Reference even malformed successful HTTP responses via their raw identity.
        batch = (
            connection.execute(
                """SELECT batch_id FROM ingestion_batches WHERE dataset_id = %s
                AND content_sha256 = %s""",
                ("ctx-" + request.request_key, hashlib.sha256(response.payload).hexdigest()),
            ).fetchone()
            if response
            else None
        )
        next_allowed = getattr(error, "next_allowed_at", None)
        with connection.transaction():
            connection.execute(
                """UPDATE context_attempts SET ended_at = clock_timestamp(), status = 'failed',
                error_code = %s, http_status = %s, batch_id = %s, retrieved_at = %s,
                next_allowed_at = greatest(next_allowed_at, %s) WHERE attempt_id = %s""",
                (
                    error.code if isinstance(error, ContextError) else type(error).__name__,
                    response.status if response else getattr(error, "http_status", None),
                    batch[0] if batch else None,
                    response.retrieved_at if response else None,
                    next_allowed or now + cooldown,
                    attempt_id,
                ),
            )
            finish_run(connection, run_id, error=error)
        raise


def read_demo_context(
    settings: Settings,
    connection,
    port_id: str,
    as_of_utc: datetime,
    *,
    batch_id: str | None = None,
) -> dict:
    """Retrospective historical weather prefix; recent news is always explicitly excluded."""
    request = plan_context(settings, port_id, "weather", demo=True)
    cutoff = utc(as_of_utc)
    if not request.start_utc <= cutoff <= request.end_utc:
        raise ContextError("demo_context_cutoff_outside_period")
    selected = cached_response(connection, request)
    chosen = batch_id or (selected.batch_id if selected else None)
    if chosen is None:
        return dict(
            weather=[],
            weather_status="unavailable",
            news=[],
            news_status="excluded_recent_source",
            interpretation="retrospective_reanalysis",
        )
    row = connection.execute(
        "SELECT source_metadata FROM context_responses WHERE batch_id = %s AND request_key = %s",
        (chosen, request.request_key),
    ).fetchone()
    if not row:
        raise ContextError("weather_revision_not_in_demo_partition")
    weather = connection.execute(
        """SELECT valid_time_utc, wind_speed_10m, wind_gusts_10m, precipitation, quality_flags
        FROM weather_values WHERE batch_id = %s AND valid_time_utc >= %s
        AND valid_time_utc < %s AND valid_time_utc <= %s ORDER BY valid_time_utc""",
        (chosen, request.start_utc, request.end_utc, cutoff),
    ).fetchall()
    return dict(
        batch_id=chosen,
        weather=weather,
        weather_status="available",
        metadata=row[0],
        news=[],
        news_status="excluded_recent_source",
        interpretation="retrospective_reanalysis",
    )
