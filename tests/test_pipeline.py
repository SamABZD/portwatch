"""Workflow bounds, secret-safe errors and independent manual recovery."""

from datetime import UTC, datetime, timedelta

import psycopg
from typer.testing import CliRunner

from portwatch.cli import app
from portwatch.ingest import plan
from portwatch.pipeline import day_weather, lock_id, sanitized
from portwatch.settings import Settings


def test_exact_backfill_options_and_required_bounds():
    runner = CliRunner()
    assert runner.invoke(app, ["pipeline", "run"]).exit_code == 2
    result = runner.invoke(
        app, ["pipeline", "backfill", "--start", "2024-01-01", "--end", "2024-01-09", "--local"]
    )
    assert result.exit_code == 1 and "ValueError" in result.output
    assert runner.invoke(app, ["pipeline", "backfill", "--start", "2024-01-01"]).exit_code == 2
    assert "--start" in runner.invoke(app, ["pipeline", "backfill", "--help"]).output


def test_source_days_and_historical_weather_are_same_half_open_period():
    settings = Settings(ais_api_key=None)
    requests = plan(settings, "los_angeles", False, "2024-01-01", "2024-01-03")
    assert len(requests) == 2 and requests[0][0].dataset_id != requests[1][0].dataset_id
    for spec, params in requests:
        weather = day_weather(settings, "los_angeles", params.start_utc, params.end_utc)
        assert weather.start_utc == spec.source_start_utc
        assert weather.end_utc == spec.source_end_utc
        assert weather.end_utc - weather.start_utc == timedelta(days=1)
        assert weather.product == "historical_reanalysis"
        assert weather.parameters["end_date"] == weather.start_utc.date().isoformat()
    assert (
        requests[0][0].dataset_id
        == plan(settings, "los_angeles", True, None, None)[0][0].dataset_id
    )
    assert requests[1][0].source_start_utc == datetime(2024, 1, 2, tzinfo=UTC)


def test_bounded_db_retry_never_repeats_auth_schema_or_exposes_secret():
    for error in (
        psycopg.errors.InvalidPassword("synthetic_secret"),
        psycopg.errors.UndefinedTable("synthetic_secret"),
        ValueError("synthetic_secret"),
    ):
        safe = sanitized(error)
        assert not safe.transient and "synthetic_secret" not in str(safe)
    assert sanitized(psycopg.errors.SerializationFailure("synthetic_secret")).transient
    assert sanitized(psycopg.errors.DeadlockDetected("synthetic_secret")).transient
    assert sanitized(psycopg.OperationalError("synthetic_secret")).transient


def test_lock_namespaces_are_stable_and_independent():
    assert lock_id("workflow", "synthetic") == lock_id("workflow", "synthetic")
    assert lock_id("workflow", "synthetic") != lock_id("mutation", "synthetic")
