"""Meaningful event sequences; every record in this module is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from portwatch.cli import app
from portwatch.visits import InferenceError, TrackPoint, VisitRules, infer_track

BASE = datetime(2024, 1, 1, tzinfo=UTC)
RULES = VisitRules()


def point(minute, kind="outside", speed=2, *, base=BASE, identity=None):
    return TrackPoint(
        int(minute * 1000 + 100000),
        identity or f"synthetic-{minute}-{kind}",
        base + timedelta(minutes=minute),
        speed,
        kind in {"harbor", "harbor_wait"},
        kind == "terminal",
        ("candidate",) if kind in {"waiting", "harbor_wait"} else (),
    )


def infer(points, as_of=None, rules=RULES):
    return infer_track(points, rules, "synthetic-partition", as_of)


def test_normal_entry_exit_preserves_uncertainty_intervals():
    result = infer(
        [
            point(0),
            point(5, "harbor"),
            point(7, "harbor"),
            point(10, "terminal"),
            point(15, "terminal"),
            point(20),
            point(22),
        ]
    )
    (visit,) = result.visits
    assert visit.completed and visit.completeness_reasons == ()
    assert (visit.arrival_lower, visit.arrival_upper) == (BASE, BASE + timedelta(minutes=5))
    assert (visit.departure_lower, visit.departure_upper) == (
        BASE + timedelta(minutes=15),
        BASE + timedelta(minutes=20),
    )
    assert visit.departure_confirmed_at == BASE + timedelta(minutes=22)
    assert (visit.duration_min_seconds, visit.duration_max_seconds) == (600, 1200)


def test_single_crossing_and_duplicate_time_cannot_create_visits():
    assert not infer([point(0), point(5, "harbor"), point(6), point(8)]).visits
    duplicate = point(5, "harbor", identity="synthetic-second-report-same-time")
    assert not infer([point(0), point(5, "harbor"), duplicate, point(6)]).visits


def test_boundary_jitter_does_not_split_and_is_not_supported_dwell():
    (visit,) = infer(
        [
            point(0),
            point(5, "harbor"),
            point(7, "harbor"),
            point(8),
            point(9, "harbor"),
            point(11, "harbor"),
            point(15),
            point(17),
        ]
    ).visits
    assert visit.completed and visit.boundary_jitter_count == 1
    assert visit.supported_inside_seconds == 240
    assert visit.observed_span_seconds == 360


def test_first_and_last_seen_inside_are_partial_with_unknown_boundaries():
    result = infer(
        [point(0, "harbor"), point(2, "harbor"), point(10, "terminal")],
        BASE + timedelta(minutes=60),
    )
    (visit,) = result.visits
    assert not visit.completed and visit.arrival_lower is None and visit.departure_lower is None
    assert visit.duration_min_seconds is None and visit.duration_max_seconds is None
    assert visit.observed_end == BASE + timedelta(minutes=10)
    assert visit.observed_span_seconds == 600 and result.current_state == "unknown"
    assert "trailing_reporting_gap" in visit.completeness_reasons


def test_reporting_gap_splits_visits_without_fabricated_departures():
    first, second = infer(
        [point(0, "harbor"), point(2, "harbor"), point(40, "harbor"), point(42, "harbor")]
    ).visits
    assert first.departure_lower is None and second.arrival_lower is None
    assert first.supported_inside_seconds == second.supported_inside_seconds == 120
    assert "reporting_gap" in first.gap_indicators and "reporting_gap" in second.gap_indicators


def test_stationary_waiting_is_candidate_only_and_long_gaps_do_not_accumulate():
    states = [point(t, "waiting", 0) for t in (0, 10, 20, 30, 100, 110, 120, 130)]
    waits = infer(states).waiting
    assert len(waits) == 2 and all(w.candidate_duration_seconds == 1800 for w in waits)
    assert all(w.associated_visit_id is None for w in waits)
    assert "reporting_gap" in waits[0].completeness_reasons


def test_bounded_waiting_candidate_retains_transition_intervals():
    result = infer([point(-5), *[point(t, "waiting", 0) for t in (0, 10, 20, 30)], point(40)])
    (waiting,) = result.waiting
    assert waiting.completed and waiting.start_lower == BASE - timedelta(minutes=5)
    assert waiting.start_upper == BASE and waiting.end_lower == BASE + timedelta(minutes=30)
    assert waiting.end_upper == BASE + timedelta(minutes=40)
    assert waiting.qualification_known_at == BASE + timedelta(minutes=30)


def test_terminal_low_speed_and_unknown_speed_are_not_continuous_waiting():
    points = [
        TrackPoint(
            i, f"synthetic-terminal-{i}", BASE + timedelta(minutes=t), 0, True, True, ("candidate",)
        )
        for i, t in enumerate((0, 10, 20, 30))
    ]
    assert not infer(points).waiting
    points = [point(t, "waiting", 0) for t in (0, 10, 20)]
    points += [point(30, "waiting", None), *[point(t, "waiting", 0) for t in (40, 50, 60, 70)]]
    (waiting,) = infer(points).waiting
    assert waiting.observed_start == BASE + timedelta(minutes=40)
    assert waiting.candidate_duration_seconds == 1800 and not waiting.completed


def test_cross_midnight_and_reordered_delivery_are_deterministic():
    base = datetime(2024, 1, 1, 23, 50, tzinfo=UTC)
    points = [
        point(t, kind, base=base)
        for t, kind in (
            (0, "outside"),
            (5, "harbor"),
            (7, "harbor"),
            (20, "terminal"),
            (25, "outside"),
            (27, "outside"),
        )
    ]
    first = infer(points)
    assert first == infer(list(reversed(points)))
    (visit,) = first.visits
    assert visit.completed and visit.observed_start.date() != visit.observed_end.date()


def test_future_confirmation_and_port_association_are_excluded_from_prefix():
    points = [
        point(-5),
        *[point(t, "waiting", 0) for t in (0, 10, 20, 30)],
        point(35, "harbor"),
        point(37, "harbor"),
        point(40),
        point(42),
    ]
    before = infer(points, BASE + timedelta(minutes=36))
    assert not before.visits and before.waiting[0].associated_visit_id is None
    full = infer(points)
    assert full.visits[0].completed
    assert full.waiting[0].associated_visit_id == full.visits[0].visit_id
    assert full.waiting[0].association_known_at == BASE + timedelta(minutes=37)
    assert full.waiting[0].known_at >= full.waiting[0].association_known_at
    earlier_exit = infer(points, BASE + timedelta(minutes=40)).visits[0]
    assert earlier_exit.departure_confirmed_at is None and not earlier_exit.completed


def test_late_inside_point_changes_episode_identity_and_numeric_rules_are_stable():
    points = [point(0), point(10, "harbor"), point(12, "harbor"), point(20), point(22)]
    old = infer(points).visits[0]
    new = infer([*points, point(8, "harbor")]).visits[0]
    assert new.visit_id != old.visit_id and new.observed_start < old.observed_start
    assert VisitRules(max_gap_seconds=900.0).rule_id == RULES.rule_id
    assert VisitRules(waiting_speed_knots=0.5).rule_id != RULES.rule_id


def test_conflicting_timestamp_is_unknown_and_breaks_continuity():
    points = [
        point(0, "harbor"),
        point(2, "harbor"),
        point(5, "harbor"),
        point(5, "outside"),
        point(8, "harbor"),
        point(10, "harbor"),
    ]
    result = infer(points)
    assert result.states[2].state == "unknown" and len(result.visits) == 2
    assert all(not v.completed for v in result.visits)
    assert result.states[2].interval_state_before == "unknown"


def test_inclusive_thresholds_and_missing_cutoff_validation():
    assert infer([point(t, "waiting", 1) for t in (0, 15, 30)]).waiting
    assert not infer([point(t, "waiting", 1.01) for t in (0, 15, 30)]).waiting
    with pytest.raises(InferenceError, match="UTC"):
        infer([point(0)], datetime(2024, 1, 1))
    assert CliRunner().invoke(app, ["transform", "visits"]).exit_code == 2
    assert (
        CliRunner()
        .invoke(app, ["transform", "visits", "--demo", "--as-of", "2024-01-01"])
        .exit_code
        == 2
    )
