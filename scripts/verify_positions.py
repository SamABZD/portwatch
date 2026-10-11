"""Verify actual saved Step05 inputs twice, business hashes, reconciliation and lineage."""

import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from portwatch.cleaning import transform_positions
from portwatch.db import connect_db
from portwatch.positions import PROCESSING_VERSION
from portwatch.settings import Settings
from portwatch.storage import _sync_directory


def business_hash(connection):
    digest = hashlib.sha256()
    for table, order in (
        ("vessel_positions", "observation_id,processing_version"),
        ("clean_records", "source_record_id,processing_version"),
        ("vessel_static_messages", "source_record_id,processing_version"),
    ):
        digest.update(table.encode())
        # Constant table names/order, and a server cursor keeps client memory bounded.
        with connection.transaction(), connection.cursor(name="verify_business") as cursor:
            cursor.execute(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY {order}")
            while rows := cursor.fetchmany(4096):
                for (row,) in rows:
                    digest.update(row.encode())
                    digest.update(b"\n")
    return digest.hexdigest()


def main():
    settings = Settings()
    started = time.perf_counter()
    with connect_db(settings) as connection:
        passes = []
        for _ in range(2):
            reports = []
            for demo, pending in ((True, False), (False, True)):
                for input_id, counts, reused in transform_positions(
                    settings, connection, "los_angeles", demo=demo, pending_live=pending
                ):
                    reports.append(dict(input_id=input_id, counts=counts.__dict__, reused=reused))
            passes.append(dict(inputs=reports, business_sha256=business_hash(connection)))
        assert passes[0]["business_sha256"] == passes[1]["business_sha256"]
        assert [(r["input_id"], r["counts"]) for r in passes[0]["inputs"]] == [
            (r["input_id"], r["counts"]) for r in passes[1]["inputs"]
        ]
        assert all(r["reused"] for r in passes[1]["inputs"])
        checks = {
            "bad_position_geometry": """SELECT count(*) FROM vessel_positions WHERE
                public.ST_SRID(position)<>4326 OR public.ST_GeometryType(position)<>'ST_Point'
                OR public.ST_X(position)<>longitude OR public.ST_Y(position)<>latitude""",
            "missing_position_lineage": """SELECT count(*) FROM vessel_positions p
                LEFT JOIN clean_records r ON p.source_record_id=r.source_record_id
                    AND p.processing_version=r.processing_version
                WHERE r.position_id IS DISTINCT FROM p.id OR r.outcome<>'valid'""",
            "bad_timestamp_choice": """SELECT count(*) FROM vessel_positions WHERE
                (observed_at IS NOT NULL AND effective_at<>observed_at) OR
                (observed_at IS NULL AND effective_at<>received_at)""",
            "unreconciled_batches": """SELECT count(*) FROM clean_batches WHERE status='processed'
                AND input_records<>valid_positions+duplicate_records+quarantined_records
                    +skipped_records""",
            "unfinished_batches": "SELECT count(*) FROM clean_batches WHERE status<>'processed'",
        }
        results = {name: connection.execute(query).fetchone()[0] for name, query in checks.items()}
        assert not any(results.values()), results
        datasets = [
            dict(
                dataset_id=row[0],
                positions=row[1],
                vessels=row[2],
                first_effective_at=row[3].isoformat(),
                last_effective_at=row[4].isoformat(),
            )
            for row in connection.execute("""SELECT dataset_id,count(*),count(DISTINCT mmsi),
                        min(effective_at),max(effective_at) FROM vessel_positions
                        GROUP BY dataset_id
                        ORDER BY dataset_id""")
        ]
        flags = dict(
            connection.execute("""SELECT flag,count(*) FROM vessel_positions,
            unnest(quality_flags) AS flag GROUP BY flag ORDER BY flag""").fetchall()
        )
        examples = [
            dict(
                source_id=row[0],
                observed_at=row[1].isoformat() if row[1] else None,
                received_at=row[2].isoformat(),
                effective_at=row[3].isoformat(),
                provider_reported_at=row[4].isoformat() if row[4] else None,
                source_timestamp_raw=row[5],
                timestamp_quality=row[6],
            )
            for row in connection.execute("""SELECT DISTINCT ON(source_id) source_id,
                        observed_at,received_at,effective_at,provider_reported_at,source_timestamp_raw,
                        timestamp_quality FROM vessel_positions
                        ORDER BY source_id,effective_at,observation_id""")
        ]
        report = dict(
            processing_version=PROCESSING_VERSION,
            verified_at=datetime.now(UTC).isoformat(),
            passes=passes,
            checks=results,
            datasets=datasets,
            quality_flags=flags,
            timestamp_examples=examples,
            elapsed_seconds=time.perf_counter() - started,
        )
    destination = settings.project_path(Path("artifacts/step05/verification.json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Verification is a mutable latest report; immutable raw publication refuses replacement.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, suffix=".part", delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(report, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, destination)
        _sync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps(report, indent=2))
    print(f"Verification artifact: {destination}")


if __name__ == "__main__":
    main()
