"""Health semantics use explicit synthetic snapshots; never contact a provider."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest
from typer.testing import CliRunner

from portwatch.cli import app
from portwatch.health import (
    age,
    collect_health,
    collector_health,
    disk_health,
    json_value,
    operation_health,
)
from portwatch.settings import Settings

NOW = datetime(2026, 10, 12, 18, tzinfo=UTC)


def test_backup_health_never_treats_missing_or_truncated_copies_as_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTWATCH_CLOUD_LIVE", "1")
    settings = Settings(raw_dir=tmp_path / "raw", live_status_dir=tmp_path / "status")
    root = tmp_path / "backups"
    root.mkdir()
    settings.live_status_dir.mkdir()
    (settings.live_status_dir / "maintenance.json").write_text(
        json.dumps({"checked_at_utc": NOW.isoformat()})
    )
    assert operation_health(settings, NOW)["status"] == "degraded"
    receipt = dict(
        database=settings.db_name, status="complete", bytes=3, created_at_utc=NOW.isoformat()
    )
    (root / "portwatch-20261012.json").write_text(json.dumps(receipt))
    payload = root / "portwatch-20261012.dump"
    payload.write_bytes(b"123")
    assert operation_health(settings, NOW)["status"] == "ok"
    payload.write_bytes(b"12")
    assert operation_health(settings, NOW)["status"] == "degraded"
    payload.write_bytes(b"123")
    assert operation_health(settings, NOW + timedelta(hours=27))["status"] == "degraded"


def snapshot(**changes):
    return (
        dict(
            state="subscribed",
            connected=True,
            subscription_confirmed=True,
            process_id=123,
            process_created_at_utc=(NOW - timedelta(minutes=2)).isoformat(),
            status_updated_at_utc=NOW.isoformat(),
            last_socket_heartbeat=NOW.isoformat(),
            last_ais_observation_at=None,
            reconnect_count=0,
        )
        | changes
    )


def test_connected_collector_without_positions_is_alive_and_coverage_unknown():
    report = collector_health(snapshot(), NOW, Settings(), probe=lambda *_: True)
    assert report["status"] == "healthy_connected"
    assert report["observation_state"] == "no_recent_observations"
    assert report["last_observed_ais_message_utc"] is None
    dead = collector_health(snapshot(), NOW, Settings(), probe=lambda *_: False)
    assert dead["status"] == "dead_process"


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"state": "backoff", "connected": False, "reconnect_count": 1}, "reconnecting"),
        (
            {"status_updated_at_utc": (NOW - timedelta(seconds=61)).isoformat()},
            "unresponsive_process",
        ),
        (
            {"last_socket_heartbeat": (NOW - timedelta(seconds=61)).isoformat()},
            "heartbeat_unknown_or_stale",
        ),
        ({"state": "stopped", "connected": False}, "stopped"),
        ({"state": "failed", "failure_code": "synthetic_secret"}, "failed"),
    ],
)
def test_statuses_are_distinct_and_error_text_never_escapes(changes, expected):
    report = collector_health(snapshot(**changes), NOW, Settings(), probe=lambda *_: True)
    assert report["status"] == expected
    assert "synthetic_secret" not in json.dumps(report)


def test_old_status_without_process_identity_does_not_claim_a_live_process():
    report = collector_health(
        snapshot(process_id=None, process_created_at_utc=None), NOW, Settings()
    )
    assert report["status"] == "unverified_process" and report["process_alive"] is None
    assert age(NOW, NOW + timedelta(seconds=1)) is None
    assert age(NOW, "invalid") is None
    assert age(NOW, "2026-10-12T18:00:00") is None


def test_disk_pressure_and_decimal_uuid_serialization(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "portwatch.health.shutil.disk_usage", lambda _: SimpleNamespace(free=10, total=100)
    )
    assert (
        disk_health(Settings(raw_dir=tmp_path, storage_min_free_bytes=11))["status"] == "low_space"
    )
    assert json_value(Decimal("3")) == 3
    assert json_value(Decimal("3.5")) == 3.5
    assert json_value(UUID(int=1)) == str(UUID(int=1))


def test_database_outage_keeps_unknown_counts_and_preserves_stdout_json(tmp_path, monkeypatch):
    settings = Settings(raw_dir=tmp_path, live_status_dir=tmp_path, ais_api_key=None)

    def unavailable(_):
        raise psycopg.OperationalError("synthetic_secret")

    monkeypatch.setattr("portwatch.health.connect_db", unavailable)
    report = collect_health(settings, now=NOW)
    assert report["database"]["status"] == "unavailable"
    assert report["live"]["status"] == "unknown" and "accepted_records" not in report["live"]
    assert report["collector"]["status"] == "not_configured"
    assert "synthetic_secret" not in json.dumps(report)
    monkeypatch.setattr("portwatch.health.collect_health", lambda *_: report)
    result = CliRunner().invoke(app, ["--log-json", "health", "--json"])
    assert result.exit_code == 1 and json.loads(result.stdout)["overall"] == "degraded"
    assert "synthetic_secret" not in result.output


def test_opt_in_events_use_stderr_and_cleanup_after_cli_invocation(monkeypatch):

    from portwatch.events import LOGGER, emit

    def health_stub(*_):
        emit("synthetic_health_check", count=1)
        return dict(overall="ok")

    monkeypatch.setattr("portwatch.health.collect_health", health_stub)
    result = CliRunner().invoke(app, ["--log-json", "health", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"overall": "ok"}
    assert json.loads(result.stderr)["event"] == "synthetic_health_check"
    assert not any(getattr(handler, "portwatch_owned", False) for handler in LOGGER.handlers)
