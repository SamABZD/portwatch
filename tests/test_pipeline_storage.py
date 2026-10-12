"""Real PostgreSQL ownership/checkpoint recovery with synthetic inputs in owned schemas."""

from dataclasses import replace
from datetime import timedelta

import pytest
from psycopg import sql

from portwatch.cleaning import load_batch
from portwatch.db import connect_db
from portwatch.marts import recompute_marts
from portwatch.pipeline import (
    ControlledStop,
    PipelineError,
    available_context,
    begin_workflow,
    checkpointed,
    day_request,
    execute_step,
    owned_lock,
    run_day,
    run_historical,
)
from portwatch.settings import Settings
from portwatch.storage import BatchSpec, RawStore
from tests.test_bookkeeping import connection as pg_connection
from tests.test_cleaning import prepare
from tests.test_historical import source as synthetic_source
from tests.test_mart_storage import CONFIG
from tests.test_visit_storage import BASE, prepare_track, records

connection = pg_connection
source = synthetic_source
pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_pipeline(connection, tmp_path, monkeypatch):
    import portwatch.pipeline as pipeline

    settings = Settings(
        raw_dir=tmp_path / "raw",
        derived_dir=tmp_path / "derived",
        quarantine_dir=tmp_path / "quarantine",
        ais_api_key=None,
    )
    schema = connection.execute("SELECT current_schema()").fetchone()[0]

    def connect(settings):
        conn = connect_db(settings)
        conn.execute(sql.SQL("SET search_path TO {},public").format(sql.Identifier(schema)))
        return conn

    monkeypatch.setattr(pipeline, "Settings", lambda: settings)
    monkeypatch.setattr(pipeline, "connect_db", connect)
    return settings, connect


def test_checkpoint_after_commit_failure_resumes_and_counts_attempts(connection):
    parent = begin_workflow(connection, "synthetic-parent", "pipeline_historical")
    connection.execute("CREATE TABLE synthetic_output (id integer PRIMARY KEY)")

    def failure():
        with connection.transaction():
            connection.execute("INSERT INTO synthetic_output VALUES (1)")
            raise RuntimeError("synthetic_secret")

    with pytest.raises(PipelineError):
        checkpointed(connection, "synthetic", "clean", "a" * 64, parent, failure)
    assert connection.execute("SELECT count(*) FROM synthetic_output").fetchone() == (0,)
    assert connection.execute("SELECT count(*) FROM pipeline_checkpoints").fetchone() == (0,)

    def commit():
        with connection.transaction():
            connection.execute("INSERT INTO synthetic_output VALUES (1)")
        return dict(input_count=1, output_count=1)

    assert not checkpointed(connection, "synthetic", "clean", "a" * 64, parent, commit)[
        "checkpoint_reused"
    ]
    assert checkpointed(connection, "synthetic", "clean", "a" * 64, parent, failure)[
        "checkpoint_reused"
    ]
    attempts = connection.execute(
        "SELECT attempt_number,status,duration_seconds,details "
        "FROM pipeline_runs WHERE stage='pipeline_clean' ORDER BY id"
    ).fetchall()
    assert [row[0] for row in attempts] == [1, 2, 3]
    assert [row[1] for row in attempts] == ["failed", "succeeded", "succeeded"]
    assert all(row[2] >= 0 for row in attempts)
    assert "synthetic_secret" not in str(attempts)


def test_owned_lock_excludes_other_sessions_releases_after_failure(connection):
    with connect_db(Settings()) as other:
        schema = connection.execute("SELECT current_schema()").fetchone()[0]
        other.execute(sql.SQL("SET search_path TO {},public").format(sql.Identifier(schema)))
        with pytest.raises(RuntimeError), owned_lock(connection, "workflow", "synthetic-lock"):
            with (
                pytest.raises(PipelineError, match="busy"),
                owned_lock(other, "workflow", "synthetic-lock"),
            ):
                pytest.fail("competing writer entered")
            raise RuntimeError("synthetic failure")
        with owned_lock(other, "workflow", "synthetic-lock"):
            assert True


def test_lost_session_releases_lock_and_marks_old_audit_interrupted(connection):
    other = connect_db(Settings())
    schema = connection.execute("SELECT current_schema()").fetchone()[0]
    other.execute(sql.SQL("SET search_path TO {},public").format(sql.Identifier(schema)))
    with owned_lock(other, "workflow", "synthetic-crash"):
        other.close()  # Model an OS-terminated worker, without a cooperative finally.
    with owned_lock(connection, "workflow", "synthetic-crash"):
        previous = begin_workflow(connection, "synthetic-crash", "pipeline_historical")
        replacement = begin_workflow(connection, "synthetic-crash", "pipeline_historical")
    assert previous != replacement
    assert connection.execute(
        "SELECT status,error_summary,attempt_number FROM pipeline_runs WHERE run_id=%s", (previous,)
    ).fetchone() == ("interrupted", "AbandonedSession", 1)
    assert connection.execute(
        "SELECT attempt_number FROM pipeline_runs WHERE run_id=%s", (replacement,)
    ).fetchone() == (2,)


