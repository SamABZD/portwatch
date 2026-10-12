"""Measured Step11 drills: real saved replay and clearly isolated synthetic failures."""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql
from pydantic import SecretStr
from shapely import Point, to_wkb
from verify_marts import mart_hash
from verify_positions import business_hash
from websockets.asyncio.server import serve

from portwatch.backup import owned_database
from portwatch.cleaning import transform_positions
from portwatch.db import connect_db, migrate
from portwatch.download import remember_selection
from portwatch.events import emit, enable_event_logs
from portwatch.health import collect_health, collector_health
from portwatch.ingest import ingest_historical, plan
from portwatch.live import LiveCollector, live_spec
from portwatch.marts import MartConfig, configured_marts, recompute_marts
from portwatch.settings import PROJECT_ROOT, Settings
from portwatch.storage import RawStore, checksum_file
from portwatch.visits import VisitRules
from portwatch.zones import AssignmentScope, load_geofence, validate_geofence

sys.path.insert(0, str(PROJECT_ROOT))
from tests.test_zones import BOUNDS, synthetic_geofence  # noqa: E402

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def synthetic_parquet(path, timeline):
    coords = {"outside": (-118.235, 33.78), "harbor": (-118.275, 33.745)}
    table = pa.table(
        dict(
            mmsi=pa.array(
                [item[2] if len(item) > 2 else 123456789 for item in timeline], type=pa.int32()
            ),
            base_date_time=pa.array(
                [(BASE + timedelta(minutes=item[0])).replace(tzinfo=None) for item in timeline],
                type=pa.timestamp("ms"),
            ),
            sog=pa.array([2.0] * len(timeline), type=pa.float32()),
            cog=pa.array([0.0] * len(timeline), type=pa.float32()),
            heading=pa.array([90] * len(timeline), type=pa.int32()),
            geometry=pa.array(
                [to_wkb(Point(*coords[item[1]])) for item in timeline], type=pa.binary()
            ),
        )
    ).replace_schema_metadata(
        {
            b"fixture_kind": b"explicit_synthetic_step11",
            b"geo": json.dumps(
                dict(
                    version="1.0.0",
                    primary_column="geometry",
                    columns={"geometry": dict(encoding="WKB", geometry_types=["Point"])},
                )
            ).encode(),
        }
    )
    pq.write_table(table, path, row_group_size=2)


