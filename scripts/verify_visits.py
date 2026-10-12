"""Replay real saved tracks, check SQL invariants, and export retrospective review evidence."""

import hashlib
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from verify_positions import business_hash

from portwatch.db import connect_db
from portwatch.settings import Settings
from portwatch.visit_storage import PARTITION_FILTER, partition_parameters, transform_visits
from portwatch.zones import demo_scope


def derived_hash(connection):
    digest = hashlib.sha256()
    for table, order in (
        ("visit_rule_sets", "rule_id"),
        ("visit_inference_partitions", "partition_id"),
        ("vessel_activity_states", "partition_id,position_id"),
        ("vessel_visits", "visit_id"),
        ("waiting_episodes", "episode_id"),
    ):
        digest.update(table.encode())
        expression = (
            "to_jsonb(t)-'computed_at'" if table == "visit_inference_partitions" else "to_jsonb(t)"
        )
        with connection.transaction(), connection.cursor(name="visit_hash") as cursor:
            cursor.execute(f"SELECT ({expression})::text FROM {table} t ORDER BY {order}")
            while rows := cursor.fetchmany(2048):
                for (row,) in rows:
                    digest.update(row.encode())
                    digest.update(b"\n")
    return digest.hexdigest()


def inspect_invariants(connection, target, rules):
    with connection.transaction():
        connection.execute(
            "CREATE TEMP TABLE pw_check_partitions ON COMMIT DROP AS "
            "SELECT partition_id FROM visit_inference_partitions WHERE " + PARTITION_FILTER,
            target,
        )
        checks = {}
        checks["missing_or_wrong_source_states"] = connection.execute(
            """SELECT count(*) FROM vessel_activity_states s
            JOIN pw_check_partitions t USING(partition_id)
            JOIN visit_inference_partitions i USING(partition_id)
            JOIN vessel_positions p ON p.id=s.position_id
            WHERE p.source_id<>i.source_id OR p.dataset_id<>i.dataset_id OR p.mmsi<>i.mmsi
                OR p.processing_version<>i.clean_version OR p.effective_at<>s.observed_at"""
        ).fetchone()[0]
        checks["partition_reconciliation"] = connection.execute(
            """SELECT count(*) FROM visit_inference_partitions i
            JOIN pw_check_partitions t USING(partition_id)
            WHERE i.position_count<>(SELECT count(*) FROM vessel_activity_states s
                WHERE s.partition_id=i.partition_id)
                OR i.distinct_event_times<>(SELECT count(DISTINCT observed_at)
                    FROM vessel_activity_states s
                    WHERE s.partition_id=i.partition_id)"""
        ).fetchone()[0]
        checks["invalid_visit_endpoints_or_duration"] = connection.execute(
            """SELECT count(*) FROM vessel_visits v JOIN pw_check_partitions t USING(partition_id)
            JOIN vessel_positions first ON first.id=v.first_position_id
            JOIN vessel_positions last ON last.id=v.last_position_id
            JOIN visit_inference_partitions i USING(partition_id)
            WHERE first.effective_at<>v.observed_start OR last.effective_at<>v.observed_end
                OR first.mmsi<>i.mmsi OR last.mmsi<>i.mmsi
                OR v.observed_span_seconds<>extract(epoch FROM v.observed_end-v.observed_start)
                OR v.known_at>i.result_as_of_utc OR v.arrival_confirmed_at<v.observed_start
                OR (v.completed AND (v.duration_min_seconds<>
                    extract(epoch FROM v.departure_lower-v.arrival_upper)
                    OR v.duration_max_seconds<>
                    extract(epoch FROM v.departure_upper-v.arrival_lower)))
                OR (NOT v.completed AND (v.duration_min_seconds IS NOT NULL
                    OR v.duration_max_seconds IS NOT NULL))"""
        ).fetchone()[0]
        checks["insufficient_entry_persistence"] = connection.execute(
            """SELECT count(*) FROM vessel_visits v JOIN pw_check_partitions t USING(partition_id)
            WHERE extract(epoch FROM v.arrival_confirmed_at-v.observed_start)<%s
                OR (SELECT count(DISTINCT s.observed_at) FROM vessel_activity_states s
                    WHERE s.partition_id=v.partition_id AND s.observed_at BETWEEN v.observed_start
                    AND v.arrival_confirmed_at AND s.port_inside IS TRUE)<%s
                OR EXISTS (SELECT 1 FROM vessel_activity_states s
                    WHERE s.partition_id=v.partition_id
                    AND s.observed_at BETWEEN v.observed_start AND v.arrival_confirmed_at
                    AND s.port_inside IS DISTINCT FROM TRUE)""",
            (rules.persistence_seconds, rules.min_consistent_observations),
        ).fetchone()[0]
        checks["insufficient_exit_persistence"] = connection.execute(
            """SELECT count(*) FROM vessel_visits v JOIN pw_check_partitions t USING(partition_id)
            WHERE v.departure_upper IS NOT NULL AND (
                extract(epoch FROM v.departure_confirmed_at-v.departure_upper)<%s
                OR (SELECT count(DISTINCT observed_at) FROM vessel_activity_states s
                    WHERE s.partition_id=v.partition_id AND s.observed_at BETWEEN v.departure_upper
                    AND v.departure_confirmed_at AND s.port_inside IS FALSE)<%s
                OR EXISTS (SELECT 1 FROM vessel_activity_states s
                    WHERE s.partition_id=v.partition_id
                    AND s.observed_at BETWEEN v.departure_upper AND v.departure_confirmed_at
                    AND s.port_inside IS DISTINCT FROM FALSE))""",
            (rules.persistence_seconds, rules.min_consistent_observations),
        ).fetchone()[0]
        checks["visits_cross_reporting_gaps"] = connection.execute(
            """SELECT count(*) FROM vessel_visits v JOIN pw_check_partitions t USING(partition_id)
            WHERE EXISTS (SELECT 1 FROM vessel_activity_states s
                WHERE s.partition_id=v.partition_id AND s.observed_at>v.observed_start
                AND s.observed_at<=v.observed_end AND s.gap_before_seconds>%s)""",
            (rules.max_gap_seconds,),
        ).fetchone()[0]
        checks["unsupported_waiting_interval"] = connection.execute(
            """SELECT count(*) FROM waiting_episodes w
            JOIN pw_check_partitions t USING(partition_id)
            JOIN visit_inference_partitions i USING(partition_id)
            WHERE w.candidate_duration_seconds<%s
                OR w.candidate_duration_seconds<>extract(epoch FROM w.observed_end-w.observed_start)
                OR w.known_at>i.result_as_of_utc
                OR EXISTS (SELECT 1 FROM vessel_activity_states s
                    WHERE s.partition_id=w.partition_id
                    AND s.observed_at BETWEEN w.observed_start AND w.observed_end
                    AND (s.waiting_candidate IS DISTINCT FROM TRUE
                        OR (s.observed_at>w.observed_start AND s.gap_before_seconds>%s)))""",
            (rules.waiting_min_seconds, rules.max_gap_seconds),
        ).fetchone()[0]
        checks["association_without_port_visit_evidence"] = connection.execute(
            """SELECT count(*) FROM waiting_episodes w
            JOIN pw_check_partitions t USING(partition_id)
            JOIN vessel_visits v ON v.visit_id=w.associated_visit_id
            WHERE w.partition_id<>v.partition_id OR w.segment<>v.segment
                OR w.association_known_at<v.arrival_confirmed_at
                OR w.association_known_at<w.qualification_known_at
                OR NOT ((v.observed_start<=w.observed_end AND v.observed_end>=w.observed_start)
                    OR (v.observed_start>w.observed_end
                        AND extract(epoch FROM v.observed_start-w.observed_end)<=%s))""",
            (rules.max_gap_seconds,),
        ).fetchone()[0]
    if any(checks.values()):
        raise ValueError(f"inference invariants failed: {checks}")
    return checks