def test_raw_interrupt_restart_has_no_analytics_before_commit_and_uses_real_extraction(
    connection, isolated_pipeline, source
):
    settings, _ = isolated_pipeline
    store, parent, _ = source
    spec, _ = day_request(settings, "los_angeles", "2024-01-01")
    RawStore(settings.raw_dir).write([store._path(parent.local_path).read_bytes()], spec)
    with pytest.raises(ControlledStop):
        run_day("los_angeles", "2024-01-01", stop_after_raw=True)
    assert connection.execute("SELECT count(*) FROM ingestion_batches").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM vessel_positions").fetchone() == (0,)
    assert connection.execute("SELECT step FROM pipeline_checkpoints").fetchall() == [("raw",)]

    # Keep this deterministic source-only test independent of the weather provider.
    def steps(port, day, step, key, parent, **kwargs):
        if step == "weather":
            return dict(input_count=0, output_count=0)
        return execute_step(port, day, step, key, parent, **kwargs)

    result = run_day("los_angeles", "2024-01-01", step_runner=steps)
    assert result["status"] == "succeeded"
    assert result["steps"]["clean"]["input_count"] == 2
    assert result["steps"]["validate"]["output_count"] == 24
    again = run_day("los_angeles", "2024-01-01", step_runner=steps)
    assert again["steps"]["clean"]["checkpoint_reused"]
    assert again["steps"]["publish"]["checkpoint_reused"]
    assert connection.execute(
        "SELECT status FROM pipeline_runs WHERE stage='pipeline_historical' ORDER BY id"
    ).fetchall() == [("interrupted",), ("succeeded",), ("succeeded",)]


def test_missing_day_is_gap_and_other_partition_isolated(connection, isolated_pipeline):
    def steps(port, day, step, key, parent, **kwargs):
        if day == "2024-01-01":
            raise PipelineError("http_404")
        return dict(input_count=0, output_count=0)

    report = run_historical(demo=False, start="2024-01-01", end="2024-01-03", step_runner=steps)
    assert report["status"] == "failed" and report["failures"][0]["day"] == "2024-01-01"
    assert report["partitions"][0]["day"] == "2024-01-02"
    assert report["partitions"][0]["status"] == "succeeded"
    assert connection.execute("SELECT reason,resolved_at FROM pipeline_source_gaps").fetchall() == [
        ("http_404", None)
    ]


def test_overlapping_request_returns_busy_without_running_any_stage(connection, isolated_pipeline):
    def forbidden(*args, **kwargs):
        pytest.fail("competing request executed a stage")

    key = "pipeline-v1:los_angeles:los_angeles_2024_01_01_noaa_ocm_geoparquet_v1"
    with owned_lock(connection, "workflow", key):
        report = run_day("los_angeles", "2024-01-01", step_runner=forbidden)
    assert report["status"] == "busy"
    assert connection.execute("SELECT count(*) FROM pipeline_checkpoints").fetchone() == (0,)


def test_midnight_context_keeps_canonical_dataset_lineage_and_no_new_arrival(connection, tmp_path):
    store = RawStore(tmp_path / "raw")

    def parent(day, dataset):
        return store.write(
            [b"synthetic midnight " + dataset.encode()],
            BatchSpec(
                source_id="synthetic_noaa",
                dataset_id=dataset,
                source_url="https://example.invalid/" + dataset,
                mode="historical",
                source_date=day.date(),
                file_suffix=".parquet",
                source_start_utc=day,
                source_end_utc=day + timedelta(days=1),
            ),
        )

    first = parent(BASE, "synthetic_day1")
    prepare_track(
        connection,
        tmp_path,
        first,
        [(1426, "outside"), (1430, "harbor"), (1432, "harbor"), (1439, "harbor")],
    )
    second = parent(BASE + timedelta(days=1), "synthetic_day2")
    clean = prepare(connection, tmp_path, second)
    load_batch(
        connection,
        clean,
        tmp_path / "quarantine",
        records=records(second, [(1440, "harbor"), (1445, "outside"), (1447, "outside")]),
    )
    scope = replace(
        prepare_track(
            connection,
            tmp_path,
            first,
            [(1426, "outside"), (1430, "harbor"), (1432, "harbor"), (1439, "harbor")],
        ),
        dataset_id=second.dataset_id,
        start_utc=BASE + timedelta(days=1),
        end_utc=BASE + timedelta(days=2),
        context_dataset_ids=(first.dataset_id, second.dataset_id),
    )
    before = connection.execute(
        "SELECT observation_id,dataset_id FROM vessel_positions ORDER BY observation_id"
    ).fetchall()
    result = recompute_marts(connection, scope, CONFIG)
    assert result.arrivals == 0 and result.departures == 1 and result.completed_dwell_samples == 1
    row = connection.execute("SELECT arrival_confirmed_at,completed FROM vessel_visits").fetchone()
    assert row == (BASE + timedelta(minutes=1432), True)
    assert (
        connection.execute(
            "SELECT observation_id,dataset_id FROM vessel_positions ORDER BY observation_id"
        ).fetchall()
        == before
    )
    assert available_context(connection, second) == (first.dataset_id, second.dataset_id)
    assert CONFIG.analytical_version(scope) != CONFIG.analytical_version(
        replace(scope, context_dataset_ids=())
    )
