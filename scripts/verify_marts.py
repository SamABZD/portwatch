"""Reconcile real hourly metrics, test reruns/prefixes, preserve source and visit hashes."""

import hashlib
import json
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from psycopg.rows import dict_row
from verify_positions import business_hash
from verify_visits import derived_hash

from portwatch.db import connect_db
from portwatch.marts import (
    configured_marts,
    read_completed_hours,
    read_prefix_hours,
    recompute_marts,
)
from portwatch.settings import Settings
from portwatch.storage import RawStore
from portwatch.visit_storage import PARTITION_FILTER, partition_parameters


def mart_hash(connection, partition_id):
    digest = hashlib.sha256()
    row = connection.execute(
        "SELECT (to_jsonb(p)-'computed_at')::text FROM mart_partitions p WHERE partition_id=%s",
        (partition_id,),
    ).fetchone()
    digest.update(row[0].encode())
    for (body,) in connection.execute(
        "SELECT to_jsonb(h)::text FROM port_activity_hourly h "
        "WHERE partition_id=%s ORDER BY hour_utc",
        (partition_id,),
    ):
        digest.update(body.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def reconciliation(connection, scope, config):
    descriptor = (scope.source_id, scope.dataset_id, scope.clean_version)
    checks = {}
    checks["hourly_observation_mismatch"] = connection.execute(
        """SELECT count(*) FROM port_activity_hourly h WHERE h.partition_id=%s
        AND coalesce(h.observation_count,0)<>(SELECT count(*) FROM vessel_positions p
            WHERE p.source_id=%s AND p.dataset_id=%s AND p.processing_version=%s
            AND p.effective_at>=h.hour_utc AND p.effective_at<h.hour_end_utc)""",
        (config.partition_id(scope), *descriptor),
    ).fetchone()[0]
    checks["snapshot_distinct_mismatch"] = connection.execute(
        """SELECT count(*) FROM port_activity_hourly h JOIN LATERAL (
            SELECT count(DISTINCT p.mmsi) AS expected FROM vessel_positions p
            WHERE p.source_id=%s AND p.dataset_id=%s AND p.processing_version=%s
            AND p.effective_at>=h.hour_utc-%s*interval '1 second' AND p.effective_at<=h.hour_utc
        ) s ON true WHERE h.partition_id=%s AND h.snapshot_vessel_count IS DISTINCT FROM
            CASE WHEN h.snapshot_interval_available OR s.expected>0 THEN s.expected END""",
        (*descriptor, config.snapshot_staleness_seconds, config.partition_id(scope)),
    ).fetchone()[0]
    checks["weather_revision_or_value_mismatch"] = connection.execute(
        """SELECT count(*) FROM port_activity_hourly h JOIN mart_partitions p USING(partition_id)
        LEFT JOIN weather_values w ON w.batch_id=p.weather_batch_id AND w.valid_time_utc=h.hour_utc
        WHERE h.partition_id=%s AND (h.weather_wind_speed_mps IS DISTINCT FROM w.wind_speed_10m
            OR h.weather_gusts_mps IS DISTINCT FROM w.wind_gusts_10m
            OR h.weather_preceding_hour_precipitation_mm IS DISTINCT FROM w.precipitation)""",
        (config.partition_id(scope),),
    ).fetchone()[0]
    target = partition_parameters(
        scope, config.port_id, config.geofence_version, config.visit_rules
    )
    event_counts = connection.execute(
        """SELECT count(*) FILTER (WHERE arrival_lower IS NOT NULL AND arrival_confirmed_at>=%s
            AND arrival_confirmed_at<%s),count(*) FILTER (WHERE departure_confirmed_at>=%s
            AND departure_confirmed_at<%s),count(*) FILTER (WHERE completed
            AND departure_confirmed_at>=%s AND departure_confirmed_at<%s),
            percentile_cont(0.5) WITHIN GROUP
                (ORDER BY (duration_min_seconds+duration_max_seconds)/2)
            FILTER (WHERE completed AND departure_confirmed_at>=%s AND departure_confirmed_at<%s)
        FROM vessel_visits WHERE partition_id IN
            (SELECT partition_id FROM visit_inference_partitions WHERE """
        + PARTITION_FILTER
        + ")",
        (scope.start_utc, scope.end_utc) * 4 + target,
    ).fetchone()
    actual = connection.execute(
        """SELECT sum(confirmed_arrival_count),sum(confirmed_departure_count),
        sum(completed_dwell_sample_count) FROM port_activity_hourly WHERE partition_id=%s""",
        (config.partition_id(scope),),
    ).fetchone()
    checks["visit_event_or_dwell_sample_mismatch"] = int(tuple(actual) != event_counts[:3])
    checks["unattributed_reject_mismatch"] = connection.execute(
        """SELECT count(*) FROM mart_partitions p WHERE partition_id=%s
        AND p.unattributed_rejected_record_count IS DISTINCT FROM
            (SELECT count(*) FROM clean_records c
             JOIN ingestion_batches b ON b.batch_id=c.raw_batch_id
             WHERE b.source_id=%s AND b.dataset_id=%s AND c.processing_version=%s
             AND c.outcome='quarantined')""",
        (config.partition_id(scope), *descriptor),
    ).fetchone()[0]
    assert not any(checks.values()), checks
    return checks, dict(
        confirmed_arrivals=event_counts[0],
        confirmed_departures=event_counts[1],
        completed_dwell_samples=event_counts[2],
        overall_completed_midpoint_median_seconds=event_counts[3],
    )


def documented_queries(settings, connection, scope, config):
    body = settings.project_path(Path("docs/ANALYTICS.md")).read_text("utf-8")
    queries = re.findall(r"```sql\n-- query: (\w+)\n(.*?)\n```", body, re.S)
    if {label for label, _ in queries} != {"activity", "waiting", "dwell", "freshness", "rejected"}:
        raise ValueError("the five documented query examples are missing")
    parameters = dict(
        port_id=config.port_id,
        source_id=scope.source_id,
        dataset_id=scope.dataset_id,
        version=config.analytical_version(scope),
        clean_version=scope.clean_version,
        start_utc=scope.start_utc,
        end_utc=scope.end_utc,
        as_of=scope.end_utc,
    )
    results = {}
    for label, query in queries:
        rows = connection.execute(query, parameters).fetchall()
        results[label] = len(rows)
        if label == "activity":
            expected = connection.execute(
                """SELECT count(*) FROM vessel_positions WHERE source_id=%s AND dataset_id=%s
                AND processing_version=%s AND effective_at>=%s AND effective_at<%s""",
                (
                    scope.source_id,
                    scope.dataset_id,
                    scope.clean_version,
                    scope.start_utc,
                    scope.end_utc,
                ),
            ).fetchone()[0]
            assert sum(row[1] or 0 for row in rows) == expected
    return results


def main():
    started = time.perf_counter()
    settings = Settings()
    with connect_db(settings) as connection:
        scope, config = configured_marts(settings, connection, "los_angeles")
        source_before = business_hash(connection)
        visits_before = derived_hash(connection)
        passes = []
        for _ in range(2):
            result = recompute_marts(connection, scope, config)
            passes.append(
                dict(
                    result=asdict(result),
                    business_sha256=mart_hash(connection, result.partition_id),
                )
            )
        assert passes[0] == passes[1]
        checks, event_reconciliation = reconciliation(connection, scope, config)
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM port_activity_hourly WHERE partition_id=%s ORDER BY hour_utc",
                (result.partition_id,),
            )
            rows = cursor.fetchall()
        baseline = mart_hash(connection, result.partition_id)
        audit = connection.execute("SELECT count(*) FROM pipeline_runs").fetchone()
        prefixes = []
        for offset in (
            timedelta(hours=12, minutes=30),
            timedelta(hours=20, minutes=40),
            timedelta(hours=20, minutes=43, seconds=2),
        ):
            cutoff = scope.start_utc + offset
            prefix = read_prefix_hours(connection, scope, config, cutoff)
            count = connection.execute(
                """SELECT count(*) FROM vessel_positions WHERE source_id=%s AND dataset_id=%s
                AND processing_version=%s AND effective_at>=%s AND effective_at<=%s""",
                (scope.source_id, scope.dataset_id, scope.clean_version, scope.start_utc, cutoff),
            ).fetchone()[0]
            assert sum(row["observation_count"] or 0 for row in prefix) == count
            assert prefix[-1]["metric_as_of_utc"] == cutoff and not prefix[-1]["complete_hour"]
            prefixes.append(
                dict(
                    cutoff=cutoff,
                    hours=len(prefix),
                    completed_hours=sum(r["complete_hour"] for r in prefix),
                    observations=count,
                    last_hour=prefix[-1],
                )
            )
        assert prefixes[1]["last_hour"]["regional_waiting_count"] == 1
        assert prefixes[1]["last_hour"]["port_associated_waiting_count"] == 0
        assert prefixes[2]["last_hour"]["port_associated_waiting_count"] == 1
        assert (
            read_completed_hours(connection, scope, config, scope.start_utc + timedelta(hours=12))
            == []
        )
        assert len(read_completed_hours(connection, scope, config, scope.end_utc)) == 24
        assert connection.execute("SELECT count(*) FROM pipeline_runs").fetchone() == audit
        assert mart_hash(connection, result.partition_id) == baseline
        source_after = business_hash(connection)
        visits_after = derived_hash(connection)
        assert source_before == source_after and visits_before == visits_after
        query_results = documented_queries(settings, connection, scope, config)
        report = dict(
            verified_at_utc=datetime.now(UTC),
            scope=asdict(scope),
            config=asdict(config),
            analytical_version=result.analytical_version,
            partition_id=result.partition_id,
            passes=passes,
            checks=checks,
            event_reconciliation=event_reconciliation,
            hourly_rows=rows,
            prefixes=prefixes,
            prefixes_preserve_stored_rows=True,
            source_business_sha256=source_after,
            visit_business_sha256=visits_after,
            source_unchanged=True,
            visits_unchanged=True,
            documented_query_row_counts=query_results,
        )
    inspections = RawStore(settings.project_path(settings.raw_dir)).inspect()
    assert all(item.state == "complete" for item in inspections)
    report["verified_raw_batches"] = len(inspections)
    report["elapsed_seconds"] = time.perf_counter() - started
    output = settings.project_path(Path("artifacts/step09/verification.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, default=str, indent=2) + "\n", encoding="utf-8")
    print(
        f"Mart verification PASS: {result.hours} hours/{result.observations} observations; "
        f"{result.arrivals} confirmed arrivals/{result.departures} departures; "
        f"{result.completed_dwell_samples} completed samples. Zero reconciliation violations."
    )
    print(f"Report: {output}")


if __name__ == "__main__":
    main()
