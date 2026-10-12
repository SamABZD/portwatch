"""PortWatch command line interface."""

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from portwatch.bookkeeping import finish_run, reconcile, register_batch, start_run
from portwatch.cleaning import transform_positions
from portwatch.db import connect_db, migrate
from portwatch.doctor import diagnose, required_checks_pass
from portwatch.download import DownloadError
from portwatch.ingest import artifact_path, ingest_historical, plan
from portwatch.live import CaptureError, LiveCollector, live_spec
from portwatch.settings import Settings
from portwatch.storage import RawStore
from portwatch.visit_storage import transform_visits
from portwatch.visits import InferenceError, utc
from portwatch.zones import configured_geofence, load_geofence, transform_zones

app = typer.Typer(help="PortWatch local data engineering commands.")
db_app = typer.Typer(help="PostgreSQL schema commands.")
storage_app = typer.Typer(help="Verify raw files and recover database registration.")
ingest_app = typer.Typer(help="Acquire historical AIS or collect live AIS independently.")
transform_app = typer.Typer(help="Normalize saved source batches with deterministic lineage.")
geo_app = typer.Typer(help="Load immutable approximate analytical geofences.")
app.add_typer(db_app, name="db")
app.add_typer(storage_app, name="storage")
app.add_typer(ingest_app, name="ingest")
app.add_typer(transform_app, name="transform")
app.add_typer(geo_app, name="geo")


@transform_app.command("visits")
def visits_transform(
    port: Annotated[str, typer.Option(help="Configured port ID.")] = "los_angeles",
    demo: Annotated[bool, typer.Option(help="Infer from the saved historical dataset.")] = False,
    as_of: Annotated[
        str | None, typer.Option(help="Read-only UTC event-time prefix; ISO timestamp.")
    ] = None,
    version: Annotated[str | None, typer.Option(help="Previously loaded geofence version.")] = None,
) -> None:
    """Infer visits and waiting candidates; retain gaps, partials and timing uncertainty."""
    if not demo:
        typer.echo("Select --demo for the saved historical dataset.", err=True)
        raise typer.Exit(code=2)
    cutoff = None
    if as_of:
        try:
            cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            utc(cutoff)
        except ValueError:
            typer.echo("--as-of requires an explicit UTC ISO timestamp.", err=True)
            raise typer.Exit(code=2) from None
    try:
        settings = Settings()
        with connect_db(settings) as connection:
            selected, rules, counts = transform_visits(
                settings, connection, port, as_of_utc=cutoff, version=version
            )
        typer.echo(
            f"Geofence: {selected}; rules: {rules.rule_id}; "
            f"mode: {'read-only event-time prefix' if cutoff else 'retrospective full-history'}"
        )
        typer.echo(
            f"Positions: {counts.positions}; tracks: {counts.tracks}; visits: {counts.visits}; "
            f"completed: {counts.completed_visits}; partial: {counts.partial_visits}; "
            f"waiting candidates: {counts.waiting_episodes}; "
            f"region only: {counts.regional_candidates}; "
            f"visit associated: {counts.port_associated_candidates}"
        )
        typer.echo("Waiting is a low-speed observation proxy, not a confirmed port queue.")
    except Exception as error:
        detail = str(error) if isinstance(error, InferenceError) else type(error).__name__
        typer.echo(f"Visit inference failed: {detail}", err=True)
        raise typer.Exit(code=1) from None


@geo_app.command("load")
def geo_load(
    port: Annotated[str, typer.Option(help="Configured port ID.")] = "los_angeles",
    file: Annotated[Path | None, typer.Option(help="Versioned GeoJSON override.")] = None,
) -> None:
    """Validate polygons without repair and load or reuse an immutable version."""
    try:
        settings = Settings()
        geofence = configured_geofence(settings, port, file)
        with connect_db(settings) as connection:
            created = load_geofence(connection, geofence)
        typer.echo(
            f"Geofence {geofence.document.version}: {'loaded' if created else 'reused'}; "
            f"zones: {len(geofence.document.features)}; SHA-256: {geofence.content_sha256}"
        )
        typer.echo("Approximate analytical zones; candidate waiting areas are regional.")
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        typer.echo(f"Geofence load failed: {detail}", err=True)
        raise typer.Exit(code=1) from None


