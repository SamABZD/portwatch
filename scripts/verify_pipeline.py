"""Real cached-data Prefect workflows, controlled interruption, reruns and exclusion."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from verify_marts import mart_hash, reconciliation
from verify_positions import business_hash

from portwatch.db import connect_db
from portwatch.marts import configured_marts
from portwatch.orchestration import backfill_flow, historical_flow
from portwatch.pipeline import PIPELINE_VERSION, ControlledStop, PipelineError, owned_lock
from portwatch.settings import Settings
from portwatch.storage import RawStore


def main():
    settings = Settings()
    started = time.perf_counter()
    with connect_db(settings) as connection:
        scope, config = configured_marts(settings, connection, "los_angeles")
        before = dict(
            source=business_hash(connection), mart=mart_hash(connection, config.partition_id(scope))
        )
        try:
            historical_flow(stop_after_raw=True)
        except ControlledStop:
            interrupted = True
        else:
            raise AssertionError("controlled interruption did not occur")
        after_raw = dict(
            source=business_hash(connection), mart=mart_hash(connection, config.partition_id(scope))
        )
        assert after_raw == before
        recovered = historical_flow(force=True)
        assert recovered["status"] == "succeeded"
        first = backfill_flow("los_angeles", "2024-01-01", "2024-01-02")
        middle = dict(
            source=business_hash(connection), mart=mart_hash(connection, config.partition_id(scope))
        )
        second = backfill_flow("los_angeles", "2024-01-01", "2024-01-02")
        after = dict(
            source=business_hash(connection), mart=mart_hash(connection, config.partition_id(scope))
        )
        assert before == middle == after
        assert first["status"] == second["status"] == "succeeded"
        for result in (first, second):
            assert result["partitions"][0]["steps"]["publish"]["checkpoint_reused"]
        key = f"{PIPELINE_VERSION}:los_angeles:{scope.dataset_id}"
        audit_before = connection.execute("SELECT count(*) FROM pipeline_checkpoints").fetchone()[0]
        with owned_lock(connection, "workflow", key):
            try:
                historical_flow()
            except PipelineError as error:
                assert error.code == "overlapping_request_busy"
                excluded = True
            else:
                raise AssertionError("competing workflow was not excluded")
        assert (
            connection.execute("SELECT count(*) FROM pipeline_checkpoints").fetchone()[0]
            == audit_before
        )
        checks, event_reconciliation = reconciliation(connection, scope, config)
        assert all(value == 0 for value in checks.values())
        raw = RawStore(settings.project_path(settings.raw_dir)).inspect()
        assert all(item.state == "complete" for item in raw)
        audit = connection.execute(
            """SELECT stage,status,attempt_number,input_count,output_count,duration_seconds,details
            FROM pipeline_runs WHERE workflow_key=%s ORDER BY id DESC LIMIT 8""",
            (key,),
        ).fetchall()
    report = dict(
        verified_at_utc=datetime.now(UTC).isoformat(),
        elapsed_seconds=time.perf_counter() - started,
        hashes=after,
        controlled_interrupt_after_raw=interrupted,
        resumed=recovered,
        first_backfill=first,
        second_backfill=second,
        overlap_excluded=excluded,
        reconciliation=checks,
        event_reconciliation=event_reconciliation,
        complete_raw_batches=len(raw),
        audit=audit,
        scope="One genuine frozen source day; multi-day gaps and midnight verified synthetically",
    )
    target = settings.project_path(Path("artifacts/step10/verification.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            dict(
                hashes=after,
                reconciliation=checks,
                overlap_excluded=excluded,
                elapsed_seconds=report["elapsed_seconds"],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
