"""Finite, resumable business workflows. This module works without a Prefect server."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pyarrow.parquet as pq
from psycopg.types.json import Jsonb

from portwatch.bookkeeping import finish_run, register_batch
from portwatch.cleaning import historical_input, load_batch, transform_positions
from portwatch.context import ARCHIVE_ENDPOINT, ContextError, plan_context
from portwatch.context_storage import cached_response, ingest_context
from portwatch.db import connect_db
from portwatch.download import DownloadError, acquire
from portwatch.events import emit
from portwatch.historical import extract
from portwatch.ingest import plan
from portwatch.live import live_spec
from portwatch.marts import MartConfig, recompute_marts, validate_rows
from portwatch.positions import PROCESSING_VERSION
from portwatch.settings import Settings, load_port_config
from portwatch.storage import RawStore
from portwatch.visit_storage import recompute_visits, rules_for_port
from portwatch.visits import identity
from portwatch.zones import AssignmentScope, assign_zones, configured_geofence, load_geofence

PIPELINE_VERSION = "pipeline-v1"


class PipelineError(RuntimeError):
    """Only a trusted reason code crosses the CLI/Prefect boundary."""

    def __init__(self, code, *, transient=False):
        self.code, self.transient = code, transient
        super().__init__(code)


class ControlledStop(PipelineError):
    def __init__(self):
        super().__init__("controlled_stop_after_raw")


def sanitized(error):
    if isinstance(error, PipelineError):
        return error
    if isinstance(error, (ContextError, DownloadError)):
        return PipelineError(str(error))
    # Authentication, missing objects and invalid SQL are not transient.
    transient = isinstance(
        error,
        (
            psycopg.OperationalError,
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
        ),
    ) and getattr(error, "sqlstate", None) not in {"28P01", "28000", "3D000"}
    return PipelineError(type(error).__name__, transient=transient)


def lock_id(namespace, key):
    return int.from_bytes(
        hashlib.sha256(f"portwatch:{namespace}:{key}".encode()).digest()[:8], "big", signed=True
    )


@contextmanager
def owned_lock(connection, namespace, key):
    """Session ownership survives commits; closing a crashed session releases its locks."""
    # Different schemas own different tables (including the isolated integration fixtures).
    schema = connection.execute("SELECT current_schema()").fetchone()[0]
    lock = lock_id(namespace, f"{schema}:{key}")
    if not connection.execute("SELECT pg_try_advisory_lock(%s)", (lock,)).fetchone()[0]:
        raise PipelineError("overlapping_request_busy")
    try:
        yield
    finally:
        if not connection.closed:
            connection.execute("SELECT pg_advisory_unlock(%s)", (lock,))


def begin_workflow(connection, key, stage, parent=None):
    # Caller holds this workflow's lock, so an old running attempt has lost its owner.
    connection.execute(
        """UPDATE pipeline_runs SET status='interrupted',ended_at=clock_timestamp(),
        error_summary='AbandonedSession' WHERE workflow_key=%s AND status='running'""",
        (key,),
    )
    attempt = connection.execute(
        "SELECT coalesce(max(attempt_number),0)+1 FROM pipeline_runs WHERE workflow_key=%s",
        (key,),
    ).fetchone()[0]
    run_id = uuid4()
    connection.execute(
        """INSERT INTO pipeline_runs(run_id,stage,workflow_key,parent_run_id,attempt_number)
        VALUES (%s,%s,%s,%s,%s)""",
        (run_id, stage, key, parent, attempt),
    )
    emit("workflow_attempt_started", run_id=str(run_id), stage=stage, attempt_number=attempt)
    return run_id


def checkpointed(connection, key, step, fingerprint, parent, function, *, force=False):
    previous = connection.execute(
        "SELECT input_sha256,result FROM pipeline_checkpoints WHERE workflow_key=%s AND step=%s",
        (key, step),
    ).fetchone()
    run_id = begin_workflow(connection, f"{key}:{step}", "pipeline_" + step, parent)
    try:
        reused = bool(previous and previous[0] == fingerprint and not force)
        result = previous[1] if reused else function()
        # Functions have returned from their data transactions before this checkpoint commits.
        with connection.transaction():
            connection.execute(
                """INSERT INTO pipeline_checkpoints(workflow_key,step,input_sha256,run_id,result)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(workflow_key,step) DO UPDATE
                SET input_sha256=excluded.input_sha256,run_id=excluded.run_id,
                result=excluded.result,committed_at=clock_timestamp()""",
                (key, step, fingerprint, run_id, Jsonb(result)),
            )
            connection.execute(
                "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
                (Jsonb(dict(result, checkpoint_reused=reused)), run_id),
            )
            finish_run(
                connection,
                run_id,
                input_count=result.get("input_count"),
                output_count=result.get("output_count"),
            )
        return dict(result, checkpoint_reused=reused)
    except BaseException as error:
        connection.execute(
            "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
            (Jsonb({"reason": sanitized(error).code}), run_id),
        )
        finish_run(connection, run_id, error=error)
        raise sanitized(error) from None


def day_weather(settings, port, start, end):
    """The same audited ERA5 variables, pinned to the requested source day."""
    template = plan_context(settings, port, "weather", demo=True)
    return replace(
        template,
        start_utc=start,
        end_utc=end,
        endpoint=ARCHIVE_ENDPOINT,
        parameters=dict(
            template.parameters,
            start_date=start.date().isoformat(),
            end_date=(end - timedelta(days=1)).date().isoformat(),
        ),
    )


def day_request(settings, port, day):
    end = (datetime.fromisoformat(day).date() + timedelta(days=1)).isoformat()
    return plan(settings, port, False, day, end)[0]


def available_context(connection, spec):
    """At most seven contiguous processed UTC days; a missing day breaks the context."""
    rows = connection.execute(
        """SELECT DISTINCT b.dataset_id,b.source_start_utc FROM ingestion_batches b
        JOIN clean_batches c ON c.raw_batch_id=b.batch_id WHERE b.source_id=%s
        AND c.processing_version=%s AND c.status='processed'
        AND b.source_start_utc>=%s AND b.source_start_utc<%s""",
        (
            spec.source_id,
            PROCESSING_VERSION,
            spec.source_start_utc - timedelta(days=6),
            spec.source_end_utc + timedelta(days=6),
        ),
    ).fetchall()
    by_day = {start: dataset for dataset, start in rows}
    selected = {spec.source_start_utc: spec.dataset_id}
    # Prefer earlier context (known entry), then later exit, bounded total resource use.
    for direction in (-1, 1):
        cursor = spec.source_start_utc + timedelta(days=direction)
        while cursor in by_day and len(selected) < 7:
            selected[cursor] = by_day[cursor]
            cursor += timedelta(days=direction)
    return tuple(selected[key] for key in sorted(selected))


def publication_inputs(connection, scope):
    # Late reports and changed quality flags invalidate analytical checkpoints.
    digest = hashlib.sha256()
    with connection.transaction(), connection.cursor(name="pipeline_fingerprint") as cursor:
        cursor.execute(
            """SELECT observation_id,effective_at,speed_knots,quality_flags FROM vessel_positions
            WHERE source_id=%s AND dataset_id=ANY(%s) AND processing_version=%s
            ORDER BY observation_id""",
            (scope.source_id, list(scope.dataset_ids), scope.clean_version),
        )
        while rows := cursor.fetchmany(4096):
            for row in rows:
                digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
                digest.update(b"\n")
    return digest.hexdigest()


def validate_publication(connection, scope, config):
    from psycopg.rows import dict_row

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT * FROM port_activity_hourly WHERE partition_id=%s ORDER BY hour_utc",
            (config.partition_id(scope),),
        )
        rows = cursor.fetchall()
    expected = connection.execute(
        """SELECT count(*) FROM vessel_positions WHERE source_id=%s AND dataset_id=ANY(%s)
        AND processing_version=%s AND effective_at>=%s AND effective_at<%s""",
        (
            scope.source_id,
            list(scope.dataset_ids),
            scope.clean_version,
            scope.start_utc,
            scope.end_utc,
        ),
    ).fetchone()[0]
    validate_rows(
        rows,
        expected_observations=expected,
        expected_hours=int((scope.end_utc - scope.start_utc).total_seconds() / 3600),
    )
    return dict(
        input_count=expected, output_count=len(rows), partition_id=config.partition_id(scope)
    )


def execute_step(port, day, step, key, parent, *, force=False):
    """Arguments contain public identities only, never Settings or credentials."""
    settings = Settings()
    spec, params = day_request(settings, port, day)
    store = RawStore(settings.project_path(settings.raw_dir))
    with connect_db(settings) as connection:
        if step == "raw":

            def raw():
                result = acquire(store, spec)
                with pq.ParquetFile(store._path(result.manifest.local_path)) as source:
                    if source.metadata.num_rows == 0:
                        raise PipelineError("empty_national_source")
                register_batch(connection, store, result.manifest)
                return dict(
                    batch_id=result.manifest.batch_id,
                    input_count=result.manifest.byte_count,
                    output_count=1,
                )

            # Always reverify the saved bytes, even when later steps are checkpointed.
            return checkpointed(
                connection, key, step, identity([spec.source_url]), parent, raw, force=True
            )
        saved = connection.execute(
            "SELECT result FROM pipeline_checkpoints WHERE workflow_key=%s AND step='raw'", (key,)
        ).fetchone()[0]
        batch = next(
            item.manifest
            for item in store.inspect()
            if item.state == "complete" and item.manifest.batch_id == saved["batch_id"]
        )
        if step == "extract":

            def extraction():
                derived, _ = extract(
                    store._path(batch.local_path),
                    batch,
                    params,
                    settings.project_path(settings.derived_dir),
                )
                return dict(
                    input_count=derived.source_rows_inspected,
                    output_count=derived.rows_selected,
                    derivation_id=derived.derivation_id,
                )

            # extract() verifies both the immutable parent and its derived artifact when cached.
            return checkpointed(
                connection,
                key,
                step,
                identity([batch.batch_id, params.model_dump(mode="json")]),
                parent,
                extraction,
                force=True,
            )
        if step == "clean":
            source = historical_input(settings, store, spec, params)

            def clean():
                with owned_lock(connection, "mutation", port):
                    counts, _ = load_batch(
                        connection, source, settings.project_path(settings.quarantine_dir)
                    )
                return dict(input_count=counts.input_records, output_count=counts.valid_positions)

            return checkpointed(
                connection,
                key,
                step,
                identity([source.input_id, source.content_sha256, PROCESSING_VERSION]),
                parent,
                clean,
                force=force,
            )
        if step == "weather":
            request = day_weather(settings, port, params.start_utc, params.end_utc)

            def weather():
                result = ingest_context(settings, connection, request)
                return dict(input_count=1, output_count=result.rows, batch_id=result.batch_id)

            # Connector selection is checked every time, and may change after manual refresh.
            return checkpointed(
                connection, key, step, request.request_key, parent, weather, force=True
            )
        geofence = configured_geofence(settings, port)
        port_config = load_port_config(settings.project_path(settings.port_config))
        scope = AssignmentScope(
            spec.source_id,
            spec.dataset_id,
            params.start_utc,
            params.end_utc,
            context_dataset_ids=available_context(connection, spec),
        )
        request = day_weather(settings, port, params.start_utc, params.end_utc)
        weather = cached_response(connection, request)
        config = MartConfig(
            port,
            geofence.document.version,
            rules_for_port(port_config),
            port_config.snapshot_staleness_minutes * 60,
            weather.batch_id if weather else None,
        )
        fingerprint = identity(
            [
                PIPELINE_VERSION,
                publication_inputs(connection, scope),
                geofence.content_sha256,
                config.analytical_version(scope),
            ]
        )
        if step == "publish":

            def publish():
                with owned_lock(connection, "mutation", port):
                    load_geofence(connection, geofence)
                    result = recompute_marts(connection, scope, config)
                return dict(
                    asdict(result), input_count=result.observations, output_count=result.hours
                )

            return checkpointed(connection, key, step, fingerprint, parent, publish, force=force)
        if step == "validate":
            return checkpointed(
                connection,
                key,
                step,
                fingerprint,
                parent,
                lambda: validate_publication(connection, scope, config),
                force=True,
            )
        raise PipelineError("unknown_pipeline_step")


def run_day(
    port, day, *, prepare_only=False, stop_after_raw=False, force=False, step_runner=execute_step
):
    settings = Settings()
    spec, _ = day_request(settings, port, day)
    key = f"{PIPELINE_VERSION}:{port}:{spec.dataset_id}"
    with connect_db(settings) as connection:
        try:
            with owned_lock(connection, "workflow", key):
                run_id = begin_workflow(connection, key, "pipeline_historical")
                try:
                    reports = {}
                    for step in (
                        ("raw", "extract", "clean", "weather")
                        if prepare_only
                        else ("raw", "extract", "clean", "weather", "publish", "validate")
                    ):
                        reports[step] = step_runner(port, day, step, key, str(run_id), force=force)
                        if stop_after_raw and step == "raw":
                            raise ControlledStop()
                    connection.execute(
                        "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
                        (Jsonb(reports), run_id),
                    )
                    finish_run(
                        connection,
                        run_id,
                        input_count=reports["clean"]["input_count"],
                        output_count=reports.get("validate", {}).get("output_count", 0),
                    )
                    connection.execute(
                        "UPDATE pipeline_source_gaps SET resolved_at=clock_timestamp() "
                        "WHERE port_id=%s AND source_id=%s AND dataset_id=%s",
                        (port, spec.source_id, spec.dataset_id),
                    )
                    return dict(
                        day=day,
                        run_id=str(run_id),
                        status="prepared" if prepare_only else "succeeded",
                        steps=reports,
                    )
                except BaseException as error:
                    safe = sanitized(error)
                    connection.execute(
                        "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
                        (Jsonb({"reason": safe.code}), run_id),
                    )
                    if isinstance(error, ControlledStop):
                        connection.execute(
                            "UPDATE pipeline_runs SET status='interrupted',"
                            "ended_at=clock_timestamp(),error_summary=%s WHERE run_id=%s",
                            (safe.code, run_id),
                        )
                    else:
                        finish_run(connection, run_id, error=safe)
                    if safe.code == "http_404":
                        connection.execute(
                            """INSERT INTO pipeline_source_gaps VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)
                            ON CONFLICT(port_id,source_id,dataset_id) DO UPDATE SET
                            run_id=excluded.run_id,reason=excluded.reason,resolved_at=NULL""",
                            (
                                port,
                                spec.source_id,
                                spec.dataset_id,
                                spec.source_start_utc,
                                spec.source_end_utc,
                                run_id,
                                safe.code,
                            ),
                        )
                    raise safe from None
        except PipelineError as error:
            if error.code == "overlapping_request_busy":
                # Busy has a terminal audited outcome and never advances checkpoints.
                run_id = uuid4()
                connection.execute(
                    "INSERT INTO pipeline_runs(run_id,stage,workflow_key,details) "
                    "VALUES (%s,'pipeline_busy',%s,%s)",
                    (run_id, key, Jsonb({"reason": error.code})),
                )
                finish_run(connection, run_id, error=error)
                return dict(day=day, status="busy", run_id=str(run_id))
            raise


def run_historical(
    port="los_angeles",
    *,
    demo=True,
    start=None,
    end=None,
    stop_after_raw=False,
    force=False,
    step_runner=execute_step,
):
    requests = plan(Settings(), port, demo, start, end)
    reports, failed = [], []
    # One source day at a time; locks/transactions are released between finite stages/days.
    # Prepare all available inputs before analytics so neighboring days form complete context.
    for spec, _ in requests:
        day = spec.source_date.isoformat()
        try:
            result = run_day(
                port,
                day,
                prepare_only=not demo,
                stop_after_raw=stop_after_raw,
                force=force,
                step_runner=step_runner,
            )
            reports.append(result)
        except ControlledStop:
            raise
        except PipelineError as error:
            failed.append(dict(day=day, status="failed", reason=error.code))
    if not demo:
        for prepared in list(reports):
            if prepared["status"] != "prepared":
                continue
            try:
                reports[reports.index(prepared)] = run_day(
                    port, prepared["day"], force=force, step_runner=step_runner
                )
            except PipelineError as error:
                failed.append(dict(day=prepared["day"], status="failed", reason=error.code))
    return dict(
        status="failed"
        if failed
        else ("busy" if any(r["status"] == "busy" for r in reports) else "succeeded"),
        partitions=reports,
        failures=failed,
    )


def run_context(kind, port="los_angeles"):
    settings = Settings()
    with connect_db(settings) as connection:
        request = plan_context(settings, port, kind)
        key = f"{PIPELINE_VERSION}:{port}:{kind}:{request.request_key}"
        with owned_lock(connection, "workflow", key):
            run_id = begin_workflow(connection, key, "pipeline_" + kind)
            try:
                result = ingest_context(settings, connection, request, refresh=True)
                report = dict(
                    batch_id=result.batch_id,
                    rows=result.rows,
                    retrieved_at=result.retrieved_at.isoformat(),
                    product=request.product,
                )
                connection.execute(
                    "UPDATE pipeline_runs SET details=%s WHERE run_id=%s", (Jsonb(report), run_id)
                )
                finish_run(connection, run_id, input_count=1, output_count=result.rows)
                return report
            except Exception as error:
                connection.execute(
                    "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
                    (Jsonb({"reason": sanitized(error).code}), run_id),
                )
                finish_run(connection, run_id, error=error)
                raise sanitized(error) from None


def run_live(port="los_angeles"):
    settings = Settings()
    spec, _ = live_spec(settings, port)
    key = f"{PIPELINE_VERSION}:{port}:{spec.dataset_id}"
    with connect_db(settings) as connection:
        with owned_lock(connection, "workflow", key), owned_lock(connection, "mutation", port):
            run_id = begin_workflow(connection, key, "pipeline_live")
            try:
                reports = transform_positions(
                    settings, connection, port, demo=False, pending_live=True
                )
                first, last = connection.execute(
                    "SELECT min(effective_at),max(effective_at) FROM vessel_positions "
                    "WHERE source_id=%s AND dataset_id=%s AND processing_version=%s",
                    (spec.source_id, spec.dataset_id, PROCESSING_VERSION),
                ).fetchone()
                published = []
                if first is not None:
                    fence = configured_geofence(settings, port)
                    load_geofence(connection, fence)
                    config_port = load_port_config(settings.project_path(settings.port_config))
                    request = plan_context(settings, port, "weather")
                    weather = cached_response(connection, request)
                    config = MartConfig(
                        port,
                        fence.document.version,
                        rules_for_port(config_port),
                        config_port.snapshot_staleness_minutes * 60,
                        weather.batch_id if weather else None,
                        "current_forecast",
                    )
                    # One stable live series carries complete history across midnight. Publish the
                    # last seven observed days in bounded windows; no historical NOAA is mixed in.
                    start = max(
                        first.replace(hour=0, minute=0, second=0, microsecond=0),
                        last.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6),
                    )
                    end = min(
                        last.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
                        datetime.now(UTC).replace(minute=0, second=0, microsecond=0),
                    )
                    track_scope = AssignmentScope(
                        spec.source_id, spec.dataset_id, first, last + timedelta(microseconds=1)
                    )
                    # Visits include the current hour, but only elapsed hours are published.
                    fingerprint = identity(
                        [
                            publication_inputs(connection, track_scope),
                            config.analytical_version(track_scope),
                            start,
                            end,
                        ]
                    )

                    def publish_live():
                        if start < end:
                            scope = AssignmentScope(spec.source_id, spec.dataset_id, start, end)
                            result = recompute_marts(connection, scope, config)
                            validate_publication(connection, scope, config)
                            return dict(
                                asdict(result),
                                input_count=result.observations,
                                output_count=result.hours,
                            )
                        assign_zones(connection, port, fence.document.version, track_scope)
                        result = recompute_visits(
                            connection,
                            track_scope,
                            port,
                            fence.document.version,
                            config.visit_rules,
                        )
                        return dict(input_count=result.positions, output_count=0, hours=0)

                    published.append(
                        checkpointed(
                            connection, key, "live_publish", fingerprint, run_id, publish_live
                        )
                    )
                report = dict(
                    status="succeeded",
                    completed_batches=len(reports),
                    new_batches=sum(not reused for _, _, reused in reports),
                    new_positions=sum(
                        count.valid_positions for _, count, reused in reports if not reused
                    ),
                    latest_observation_utc=last.isoformat() if last else None,
                    observation_lag_seconds=max(0, (datetime.now(UTC) - last).total_seconds())
                    if last
                    else None,
                    marts=published,
                )
                connection.execute(
                    "UPDATE pipeline_runs SET details=%s WHERE run_id=%s", (Jsonb(report), run_id)
                )
                finish_run(
                    connection,
                    run_id,
                    input_count=len(reports),
                    output_count=report["new_positions"],
                )
                return report
            except BaseException as error:
                connection.execute(
                    "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
                    (Jsonb({"reason": sanitized(error).code}), run_id),
                )
                finish_run(connection, run_id, error=error)
                raise sanitized(error) from None