def isolated_drills(settings, workspace):
    with owned_database(settings) as scoped:
        scoped = scoped.model_copy(
            update=dict(
                raw_dir=workspace / "raw",
                derived_dir=workspace / "derived",
                quarantine_dir=workspace / "quarantine",
                live_status_dir=workspace / "status",
            )
        )
        with connect_db(scoped) as conn:
            migrate(conn)
        spec, params = plan(scoped, "los_angeles", True, None, None)[0]
        source = workspace / "synthetic-national.parquet"
        synthetic_parquet(
            source,
            [(0, "outside"), (10, "harbor"), (12, "harbor"), (20, "outside"), (22, "outside")],
        )
        store = RawStore(scoped.raw_dir)
        parent = store.write([source.read_bytes()], spec)
        raw_before = checksum_file(store._path(parent.local_path))
        name = scoped.db_name
        # The context owns exactly this new database; the main server/database remain online.
        with connect_db(settings) as admin:
            admin.execute(
                sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(sql.Identifier(name))
            )
            try:
                down = collect_health(scoped)
                assert down["database"]["status"] == "unavailable"
                assert down["raw_storage"]["complete_batches"] == 1
                assert down["historical"]["status"] == "unknown"
            finally:
                admin.execute(
                    sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(sql.Identifier(name))
                )
        recovery_started = time.perf_counter()
        with connect_db(scoped) as conn:
            waiting = collect_health(scoped, connection=conn)
            assert waiting["historical"]["awaiting_registration"] == 1
            ingest_historical(scoped, conn, [(spec, params)])
            reports = transform_positions(
                scoped, conn, "los_angeles", demo=True, pending_live=False
            )
            assert reports[0][1].valid_positions == 5
            assert collect_health(scoped, connection=conn)["historical"]["awaiting_processing"] == 0
            assert checksum_file(store._path(parent.local_path)) == raw_before
            recovery_seconds = time.perf_counter() - recovery_started
            fence = validate_geofence(synthetic_geofence(), "los_angeles", BOUNDS)
            load_geofence(conn, fence)
            scope = AssignmentScope(
                spec.source_id, spec.dataset_id, BASE, BASE + timedelta(hours=1)
            )
            config = MartConfig("los_angeles", fence.document.version, VisitRules())
            first = recompute_marts(conn, scope, config)
            original_visit = conn.execute("SELECT visit_id FROM vessel_visits").fetchone()[0]
            original_mart = mart_hash(conn, first.partition_id)
            late = workspace / "synthetic-late.parquet"
            synthetic_parquet(late, [(8, "harbor")])
            late_parent = store.write([late.read_bytes()], spec)
            remember_selection(store, spec, late_parent, None)
            ingest_historical(scoped, conn, [(spec, params)])
            transform_positions(scoped, conn, "los_angeles", demo=True, pending_live=False)

            def failed_publication():
                raise RuntimeError("synthetic controlled publication failure")

            try:
                recompute_marts(conn, scope, config, before_commit=failed_publication)
            except RuntimeError:
                pass
            else:
                raise AssertionError("publication failure did not occur")
            assert mart_hash(conn, first.partition_id) == original_mart
            failed_health = collect_health(scoped, connection=conn)
            assert failed_health["runs"]["consecutive_failures_by_stage"]["marts"] == 1
            late_started = time.perf_counter()
            updated = recompute_marts(conn, scope, config)
            assert updated.observations == 6 and updated.completed_dwell_samples == 1
            assert (
                conn.execute(
                    "SELECT count(*) FROM vessel_visits WHERE visit_id=%s", (original_visit,)
                ).fetchone()[0]
                == 0
            )
            assert conn.execute("SELECT count(*) FROM vessel_visits").fetchone()[0] == 1
            assert mart_hash(conn, first.partition_id) != original_mart
            late_seconds = time.perf_counter() - late_started
            malformed = workspace / "synthetic-malformed.parquet"
            synthetic_parquet(
                malformed, [(25, "harbor", 987654321), (25, "harbor", 987654321), (26, "harbor", 0)]
            )
            malformed_parent = store.write([malformed.read_bytes()], spec)
            remember_selection(store, spec, malformed_parent, None)
            ingest_historical(scoped, conn, [(spec, params)])
            report = transform_positions(
                scoped, conn, "los_angeles", demo=True, pending_live=False
            )[0][1]
            assert (
                report.valid_positions,
                report.duplicate_records,
                report.quarantined_records,
            ) == (1, 1, 1)
            path, reason = conn.execute(
                "SELECT quarantine_path,reason_code FROM clean_records WHERE outcome='quarantined'"
            ).fetchone()
            quarantine = scoped.quarantine_dir / path
            assert quarantine.is_file() and Path(str(quarantine) + ".lineage.json").is_file()
            before = business_hash(conn)
            duplicate_started = time.perf_counter()
            again = transform_positions(scoped, conn, "los_angeles", demo=True, pending_live=False)
            assert again[0][2] and business_hash(conn) == before
            duplicate_seconds = time.perf_counter() - duplicate_started
            corrupt_before = source.read_bytes()
            raw_path = store._path(parent.local_path)
            raw_path.write_bytes(corrupt_before[:17])
            corrupt_health = collect_health(scoped, connection=conn)
            assert corrupt_health["raw_storage"]["invalid_batches"] == 1
            raw_path.write_bytes(corrupt_before)  # Restore only this owned synthetic fixture.
            assert checksum_file(raw_path) == raw_before
            final = collect_health(scoped, connection=conn)
            assert final["raw_storage"]["invalid_batches"] == 0
            assert final["historical"]["accepted_records"] == 7
            assert final["historical"]["quarantined_records"] == 1
            result = dict(
                scope="synthetic only; one newly created database and file root",
                database_outage=dict(
                    detected=True,
                    backlog_batches=1,
                    recovered_positions=5,
                    recovery_seconds=recovery_seconds,
                    persisted_raw_unchanged=True,
                ),
                publication_rollback=dict(detected=True, previous_valid_metrics_usable=True),
                late_report=dict(
                    observations_before=5,
                    observations_after=6,
                    visit_count=1,
                    obsolete_visit_removed=True,
                    recovery_seconds=late_seconds,
                ),
                malformed_record=dict(
                    accepted=1,
                    duplicate=1,
                    quarantined=1,
                    original_and_lineage_preserved=True,
                    reason=reason,
                ),
                duplicate_replay=dict(
                    business_hash_unchanged=True, elapsed_seconds=duplicate_seconds
                ),
                corruption=dict(detected=True, restored_owned_fixture=True),
                final_health=final,
            )
    result["owned_test_database_removed"] = True
    return result


