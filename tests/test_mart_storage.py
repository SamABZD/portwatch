"""SQL metric behavior against actual PostgreSQL, only synthetic data in owned schemas."""

from dataclasses import replace
from datetime import timedelta

import pytest

from portwatch.cleaning import load_batch
from portwatch.context_storage import ingest_context
from portwatch.marts import (
    MartConfig,
    MartError,
    read_completed_hours,
    read_prefix_hours,
    recompute_marts,
)
from portwatch.settings import Settings
from portwatch.storage import BatchSpec, RawStore
from portwatch.visits import VisitRules
from portwatch.zones import AssignmentScope
from tests.test_bookkeeping import connection as pg_connection
from tests.test_cleaning import prepare
from tests.test_context import NOW, news_payload, request, weather_payload
from tests.test_context_storage import fetcher
from tests.test_positions import noaa_row
from tests.test_visit_storage import BASE, VERSION, prepare_track, records

connection = pg_connection
pytestmark = pytest.mark.integration
CONFIG = MartConfig("los_angeles", VERSION, VisitRules())


@pytest.fixture
def parent(tmp_path):
    return RawStore(tmp_path / "raw").write(
        [b"synthetic mart source"],
        BatchSpec(
            source_id="synthetic_noaa",
            dataset_id="synthetic_la",
            source_url="https://example.invalid/marts",
            mode="historical",
            source_date=BASE.date(),
            file_suffix=".parquet",
            source_start_utc=BASE - timedelta(hours=1),
            source_end_utc=BASE + timedelta(days=2),
        ),
    )


def hour_rows(connection, config=CONFIG, scope=None):
    clause = "" if scope is None else " WHERE partition_id=%s"
    params = () if scope is None else (config.partition_id(scope),)
    return connection.execute(
        "SELECT to_jsonb(h)-'partition_id' AS row FROM port_activity_hourly h"
        + clause
        + " ORDER BY hour_utc",
        params,
    ).fetchall()


def add_late(connection, tmp_path, parent, timeline, mmsi=123456789):
    spec = BatchSpec(
        **parent.model_dump(
            exclude={
                "schema_version",
                "state",
                "batch_id",
                "content_sha256",
                "byte_count",
                "local_path",
            }
        )
    )
    batch = RawStore(tmp_path / "raw").write([b"synthetic late " + str(timeline).encode()], spec)
    source = prepare(connection, tmp_path, batch)
    load_batch(
        connection, source, tmp_path / "quarantine", records=records(batch, timeline, mmsi=mmsi)
    )


def test_hour_grain_distinct_activity_and_snapshot_are_different(connection, tmp_path, parent):
    scope = prepare_track(
        connection, tmp_path, parent, [(0, "harbor"), (2, "harbor"), (60, "harbor"), (62, "harbor")]
    )
    scope = replace(scope, end_utc=BASE + timedelta(hours=2))
    add_late(connection, tmp_path, parent, [(70, "outside"), (72, "outside")], mmsi=987654321)
    result = recompute_marts(connection, scope, CONFIG)
    rows = [row[0] for row in hour_rows(connection)]
    assert result.hours == 2 and result.observations == 6
    assert rows[1]["distinct_vessels_observed"] == 2 and rows[1]["snapshot_vessel_count"] == 1
    assert sum(rows[1]["snapshot_zone_counts"].values()) == 1
    assert rows[1]["snapshot_zone_counts"]["harbor"] == 1
    assert connection.execute("SELECT count(*) FROM port_activity_hourly").fetchone() == (2,)


def test_completed_dwell_and_partial_visits_remain_separate(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent)
    scope = replace(scope, end_utc=BASE + timedelta(hours=1))
    add_late(connection, tmp_path, parent, [(5, "harbor"), (7, "harbor")], mmsi=987654321)
    recompute_marts(connection, scope, CONFIG)
    row = hour_rows(connection)[0][0]
    assert row["confirmed_arrival_count"] == 1 and row["confirmed_departure_count"] == 1
    assert row["completed_dwell_sample_count"] == 1
    assert row["completed_dwell_median_seconds"] == 660
    assert row["completed_dwell_lower_median_seconds"] == 120
    assert row["completed_dwell_upper_median_seconds"] == 1200
    assert row["incomplete_visit_count"] == 1


