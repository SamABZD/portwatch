"""Read-only operational health with explicit historical/live/unknown distinctions."""

import json
import shutil
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import psutil
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from portwatch.db import connect_db
from portwatch.events import emit
from portwatch.ingest import plan
from portwatch.live import live_spec
from portwatch.positions import PROCESSING_VERSION
from portwatch.settings import Settings, for_port, load_port_config
from portwatch.storage import RawStore

COLLECTOR_STATES = {
    "starting",
    "awaiting_confirmation",
    "subscribed",
    "disconnected",
    "backoff",
    "draining",
    "stopped",
    "failed",
}
FAILURE_CODES = {
    "missing_api_key",
    "subscription_rejected",
    "compression_not_confirmed",
    "invalid_subscription_confirmation",
    "subscription_confirmation_timeout",
    "subscription_not_confirmed",
    "repeated_unconfirmed_disconnects",
    "socket_policy_or_protocol_failure",
    "buffer_pressure",
    "active_recovery_failure",
    "raw_persistence_failure",
    "raw_writer_stopped",
    "transport_failure",
}


def parsed_time(value):
    try:
        stamp = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        return stamp.astimezone(UTC) if stamp.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def age(now, value):
    stamp = parsed_time(value)
    seconds = (now - stamp).total_seconds() if stamp else None
    return seconds if seconds is not None and seconds >= 0 else None


def number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, UUID):
        return str(value)
    raise TypeError("unsupported health value")


def process_matches(pid, started):
    if not number(pid) or parsed_time(started) is None:
        return None
    try:
        process = psutil.Process(pid)
        return (
            process.is_running()
            and abs(process.create_time() - parsed_time(started).timestamp()) < 1
        )
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return None


def collector_health(snapshot, now, settings, *, probe=process_matches):
    """Pongs prove connection health; report silence never establishes absence of vessels."""
    if not snapshot:
        return dict(
            status="not_observed",
            process_alive=None,
            connected=None,
            observation_state="unknown",
            last_observed_ais_message_utc=None,
        )
    state = snapshot.get("state") if snapshot.get("state") in COLLECTOR_STATES else "unknown"
    alive = probe(snapshot.get("process_id"), snapshot.get("process_created_at_utc"))
    update_age = age(now, snapshot.get("status_updated_at_utc"))
    heartbeat_age = age(now, snapshot.get("last_socket_heartbeat"))
    observation_age = age(now, snapshot.get("last_ais_observation_at"))
    connected = snapshot.get("connected") is True
    confirmed = snapshot.get("subscription_confirmed") is True
    if state in {"stopped", "failed"}:
        status = state
    elif alive is False:
        status = "dead_process"
    elif alive is None:
        status = "unverified_process"
    elif update_age is None or update_age > settings.health_status_stale_seconds:
        status = "unresponsive_process"
    elif state in {"backoff", "disconnected"}:
        status = "reconnecting"
    elif not connected or not confirmed:
        status = "connecting"
    elif heartbeat_age is None or heartbeat_age > settings.health_status_stale_seconds:
        status = "heartbeat_unknown_or_stale"
    else:
        status = "healthy_connected"
    observation_state = (
        "recent"
        if observation_age is not None
        and observation_age <= (settings.health_observation_stale_seconds)
        else "no_recent_observations"
    )

    def timestamp(name):
        stamp = parsed_time(snapshot.get(name))
        return stamp.isoformat() if stamp else None

    return dict(
        status=status,
        reported_state=state,
        process_alive=alive,
        process_id=number(snapshot.get("process_id")),
        connected=connected,
        subscription_confirmed=confirmed,
        status_age_seconds=update_age,
        heartbeat_age_seconds=heartbeat_age,
        last_heartbeat_utc=timestamp("last_socket_heartbeat"),
        last_observed_ais_message_utc=timestamp("last_ais_observation_at"),
        last_socket_message_utc=timestamp("last_socket_message_at"),
        observation_age_seconds=observation_age,
        observation_state=observation_state,
        timestamp_semantics="local_receipt_not_verified_provider_event_time",
        reconnect_count=number(snapshot.get("reconnect_count")),
        connections=number(snapshot.get("connections")),
        raw_persistence_failures=number(snapshot.get("raw_persistence_failures")),
        failure_code=snapshot.get("failure_code")
        if snapshot.get("failure_code") in FAILURE_CODES
        else ("reported_failure" if state == "failed" else None),
    )


