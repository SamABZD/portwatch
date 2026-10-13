"""Real PostgreSQL reads in fixture-owned schemas; synthetic points never enter project data."""

from dataclasses import replace
from datetime import timedelta

import pytest

from portwatch.demo.replay import ReplayError, load_dataset
from portwatch.marts import MartConfig, read_prefix_hours
from portwatch.settings import Settings, load_port_config
from portwatch.visits import VisitRules
from portwatch.zones import validate_geofence
from tests.test_bookkeeping import connection as pg_connection
from tests.test_positions import parent as synthetic_parent
from tests.test_visit_storage import BASE, VERSION, prepare_track
from tests.test_zones import BOUNDS, synthetic_geofence

connection = pg_connection
parent = synthetic_parent
pytestmark = pytest.mark.integration


def metadata(scope):
    return dict(
        port_id="los_angeles",
        mode="historical",
        port_name="Synthetic replay fixture",
        source_id=scope.source_id,
        dataset_id=scope.dataset_id,
        start_utc=BASE,
        end_utc=BASE + timedelta(days=1),
        revision="synthetic-storage-revision",
        weather_batch_id=None,
        news_batch_id=None,
        clean_version=scope.clean_version,
        geofence_version=VERSION,
        analytical_version="synthetic",
        bounds=list(BOUNDS),
    )


def test_port_snapshot_is_read_only_source_scoped_and_matches_completed_hour_activity(
    connection, tmp_path, parent
):
    scope = prepare_track(connection, tmp_path, parent)
    fence = validate_geofence(synthetic_geofence(), "los_angeles", BOUNDS)
    settings = Settings()
    port = load_port_config(settings.project_path(settings.port_config))
    before = connection.execute("SELECT count(*) FROM pipeline_runs").fetchone()
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        saved = load_dataset(connection, metadata(scope), fence, port)
    early = saved.frame(BASE + timedelta(minutes=12))
    assert early["prefix_observations"] == 3
    assert early["metrics"]["completed_dwell_sample_count"] == 0
    assert early["vessels"][0]["visit_status"] == "partial"
    full = saved.frame(BASE + timedelta(hours=1))
    rows = read_prefix_hours(
        connection,
        replace(scope, end_utc=BASE + timedelta(days=1)),
        MartConfig("los_angeles", VERSION, VisitRules()),
        BASE + timedelta(hours=1),
        completed_only=True,
    )
    assert full["chart"][0]["observed_vessels"] == rows[0]["distinct_vessels_observed"]
    assert full["chart"][0]["observations"] == rows[0]["observation_count"]
    assert (
        full["metrics"]["completed_dwell_sample_count"] == rows[0]["completed_dwell_sample_count"]
    )
    assert (
        full["metrics"]["completed_dwell_median_seconds"]
        == rows[0]["completed_dwell_median_seconds"]
    )
    assert connection.execute("SELECT count(*) FROM pipeline_runs").fetchone() == before
    assert connection.execute("SELECT count(*) FROM vessel_positions").fetchone() == (5,)


def test_missing_spatial_preparation_is_actionable_without_render_side_effects(
    connection, tmp_path, parent
):
    scope = prepare_track(connection, tmp_path, parent)
    position = connection.execute("SELECT min(id) FROM vessel_positions").fetchone()[0]
    # Delete only one assignment in this newly created fixture schema.
    connection.execute("DELETE FROM position_zone_memberships WHERE position_id=%s", (position,))
    connection.execute("DELETE FROM position_zone_assignments WHERE position_id=%s", (position,))
    settings = Settings()
    port = load_port_config(settings.project_path(settings.port_config))
    fence = validate_geofence(synthetic_geofence(), "los_angeles", BOUNDS)
    before = connection.execute("SELECT count(*) FROM pipeline_runs").fetchone()
    with pytest.raises(ReplayError, match="spatial_assignment_missing"):
        load_dataset(connection, metadata(scope), fence, port)
    assert connection.execute("SELECT count(*) FROM pipeline_runs").fetchone() == before