def test_snapshot_age_limit_and_valid_zero_differ_from_unknown(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent, [(30, "harbor"), (32, "harbor")])
    scope = replace(scope, end_utc=BASE + timedelta(hours=2))
    add_late(connection, tmp_path, parent, [(45, "harbor"), (47, "harbor")], mmsi=987654321)
    recompute_marts(connection, scope, CONFIG)
    row = hour_rows(connection, CONFIG, scope)[1][0]
    assert row["snapshot_vessel_count"] == 1 and row["snapshot_stale_vessel_count"] == 1
    assert row["observation_count"] == 0 and row["distinct_vessels_observed"] == 0
    stricter = replace(CONFIG, snapshot_staleness_seconds=600)
    recompute_marts(connection, scope, stricter)
    row = hour_rows(connection, stricter, scope)[1][0]
    assert row["snapshot_vessel_count"] == 0 and row["snapshot_stale_vessel_count"] == 2
    assert all(count == 0 for count in row["snapshot_zone_counts"].values())


def test_weather_revision_and_articles_do_not_multiply_metrics(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent)
    scope = replace(scope, end_utc=BASE + timedelta(days=1))
    settings = Settings(raw_dir=tmp_path / "context")
    req = request()
    weather = ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)
    revised = ingest_context(
        settings,
        connection,
        req,
        fetch=fetcher(weather_payload(speed=50)),
        refresh=True,
        now=NOW + timedelta(minutes=2),
    )
    ingest_context(
        settings,
        connection,
        request("news", False),
        fetch=fetcher(news_payload(duplicates=True)),
        now=NOW,
    )
    pinned = replace(CONFIG, weather_batch_id=weather.batch_id)
    first = recompute_marts(connection, scope, pinned)
    row = hour_rows(connection, pinned, scope)[0][0]
    assert first.observations == 5 and row["observation_count"] == 5
    assert row["weather_wind_speed_mps"] == 3.2 and row["weather_status"] == "available"
    other = replace(CONFIG, weather_batch_id=revised.batch_id)
    second = recompute_marts(connection, scope, other)
    assert first.analytical_version != second.analytical_version
    assert hour_rows(connection, other, scope)[0][0]["weather_wind_speed_mps"] == 50
    assert hour_rows(connection, pinned, scope)[0][0]["weather_wind_speed_mps"] == 3.2
    assert second.observations == first.observations


def test_unknown_source_interval_is_not_zero_and_missing_weather_is_null(connection, tmp_path):
    parent = RawStore(tmp_path / "raw").write(
        [b"synthetic bounded hour"],
        BatchSpec(
            source_id="synthetic_noaa",
            dataset_id="synthetic_la",
            source_url="https://example.invalid/hour",
            mode="historical",
            source_date=BASE.date(),
            file_suffix=".parquet",
            source_start_utc=BASE,
            source_end_utc=BASE + timedelta(hours=1),
        ),
    )
    scope = prepare_track(connection, tmp_path, parent, [(5, "harbor"), (7, "harbor")])
    scope = replace(scope, end_utc=BASE + timedelta(hours=3))
    recompute_marts(connection, scope, CONFIG)
    rows = [row[0] for row in hour_rows(connection)]
    assert rows[0]["snapshot_vessel_count"] is None
    assert rows[1]["observation_count"] is None and rows[1]["distinct_vessels_observed"] is None
    assert rows[1]["snapshot_vessel_count"] is None
    assert rows[0]["weather_wind_speed_mps"] is None and rows[0]["weather_status"] == "unavailable"
    assert "source_interval_unknown" in rows[1]["quality_flags"]


