"""Transactional late-data and source-isolation tests; only synthetic, owned schemas."""

from datetime import UTC, datetime, timedelta

import pytest

from portwatch.cleaning import load_batch
from portwatch.storage import BatchSpec, RawStore
from portwatch.visit_storage import preview_visits, recompute_visits
from portwatch.visits import InferenceError, VisitRules
from portwatch.zones import AssignmentScope, assign_zones, load_geofence, validate_geofence
from tests.test_bookkeeping import connection as pg_connection
from tests.test_cleaning import prepare
from tests.test_positions import noaa_row
from tests.test_positions import parent as synthetic_parent
from tests.test_zones import BOUNDS, synthetic_geofence

connection = pg_connection
parent = synthetic_parent
pytestmark = pytest.mark.integration
BASE = datetime(2024, 1, 1, tzinfo=UTC)
RULES = VisitRules()
VERSION = "synthetic-v1"


def records(parent, timeline, base=BASE, mmsi=123456789):
    coords = {
        "outside": (-118.235, 33.78),
        "harbor": (-118.275, 33.745),
        "waiting": (-118.325, 33.705),
        "terminal": (-118.3, 33.72),
    }
    return [
        (
            i,
            noaa_row(
                parent,
                i,
                base + timedelta(minutes=t),
                longitude=coords[k][0],
                latitude=coords[k][1],
                sog=0.0 if k == "waiting" else 2,
                mmsi=mmsi,
            ),
        )
        for i, (t, k) in enumerate(timeline)
    ]


def prepare_track(connection, tmp_path, parent, timeline=None, base=BASE):
    timeline = timeline or [
        (0, "outside"),
        (10, "harbor"),
        (12, "harbor"),
        (20, "outside"),
        (22, "outside"),
    ]
    source = prepare(connection, tmp_path, parent)
    load_batch(connection, source, tmp_path / "quarantine", records=records(parent, timeline, base))
    load_geofence(connection, validate_geofence(synthetic_geofence(), "los_angeles", BOUNDS))
    scope = AssignmentScope(parent.source_id, parent.dataset_id, BASE, BASE + timedelta(days=2))
    assign_zones(connection, "los_angeles", VERSION, scope)
    return scope


def visit_rows(connection):
    return connection.execute(
        "SELECT visit_id,partition_id,observed_start,observed_end,completed,completeness_reasons "
        "FROM vessel_visits ORDER BY visit_id"
    ).fetchall()


def test_replay_and_late_report_remove_obsolete_episode_ids(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent)
    first = recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    assert (first.positions, first.visits, first.completed_visits) == (5, 1, 1)
    old = visit_rows(connection)
    assert recompute_visits(connection, scope, "los_angeles", VERSION, RULES) == first
    assert visit_rows(connection) == old
    late = RawStore(tmp_path / "raw").write(
        [b"synthetic late report"],
        BatchSpec(
            source_id=parent.source_id,
            dataset_id=parent.dataset_id,
            source_url=parent.source_url,
            mode="historical",
            source_date=parent.source_date,
            file_suffix=".parquet",
        ),
    )
    source = prepare(connection, tmp_path, late)
    load_batch(connection, source, tmp_path / "quarantine", records=records(late, [(8, "harbor")]))
    assign_zones(connection, "los_angeles", VERSION, scope)
    changed = recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    assert changed.positions == 6 and changed.visits == 1
    new = visit_rows(connection)
    assert new[0][0] != old[0][0] and new[0][2] < old[0][2]
    assert connection.execute(
        "SELECT count(*) FROM vessel_visits WHERE visit_id=%s", (old[0][0],)
    ).fetchone() == (0,)
    assert connection.execute("SELECT count(*) FROM vessel_activity_states").fetchone() == (6,)