def read_collector(settings, port, now):
    path = settings.project_path(settings.live_status_dir) / f"{port}-status.json"
    if not path.is_file():
        return collector_health(None, now, settings)
    try:
        if path.stat().st_size > 65536:
            raise ValueError
        snapshot = json.loads(path.read_text("utf-8"))
        spec, _ = live_spec(settings, port)
        if not isinstance(snapshot, dict) or (
            snapshot.get("source_id"),
            snapshot.get("dataset_id"),
        ) != (spec.source_id, spec.dataset_id):
            raise ValueError
        return collector_health(snapshot, now, settings)
    except (OSError, ValueError, TypeError):
        return dict(status="invalid_status_file", process_alive=None, observation_state="unknown")


def disk_health(settings):
    path = settings.project_path(settings.raw_dir)
    existing = path
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    try:
        usage = shutil.disk_usage(existing)
        return dict(
            status="low_space" if usage.free < settings.storage_min_free_bytes else "ok",
            free_bytes=usage.free,
            total_bytes=usage.total,
            minimum_free_bytes=settings.storage_min_free_bytes,
            checked_path=str(existing),
        )
    except OSError:
        return dict(
            status="unavailable",
            free_bytes=None,
            minimum_free_bytes=settings.storage_min_free_bytes,
        )


def series_health(connection, spec, complete, now, settings):
    descriptor = (spec.source_id, spec.dataset_id, PROCESSING_VERSION)
    batches = connection.execute(
        """SELECT b.batch_id,coalesce(bool_or(c.status='processed'),false)
        FROM ingestion_batches b LEFT JOIN clean_batches c ON c.raw_batch_id=b.batch_id
        AND c.processing_version=%s WHERE b.source_id=%s AND b.dataset_id=%s GROUP BY b.batch_id""",
        (PROCESSING_VERSION, spec.source_id, spec.dataset_id),
    ).fetchall()
    registered = dict(batches)
    raw = [
        item.manifest
        for item in complete
        if item.manifest.source_id == spec.source_id and item.manifest.dataset_id == spec.dataset_id
    ]
    waiting = [batch for batch in raw if not registered.get(batch.batch_id, False)]
    oldest = min((batch.received_at for batch in waiting), default=None)
    counts = connection.execute(
        """SELECT coalesce(sum(c.input_records),0),coalesce(sum(c.valid_positions),0),
        coalesce(sum(c.duplicate_records),0),coalesce(sum(c.quarantined_records),0),
        coalesce(sum(c.skipped_records),0),max(c.completed_at) FROM clean_batches c
        JOIN ingestion_batches b ON b.batch_id=c.raw_batch_id
        WHERE b.source_id=%s AND b.dataset_id=%s
        AND c.processing_version=%s AND c.status='processed'""",
        descriptor,
    ).fetchone()
    positions, latest = connection.execute(
        "SELECT count(*),max(effective_at) FROM vessel_positions "
        "WHERE source_id=%s AND dataset_id=%s "
        "AND processing_version=%s",
        descriptor,
    ).fetchone()
    latency = connection.execute(
        """SELECT count(*),percentile_cont(0.5) WITHIN GROUP (ORDER BY
        extract(epoch FROM c.completed_at-b.received_at)),max(extract(epoch FROM
        c.completed_at-b.received_at)) FROM clean_batches c JOIN ingestion_batches b
        ON b.batch_id=c.raw_batch_id WHERE b.source_id=%s AND b.dataset_id=%s
        AND c.processing_version=%s AND c.status='processed' AND c.completed_at>=b.received_at""",
        descriptor,
    ).fetchone()
    published, hours = connection.execute(
        "SELECT max(computed_at),"
        "coalesce(sum((extract(epoch FROM end_utc-start_utc)/3600)::integer),0) "
        "FROM mart_partitions WHERE source_id=%s AND dataset_id=%s AND clean_version=%s",
        descriptor,
    ).fetchone()
    if waiting:
        status = (
            "backlog_overdue"
            if age(now, oldest) is not None
            and age(now, oldest) > (settings.health_backlog_warn_seconds)
            else "backlog"
        )
    elif not raw:
        status = "no_saved_input"
    elif spec.mode == "historical":
        status = "ready_historical" if published else "cleaned_not_published"
    elif latest is None:
        status = "no_observed_positions"
    else:
        status = (
            "recent_observations"
            if age(now, latest) is not None
            and age(now, latest) <= (settings.health_observation_stale_seconds)
            else "stale_observations"
        )
    return dict(
        status=status,
        source_id=spec.source_id,
        dataset_id=spec.dataset_id,
        mode=spec.mode,
        complete_saved_batches=len(raw),
        awaiting_processing=len(waiting),
        awaiting_registration=sum(batch.batch_id not in registered for batch in raw),
        oldest_unprocessed_utc=oldest,
        oldest_unprocessed_age_seconds=age(now, oldest),
        accepted_records=counts[1],
        canonical_positions=positions,
        input_records=counts[0],
        duplicate_records=counts[2],
        quarantined_records=counts[3],
        skipped_records=counts[4],
        last_clean_commit_utc=counts[5],
        last_observation_utc=latest,
        observation_age_seconds=age(now, latest),
        last_mart_publication_utc=published,
        published_hours_across_versions=hours,
        receipt_to_analytical_latency=dict(
            measure="raw_receipt_to_clean_commit",
            sample_batches=latency[0],
            median_seconds=latency[1],
            max_seconds=latency[2],
            semantics="batch_receipt_to_transactional_clean_publication_not_upstream_latency",
        ),
        coverage="observed_reports_not_total_ais",
        historical_age_is_failure=False,
    )


