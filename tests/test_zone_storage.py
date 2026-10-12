"""Real PostGIS tests in owned schemas; all positions and polygons are synthetic."""

from datetime import UTC, datetime

import pytest
from psycopg.errors import RaiseException

from portwatch.cleaning import load_batch
from portwatch.zones import AssignmentScope, assign_zones, load_geofence, validate_geofence
from tests.test_bookkeeping import connection as pg_connection
from tests.test_cleaning import prepare
from tests.test_positions import noaa_row
from tests.test_positions import parent as synthetic_parent
from tests.test_zones import BOUNDS, rectangle, synthetic_geofence

connection = pg_connection
parent = synthetic_parent
pytestmark = pytest.mark.integration


def setup_positions(connection, tmp_path, parent):
    source = prepare(connection, tmp_path, parent)
    points = [
        (-118.30, 33.72),
        (-118.235, 33.78),
        (-118.31, 33.73),
        (-118.275, 33.745),
        (-118.305, 33.712),
    ]
    records = [
        (i, noaa_row(parent, i, longitude=lon, latitude=lat)) for i, (lon, lat) in enumerate(points)
    ]
    load_batch(connection, source, tmp_path / "quarantine", records=records)
    return AssignmentScope(
        parent.source_id,
        parent.dataset_id,
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )


def memberships(connection, version="synthetic-v1"):
    return connection.execute(
        """SELECT p.record_offset,a.state,a.primary_zone_id,a.membership_count,
        ARRAY(SELECT m.zone_id FROM position_zone_memberships m
            WHERE m.position_id=p.id AND m.geofence_version=a.geofence_version
            AND m.port_id=a.port_id AND m.assignment_version=a.assignment_version
            ORDER BY m.zone_id)
        FROM vessel_positions p JOIN position_zone_assignments a ON p.id=a.position_id
        WHERE a.geofence_version=%s ORDER BY p.record_offset""",
        (version,),
    ).fetchall()


def test_inside_outside_exact_boundary_overlaps_replay_and_scope(connection, tmp_path, parent):
    scope = setup_positions(connection, tmp_path, parent)
    geofence = validate_geofence(synthetic_geofence(), "los_angeles", BOUNDS)
    assert load_geofence(connection, geofence)
    assert not load_geofence(connection, geofence)
    before = connection.execute(
        "SELECT row_to_json(p) FROM vessel_positions p ORDER BY id"
    ).fetchall()
    result = assign_zones(connection, "los_angeles", geofence.document.version, scope)
    assert (
        result.input_positions,
        result.newly_assigned,
        result.inside,
        result.outside,
        result.memberships,
        result.overlaps,
    ) == (5, 5, 4, 1, 8, 3)
    rows = memberships(connection)
    assert rows == [
        (0, "inside", "terminal", 2, ["harbor", "terminal"]),
        (1, "outside", None, 0, []),
        (2, "inside", "terminal", 2, ["harbor", "terminal"]),
        (3, "inside", "harbor", 1, ["harbor"]),
        (4, "inside", "terminal", 3, ["candidate", "harbor", "terminal"]),
    ]
    repeated = assign_zones(connection, "los_angeles", geofence.document.version, scope)
    assert repeated.newly_assigned == 0 and memberships(connection) == rows
    assert (
        before
        == connection.execute(
            "SELECT row_to_json(p) FROM vessel_positions p ORDER BY id"
        ).fetchall()
    )
    wrong = AssignmentScope("synthetic_other", scope.dataset_id, scope.start_utc, scope.end_utc)
    with pytest.raises(ValueError, match="no cleaned positions"):
        assign_zones(connection, "los_angeles", geofence.document.version, wrong)
    wrong = AssignmentScope(scope.source_id, "synthetic_other", scope.start_utc, scope.end_utc)
    with pytest.raises(ValueError, match="no cleaned positions"):
        assign_zones(connection, "los_angeles", geofence.document.version, wrong)


def test_two_versions_changed_content_and_database_immutability(connection, tmp_path, parent):
    scope = setup_positions(connection, tmp_path, parent)
    data = synthetic_geofence()
    original = validate_geofence(data, "los_angeles", BOUNDS)
    load_geofence(connection, original)
    assign_zones(connection, "los_angeles", original.document.version, scope)
    old_rows = memberships(connection)
    data["features"][1]["geometry"]["coordinates"] = [rectangle(-118.25, 33.77, -118.23, 33.79)]
    with pytest.raises(ValueError, match="changed content"):
        load_geofence(connection, validate_geofence(data, "los_angeles", BOUNDS))
    data["version"] = "synthetic-v2"
    assert load_geofence(connection, validate_geofence(data, "los_angeles", BOUNDS))
    result = assign_zones(connection, "los_angeles", "synthetic-v2", scope)
    assert result.input_positions == 5 and result.inside == 5
    assert memberships(connection) == old_rows
    assert memberships(connection, "synthetic-v2") != old_rows
    with pytest.raises(RaiseException, match="immutable"):
        connection.execute("UPDATE port_zones SET name='synthetic edit'")
    with pytest.raises(RaiseException, match="immutable"):
        connection.execute("DELETE FROM geofence_versions")


def test_assignment_transaction_failure_and_retry(connection, tmp_path, parent):
    scope = setup_positions(connection, tmp_path, parent)
    load_geofence(connection, validate_geofence(synthetic_geofence(), "los_angeles", BOUNDS))

    def fail():
        raise RuntimeError("synthetic failure before commit")

    with pytest.raises(RuntimeError):
        assign_zones(connection, "los_angeles", "synthetic-v1", scope, before_commit=fail)
    assert connection.execute("SELECT count(*) FROM position_zone_assignments").fetchone() == (0,)
    assert connection.execute("SELECT count(*) FROM position_zone_memberships").fetchone() == (0,)
    assert connection.execute(
        "SELECT status FROM pipeline_runs WHERE stage='zones'"
    ).fetchone() == ("failed",)
    assert assign_zones(connection, "los_angeles", "synthetic-v1", scope).newly_assigned == 5


def test_equal_priority_tie_keeps_both_memberships(connection, tmp_path, parent):
    scope = setup_positions(connection, tmp_path, parent)
    data = synthetic_geofence()
    duplicate = {
        **data["features"][1],
        "properties": {**data["features"][1]["properties"], "zone_id": "aaa_terminal"},
    }
    data["features"].append(duplicate)
    load_geofence(connection, validate_geofence(data, "los_angeles", BOUNDS))
    assign_zones(connection, "los_angeles", "synthetic-v1", scope)
    row = memberships(connection)[0]
    assert row[2:] == ("aaa_terminal", 3, ["aaa_terminal", "harbor", "terminal"])