def review_tracks(connection, target, destination):
    candidates = connection.execute(
        "SELECT partition_id,mmsi FROM visit_inference_partitions WHERE "
        + PARTITION_FILTER
        + " ORDER BY mmsi",
        target,
    ).fetchall()
    by_id = dict(candidates)
    selected = []
    for query in (
        "SELECT partition_id FROM waiting_episodes ORDER BY candidate_duration_seconds DESC",
        "SELECT partition_id FROM vessel_visits WHERE completed ORDER BY observed_start",
        "SELECT partition_id FROM vessel_visits ORDER BY boundary_jitter_count DESC,visit_id",
        "SELECT partition_id FROM vessel_visits WHERE cardinality(gap_indicators)>0 "
        "ORDER BY visit_id",
    ):
        for (partition_id,) in connection.execute(query):
            if partition_id in by_id and partition_id not in selected:
                selected.append(partition_id)
                break
    selected = selected[:4]
    fig, axes = plt.subplots(
        len(selected), 1, figsize=(12, 3 * len(selected)), sharex=True, squeeze=False
    )
    states_y = {"unknown": 0, "outside": 1, "candidate_waiting": 2, "harbor": 3, "terminal": 4}
    colors = {
        "unknown": "#777777",
        "outside": "#598196",
        "candidate_waiting": "#916fad",
        "harbor": "#29807d",
        "terminal": "#bd6a33",
    }
    reviewed = []
    for ax, partition_id in zip(axes[:, 0], selected, strict=True):
        rows = connection.execute(
            """SELECT s.observed_at,s.observed_state,min(p.speed_knots),max(p.speed_knots),
                max(s.gap_before_seconds),bool_or(s.port_inside),bool_or(s.waiting_candidate)
            FROM vessel_activity_states s JOIN vessel_positions p ON p.id=s.position_id
            WHERE s.partition_id=%s GROUP BY s.observed_at,s.observed_state
            ORDER BY s.observed_at""",
            (partition_id,),
        ).fetchall()
        start = rows[0][0].replace(hour=0, minute=0, second=0, microsecond=0)
        hours = [(row[0] - start).total_seconds() / 3600 for row in rows]
        for state in states_y:
            filtered = [
                (x, states_y[state]) for x, row in zip(hours, rows, strict=True) if row[1] == state
            ]
            if filtered:
                x, y = zip(*filtered, strict=True)
                ax.scatter(x, y, s=7, color=colors[state], label=state)
        visits = connection.execute(
            "SELECT to_jsonb(v) FROM vessel_visits v WHERE partition_id=%s ORDER BY observed_start",
            (partition_id,),
        ).fetchall()
        waiting = connection.execute(
            "SELECT to_jsonb(w) FROM waiting_episodes w WHERE partition_id=%s "
            "ORDER BY observed_start",
            (partition_id,),
        ).fetchall()
        for row in rows:
            if row[4] > 900:
                end = (row[0] - start).total_seconds() / 3600
                ax.axvspan(end - row[4] / 3600, end, color="#bbbbbb", alpha=0.16)
        for (visit,) in visits:
            first = datetime.fromisoformat(visit["observed_start"])
            last = datetime.fromisoformat(visit["observed_end"])
            ax.axvspan(
                (first - start).total_seconds() / 3600,
                (last - start).total_seconds() / 3600,
                color="#568955" if visit["completed"] else "#d6a65a",
                alpha=0.10,
            )
        ax.set_yticks(list(states_y.values()), list(states_y))
        ax.set(
            xlim=(0, 24),
            ylim=(-0.4, 4.6),
            title=f"MMSI {by_id[partition_id]}: {len(visits)} inferred visits, "
            f"{len(waiting)} waiting candidates",
        )
        ax.grid(alpha=0.15)
        reviewed.append(
            {
                "mmsi": by_id[partition_id],
                "partition_id": partition_id,
                "distinct_times": len(rows),
                "first_observed": str(rows[0][0]),
                "last_observed": str(rows[-1][0]),
                "largest_gap_seconds": max(row[4] for row in rows),
                "state_counts": {state: sum(row[1] == state for row in rows) for state in states_y},
                "visits": [v for (v,) in visits],
                "waiting": [w for (w,) in waiting],
            }
        )
        (destination / f"track-{by_id[partition_id]}.json").write_text(
            json.dumps(reviewed[-1], indent=2), encoding="utf-8"
        )
    axes[-1, 0].set_xlabel("Hours from 2024-01-01 00:00 UTC")
    fig.suptitle("PortWatch: retrospective observation and episode review", fontsize=14)
    fig.tight_layout(rect=(0, 0.055, 1, 0.97))
    fig.text(
        0.12,
        0.01,
        "Points are observations; blank/shaded reporting gaps are unknown. "
        "No movement is interpolated.\n"
        "Green/amber bands show completed/partial observed visit spans. "
        "Low speed is a candidate proxy, not a queue.",
        fontsize=9,
    )
    fig.savefig(destination / "track-review.png", dpi=160)
    plt.close(fig)
    return reviewed