def context_health(connection, port, product, now, settings):
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """SELECT a.status,a.started_at,a.retrieved_at,a.http_status,
            a.error_code,a.next_allowed_at,r.source_id
            FROM context_attempts a JOIN context_requests r USING(request_key)
            WHERE r.port_id=%s AND r.product=%s
            ORDER BY a.started_at DESC,a.ended_at DESC NULLS FIRST LIMIT 1""",
            (port, product),
        )
        attempt = cursor.fetchone()
        cursor.execute(
            """SELECT r.selected_retrieved_at,r.selected_batch_id,c.row_count,r.start_utc,r.end_utc,
            r.source_id
            FROM context_requests r JOIN context_responses c ON c.batch_id=r.selected_batch_id
            WHERE r.port_id=%s AND r.product=%s ORDER BY r.selected_retrieved_at DESC LIMIT 1""",
            (port, product),
        )
        success = cursor.fetchone()
    historical = product == "historical_reanalysis"
    if success:
        status = (
            "available_historical"
            if historical
            else (
                "stale"
                if age(now, success["selected_retrieved_at"]) is None
                or age(now, success["selected_retrieved_at"])
                > settings.health_context_stale_seconds
                else "valid_empty"
                if success["row_count"] == 0
                else "available"
            )
        )
    else:
        status = "unavailable" if attempt else "not_requested"
    # Surface latest failure while preserving an earlier successful revision/fetch separately.
    if (
        attempt
        and attempt["status"] == "failed"
        and (not success or success["source_id"] == attempt["source_id"] or status == "stale")
    ):
        status = "failed_with_saved_revision" if success else "unavailable"
    return dict(
        product=product,
        source_id=success["source_id"] if success else attempt["source_id"] if attempt else None,
        using_fallback=bool(success and success["source_id"] == "port_oakland_rss"),
        status=status,
        last_successful_fetch_utc=success["selected_retrieved_at"] if success else None,
        success_age_seconds=age(now, success["selected_retrieved_at"]) if success else None,
        saved_batch_id=success["selected_batch_id"] if success else None,
        saved_row_count=success["row_count"] if success else None,
        valid_start_utc=success["start_utc"] if success else None,
        valid_end_utc=success["end_utc"] if success else None,
        last_attempt_status=attempt["status"] if attempt else None,
        latest_attempt_source_id=attempt["source_id"] if attempt else None,
        last_http_status=attempt["http_status"] if attempt else None,
        latest_attempt_utc=attempt["started_at"] if attempt else None,
        next_allowed_utc=attempt["next_allowed_at"] if attempt else None,
        error_class="ContextSourceFailure" if attempt and attempt["status"] == "failed" else None,
    )


