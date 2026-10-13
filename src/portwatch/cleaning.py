"""Bounded adapters, bulk transactional loading, quarantine, and deterministic replay."""

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, fields
from pathlib import Path

import pyarrow.parquet as pq
from psycopg import sql
from psycopg.types.json import Jsonb

from portwatch.bookkeeping import finish_run, register_batch, start_run
from portwatch.historical import DerivedManifest, derivation_identity
from portwatch.ingest import plan
from portwatch.live import live_spec
from portwatch.live_spool import MAX_LINE_BYTES
from portwatch.positions import (
    PROCESSING_VERSION,
    CleanPosition,
    RejectedRecord,
    identity,
    json_original,
    live_record,
    noaa_position,
    position_values,
    source_record_identity,
)
from portwatch.settings import Settings
from portwatch.storage import Manifest, RawStore, _publish_json, checksum_file, quarantine_record

COLUMNS = [field.name for field in fields(CleanPosition)]
COUNT_COLUMNS = (
    "input_records",
    "valid_positions",
    "duplicate_records",
    "quarantined_records",
    "skipped_records",
)


@dataclass(frozen=True)
class Counts:
    input_records: int
    valid_positions: int
    duplicate_records: int
    quarantined_records: int
    skipped_records: int

    def __post_init__(self):
        if self.input_records != sum(
            (
                self.valid_positions,
                self.duplicate_records,
                self.quarantined_records,
                self.skipped_records,
            )
        ):
            raise ValueError("cleaning counts do not reconcile")


@dataclass(frozen=True)
class CleanInput:
    parent: Manifest
    input_id: str
    content_sha256: str
    path: Path
    offset_unit: str


def historical_input(settings: Settings, store: RawStore, spec, parameters) -> CleanInput:
    # Read saved current-source pointer; transformation never downloads provider data.
    from portwatch.download import selection_path

    pointer = selection_path(store, spec)
    matches = []
    for item in store.inspect(source_id=spec.source_id, mode="historical"):
        parent = item.manifest
        if parent and (parent.source_id, parent.dataset_id, parent.source_url) == (
            spec.source_id,
            spec.dataset_id,
            spec.source_url,
        ):
            if item.state != "complete":
                raise ValueError("historical raw storage integrity issue")
            matches.append(parent)
    if not matches:
        raise ValueError("historical snapshot missing; run ingest historical --demo")
    if pointer.is_file():
        saved = json.loads(pointer.read_text("utf-8"))
        matches = [parent for parent in matches if parent.batch_id == saved.get("batch_id")]
        if len(matches) != 1 or saved.get("schema_version") != 1:
            raise ValueError("historical source pointer has no verified batch")
        parent = matches[0]
    else:
        # Step03 caches predating source-selection pointers use the same latest-receipt rule.
        parent = max(matches, key=lambda batch: batch.received_at)
    if (parent.source_id, parent.dataset_id, parent.source_url) != (
        spec.source_id,
        spec.dataset_id,
        spec.source_url,
    ):
        raise ValueError("historical source selection conflicts with requested source")
    derivation = derivation_identity(parent, parameters)
    root = settings.project_path(settings.derived_dir)
    path = (
        root
        / spec.source_id
        / spec.dataset_id
        / parameters.port_id
        / derivation
        / "positions.parquet"
    )
    manifest = DerivedManifest.model_validate_json(
        Path(str(path) + ".manifest.json").read_text("utf-8")
    )
    if (
        manifest.parent_batch_id != parent.batch_id
        or manifest.derivation_id != derivation
        or manifest.parent_raw_sha256 != parent.content_sha256
        or manifest.parameters != parameters
        or (root / manifest.artifact_path).resolve() != path.resolve()
        or checksum_file(path) != (manifest.content_sha256, manifest.byte_count)
    ):
        raise ValueError("derived artifact lineage or checksum mismatch")
    return CleanInput(
        parent,
        identity([parent.batch_id, derivation, manifest.content_sha256]),
        manifest.content_sha256,
        path,
        "national_row",
    )


def input_records(source: CleanInput) -> Iterator[tuple[int, dict | bytes]]:
    if source.offset_unit == "national_row":
        with pq.ParquetFile(source.path, pre_buffer=False) as parquet:
            for batch in parquet.iter_batches(batch_size=4096):
                for row in batch.to_pylist():
                    offset = row.get("source_row_offset")
                    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                        # Cannot safely manufacture raw-file lineage for a broken derivative.
                        raise ValueError("derived record offset is invalid")
                    yield offset, row
    else:
        with source.path.open("rb") as stream:
            while True:
                offset = stream.tell()
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError("raw envelope exceeds storage contract")
                yield offset, line