@transform_app.command("zones")
def zones_transform(
    port: Annotated[str, typer.Option(help="Configured port ID.")] = "los_angeles",
    demo: Annotated[bool, typer.Option(help="Classify the saved historical demo.")] = False,
    version: Annotated[str | None, typer.Option(help="Previously loaded geofence version.")] = None,
) -> None:
    """Preserve every membership and an explicit outside state for each saved position."""
    if not demo:
        typer.echo("Select --demo; this stage classifies the saved historical dataset.", err=True)
        raise typer.Exit(code=2)
    try:
        settings = Settings()
        with connect_db(settings) as connection:
            selected, counts = transform_zones(settings, connection, port, version)
        typer.echo(
            f"Geofence: {selected}; positions: {counts.input_positions}; "
            f"newly assigned: {counts.newly_assigned}; inside: {counts.inside}; "
            f"outside: {counts.outside}; memberships: {counts.memberships}; "
            f"overlapping positions: {counts.overlaps}"
        )
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        typer.echo(f"Zone transform failed: {detail}", err=True)
        raise typer.Exit(code=1) from None


@transform_app.command("positions")
def positions_transform(
    port: Annotated[str, typer.Option(help="Configured port ID.")] = "los_angeles",
    demo: Annotated[bool, typer.Option(help="Clean the saved historical demo.")] = False,
    pending_live: Annotated[
        bool, typer.Option(help="Process saved live batches; reuse completed counts.")
    ] = False,
) -> None:
    """Clean saved positions, preserve rejects, and safely replay completed inputs."""
    try:
        settings = Settings()
        with connect_db(settings) as connection:
            reports = transform_positions(
                settings, connection, port, demo=demo, pending_live=pending_live
            )
        for input_id, counts, reused in reports:
            typer.echo(f"Input: {input_id}; {'reused' if reused else 'processed'}")
            typer.echo(
                f"Records: {counts.input_records}; valid: {counts.valid_positions}; "
                f"duplicates: {counts.duplicate_records}; "
                f"quarantined: {counts.quarantined_records}; "
                f"skipped: {counts.skipped_records}"
            )
        if not reports:
            typer.echo("No saved live batches; capture live data first.")
    except Exception as error:
        typer.echo(
            f"Position transform failed ({type(error).__name__}); "
            "inspect storage, migrations and runbook.",
            err=True,
        )
        raise typer.Exit(code=1) from None


@app.callback()
def main() -> None:
    """PortWatch CLI."""