def main():
    settings = Settings()
    destination = settings.project_path(Path("artifacts/step07"))
    destination.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with connect_db(settings) as connection:
        original_before = business_hash(connection)
        passes = []
        for _ in range(2):
            version, rules, counts = transform_visits(settings, connection, "los_angeles")
            passes.append({"counts": asdict(counts), "business_sha256": derived_hash(connection)})
        if passes[0] != passes[1]:
            raise ValueError("inference replay changed business rows")
        target = partition_parameters(
            demo_scope(settings, "los_angeles"), "los_angeles", version, rules
        )
        invariants = inspect_invariants(connection, target, rules)
        _, _, prefix = transform_visits(
            settings, connection, "los_angeles", as_of_utc=datetime(2024, 1, 1, 12, tzinfo=UTC)
        )
        if derived_hash(connection) != passes[-1]["business_sha256"]:
            raise ValueError("read-only prefix changed stored inference")
        original_after = business_hash(connection)
        expected = (destination / "source-before.sha256").read_text().strip()
        if original_before != original_after or original_after != expected:
            raise ValueError("visit inference changed original clean business data")
        reviewed = review_tracks(connection, target, destination)
    report = {
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "geofence_version": version,
        "rules": rules.document,
        "rule_id": rules.rule_id,
        "semantics": "retrospective_full_history",
        "replays": passes,
        "invariants": invariants,
        "source_sha256_before": original_before,
        "source_sha256_after": original_after,
        "source_unchanged": True,
        "read_only_noon_prefix_counts": asdict(prefix),
        "reviewed_tracks": [
            {k: v for k, v in track.items() if k not in {"visits", "waiting"}} for track in reviewed
        ],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    (destination / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Evidence: {destination}")


if __name__ == "__main__":
    main()
