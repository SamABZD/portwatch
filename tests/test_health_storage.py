"""Read-only status, backlog, failures and data counts in isolated real PostgreSQL schemas."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from portwatch.bookkeeping import finish_run, start_run
from portwatch.cleaning import load_batch
from portwatch.health import collect_health
from portwatch.ingest import plan
from portwatch.marts import MartConfig, recompute_marts
from portwatch.settings import Settings
from portwatch.storage import RawStore
from portwatch.visits import VisitRules
from tests.test_bookkeeping import connection as pg_connection
from tests.test_cleaning import prepare
from tests.test_visit_storage import BASE, VERSION, prepare_track

connection = pg_connection
pytestmark = pytest.mark.integration


def test_unregistered_raw_backlog_then_old_historical_input_is_ready_not_stale(
    connection, tmp_path
):
    settings = Settings(
        raw_dir=tmp_path / "raw", live_status_dir=tmp_path / "status", ais_api_key=None
    )
    spec, _ = plan(settings, "los_angeles", True, None, None)[0]
    parent = RawStore(settings.raw_dir).write([b"synthetic health source"], spec)
    now = max(datetime.now(UTC), parent.received_at + timedelta(seconds=121))
    report = collect_health(settings, connection=connection, now=now)
    assert report["historical"]["awaiting_processing"] == 1
    assert report["historical"]["awaiting_registration"] == 1
    assert report["historical"]["status"] == "backlog_overdue"
    scope = prepare_track(connection, tmp_path, parent)
    # This publishes synthetic events only into this fixture's schema.
    from dataclasses import replace

    scope = replace(scope, end_utc=BASE + timedelta(days=1))
    recompute_marts(connection, scope, MartConfig("los_angeles", VERSION, VisitRules()))
    audit = connection.execute("SELECT count(*) FROM pipeline_runs").fetchone()
    report = collect_health(settings, connection=connection, now=now)
    assert report["historical"]["status"] == "ready_historical"
    assert report["historical"]["awaiting_processing"] == 0
    assert report["historical"]["canonical_positions"] == 5
    assert report["historical"]["observation_age_seconds"] > 86400
    assert report["historical"]["receipt_to_analytical_latency"]["sample_batches"] == 1
    assert connection.execute("SELECT count(*) FROM pipeline_runs").fetchone() == audit


def test_malformed_record_duplicate_counts_corruption_and_consecutive_failures(
    connection, tmp_path
):
    from tests.test_positions import noaa_row

    settings = Settings(
        raw_dir=tmp_path / "raw", live_status_dir=tmp_path / "status", ais_api_key=None
    )
    spec, _ = plan(settings, "los_angeles", True, None, None)[0]
    store = RawStore(settings.raw_dir)
    parent = store.write([b"synthetic health malformed source"], spec)
    source = prepare(connection, tmp_path, parent)
    load_batch(
        connection,
        source,
        tmp_path / "quarantine",
        records=[
            (0, noaa_row(parent)),
            (1, noaa_row(parent, 1)),
            (2, noaa_row(parent, 2, latitude=91)),
        ],
    )
    finish_run(connection, start_run(connection, "marts"), error=RuntimeError("synthetic_secret"))
    finish_run(connection, start_run(connection, "marts"), error=RuntimeError("synthetic_secret"))
    report = collect_health(settings, connection=connection)
    assert report["historical"]["accepted_records"] == 1
    assert report["historical"]["duplicate_records"] == 1
    assert report["historical"]["quarantined_records"] == 1
    assert report["runs"]["consecutive_failures_by_stage"]["marts"] == 2
    assert "synthetic_secret" not in json.dumps(report)
    finish_run(connection, start_run(connection, "marts"))
    store._path(parent.local_path).write_bytes(b"synthetic truncation")
    report = collect_health(settings, connection=connection)
    assert report["runs"]["consecutive_failures_by_stage"]["marts"] == 0
    assert report["raw_storage"]["invalid_batches"] == 1
    assert "raw_integrity_or_publication_issue" in report["issues"]
