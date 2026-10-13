"""Inspect genuine saved context, cached replay, source isolation and failure semantics."""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from verify_positions import business_hash
from verify_visits import derived_hash

from portwatch.context import plan_context
from portwatch.context_storage import cached_response, ingest_context, read_demo_context
from portwatch.db import connect_db
from portwatch.settings import Settings
from portwatch.storage import RawStore


def weather_hash(connection, batch_id):
    rows = connection.execute(
        """SELECT to_jsonb(w)::text FROM weather_values w WHERE batch_id = %s
        ORDER BY valid_time_utc""",
        (batch_id,),
    ).fetchall()
    return hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest()


def main():
    settings = Settings()
    historical = plan_context(settings, "los_angeles", "weather", demo=True)

    def no_network(url):
        raise AssertionError("verification must use previously saved responses")

    with connect_db(settings) as connection:
        selected = cached_response(connection, historical)
        if not selected:
            raise ValueError("ingest matching historical weather before verification")
        before = weather_hash(connection, selected.batch_id)
        repeats = [
            asdict(ingest_context(settings, connection, historical, fetch=no_network))
            for _ in range(2)
        ]
        assert all(row["batch_id"] == selected.batch_id and row["reused"] for row in repeats)
        assert weather_hash(connection, selected.batch_id) == before
        full = read_demo_context(settings, connection, "los_angeles", historical.end_utc)
        prefix = read_demo_context(
            settings,
            connection,
            "los_angeles",
            historical.start_utc + timedelta(hours=12, minutes=30),
        )
        assert len(full["weather"]) == 24 and len(prefix["weather"]) == 13
        assert all(
            row[0] <= historical.start_utc + timedelta(hours=12, minutes=30)
            for row in prefix["weather"]
        )
        assert full["news"] == prefix["news"] == []
        checks = {
            "selection_wrong_partition": connection.execute(
                """SELECT count(*) FROM context_requests r LEFT JOIN context_responses c
                ON c.batch_id = r.selected_batch_id WHERE r.selected_batch_id IS NOT NULL
                AND (c.batch_id IS NULL OR c.request_key <> r.request_key)"""
            ).fetchone()[0],
            "weather_outside_valid_period": connection.execute(
                """SELECT count(*) FROM weather_values w JOIN context_responses c USING(batch_id)
                JOIN context_requests r USING(request_key) WHERE r.kind <> 'weather'
                OR w.valid_time_utc < r.start_utc OR w.valid_time_utc >= r.end_utc"""
            ).fetchone()[0],
            "failed_attempt_false_empty": connection.execute(
                """SELECT count(*) FROM context_attempts WHERE status = 'valid_empty'
                AND (error_code IS NOT NULL OR http_status <> 200)"""
            ).fetchone()[0],
            "unfinished_context_attempts": connection.execute(
                "SELECT count(*) FROM context_attempts WHERE status = 'running'"
            ).fetchone()[0],
        }
        assert not any(checks.values())
        actual_source_hash = business_hash(connection)
        expected_source = (
            settings.project_path(Path("artifacts/step07/source-before.sha256")).read_text().strip()
        )
        assert actual_source_hash == expected_source
        actual_visit_hash = derived_hash(connection)
        visit_report = json.loads(
            settings.project_path(Path("artifacts/step07/verification.json")).read_text()
        )
        expected_visits = visit_report["replays"][-1]["business_sha256"]
        assert actual_visit_hash == expected_visits
        # Earlier verifier's two passes provide the already frozen inference baseline.
        attempts = connection.execute(
            """SELECT r.kind, r.product, r.start_utc, r.end_utc, a.started_at,
            a.retrieved_at, a.status, a.http_status, a.error_code, a.next_allowed_at,
            a.batch_id, r.parameters FROM context_attempts a
            JOIN context_requests r USING(request_key)
            ORDER BY a.started_at"""
        ).fetchall()
        weather_summary = connection.execute(
            """SELECT r.product, c.batch_id, c.row_count, min(w.valid_time_utc),
            max(w.valid_time_utc), min(w.wind_speed_10m), max(w.wind_speed_10m),
            count(*) FILTER (WHERE w.wind_gusts_10m IS NULL), sum(w.precipitation),
            r.parameters, c.source_metadata, c.quality
            FROM context_requests r JOIN context_responses c ON c.batch_id = r.selected_batch_id
            JOIN weather_values w ON w.batch_id = c.batch_id
            GROUP BY r.product,c.batch_id,c.row_count,r.parameters,c.source_metadata,c.quality
            ORDER BY r.product"""
        ).fetchall()
        report = dict(
            generated_at=datetime.now(UTC),
            selected_historical=asdict(selected),
            cached_replays=repeats,
            weather_business_sha256=before,
            source_business_sha256=actual_source_hash,
            visit_business_sha256=actual_visit_hash,
            visits_unchanged=True,
            prefix_hour_count=len(prefix["weather"]),
            full_hour_count=len(full["weather"]),
            news_in_demo=False,
            demo_news_status=full["news_status"],
            checks=checks,
            attempts=attempts,
            selected_weather_summary=weather_summary,
        )
    items = RawStore(settings.project_path(settings.raw_dir)).inspect()
    assert all(item.state == "complete" for item in items)
    report["verified_raw_batches"] = len(items)
    output = settings.project_path(Path("artifacts/step08/verification.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, default=str, indent=2) + "\n", encoding="utf-8")
    print(
        f"Context verification PASS: {len(items)} raw batches, 24 historical hours, "
        "13 prefix hours, recent news excluded, zero integrity violations."
    )
    print(f"Report: {output}")


if __name__ == "__main__":
    main()
