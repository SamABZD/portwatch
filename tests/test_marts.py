"""Metric versions and publication invariants; explicit synthetic values."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from portwatch.cli import app
from portwatch.marts import MartConfig, MartError, validate_rows, validate_scope
from portwatch.visits import VisitRules
from portwatch.zones import AssignmentScope

BASE = datetime(2024, 1, 1, tzinfo=UTC)
SCOPE = AssignmentScope("synthetic_source", "synthetic_dataset", BASE, BASE + timedelta(days=1))
CONFIG = MartConfig("los_angeles", "synthetic-v1", VisitRules())


def test_metric_version_pins_rules_staleness_geo_and_weather():
    original = CONFIG.analytical_version(SCOPE)
    assert CONFIG.analytical_version(SCOPE) == original
    variants = [
        replace(CONFIG, snapshot_staleness_seconds=600),
        replace(CONFIG, geofence_version="synthetic-v2"),
        replace(CONFIG, visit_rules=VisitRules(persistence_seconds=180)),
        replace(CONFIG, weather_batch_id="a" * 64),
    ]
    assert all(config.analytical_version(SCOPE) != original for config in variants)
    other = replace(SCOPE, source_id="different_synthetic_source")
    assert CONFIG.partition_id(SCOPE) != CONFIG.partition_id(other)


def test_scope_and_snapshot_limits_are_explicit():
    with pytest.raises(MartError):
        replace(CONFIG, snapshot_staleness_seconds=0)
    with pytest.raises(MartError):
        validate_scope(replace(SCOPE, start_utc=BASE + timedelta(minutes=1)))
    with pytest.raises(MartError):
        validate_scope(replace(SCOPE, end_utc=BASE + timedelta(days=8)))


def row(**changes):
    return (
        dict(
            hour_utc=BASE,
            observation_count=5,
            snapshot_vessel_count=2,
            snapshot_zone_counts={"harbor": 1, "outside": 1},
            completed_dwell_sample_count=0,
            completed_dwell_median_seconds=None,
        )
        | changes
    )


def test_duplicate_grain_and_join_multiplication_refused():
    with pytest.raises(MartError, match="unique"):
        validate_rows([row(), row()])
    with pytest.raises(MartError, match="multiply"):
        validate_rows([row(snapshot_zone_counts={"harbor": 2, "outside": 1})])
    with pytest.raises(MartError, match="reconcile"):
        validate_rows([row()], expected_observations=6)
    with pytest.raises(MartError, match="no completed"):
        validate_rows([row(completed_dwell_median_seconds=42)])


def test_unknown_snapshot_has_unknown_zone_values():
    validate_rows([row(snapshot_vessel_count=None, snapshot_zone_counts={"harbor": None})])
    with pytest.raises(MartError, match="unknown snapshot"):
        validate_rows([row(snapshot_vessel_count=None, snapshot_zone_counts={"harbor": 0})])


def test_mart_cli_requires_demo_and_explicit_utc():
    runner = CliRunner()
    assert runner.invoke(app, ["transform", "marts"]).exit_code == 2
    bad = runner.invoke(app, ["transform", "marts", "--demo", "--as-of", "2024-01-01T12:30:00"])
    assert bad.exit_code == 1 and "UTC" in bad.output