def preserve_rejection(root, source, offset, payload, reason):
    namespace = root / PROCESSING_VERSION / source.parent.source_id / source.offset_unit
    path = quarantine_record(
        namespace, payload, batch_id=source.parent.batch_id, record_offset=offset, reason=reason
    )
    lineage = dict(
        source_id=source.parent.source_id,
        dataset_id=source.parent.dataset_id,
        source_batch_id=source.parent.batch_id,
        record_offset=offset,
        offset_unit=source.offset_unit,
        reason_code=reason,
        processing_version=PROCESSING_VERSION,
        input_id=source.input_id,
        representation="original_jsonl_bytes"
        if source.offset_unit == "jsonl_byte"
        else "tagged_parquet_row_json",
    )
    sidecar = Path(str(path) + ".lineage.json")
    if sidecar.exists():
        if json.loads(sidecar.read_text("utf-8")) != lineage:
            raise ValueError("quarantine lineage conflicts with saved record")
    else:
        _publish_json(sidecar, json.dumps(lineage, sort_keys=True, indent=2))
    return path.relative_to(root).as_posix()


def load_batch(
    connection,
    source: CleanInput,
    quarantine_root: Path,
    *,
    records: Iterable[tuple[int, dict | bytes]] | None = None,
    before_commit=None,
) -> tuple[Counts, bool]:
    """One batch commits positions, every record outcome, and success status together."""
    parent = source.parent
    columns = sql.SQL(",").join(map(sql.Identifier, COLUMNS))
    count_names = sql.SQL(",").join(map(sql.Identifier, COUNT_COLUMNS))
    # Lock acquisition and status inspection share the same transaction as publication.
    try:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(734920025)")
            connection.execute(
                """INSERT INTO clean_batches(input_id,processing_version,raw_batch_id,input_sha256)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (source.input_id, PROCESSING_VERSION, parent.batch_id, source.content_sha256),
            )
            saved = connection.execute(
                sql.SQL(
                    "SELECT status,input_sha256,{} FROM clean_batches "
                    "WHERE input_id=%s AND processing_version=%s"
                ).format(count_names),
                (source.input_id, PROCESSING_VERSION),
            ).fetchone()
            if saved[1] != source.content_sha256:
                raise ValueError("clean input identity conflict")
            if saved[0] == "processed":
                return Counts(*saved[2:]), True
            connection.execute(
                "UPDATE clean_batches SET status='processing',started_at=clock_timestamp(),"
                "error_summary=NULL WHERE input_id=%s AND processing_version=%s",
                (source.input_id, PROCESSING_VERSION),
            )
            connection.execute(
                "CREATE TEMP TABLE clean_candidates ON COMMIT DROP "
                "AS SELECT * FROM vessel_positions WITH NO DATA"
            )
            connection.execute(
                "ALTER TABLE clean_candidates ADD COLUMN ordinal bigint, "
                "ADD COLUMN outcome text, ADD COLUMN reason_code text, "
                "ADD COLUMN quarantine_path text, ADD COLUMN static_message jsonb, "
                "ADD COLUMN static_mmsi text"
            )
            extra = [
                "ordinal",
                "outcome",
                "reason_code",
                "quarantine_path",
                "static_message",
                "static_mmsi",
            ]
            with connection.cursor().copy(
                sql.SQL("COPY clean_candidates ({}) FROM STDIN").format(
                    sql.SQL(",").join(map(sql.Identifier, COLUMNS + extra))
                )
            ) as copy:
                for ordinal, (offset, raw) in enumerate(
                    records if records is not None else input_records(source)
                ):
                    position = static = static_mmsi = reason = quarantine_path = None
                    outcome = "valid"
                    try:
                        if source.offset_unit == "national_row":
                            position = noaa_position(raw, parent)
                        else:
                            result = live_record(raw, parent, offset)
                            position, static, static_mmsi, reason = (
                                result.position,
                                result.static_message,
                                result.mmsi,
                                result.reason,
                            )
                            if position is None:
                                outcome = "skipped"
                    except RejectedRecord as error:
                        outcome, reason = "quarantined", error.reason
                        payload = raw if isinstance(raw, bytes) else json_original(raw)
                        quarantine_path = preserve_rejection(
                            quarantine_root, source, offset, payload, reason
                        )
                    values = (
                        position_values(position)
                        if position
                        else {
                            "source_record_id": source_record_identity(
                                parent, offset, source.offset_unit
                            ),
                            "processing_version": PROCESSING_VERSION,
                            "source_batch_id": parent.batch_id,
                            "record_offset": offset,
                            "offset_unit": source.offset_unit,
                        }
                    )
                    if position:
                        values["quality_flags"] = list(position.quality_flags)
                    copy.write_row(
                        [values.get(name) for name in COLUMNS]
                        + [
                            ordinal,
                            outcome,
                            reason,
                            quarantine_path,
                            Jsonb(static) if static else None,
                            static_mmsi,
                        ]
                    )
            # A repeated raw offset means corrupt derivative lineage, not a business duplicate.
            if connection.execute(
                "SELECT count(*) <> count(DISTINCT source_record_id) FROM clean_candidates"
            ).fetchone()[0]:
                raise ValueError("input contains repeated source-record identity")
            connection.execute(
                sql.SQL("""INSERT INTO vessel_positions ({},position)
                SELECT DISTINCT ON (observation_id,processing_version) {},
                    public.ST_SetSRID(public.ST_MakePoint(longitude,latitude),4326)
                FROM clean_candidates WHERE outcome='valid'
                ORDER BY observation_id,processing_version,ordinal
                ON CONFLICT DO NOTHING""").format(columns, columns)
            )
            connection.execute(
                """INSERT INTO clean_records(source_record_id,processing_version,
                    input_id,raw_batch_id,
                record_offset,offset_unit,outcome,position_id,reason_code,quarantine_path)
                SELECT c.source_record_id,c.processing_version,%s,c.source_batch_id,
                    c.record_offset,c.offset_unit,
                    CASE WHEN c.outcome='valid' THEN
                        CASE WHEN p.source_record_id=c.source_record_id
                            THEN 'valid' ELSE 'duplicate' END ELSE c.outcome END,
                    p.id,c.reason_code,c.quarantine_path FROM clean_candidates c
                    LEFT JOIN vessel_positions p ON c.observation_id=p.observation_id
                        AND c.processing_version=p.processing_version
                ON CONFLICT DO NOTHING""",
                (source.input_id,),
            )
            connection.execute(
                """INSERT INTO vessel_static_messages(source_record_id,processing_version,
                    source_id,dataset_id,mmsi,original_message)
                SELECT source_record_id,processing_version,%s,%s,static_mmsi,static_message
                FROM clean_candidates WHERE static_message IS NOT NULL ON CONFLICT DO NOTHING""",
                (parent.source_id, parent.dataset_id),
            )
            # Flag adjacent extreme implied speed; retain observations and unknown maneuvers.
            # Refresh the entire source/dataset/version so later batches can repair adjacency.
            connection.execute(
                """UPDATE vessel_positions SET quality_flags=array_remove(
                    array_remove(quality_flags,'implausible_jump'),'conflicting_same_time_position')
                WHERE source_id=%s AND dataset_id=%s AND processing_version=%s""",
                (parent.source_id, parent.dataset_id, PROCESSING_VERSION),
            )
            connection.execute(
                """WITH ordered AS (
                SELECT id,position,effective_at,
                    lag(position) OVER w AS previous_position,
                    lag(effective_at) OVER w AS previous_time
                FROM vessel_positions WHERE source_id=%s AND dataset_id=%s AND processing_version=%s
                WINDOW w AS (PARTITION BY mmsi ORDER BY effective_at,observation_id)
            ), suspicious AS (
                SELECT id,CASE WHEN effective_at=previous_time
                    THEN 'conflicting_same_time_position' ELSE 'implausible_jump' END AS flag
                FROM ordered WHERE previous_position IS NOT NULL
                AND public.ST_Distance(position::public.geography,
                    previous_position::public.geography) > 100
                AND (effective_at=previous_time OR public.ST_Distance(position::public.geography,
                    previous_position::public.geography)
                    / NULLIF(extract(epoch FROM effective_at-previous_time),0)
                    * 3600 / 1852 > 80)
            ) UPDATE vessel_positions p SET quality_flags=array_append(p.quality_flags,s.flag)
                FROM suspicious s WHERE p.id=s.id""",
                (parent.source_id, parent.dataset_id, PROCESSING_VERSION),
            )
            counts = Counts(
                *connection.execute("""SELECT count(*),count(*) FILTER (WHERE r.outcome='valid'),
                count(*) FILTER (WHERE r.outcome='duplicate'),
                count(*) FILTER (WHERE r.outcome='quarantined'),
                count(*) FILTER (WHERE r.outcome='skipped')
                FROM clean_candidates c
                JOIN clean_records r USING(source_record_id,processing_version)""").fetchone()
            )
            connection.execute(
                """UPDATE clean_batches SET status='processed',completed_at=clock_timestamp(),
                input_records=%s,valid_positions=%s,duplicate_records=%s,
                quarantined_records=%s,skipped_records=%s
                WHERE input_id=%s AND processing_version=%s""",
                (*counts.__dict__.values(), source.input_id, PROCESSING_VERSION),
            )
            connection.execute(
                "UPDATE ingestion_batches SET status='processed',error_summary=NULL "
                "WHERE batch_id=%s",
                (parent.batch_id,),
            )
            if before_commit:
                before_commit()
        return counts, False
    except Exception as error:
        # The failed transaction leaves no analytical rows or successful status behind.
        with connection.transaction():
            connection.execute(
                """INSERT INTO clean_batches(input_id,processing_version,raw_batch_id,
                    input_sha256,status,error_summary)
                VALUES (%s,%s,%s,%s,'failed',%s) ON CONFLICT(input_id,processing_version) DO UPDATE
                SET status='failed',error_summary=excluded.error_summary
                WHERE clean_batches.status<>'processed'""",
                (
                    source.input_id,
                    PROCESSING_VERSION,
                    parent.batch_id,
                    source.content_sha256,
                    type(error).__name__,
                ),
            )
        raise


def transform_positions(
    settings: Settings, connection, port: str, *, demo: bool, pending_live: bool
):
    if demo == pending_live:
        raise ValueError("choose exactly one of --demo and --pending-live")
    store = RawStore(settings.project_path(settings.raw_dir))
    inputs = []
    if demo:
        for spec, parameters in plan(settings, port, True, None, None):
            inputs.append(historical_input(settings, store, spec, parameters))
    else:
        spec, _ = live_spec(settings, port)
        for item in store.inspect(source_id=spec.source_id, mode="live"):
            if (
                item.manifest
                and item.manifest.source_id == spec.source_id
                and item.manifest.dataset_id == spec.dataset_id
            ):
                if item.state != "complete":
                    raise ValueError("live raw storage integrity issue")
                parent = item.manifest
                inputs.append(
                    CleanInput(
                        parent,
                        identity([parent.batch_id, parent.content_sha256, "live-jsonl"]),
                        parent.content_sha256,
                        store._path(parent.local_path),
                        "jsonl_byte",
                    )
                )
        inputs.sort(key=lambda source: (source.parent.received_at, source.parent.batch_id))
    run_id = start_run(connection, "clean")
    reports = []
    try:
        # Completed live batches are immutable. Inspect their bytes above, then reuse the
        # committed counts in one query. An interrupted profile enrichment stays pending.
        completed = {}
        if pending_live:
            columns = sql.SQL(",").join(sql.Identifier("c", name) for name in COUNT_COLUMNS)
            rows = connection.execute(
                sql.SQL(
                    "SELECT c.input_id,c.input_sha256,{} FROM clean_batches c "
                    "JOIN vessel_profile_inputs p USING(input_id,processing_version) "
                    "JOIN ingestion_batches b ON b.batch_id=c.raw_batch_id "
                    "WHERE c.status='processed' AND c.processing_version=%s "
                    "AND b.source_id=%s AND b.dataset_id=%s"
                ).format(columns),
                (PROCESSING_VERSION, spec.source_id, spec.dataset_id),
            )
            completed = {(row[0], row[1]): Counts(*row[2:]) for row in rows}
        for source in inputs:
            saved = completed.get((source.input_id, source.content_sha256))
            if saved is not None:
                reports.append((source.input_id, saved, True))
                continue
            register_batch(connection, store, source.parent)
            report, reused = load_batch(
                connection, source, settings.project_path(settings.quarantine_dir)
            )
            from portwatch.profiles import enrich_input

            enrich_input(connection, source)
            reports.append((source.input_id, report, reused))
        finish_run(
            connection,
            run_id,
            input_count=sum(r.input_records for _, r, _ in reports),
            output_count=sum(r.valid_positions for _, r, _ in reports),
        )
    except Exception as error:
        finish_run(connection, run_id, error=error)
        raise
    return reports