async def disconnected_collector(settings, workspace):
    spec, bounds = live_spec(settings, "los_angeles")
    snapshots, connections = [], 0
    confirmation = json.dumps(
        {"MessageType": "SubscriptionConfirmation", "Message": {"CompressionEnabled": True}}
    ).encode()
    position = json.dumps(
        {
            "MessageType": "PositionReport",
            "MetaData": {"MMSI": 999000001},
            "Message": {
                "PositionReport": {
                    "UserID": 999000001,
                    "Valid": True,
                    "Latitude": 33.72,
                    "Longitude": -118.26,
                    "Sog": 0,
                }
            },
        }
    ).encode()

    async def handler(socket):
        nonlocal connections
        connections += 1
        await socket.recv()  # Do not log the synthetic subscription/credential.
        await socket.send(confirmation)
        await socket.send(position)
        if connections == 1:
            await asyncio.sleep(0.2)
            await socket.close(code=1011)
        else:
            await socket.wait_closed()

    def status(snapshot):
        health = collector_health(snapshot, datetime.now(UTC), settings)
        snapshots.append(
            dict(
                at=snapshot["status_updated_at_utc"],
                state=snapshot["state"],
                status=health["status"],
                connected=snapshot["connected"],
                observations=snapshot["observations"],
                reconnect_count=snapshot["reconnect_count"],
            )
        )

    async with serve(handler, "127.0.0.1", 0, compression="deflate", ping_interval=None) as server:
        uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        collector = LiveCollector(
            SecretStr("synthetic_step11_secret"),
            bounds,
            spec,
            RawStore(workspace / "collector-raw"),
            workspace / "collector-spool",
            workspace / "collector-status.json",
            uri=uri,
            status_callback=status,
            flush_seconds=0.05,
            status_interval=0.1,
            rotate_seconds=1,
            ping_interval=0.1,
            ping_timeout=1,
            backoff_base=0.3,
            backoff_max=0.3,
        )
        started = time.perf_counter()
        await asyncio.wait_for(collector.run(duration=2), timeout=8)
        elapsed = time.perf_counter() - started
    assert connections == 2 and collector.status.reconnect_count == 1
    assert collector.status.observations == 2
    assert any(row["status"] == "reconnecting" for row in snapshots)
    assert any(row["status"] == "healthy_connected" for row in snapshots)
    disconnected = next(row for row in snapshots if row["state"] == "disconnected")
    reconnected = next(
        row
        for row in snapshots
        if row["state"] == "subscribed"
        and datetime.fromisoformat(row["at"]) > datetime.fromisoformat(disconnected["at"])
    )
    return dict(
        scope="loopback WebSocket only; synthetic frames, no real provider request",
        elapsed_seconds=elapsed,
        connections=connections,
        reconnect_count=1,
        observations=2,
        missing_connection_interval_seconds=(
            datetime.fromisoformat(reconnected["at"]) - datetime.fromisoformat(disconnected["at"])
        ).total_seconds(),
        missing_interval_means="upstream observations unknown, not zero traffic",
        snapshots=snapshots,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--collector-only", action="store_true", help="Bounded loopback disconnect drill."
    )
    args = parser.parse_args()
    settings = Settings()
    started = time.perf_counter()
    workspace = settings.project_path(Path("artifacts/step11/drills") / uuid4().hex)
    workspace.mkdir(parents=True)
    if args.collector_only:
        result = asyncio.run(disconnected_collector(settings, workspace))
        settings.project_path(Path("artifacts/step11/collector-check.json")).write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                dict(
                    elapsed_seconds=result["elapsed_seconds"],
                    connections=result["connections"],
                    observations=result["observations"],
                )
            )
        )
        return
    with connect_db(settings) as conn:
        scope, config = configured_marts(settings, conn, "los_angeles")
        before_source = business_hash(conn)
        before_mart = mart_hash(conn, config.partition_id(scope))
        reruns = []
        for _ in range(2):
            tick = time.perf_counter()
            result = recompute_marts(conn, scope, config)
            assert mart_hash(conn, result.partition_id) == before_mart
            reruns.append(
                dict(
                    hours=result.hours,
                    observations=result.observations,
                    elapsed_seconds=time.perf_counter() - tick,
                    mart_hash=before_mart,
                )
            )
        assert business_hash(conn) == before_source
    drills = isolated_drills(settings, workspace)
    collector = asyncio.run(disconnected_collector(settings, workspace))
    with connect_db(settings) as conn:
        assert business_hash(conn) == before_source
        assert mart_hash(conn, config.partition_id(scope)) == before_mart
        assert not conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'pw_step11_%'"
        ).fetchall()
    report = dict(
        verified_at_utc=datetime.now(UTC).isoformat(),
        elapsed_seconds=time.perf_counter() - started,
        real_historical_reruns=reruns,
        source_hash=before_source,
        real_historical_mart_hash=before_mart,
        isolated_drills=drills,
        collector_drill=collector,
        recovery_promise="Persisted demonstrated inputs survived downstream recovery; "
        "unflushed buffers and unreceived upstream events are excluded",
    )
    path = settings.project_path(Path("artifacts/step11/recovery-verification.json"))
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    emit(
        "recovery_drills_verified",
        elapsed_seconds=report["elapsed_seconds"],
        recovered_synthetic_positions=5,
        backup_resources_removed=True,
    )
    print(
        json.dumps(
            dict(
                verified=True,
                elapsed_seconds=report["elapsed_seconds"],
                source_hash=before_source,
                mart_hash=before_mart,
                backlog_recovery_seconds=drills["database_outage"]["recovery_seconds"],
                reconnect_interval_seconds=collector["missing_connection_interval_seconds"],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    cleanup = enable_event_logs()
    try:
        main()
    finally:
        cleanup()
