"""Measure real HTTP replay, compare SQL activity and verify unchanged project business data."""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

from verify_marts import mart_hash

from portwatch.db import connect_db
from portwatch.demo.replay import export_prepared, load_prepared, stamp
from portwatch.marts import configured_marts, read_prefix_hours
from portwatch.settings import Settings

BASE_URL = "http://127.0.0.1:8000/api/"


def historical_hash(connection, scope):
    """Background live collection must not invalidate a historical read-only check."""
    digest = hashlib.sha256()
    for table, order in (
        ("vessel_positions", "observation_id,processing_version"),
        ("vessel_static_messages", "source_record_id,processing_version"),
        ("vessel_profile_reports", "source_record_id,processing_version"),
    ):
        digest.update(table.encode())
        with connection.transaction(), connection.cursor(name="replay_history_hash") as cursor:
            cursor.execute(
                f"SELECT row_to_json(t)::text FROM {table} t WHERE source_id=%s AND dataset_id=%s "
                f"ORDER BY {order}",
                (scope.source_id, scope.dataset_id),
            )
            while rows := cursor.fetchmany(4096):
                for (row,) in rows:
                    digest.update(row.encode())
                    digest.update(b"\n")
    return digest.hexdigest()


def historical_audits(connection, scope):
    key = "pipeline-v1:los_angeles:" + scope.dataset_id
    pattern = key.replace("!", "!!").replace("_", "!_").replace("%", "!%") + ":%"
    return connection.execute(
        "SELECT count(*) FROM pipeline_runs WHERE workflow_key=%s "
        "OR workflow_key LIKE %s ESCAPE '!'",
        (key, pattern),
    ).fetchone()[0]


def request(command, parameters=None):
    url = BASE_URL + command + ("?" + urlencode(parameters) if parameters else "")
    started = time.perf_counter()
    with urlopen(url, timeout=20) as response:
        result = json.load(response)
    return result, time.perf_counter() - started


def main():
    settings = Settings()
    with connect_db(settings) as connection:
        scope, config = configured_marts(settings, connection, "los_angeles")
        source_before = historical_hash(connection, scope)
        mart_before = mart_hash(connection, config.partition_id(scope))
        audits_before = historical_audits(connection, scope)
    catalog, catalog_seconds = request("catalog")
    assert catalog["datasets"][0]["observation_count"] == 62157
    assert catalog["datasets"][0]["dataset_id"] == scope.dataset_id
    assert catalog["datasets"][1]["source_id"] != scope.source_id
    frames = []
    noon = None
    for cutoff in (
        "2024-01-01T00:00:00Z",
        "2024-01-01T12:30:00Z",
        "2024-01-01T20:40:00Z",
        "2024-01-01T20:45:00Z",
        "2024-01-02T00:00:00Z",
    ):
        parameters = dict(mode="historical", at=cutoff, stale=15, trail=30)
        frame, cold = request("frame", parameters)
        again, warm = request("frame", parameters)
        assert again["cache_hit"] and warm < 2
        moment = stamp(cutoff)
        assert all(stamp(v["last_observation_utc"]) <= moment for v in frame["vessels"])
        assert all(
            stamp(point[2]) <= moment for trail in frame["trails"] for point in trail["points"]
        )
        assert all(stamp(row["hour_end_utc"]) <= moment for row in frame["chart"])
        assert not frame["news"] and frame["news_status"] == "excluded_recent_source"
        if frame["weather"]:
            assert stamp(frame["weather"]["valid_time_utc"]) <= moment
        if "12:30" in cutoff:
            noon = frame
            assert frame["prefix_observations"] == 32632
            assert frame["metrics"]["fresh_vessels"] == 99
        if moment == scope.end_utc:
            assert frame["prefix_observations"] == 62157
            assert frame["metrics"]["completed_dwell_sample_count"] == 14
        frames.append(
            dict(
                cutoff_utc=frame["cutoff_utc"],
                first_request_seconds=cold,
                first_request_cache_hit=frame["cache_hit"],
                cached_seconds=warm,
                prefix_observations=frame["prefix_observations"],
                metrics=frame["metrics"],
                completed_chart_hours=len(frame["chart"]),
            )
        )
    uncached_at = (
        datetime(2024, 1, 1, 13, 17, 11, tzinfo=UTC)
        .replace(microsecond=max(1, datetime.now(UTC).microsecond))
        .isoformat()
    )
    sample, uncached_seconds = request("frame", dict(mode="historical", at=uncached_at))
    assert not sample["cache_hit"]
    cached_sample, uncached_sample_cached_seconds = request(
        "frame", dict(mode="historical", at=uncached_at)
    )
    assert cached_sample["cache_hit"] and uncached_sample_cached_seconds < 2
    with connect_db(settings) as connection:
        rows = read_prefix_hours(
            connection, scope, config, stamp(noon["cutoff_utc"]), completed_only=True
        )
        assert [r["observed_vessels"] for r in noon["chart"]] == [
            r["distinct_vessels_observed"] for r in rows
        ]
        assert [r["observations"] for r in noon["chart"]] == [r["observation_count"] for r in rows]
        assert noon["metrics"]["completed_dwell_sample_count"] == sum(
            r["completed_dwell_sample_count"] or 0 for r in rows
        )
        assert historical_hash(connection, scope) == source_before
        assert mart_hash(connection, config.partition_id(scope)) == mart_before
        assert historical_audits(connection, scope) == audits_before
    live, live_seconds = request("frame", dict(mode="live"))
    assert live["dataset"]["source_id"] != noon["dataset"]["source_id"]
    assert all(
        stamp(v["last_observation_utc"]) <= stamp(live["cutoff_utc"]) for v in live["vessels"]
    )
    if not live["metrics"]["fresh_vessels"]:
        assert live["metrics"]["fresh_port_occupancy"] is None
        assert live["metrics"]["candidate_waiting"] is None
    health, _ = request("health")
    portable_path = settings.project_path(Path("artifacts/step13") / f"replay-{uuid4().hex}.json")
    portable_path.parent.mkdir(parents=True, exist_ok=True)
    export_prepared(settings, portable_path)
    portable = load_prepared(portable_path)
    portable_frame = portable.frame(stamp(noon["cutoff_utc"]))
    assert portable_frame["metrics"] == noon["metrics"]
    assert portable_frame["vessels"] == noon["vessels"]
    report = dict(
        verified_at_utc=datetime.now(UTC).isoformat(),
        verified=True,
        catalog_seconds=catalog_seconds,
        frames=frames,
        genuine_uncached_frame_seconds=uncached_seconds,
        genuine_uncached_sample_repeat_seconds=uncached_sample_cached_seconds,
        live_query_seconds=live_seconds,
        live_saved_positions=live["prefix_observations"],
        live_occupancy_unknown=live["metrics"]["fresh_port_occupancy"] is None,
        collector_status=health["collector"]["status"],
        historical_news_excluded=True,
        sql_activity_reconciled=True,
        prepared_roundtrip_matches=True,
        source_hash=source_before,
        mart_hash=mart_before,
        historical_sql_audit_count_unchanged=True,
        hash_scope="Los Angeles historical positions, static messages and profile evidence",
        collector_started=False,
        provider_fetches=0,
    )
    folder = settings.project_path(Path("artifacts/step13"))
    folder.mkdir(exist_ok=True)
    (folder / "replay-verification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