def test_same_timestamp_zone_conflicts_count_one_unknown_vessel(connection, tmp_path, parent):
    scope = prepare_track(
        connection, tmp_path, parent, [(60, "harbor"), (60, "outside"), (62, "outside")]
    )
    scope = replace(scope, end_utc=BASE + timedelta(hours=2))
    recompute_marts(connection, scope, CONFIG)
    row = hour_rows(connection)[1][0]
    assert row["snapshot_vessel_count"] == 1 and row["snapshot_zone_counts"]["unknown"] == 1
    assert row["snapshot_conflicting_vessel_count"] == 1
    assert row["snapshot_port_vessel_count"] == 0


def test_candidate_duration_and_knowledge_times_are_explicit(connection, tmp_path, parent):
    timeline = [
        (0, "outside"),
        (1, "waiting"),
        (11, "waiting"),
        (21, "waiting"),
        (31, "waiting"),
        (40, "harbor"),
        (42, "harbor"),
        (50, "outside"),
        (52, "outside"),
    ]
    scope = prepare_track(connection, tmp_path, parent, timeline)
    scope = replace(scope, end_utc=BASE + timedelta(hours=1))
    recompute_marts(connection, scope, CONFIG)
    row = hour_rows(connection)[0][0]
    assert row["waiting_candidate_count"] == 1 and row["port_associated_waiting_count"] == 1
    assert (
        row["waiting_duration_sample_count"] == 1 and row["waiting_duration_median_seconds"] == 1800
    )
    before = read_prefix_hours(connection, scope, CONFIG, BASE + timedelta(minutes=30))[-1]
    qualified = read_prefix_hours(connection, scope, CONFIG, BASE + timedelta(minutes=35))[-1]
    assert before["waiting_candidate_count"] == 0
    assert qualified["waiting_candidate_count"] == 1 and qualified["regional_waiting_count"] == 1
    assert qualified["port_associated_waiting_count"] == 0


def test_read_prefix_does_not_expose_future_reports_or_write_persistent_rows(
    connection, tmp_path, parent
):
    scope = prepare_track(
        connection,
        tmp_path,
        parent,
        [(0, "outside"), (10, "harbor"), (12, "harbor"), (70, "outside"), (72, "outside")],
    )
    scope = replace(scope, end_utc=BASE + timedelta(hours=2))
    recompute_marts(connection, scope, CONFIG)
    previous = hour_rows(connection)
    audit = connection.execute("SELECT count(*) FROM pipeline_runs").fetchone()
    prefix = read_prefix_hours(connection, scope, CONFIG, BASE + timedelta(minutes=15))
    assert len(prefix) == 1 and not prefix[0]["complete_hour"]
    assert prefix[0]["source_id"] == scope.source_id
    assert prefix[0]["analytical_version"] == CONFIG.analytical_version(scope)
    assert prefix[0]["observation_count"] == 3
    assert (
        prefix[0]["confirmed_departure_count"] == 0
        and prefix[0]["completed_dwell_sample_count"] == 0
    )
    assert read_completed_hours(connection, scope, CONFIG, BASE + timedelta(hours=1)) == []
    assert read_prefix_hours(
        connection, scope, CONFIG, BASE + timedelta(hours=1, minutes=15), completed_only=True
    )[0]["complete_hour"]
    assert len(read_completed_hours(connection, scope, CONFIG, BASE + timedelta(hours=2))) == 2
    shorter = replace(scope, end_utc=BASE + timedelta(hours=1))
    assert len(read_completed_hours(connection, shorter, CONFIG, scope.end_utc)) == 1
    assert hour_rows(connection) == previous
    assert connection.execute("SELECT count(*) FROM pipeline_runs").fetchone() == audit