def run_health(connection, port, *, include_unscoped=True):
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """WITH ranked AS (
            SELECT id,run_id,stage,
            CASE WHEN status='succeeded' AND details->>'status'='deferred'
                 THEN 'deferred' ELSE status END AS status,started_at,ended_at,input_count,
            output_count,error_summary,
            workflow_key,attempt_number,duration_seconds,
            row_number() OVER (PARTITION BY stage ORDER BY id DESC) AS sequence
            FROM pipeline_runs
            WHERE (workflow_key LIKE %s ESCAPE '!' OR (%s AND workflow_key IS NULL))
            AND stage<>'pipeline_busy'
            AND parent_run_id IS NULL)
            SELECT * FROM ranked WHERE sequence<=20 ORDER BY id DESC""",
            (f"%:{port.replace('_', '!_')}:%", include_unscoped),
        )
        recent = cursor.fetchall()
    consecutive = {}
    for stage in sorted({row["stage"] for row in recent}):
        failures = 0
        for row in [row for row in recent if row["stage"] == stage]:
            if row["status"] in {"running", "deferred"}:
                continue
            if row["status"] in {"failed", "interrupted"}:
                failures += 1
            else:
                break
        consecutive[stage] = failures
    # Error payloads/details are not returned: stored legacy messages may contain credentials.
    for row in recent:
        row["error_summary"] = "RecordedRunFailure" if row["error_summary"] else None
        row.pop("workflow_key")
        row.pop("id")
        row.pop("sequence")
    return dict(
        recent=recent[:20],
        consecutive_failures_by_stage=consecutive,
        unscoped_manual_run_note="legacy manual audits are project-wide"
        if include_unscoped
        else "unscoped manual audits excluded from this port view",
        running_attempts=connection.execute(
            "SELECT count(*) FROM pipeline_runs WHERE status='running' "
            "AND (workflow_key LIKE %s ESCAPE '!' OR (%s AND workflow_key IS NULL))",
            (f"%:{port.replace('_', '!_')}:%", include_unscoped),
        ).fetchone()[0],
        per_stage_history_limit=20,
    )