def test_replacement_failure_rolls_back_to_previous_snapshot(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent)
    recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    before = visit_rows(connection)
    state_count = connection.execute("SELECT count(*) FROM vessel_activity_states").fetchone()

    def fail():
        raise RuntimeError("synthetic failure after replacement before commit")

    with pytest.raises(RuntimeError):
        recompute_visits(connection, scope, "los_angeles", VERSION, RULES, before_commit=fail)
    assert visit_rows(connection) == before
    assert (
        connection.execute("SELECT count(*) FROM vessel_activity_states").fetchone() == state_count
    )
    assert connection.execute(
        "SELECT status FROM pipeline_runs WHERE stage='visits' ORDER BY started_at DESC LIMIT 1"
    ).fetchone() == ("failed",)


def test_waiting_association_prefix_and_read_only_behavior(connection, tmp_path, parent):
    timeline = [
        (0, "outside"),
        (5, "waiting"),
        (15, "waiting"),
        (25, "waiting"),
        (35, "waiting"),
        (40, "harbor"),
        (42, "harbor"),
        (45, "outside"),
        (47, "outside"),
    ]
    scope = prepare_track(connection, tmp_path, parent, timeline)
    full = recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    assert (full.waiting_episodes, full.port_associated_candidates) == (1, 1)
    rows = visit_rows(connection)
    runs = connection.execute("SELECT count(*) FROM pipeline_runs").fetchone()
    prefix = preview_visits(
        connection, scope, "los_angeles", VERSION, RULES, BASE + timedelta(minutes=41)
    )
    assert (
        prefix.visits == 0
        and prefix.regional_candidates == 1
        and prefix.port_associated_candidates == 0
    )
    assert visit_rows(connection) == rows
    assert connection.execute("SELECT count(*) FROM pipeline_runs").fetchone() == runs
    association, known = connection.execute(
        "SELECT association_scope,association_known_at FROM waiting_episodes"
    ).fetchone()
    assert association == "port" and known == BASE + timedelta(minutes=42)


def test_rules_versions_are_separate_and_missing_zones_refuse_replacement(
    connection, tmp_path, parent
):
    scope = prepare_track(connection, tmp_path, parent)
    recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    old = visit_rows(connection)
    changed_rules = VisitRules(persistence_seconds=240)
    changed = recompute_visits(connection, scope, "los_angeles", VERSION, changed_rules)
    assert changed.visits == 0 and visit_rows(connection) == old
    assert connection.execute("SELECT count(*) FROM visit_rule_sets").fetchone() == (2,)
    connection.execute("DELETE FROM position_zone_memberships")
    connection.execute("DELETE FROM position_zone_assignments")
    with pytest.raises(InferenceError, match="missing"):
        recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    assert visit_rows(connection) == old


def test_same_mmsi_other_dataset_is_not_blended_or_deleted(connection, tmp_path, parent):
    scope = prepare_track(connection, tmp_path, parent)
    recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    old = visit_rows(connection)
    other = RawStore(tmp_path / "raw").write(
        [b"synthetic other dataset"],
        BatchSpec(
            source_id="synthetic_other_source",
            dataset_id="synthetic_other_dataset",
            source_url="https://example.invalid/other.parquet",
            mode="historical",
            source_date=parent.source_date,
            file_suffix=".parquet",
        ),
    )
    other_scope = prepare_track(connection, tmp_path, other)
    recompute_visits(connection, other_scope, "los_angeles", VERSION, RULES)
    assert len(visit_rows(connection)) == 2
    assert old[0] in visit_rows(connection)
    repeated = recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    assert repeated.positions == 5 and repeated.tracks == 1 and old[0] in visit_rows(connection)


def test_complete_vessel_history_includes_midnight_context(connection, tmp_path, parent):
    timeline = [
        (0, "outside"),
        (5, "harbor"),
        (7, "harbor"),
        (20, "terminal"),
        (25, "outside"),
        (27, "outside"),
    ]
    scope = prepare_track(
        connection, tmp_path, parent, timeline, datetime(2024, 1, 1, 23, 50, tzinfo=UTC)
    )
    counts = recompute_visits(connection, scope, "los_angeles", VERSION, RULES)
    assert counts.completed_visits == 1
    row = visit_rows(connection)[0]
    assert row[2].date() != row[3].date() and row[4]