def test_rerun_late_data_and_shorter_period_replace_obsolete_results(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent)
    scope = replace(scope, end_utc=BASE + timedelta(hours=2))
    first = recompute_marts(connection, scope, CONFIG)
    before = hour_rows(connection)
    old_visit = connection.execute("SELECT visit_id FROM vessel_visits").fetchone()[0]
    assert recompute_marts(connection, scope, CONFIG) == first and hour_rows(connection) == before
    add_late(connection, tmp_path, parent, [(8, "harbor")])
    changed = recompute_marts(connection, scope, CONFIG)
    assert changed.observations == 6
    assert connection.execute(
        "SELECT count(*) FROM vessel_visits WHERE visit_id=%s", (old_visit,)
    ).fetchone() == (0,)
    assert hour_rows(connection)[0][0]["observation_count"] == 6
    smaller = replace(scope, end_utc=BASE + timedelta(hours=1))
    shortened = recompute_marts(connection, smaller, CONFIG)
    assert shortened.partition_id == first.partition_id
    assert connection.execute("SELECT count(*) FROM port_activity_hourly").fetchone() == (1,)


def test_atomic_failure_preserves_previous_visits_and_hour_partition(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent)
    scope = replace(scope, end_utc=BASE + timedelta(hours=1))
    recompute_marts(connection, scope, CONFIG)
    rows = hour_rows(connection)
    visits = connection.execute("SELECT visit_id,observed_start FROM vessel_visits").fetchall()
    add_late(connection, tmp_path, parent, [(8, "harbor")])

    def fail():
        raise RuntimeError("synthetic failure before mart commit")

    with pytest.raises(RuntimeError):
        recompute_marts(connection, scope, CONFIG, before_commit=fail)
    assert hour_rows(connection) == rows
    assert (
        connection.execute("SELECT visit_id,observed_start FROM vessel_visits").fetchall() == visits
    )
    assert connection.execute(
        "SELECT status FROM pipeline_runs WHERE stage='marts' ORDER BY id DESC LIMIT 1"
    ).fetchone() == ("failed",)


def test_unattributed_rejects_not_given_invented_hour_and_source_series_isolation(
    connection, tmp_path, parent
):
    source = prepare(connection, tmp_path, parent)
    load_batch(
        connection,
        source,
        tmp_path / "quarantine",
        records=[
            (0, noaa_row(parent, 0, BASE, latitude=91)),
            (1, noaa_row(parent, 1, BASE + timedelta(minutes=5))),
            (2, noaa_row(parent, 2, BASE + timedelta(minutes=7))),
        ],
    )
    # Load the synthetic geofence/classification without duplicating that processed input.
    from portwatch.zones import load_geofence, validate_geofence
    from tests.test_zones import BOUNDS, synthetic_geofence

    load_geofence(connection, validate_geofence(synthetic_geofence(), "los_angeles", BOUNDS))
    scope = AssignmentScope(parent.source_id, parent.dataset_id, BASE, BASE + timedelta(hours=1))
    recompute_marts(connection, scope, CONFIG)
    row = hour_rows(connection)[0][0]
    assert row["rejected_record_count"] is None
    assert connection.execute(
        "SELECT unattributed_rejected_record_count FROM mart_partitions"
    ).fetchone() == (1,)
    other_spec = BatchSpec(
        source_id="synthetic_live_other",
        dataset_id=parent.dataset_id,
        source_url=parent.source_url,
        mode="historical",
        source_date=BASE.date(),
        file_suffix=".parquet",
    )
    other = RawStore(tmp_path / "raw").write([b"synthetic distinct source"], other_spec)
    add_late(connection, tmp_path, other, [(9, "harbor")], mmsi=987654321)
    recompute_marts(connection, scope, CONFIG)
    assert hour_rows(connection)[0][0]["observation_count"] == 2


def test_forecast_revision_is_refused_for_historical_mart(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent)
    current = request("weather", False)
    result = ingest_context(
        Settings(raw_dir=tmp_path / "context"),
        connection,
        current,
        fetch=fetcher(weather_payload(current)),
        now=NOW,
    )
    with pytest.raises(MartError, match="reanalysis"):
        recompute_marts(connection, scope, replace(CONFIG, weather_batch_id=result.batch_id))
    assert connection.execute("SELECT count(*) FROM mart_partitions").fetchone() == (0,)