@app.command()
def doctor() -> None:
    """Check package, configuration, local storage, database, and PostGIS."""
    checks = diagnose()
    for check in checks:
        typer.echo(f"[{check.status}] {check.name}: {check.detail}")
    healthy = required_checks_pass(checks)
    typer.echo(f"Overall: {'PASS' if healthy else 'FAIL'}")
    if not healthy:
        raise typer.Exit(code=1)


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply new numbered SQL migrations, preserving existing data."""
    try:
        with connect_db(Settings()) as connection:
            applied = migrate(connection)
        typer.echo(f"Migrations applied: {len(applied)}")
        for name in applied:
            typer.echo(name)
    except Exception as error:
        typer.echo(
            f"Migration failed ({type(error).__name__}); check DB settings and SQL.", err=True
        )
        raise typer.Exit(code=1) from None


def configured_store(root: Path | None) -> RawStore:
    settings = Settings()
    return RawStore(root if root is not None else settings.project_path(settings.raw_dir))


@storage_app.command("inspect")
def storage_inspect(
    root: Annotated[Path | None, typer.Option(help="Override raw root.")] = None,
) -> None:
    """Verify manifests, byte counts, and SHA-256; requires no database."""
    try:
        items = configured_store(root).inspect()
        for item in items:
            identity = item.manifest.batch_id if item.manifest else "unknown"
            typer.echo(f"{item.state}: {identity} ({item.detail})")
        complete = sum(item.state == "complete" for item in items)
        typer.echo(f"Verified complete: {complete}; issues: {len(items) - complete}")
    except Exception as error:
        typer.echo(f"Storage inspection failed ({type(error).__name__}).", err=True)
        raise typer.Exit(code=1) from None
    if len(items) != complete:
        raise typer.Exit(code=1)


@storage_app.command("reconcile")
def storage_reconcile(
    root: Annotated[Path | None, typer.Option(help="Override raw root.")] = None,
) -> None:
    """Register verified completed files without downloading them again."""
    try:
        store = configured_store(root)
        with connect_db(Settings()) as connection:
            result = reconcile(connection, store)
        typer.echo(
            f"Registered: {result.registered}; existing: {result.existing}; "
            f"pending: {result.pending}; invalid: {result.invalid}"
        )
    except Exception as error:
        typer.echo(f"Storage reconciliation failed ({type(error).__name__}).", err=True)
        raise typer.Exit(code=1) from None
    if result.pending or result.invalid:
        raise typer.Exit(code=1)


@ingest_app.command("historical")
def historical_ingest(
    port: Annotated[str, typer.Option(help="Configured port ID.")] = "los_angeles",
    demo: Annotated[bool, typer.Option(help="Use the frozen one-day demo.")] = False,
    start: Annotated[str | None, typer.Option(help="First UTC date, YYYY-MM-DD.")] = None,
    end: Annotated[str | None, typer.Option(help="Exclusive final UTC date.")] = None,
    refresh: Annotated[bool, typer.Option(help="Check remote source identity.")] = False,
) -> None:
    """Preserve NOAA's daily artifact, then extract regional positions in bounded batches."""
    try:
        settings = Settings()
        requests = plan(settings, port, demo, start, end)
    except (ValueError, KeyError, OSError) as error:
        detail = str(error) if type(error) is ValueError else type(error).__name__
        typer.echo(f"Invalid historical request: {detail}", err=True)
        raise typer.Exit(code=2) from None
    last_progress = 0.0

    def progress(count: int) -> None:
        nonlocal last_progress
        now = time.monotonic()
        if now - last_progress > 15:
            typer.echo(f"Transfer progress: {count / 1024 / 1024:.1f} MiB")
            last_progress = now

    try:
        with connect_db(settings) as connection:
            results = ingest_historical(
                settings, connection, requests, refresh=refresh, progress=progress
            )
    except Exception as error:
        detail = str(error) if isinstance(error, DownloadError) else type(error).__name__
        typer.echo(f"Historical ingestion failed: {detail}", err=True)
        raise typer.Exit(code=1) from None
    for result in results:
        report = result.manifest
        typer.echo(
            f"Raw: {'downloaded' if result.downloaded else 'reused'}; "
            f"extract: {'reused' if result.reused_extract else 'created'}"
        )
        typer.echo(
            f"Source rows: {report.source_rows_inspected}; region rows: {report.region_rows}; "
            f"unusable coordinates: {report.unusable_coordinate_rows}; "
            f"selected: {report.rows_selected}; distinct vessels: {report.distinct_vessels}"
        )
        typer.echo(f"UTC range: {report.earliest_utc} to {report.latest_utc}")
        typer.echo(
            f"Output bytes: {report.byte_count}; command seconds: {result.elapsed_seconds:.2f}"
        )
        typer.echo(
            f"Recorded extraction process peak bytes: {report.peak_process_memory_bytes} "
            f"({report.memory_measurement}); max batch rows: {report.max_batch_rows_observed}"
        )
        typer.echo(f"Artifact: {artifact_path(settings, report)}")
        for row in report.preview:
            typer.echo(
                f"Preview: {row['timestamp_utc']} lon={row['longitude']:.6f} "
                f"lat={row['latitude']:.6f}"
            )
    if sum(result.manifest.rows_selected for result in results) == 0:
        typer.echo("No usable regional observations; inspect coverage before switching days.")
        raise typer.Exit(code=1)


