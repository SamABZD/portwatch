"""PostgreSQL registration and explicit processing/run state transitions."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from psycopg.pq import TransactionStatus

from portwatch.events import emit
from portwatch.storage import Manifest, RawStore, checksum_file


def safe_error(error: BaseException) -> str:
    # Exception messages can contain connection strings, API keys, and response bodies.
    return type(error).__name__


def register_batch(connection: psycopg.Connection, store: RawStore, manifest: Manifest) -> bool:
    """Return True only on first registration; never reset existing processing state."""
    path = store._path(manifest.local_path)
    sidecar = type(path)(str(path) + ".manifest.json")
    saved = Manifest.model_validate_json(sidecar.read_text("utf-8"))
    if saved != manifest or checksum_file(path) != (manifest.content_sha256, manifest.byte_count):
        raise ValueError("raw batch is not complete and verified")
    with connection.transaction():
        inserted = connection.execute(
            """INSERT INTO ingestion_batches (
                batch_id, source_id, dataset_id, source_url, mode, local_path,
                content_sha256, byte_count, record_count, received_at, source_start_utc,
                source_end_utc, processing_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (batch_id) DO NOTHING RETURNING batch_id""",
            (
                manifest.batch_id,
                manifest.source_id,
                manifest.dataset_id,
                manifest.source_url,
                manifest.mode,
                manifest.local_path,
                manifest.content_sha256,
                manifest.byte_count,
                manifest.record_count,
                manifest.received_at,
                manifest.source_start_utc,
                manifest.source_end_utc,
                manifest.processing_version,
            ),
        ).fetchone()
        if not inserted:
            existing = connection.execute(
                """SELECT source_id, dataset_id, source_url, content_sha256, local_path, byte_count
                FROM ingestion_batches WHERE batch_id = %s""",
                (manifest.batch_id,),
            ).fetchone()
            expected = (
                manifest.source_id,
                manifest.dataset_id,
                manifest.source_url,
                manifest.content_sha256,
                manifest.local_path,
                manifest.byte_count,
            )
            if existing != expected:
                raise ValueError("registered batch conflicts with manifest")
    return bool(inserted)


def begin_batch_processing(connection: psycopg.Connection, batch_id: str) -> None:
    changed = connection.execute(
        """UPDATE ingestion_batches SET status = 'processing', attempt_count = attempt_count + 1,
        error_summary = NULL WHERE batch_id = %s AND status IN ('registered', 'failed')
        RETURNING batch_id""",
        (batch_id,),
    ).fetchone()
    if not changed:
        raise ValueError("batch cannot begin processing in its current state")


def finish_batch_processing(
    connection: psycopg.Connection, batch_id: str, error: BaseException | None = None
) -> None:
    changed = connection.execute(
        """UPDATE ingestion_batches SET status = %s, error_summary = %s
        WHERE batch_id = %s AND status = 'processing' RETURNING batch_id""",
        ("failed" if error else "processed", safe_error(error) if error else None, batch_id),
    ).fetchone()
    if not changed:
        raise ValueError("batch is not processing")


def start_run(connection: psycopg.Connection, stage: str) -> UUID:
    if stage not in {
        "storage_reconcile",
        "historical_ingest",
        "live_collect",
        "clean",
        "zones",
        "visits",
        "weather_ingest",
        "news_ingest",
        "marts",
    }:
        raise ValueError("unknown pipeline stage")
    run_id = uuid4()
    connection.execute("INSERT INTO pipeline_runs (run_id, stage) VALUES (%s, %s)", (run_id, stage))
    emit(
        "pipeline_run_started",
        run_id=str(run_id),
        stage=stage,
        audit_visibility="committed"
        if connection.info.transaction_status == TransactionStatus.IDLE
        else "pending_transaction",
    )
    return run_id


def finish_run(
    connection: psycopg.Connection,
    run_id: UUID,
    *,
    input_count: int | None = None,
    output_count: int | None = None,
    error: BaseException | None = None,
) -> None:
    changed = connection.execute(
        """UPDATE pipeline_runs SET ended_at = clock_timestamp(), status = %s,
        input_count = %s, output_count = %s, error_summary = %s
        WHERE run_id = %s AND status = 'running' RETURNING run_id""",
        (
            "failed" if error else "succeeded",
            input_count,
            output_count,
            safe_error(error) if error else None,
            run_id,
        ),
    ).fetchone()
    if not changed:
        raise ValueError("run is not running")
    emit(
        "pipeline_run_finished",
        run_id=str(run_id),
        status="failed" if error else "succeeded",
        input_count=input_count,
        output_count=output_count,
        error_class=safe_error(error) if error else None,
        audit_visibility="committed"
        if connection.info.transaction_status == TransactionStatus.IDLE
        else "pending_transaction",
    )


@dataclass
class ReconcileResult:
    registered: int = 0
    existing: int = 0
    pending: int = 0
    invalid: int = 0


def reconcile(connection: psycopg.Connection, store: RawStore) -> ReconcileResult:
    run_id = start_run(connection, "storage_reconcile")
    result = ReconcileResult()
    count = 0
    try:
        items = store.inspect()
        count = len(items)
        for item in items:
            if item.state == "pending":
                result.pending += 1
            elif item.state != "complete":
                result.invalid += 1
            elif register_batch(connection, store, item.manifest):
                result.registered += 1
            else:
                result.existing += 1
        finish_run(
            connection,
            run_id,
            input_count=count,
            output_count=result.registered,
            error=(
                ValueError("storage integrity issues") if result.pending or result.invalid else None
            ),
        )
    except Exception as error:
        finish_run(
            connection, run_id, input_count=count, output_count=result.registered, error=error
        )
        raise
    return result
