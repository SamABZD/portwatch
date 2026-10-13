"""Read-only seven-day/dbt reconciliation and boundary report on a configured local database."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from psycopg import sql
from psycopg.rows import dict_row

from portwatch.db import connect_db
from portwatch.settings import Settings


def verify(settings, schema):
    if settings.db_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("history verification is scoped to local databases")
    if not re.fullmatch(r"pw_dbt_[0-9a-f]{32}", schema):
        raise ValueError("select the owned schema from a successful models summary")
    with connect_db(settings) as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SELECT set_config('statement_timeout','120s',true)")
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                sql.SQL("SELECT * FROM {}.port_activity_hourly ORDER BY hour_utc").format(
                    sql.Identifier(schema)
                )
            )
            hours = cursor.fetchall()
        assert len(hours) == 168 and len({r["hour_utc"] for r in hours}) == 168
        assert all(row["complete_hour"] and row["source_interval_available"] for row in hours)
        partitions = sorted({row["partition_id"] for row in hours})
        assert len(partitions) == 7
        original = connection.execute(
            "SELECT to_jsonb(h) FROM port_activity_hourly h WHERE partition_id=ANY(%s) "
            "ORDER BY hour_utc",
            (partitions,),
        ).fetchall()
        canonical = json.loads(json.dumps(hours, default=str))
        # JSON datetime representations differ by adapter; compare in PostgreSQL instead.
        differences = connection.execute(
            sql.SQL("""WITH original AS (
            SELECT * FROM public.port_activity_hourly WHERE partition_id=ANY(%s))
            SELECT count(*) FROM {}.port_activity_hourly a
            FULL JOIN original o USING(partition_id,hour_utc)
            WHERE to_jsonb(a) IS DISTINCT FROM to_jsonb(o)""").format(sql.Identifier(schema)),
            (partitions,),
        ).fetchone()[0]
        assert differences == 0 and len(original) == len(canonical)
        total_positions = connection.execute(
            "SELECT count(*) FROM vessel_positions WHERE source_id=%s AND dataset_id=ANY(%s) "
            "AND processing_version='positions-v1'",
            (hours[0]["source_id"], sorted({row["dataset_id"] for row in hours})),
        ).fetchone()[0]
        assert sum(row["observation_count"] for row in hours) == total_positions
        baseline = connection.execute(
            sql.SQL("""SELECT count(*),min(baseline_hours),
            max(baseline_hours),max(baseline_days),count(*) FILTER(WHERE anomaly_label IS NOT NULL),
            count(*) FILTER(WHERE baseline_eligible)
            FROM {}.activity_baselines""").format(sql.Identifier(schema))
        ).fetchone()
        assert baseline == (168, 0, 167, 7, 0, 0)
        visits = connection.execute(
            sql.SQL("""WITH episodes AS (
                    SELECT DISTINCT p.mmsi,v.observed_start,v.observed_end,
                        v.arrival_lower,v.departure_upper,
                v.completed,v.completeness_reasons,v.duration_min_seconds,v.duration_max_seconds
            FROM {}.int_visits v JOIN public.visit_inference_partitions p USING(partition_id))
            SELECT count(*),count(*) FILTER(WHERE completed),
                count(*) FILTER(WHERE (observed_start AT TIME ZONE 'UTC')::date
                    <(observed_end AT TIME ZONE 'UTC')::date),
                count(*) FILTER(WHERE 'slice_start'=ANY(completeness_reasons)),
                count(*) FILTER(WHERE 'slice_end'=ANY(completeness_reasons)),
                    count(*) FILTER(WHERE 'slice_start'=ANY(completeness_reasons)
                        AND observed_start<'2024-01-01T00:15:00Z'::timestamptz),
                    count(*) FILTER(WHERE 'slice_end'=ANY(completeness_reasons)
                        AND observed_end>='2024-01-07T23:45:00Z'::timestamptz),
                count(*) FILTER(WHERE NOT completed AND
                    (duration_min_seconds IS NOT NULL OR duration_max_seconds IS NOT NULL))
            FROM episodes""").format(sql.Identifier(schema))
        ).fetchone()
        assert visits[-1] == 0
        report = dict(
            checked_at_utc=datetime.now(UTC).isoformat(),
            period="2024-01-01/2024-01-08 UTC exclusive end",
            port_id=hours[0]["port_id"],
            days=7,
            hours=len(hours),
            canonical_positions=total_positions,
            reference_differences=differences,
            dbt_business_sha256=hashlib.sha256(
                json.dumps(canonical, sort_keys=True).encode()
            ).hexdigest(),
            daily=[
                dict(
                    day=str(row["hour_utc"].date()),
                    observations=sum(
                        h["observation_count"]
                        for h in hours
                        if h["hour_utc"].date() == row["hour_utc"].date()
                    ),
                )
                for row in hours
                if row["hour_utc"].hour == 0
            ],
            baseline=dict(
                rows=baseline[0],
                min_prior_hours=baseline[1],
                max_prior_hours=baseline[2],
                max_prior_dates=baseline[3],
                anomaly_labels=baseline[4],
                eligible_rows=baseline[5],
            ),
            episode_projections_deduplicated=dict(
                episodes=visits[0],
                completed=visits[1],
                crossing_utc_days=visits[2],
                slice_start_partial=visits[3],
                slice_end_partial=visits[4],
                near_interval_start_partial=visits[5],
                near_interval_end_partial=visits[6],
                invalid_partial_duration_bounds=visits[7],
            ),
            database_bytes=connection.execute(
                "SELECT pg_database_size(current_database())"
            ).fetchone()[0],
            limits=[
                "not official port calls",
                "daily contextual projections are deduplicated for this report",
                "seven days cannot establish seasonality or total AIS coverage",
                "local expansion is separate from the hosted one-day historical demo",
            ],
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(Settings(), args.schema)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
