"""Real PostGIS transactional/replay checks; synthetic input only, isolated schemas."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from portwatch.bookkeeping import register_batch
from portwatch.cleaning import CleanInput, Counts, load_batch
from portwatch.positions import PROCESSING_VERSION, identity
from portwatch.storage import BatchSpec, RawStore
from tests.test_bookkeeping import connection as pg_connection
from tests.test_positions import exponent_line, live_line, noaa_row
from tests.test_positions import parent as synthetic_parent

connection = pg_connection
parent = synthetic_parent

pytestmark = pytest.mark.integration


def test_live_poll_reuses_completed_counts_and_repairs_interrupted_profiles(
    connection, tmp_path, monkeypatch
):
    import portwatch.cleaning as cleaning
    from portwatch.settings import Settings

    settings = Settings(raw_dir=tmp_path / "raw", quarantine_dir=tmp_path / "quarantine")
    spec = BatchSpec(
        source_id="synthetic_live",
        dataset_id="synthetic_live_fixture",
        source_url="wss://example.invalid/live",
        mode="live",
        file_suffix=".jsonl",
    )
    batch = RawStore(settings.raw_dir).write([b""], spec)
    monkeypatch.setattr(cleaning, "live_spec", lambda *_: (spec, None))
    first = cleaning.transform_positions(
        settings, connection, "los_angeles", demo=False, pending_live=True
    )
    assert len(first) == 1 and not first[0][2]
    original = cleaning.register_batch
    calls = []

    def register(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(cleaning, "register_batch", register)
    again = cleaning.transform_positions(
        settings, connection, "los_angeles", demo=False, pending_live=True
    )
    assert again[0][1] == first[0][1] and again[0][2] and calls == []
    connection.execute("UPDATE ingestion_batches SET content_sha256=%s", ("0" * 64,))
    with pytest.raises(ValueError, match="registered batch conflicts"):
        cleaning.transform_positions(
            settings, connection, "los_angeles", demo=False, pending_live=True
        )
    connection.execute("UPDATE ingestion_batches SET content_sha256=%s", (batch.content_sha256,))
    connection.execute("DELETE FROM vessel_profile_inputs")
    cleaning.transform_positions(settings, connection, "los_angeles", demo=False, pending_live=True)
    assert calls == [1]
    assert connection.execute("SELECT count(*) FROM vessel_profile_inputs").fetchone() == (1,)


def prepare(connection, tmp_path, parent, unit="national_row"):
    store = RawStore(tmp_path / "raw")
    register_batch(connection, store, parent)
    return CleanInput(
        parent,
        identity([parent.batch_id, unit]),
        parent.content_sha256,
        store.root / parent.local_path,
        unit,
    )


def test_replay_counts_lineage_quarantine_and_stationary(connection, tmp_path, parent):
    source = prepare(connection, tmp_path, parent)
    records = [
        (0, noaa_row(parent)),
        (1, noaa_row(parent, 1)),
        (2, noaa_row(parent, 2, datetime(2024, 1, 1, 0, 1, tzinfo=UTC))),
        (3, noaa_row(parent, 3, latitude=91)),
    ]
    counts, reused = load_batch(connection, source, tmp_path / "quarantine", records=records)
    assert counts == Counts(4, 2, 1, 1, 0) and not reused
    rows = connection.execute(
        "SELECT observation_id,effective_at,source_record_id,quality_flags "
        "FROM vessel_positions ORDER BY observation_id"
    ).fetchall()
    again, reused = load_batch(connection, source, tmp_path / "quarantine", records=records)
    assert again == counts and reused
    assert (
        connection.execute(
            "SELECT observation_id,effective_at,source_record_id,quality_flags "
            "FROM vessel_positions ORDER BY observation_id"
        ).fetchall()
        == rows
    )
    assert connection.execute("SELECT count(*) FROM clean_records").fetchone() == (4,)
    assert connection.execute(
        "SELECT public.ST_SRID(position),public.ST_X(position) FROM vessel_positions LIMIT 1"
    ).fetchone() == (4326, -118.3)
    path, reason = connection.execute(
        "SELECT quarantine_path,reason_code FROM clean_records WHERE outcome='quarantined'"
    ).fetchone()
    saved = tmp_path / "quarantine" / path
    assert reason == "unavailable_position" and json.loads(saved.read_bytes())["latitude"] == 91
    lineage = json.loads(Path(str(saved) + ".lineage.json").read_text())
    assert lineage["record_offset"] == 3 and lineage["processing_version"] == PROCESSING_VERSION
    assert lineage["source_id"] == parent.source_id


def test_failed_transaction_leaves_no_success_or_business_rows(connection, tmp_path, parent):
    source = prepare(connection, tmp_path, parent)

    def fail():
        raise RuntimeError("synthetic injected rollback")

    with pytest.raises(RuntimeError):
        load_batch(
            connection,
            source,
            tmp_path / "quarantine",
            records=[(0, noaa_row(parent))],
            before_commit=fail,
        )
    assert connection.execute("SELECT count(*) FROM vessel_positions").fetchone() == (0,)
    assert connection.execute("SELECT count(*) FROM clean_records").fetchone() == (0,)
    assert connection.execute("SELECT status,error_summary FROM clean_batches").fetchone() == (
        "failed",
        "RuntimeError",
    )
    assert connection.execute("SELECT status FROM ingestion_batches").fetchone() == ("registered",)
    assert load_batch(connection, source, tmp_path / "quarantine", records=[(0, noaa_row(parent))])[
        0
    ] == Counts(1, 1, 0, 0, 0)


def test_live_static_control_and_invalid_reconcile(connection, tmp_path, parent):
    source = prepare(connection, tmp_path, parent, "jsonl_byte")
    records = [
        (0, live_line(parent)),
        (1000, live_line(parent, message_type="ShipStaticData")),
        (2000, live_line(parent, message_type="SubscriptionConfirmation")),
        (3000, b"bad synthetic envelope\n"),
    ]
    counts, _ = load_batch(connection, source, tmp_path / "quarantine", records=records)
    assert counts == Counts(4, 1, 0, 1, 2)
    assert connection.execute("SELECT count(*) FROM vessel_static_messages").fetchone() == (1,)
    assert connection.execute(
        "SELECT observed_at,effective_at=received_at FROM vessel_positions"
    ).fetchone() == (None, True)
    saved = list((tmp_path / "quarantine").rglob("*.bin"))[0]
    assert saved.read_bytes() == records[-1][1]


def test_jump_flag_retains_valid_positions(connection, tmp_path, parent):
    source = prepare(connection, tmp_path, parent)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    records = [
        (0, noaa_row(parent, 0, start)),
        (1, noaa_row(parent, 1, start + timedelta(seconds=1), longitude=-117.3)),
    ]
    counts, _ = load_batch(connection, source, tmp_path / "quarantine", records=records)
    assert counts.valid_positions == 2
    assert connection.execute(
        "SELECT count(*) FROM vessel_positions WHERE 'implausible_jump'=ANY(quality_flags)"
    ).fetchone() == (1,)


def test_cross_batch_duplicate_has_own_lineage(connection, tmp_path, parent):
    source = prepare(connection, tmp_path, parent)
    load_batch(connection, source, tmp_path / "quarantine", records=[(0, noaa_row(parent))])
    spec = BatchSpec.model_validate(
        {name: getattr(parent, name) for name in BatchSpec.model_fields}
    )
    revised = RawStore(tmp_path / "raw").write([b"synthetic revision"], spec)
    second = prepare(connection, tmp_path, revised)
    assert load_batch(
        connection, second, tmp_path / "quarantine", records=[(0, noaa_row(revised))]
    )[0] == Counts(1, 0, 1, 0, 0)
    assert connection.execute("SELECT count(*) FROM vessel_positions").fetchone() == (1,)
    assert connection.execute(
        "SELECT count(DISTINCT raw_batch_id) FROM clean_records"
    ).fetchone() == (2,)


def test_malformed_json_numbers_do_not_roll_back_good_records(connection, tmp_path, parent):
    source = prepare(connection, tmp_path, parent, "jsonl_byte")
    records = [
        (0, live_line(parent)),
        (1000, live_line(parent, Latitude=10**400)),
        (2000, live_line(parent, message_type="ShipStaticData", Sog=float("nan"))),
        (3000, exponent_line(parent)),
    ]
    counts, _ = load_batch(connection, source, tmp_path / "quarantine", records=records)
    assert counts == Counts(4, 1, 0, 3, 0)
    assert connection.execute(
        "SELECT reason_code FROM clean_records WHERE outcome='quarantined' ORDER BY reason_code"
    ).fetchall() == [("invalid_coordinates",), ("invalid_payload_json",), ("invalid_payload_json",)]
