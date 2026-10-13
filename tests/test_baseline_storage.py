"""Execute the actual baseline model on synthetic PostgreSQL data, including leakage edges."""

import pytest
from jinja2 import Environment, StrictUndefined
from psycopg.rows import dict_row

from portwatch.settings import PROJECT_ROOT
from tests.test_bookkeeping import connection as pg_connection

connection = pg_connection
pytestmark = pytest.mark.integration


def baseline_query():
    document = (PROJECT_ROOT / "analytics/models/marts/activity_baselines.sql").read_text("utf-8")
    refs = {"port_activity_hourly": "pw_baseline_hours", "stg_partitions": "pw_baseline_parts"}
    return (
        Environment(undefined=StrictUndefined)
        .from_string(document)
        .render(
            ref=lambda name: refs[name],
            var=lambda name: {"baseline_min_hours": 168, "baseline_min_days": 7}[name],
        )
    )


def test_baseline_excludes_current_future_other_sources_and_withholds_small_samples(connection):
    connection.execute("""CREATE TEMP TABLE pw_baseline_parts(
        partition_id text,clean_version text,geofence_version text,rule_id text,parameters jsonb);
        CREATE TEMP TABLE pw_baseline_hours(
        partition_id text,hour_utc timestamptz,hour_end_utc timestamptz,port_id text,
        source_id text,dataset_id text,observation_count bigint,source_interval_available boolean,
        quality_flags text[])""")
    connection.execute("""INSERT INTO pw_baseline_parts VALUES
        ('a','positions-v1','zones-v1','rules-v1','{"weather_batch_id":"one"}'),
        ('b','positions-v1','zones-v1','rules-v1','{"weather_batch_id":"two"}');
        INSERT INTO pw_baseline_hours
        SELECT 'a','2024-01-01'::timestamptz+n*interval '1 hour',
            '2024-01-01'::timestamptz+(n+1)*interval '1 hour',
            'los_angeles','synthetic_noaa','synthetic_day_'||(n/24),
            CASE WHEN n=168 THEN 9999 ELSE 10 END,true,'{}'::text[]
        FROM generate_series(0,191) n;
        INSERT INTO pw_baseline_hours
        SELECT 'b',hour_utc,hour_end_utc,port_id,'synthetic_other',dataset_id,0,true,'{}'
        FROM pw_baseline_hours WHERE partition_id='a'""")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(baseline_query())
        rows = sorted(
            [row for row in cursor.fetchall() if row["partition_id"] == "a"],
            key=lambda row: row["hour_utc"],
        )
    assert rows[0]["baseline_hours"] == 0
    assert rows[0]["observation_p50"] is None
    assert rows[167]["baseline_hours"] == 167 and rows[167]["anomaly_label"] is None
    current = rows[168]
    assert current["baseline_hours"] == 168 and current["baseline_days"] == 7
    assert current["baseline_end_utc"] == current["hour_utc"]
    assert current["observation_p50"] == 10 and current["observation_p95"] == 10
    assert current["anomaly_label"] == "above_preliminary_p95"
    connection.execute("""UPDATE pw_baseline_hours SET source_interval_available=false
        WHERE partition_id='a' AND hour_utc='2024-01-01T00:00:00Z'""")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(baseline_query())
        revised = next(
            row
            for row in cursor.fetchall()
            if row["partition_id"] == "a" and row["hour_utc"] == current["hour_utc"]
        )
    assert revised["baseline_hours"] == 167
    assert revised["anomaly_label"] is None and not revised["baseline_eligible"]