def collect_health(
    settings: Settings, port="los_angeles", *, now=None, connection=None, include_unscoped=True
):
    settings = for_port(settings, port)
    now = parsed_time(now or datetime.now(UTC))
    if now is None:
        raise ValueError("health requires an aware time")
    configured = load_port_config(settings.project_path(settings.port_config))
    if port != configured.port_id:
        raise ValueError("select the configured port")
    historical, _ = plan(settings, port, True, None, None)[0]
    live, _ = live_spec(settings, port)
    report = dict(
        schema_version=1,
        checked_at_utc=now,
        port_id=port,
        overall="ok",
        collector=read_collector(settings, port, now),
        disk=disk_health(settings),
        database={"status": "unknown"},
        historical={"status": "unknown"},
        live={"status": "unknown"},
        context=None,
        runs=None,
        thresholds=dict(
            status_stale_seconds=settings.health_status_stale_seconds,
            observation_stale_seconds=settings.health_observation_stale_seconds,
            backlog_warn_seconds=settings.health_backlog_warn_seconds,
            context_stale_seconds=settings.health_context_stale_seconds,
        ),
    )
    report["collector"]["api_key_configured"] = bool(
        settings.ais_api_key and settings.ais_api_key.get_secret_value().strip()
    )
    if not report["collector"]["api_key_configured"] and report["collector"]["status"] in {
        "not_observed",
        "stopped",
    }:
        report["collector"]["status"] = "not_configured"
    complete = []
    try:
        root = settings.project_path(settings.raw_dir)
        items = RawStore(root).inspect() if root.is_dir() else []
        complete = [item for item in items if item.state == "complete"]
        report["raw_storage"] = dict(
            status="verified" if root.is_dir() else "missing",
            complete_batches=len(complete),
            invalid_batches=sum(item.state == "invalid" for item in items),
            pending_publications=sum(item.state == "pending" for item in items),
            unmanifested_files=sum(item.state == "unmanifested" for item in items),
        )
    except (ValueError, OSError):
        report["raw_storage"] = dict(status="unavailable", complete_batches=None)
    try:
        with nullcontext(connection) if connection is not None else connect_db(settings) as conn:
            conn.execute("SELECT 1")
            report["database"] = {"status": "connected"}
            owns = conn.info.transaction_status == TransactionStatus.IDLE
            with conn.transaction():
                if owns:
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                conn.execute("SELECT set_config('statement_timeout','10s',true)")
                report["historical"] = series_health(conn, historical, complete, now, settings)
                report["live"] = series_health(conn, live, complete, now, settings)
                report["context"] = {
                    product: context_health(conn, port, product, now, settings)
                    for product in ("historical_reanalysis", "current_forecast", "recent_news")
                }
                report["runs"] = run_health(conn, port, include_unscoped=include_unscoped)
    except Exception as error:
        report["database"] = dict(
            status="schema_or_query_failure"
            if report["database"]["status"] == "connected"
            else "unavailable",
            error_class=type(error).__name__,
        )
    issues = []
    if report["database"]["status"] != "connected":
        issues.append("database_unavailable_or_schema_failure")
    if report["disk"]["status"] != "ok":
        issues.append("disk_space_pressure_or_unknown")
    storage = report["raw_storage"]
    if storage["status"] != "verified" or any(
        storage.get(key)
        for key in ("invalid_batches", "pending_publications", "unmanifested_files")
    ):
        issues.append("raw_integrity_or_publication_issue")
    if report["collector"]["status"] in {
        "failed",
        "dead_process",
        "unresponsive_process",
        "invalid_status_file",
        "reconnecting",
    }:
        issues.append("collector_requires_attention")
    for label in ("historical", "live"):
        if report[label]["status"] == "backlog_overdue":
            issues.append(label + "_backlog_overdue")
    if report["context"] and any(
        value["status"] in {"unavailable", "failed_with_saved_revision", "stale"}
        for value in report["context"].values()
    ):
        issues.append("context_source_unavailable_or_stale")
    if report["runs"] and any(report["runs"]["consecutive_failures_by_stage"].values()):
        issues.append("recent_processing_failure")
    report["issues"] = issues
    report["overall"] = "degraded" if issues else "ok"
    # Return only JSON-compatible values to the CLI and future read-only API.
    report = json.loads(json.dumps(report, default=json_value))
    emit(
        "health_snapshot",
        port_id=port,
        overall=report["overall"],
        database_status=report["database"]["status"],
        issue_count=len(issues),
    )
    return report


def human_health(report):
    lines = [
        f"PortWatch {report['port_id']}: {report['overall'].upper()} at {report['checked_at_utc']}",
        f"Database: {report['database']['status']}",
        f"Collector: {report['collector']['status']}; observation state: "
        f"{report['collector'].get('observation_state', 'unknown')}",
        "Last observed AIS message UTC: "
        f"{report['collector'].get('last_observed_ais_message_utc')}",
    ]
    for label in ("historical", "live"):
        series = report[label]
        lines.append(
            f"{label.title()}: {series['status']}; saved={series.get('complete_saved_batches')}; "
            f"pending={series.get('awaiting_processing')}; oldest age seconds="
            f"{series.get('oldest_unprocessed_age_seconds')}"
        )
        lines.append(
            f"  Accepted={series.get('accepted_records')}; quarantined="
            f"{series.get('quarantined_records')}; duplicate={series.get('duplicate_records')}; "
            f"receipt-to-clean latency={series.get('receipt_to_analytical_latency')}"
        )
    for product, context in (report["context"] or {}).items():
        lines.append(
            f"{product}: {context['status']}; last successful fetch UTC="
            f"{context['last_successful_fetch_utc']}; latest HTTP={context['last_http_status']}"
        )
    lines.append(
        f"Disk free bytes: {report['disk'].get('free_bytes')}; minimum="
        f"{report['disk']['minimum_free_bytes']}"
    )
    lines.append(
        "Consecutive failures by stage: "
        f"{(report['runs'] or {}).get('consecutive_failures_by_stage')}"
    )
    lines.append("Issues: " + (", ".join(report["issues"]) or "none"))
    lines.append(
        "Historical input age is not live-feed failure; absent reports do not prove zero traffic."
    )
    return "\n".join(lines)