LIVE_ACTIONS = {
    "missing_api_key": (
        "Live collection unverified: set PORTWATCH_AIS_API_KEY in ignored .env using a key "
        "from https://aisstream.io/account, then run ingest live --port los_angeles --duration 60. "
        "Historical mode remains available."
    ),
    "subscription_rejected": "Verify AISStream key, subscription and account/IP connection limits.",
    "subscription_not_confirmed": "Verify key, subscription and account/IP connection limits.",
    "subscription_confirmation_timeout": "No confirmation; verify service availability and key.",
    "compression_not_confirmed": "Compression unconfirmed; check WebSocket negotiation.",
    "buffer_pressure": "Queue full; capture stopped and queued records drained. Check disk speed.",
    "raw_persistence_failure": "Check disk and PostgreSQL; active files kept for recovery.",
    "active_recovery_failure": "Inspect active files and metadata before retrying; files retained.",
    "credential_echo_refused": "Credential bytes echoed; frame refused and capture stopped.",
    "repeated_unconfirmed_disconnects": "Repeated early disconnects; verify key and service.",
    "socket_policy_or_protocol_failure": "Verify key, provider limits and WebSocket protocol.",
}


@ingest_app.command("live")
def live_ingest(
    port: Annotated[str, typer.Option(help="Configured port ID.")] = "los_angeles",
    duration: Annotated[
        float | None, typer.Option(min=0.01, help="Finite capture seconds.")
    ] = None,
) -> None:
    """Collect AISStream to durable raw batches; Ctrl+C drains and closes the batch."""
    collector = None
    try:
        settings = Settings()
        spec, bounds = live_spec(settings, port)
        if not settings.ais_api_key or not settings.ais_api_key.get_secret_value().strip():
            raise CaptureError("missing_api_key")
        store = RawStore(settings.project_path(settings.raw_dir))

        def progress(status: dict) -> None:
            typer.echo(
                f"Live {status['state']}: observations={status['observations']} "
                f"received={status['received_records']} parse_errors={status['parse_errors']} "
                f"reconnects={status['reconnect_count']} "
                f"queue={status['queue_occupancy']}/{status['queue_capacity']}"
            )

        with connect_db(settings) as connection:
            collector = LiveCollector(
                settings.ais_api_key,
                bounds,
                spec,
                store,
                settings.project_path(settings.live_spool_dir) / spec.dataset_id,
                settings.project_path(settings.live_status_dir) / f"{port}-status.json",
                register=lambda batch: register_batch(connection, store, batch),
                queue_records=settings.live_queue_records,
                rotate_seconds=settings.live_rotate_seconds,
                rotate_bytes=settings.live_rotate_bytes,
                flush_seconds=settings.live_flush_seconds,
                status_callback=progress,
            )
            run_id = start_run(connection, "live_collect")
            try:
                asyncio.run(collector.run(duration))
            except BaseException as error:
                finish_run(
                    connection,
                    run_id,
                    input_count=collector.status.received_records,
                    output_count=collector.status.finalized_records,
                    error=error,
                )
                raise
            finish_run(
                connection,
                run_id,
                input_count=collector.status.received_records,
                output_count=collector.status.finalized_records,
            )
    except CaptureError as error:
        typer.echo(
            f"Live capture failed: {error.code}. {LIVE_ACTIONS.get(error.code, 'Check runbook.')}",
            err=True,
        )
        raise typer.Exit(code=2 if error.code == "missing_api_key" else 1) from None
    except Exception as error:
        typer.echo(
            f"Live capture failed ({type(error).__name__}); check configuration/runbook.", err=True
        )
        raise typer.Exit(code=1) from None
    finally:
        if collector is not None:
            status = collector.snapshot()
            typer.echo(
                f"Capture seconds: {status['elapsed_seconds']:.2f}; observations: "
                f"{status['observations']}; distinct vessels: {status['distinct_vessels']}; "
                f"finalized records: {status['finalized_records']}; "
                f"unfinalized received: {status['unfinalized_received_records']}; "
                f"recovered tail bytes: {status['truncated_tail_bytes']}"
            )
            typer.echo(f"Status: {collector.status_path}")
            for path in status["recent_batch_paths"]:
                typer.echo(f"Raw batch: {path}")
