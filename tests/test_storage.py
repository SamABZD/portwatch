"""Synthetic fixtures only: no acquired AIS observations in these tests."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from portwatch.cli import app
from portwatch.storage import BatchSpec, RawStore, quarantine_record

SYNTHETIC = b"synthetic fixture only\nrecord-1\nrecord-2\n"


def test_exact_bytes_checksum_and_revision_no_overwrite(tmp_path: Path, spec: BatchSpec) -> None:
    store = RawStore(tmp_path)
    first = store.write([SYNTHETIC[:8], SYNTHETIC[8:]], spec)
    second = store.write([SYNTHETIC], spec)
    revised = store.write([SYNTHETIC + b"revision\n"], spec)
    assert first == second
    assert first.batch_id != revised.batch_id
    assert first.content_sha256 == hashlib.sha256(SYNTHETIC).hexdigest()
    assert first.byte_count == len(SYNTHETIC)
    assert (tmp_path / first.local_path).read_bytes() == SYNTHETIC
    assert len(store.inspect()) == 2
    assert all(item.state == "complete" for item in store.inspect())


def test_partial_download_is_never_complete(tmp_path: Path, spec: BatchSpec) -> None:
    def interrupted():
        yield SYNTHETIC[:8]
        raise RuntimeError("synthetic interruption")

    store = RawStore(tmp_path)
    with pytest.raises(RuntimeError):
        store.write(interrupted(), spec)
    assert store.inspect() == []


def test_interruption_before_data_rename_is_pending(
    tmp_path: Path, spec: BatchSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    import portwatch.storage as storage

    rename = storage.os.rename

    def interrupt_data(source, destination):
        if Path(destination).suffix == ".csv":
            raise OSError("synthetic interruption before rename")
        return rename(source, destination)

    store = RawStore(tmp_path)
    monkeypatch.setattr(storage.os, "rename", interrupt_data)
    with pytest.raises(OSError):
        store.write([SYNTHETIC], spec)
    assert [item.state for item in store.inspect()] == ["pending"]
    monkeypatch.setattr(storage.os, "rename", rename)
    store.write([SYNTHETIC], spec)
    assert [item.state for item in store.inspect()] == ["complete"]


def test_corruption_detected_and_never_overwritten(tmp_path: Path, spec: BatchSpec) -> None:
    store = RawStore(tmp_path)
    manifest = store.write([SYNTHETIC], spec)
    path = tmp_path / manifest.local_path
    path.write_bytes(b"synthetic corruption")
    with pytest.raises(ValueError, match="refusing overwrite"):
        store.write([SYNTHETIC], spec)
    assert store.inspect()[0].state == "invalid"
    assert path.read_bytes() == b"synthetic corruption"
    result = CliRunner().invoke(app, ["storage", "inspect", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "checksum_mismatch" in result.stdout


def test_no_credentials_in_manifest_or_cli_errors(
    tmp_path: Path, spec: BatchSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = "synthetic_api_secret_do_not_log"
    safe_spec = BatchSpec(
        **{
            **spec.model_dump(),
            "source_url": f"https://user:{private}@example.invalid/synthetic.csv?APIKey={private}#{private}",
        }
    )
    store = RawStore(tmp_path)
    manifest = store.write([SYNTHETIC], safe_spec)
    body = Path(str(tmp_path / manifest.local_path) + ".manifest.json").read_text()
    assert private not in body
    assert manifest.source_url == "https://example.invalid/synthetic.csv"
    with pytest.raises(ValidationError):
        BatchSpec(**spec.model_dump(), APIKey=private)

    def failed_connect(_settings):
        raise OSError(private)

    monkeypatch.setattr("portwatch.cli.connect_db", failed_connect)
    result = CliRunner().invoke(app, ["storage", "reconcile", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert private not in result.output
    assert "OSError" in result.output


def test_live_receipt_hour_and_duplicate_across_hours(tmp_path: Path) -> None:
    spec = BatchSpec(
        source_id="synthetic_live",
        dataset_id="synthetic_step02_live",
        mode="live",
        source_url="wss://example.invalid/stream",
        file_suffix=".jsonl",
        received_at=datetime(2026, 10, 11, 19, tzinfo=UTC),
        source_start_utc=datetime(2024, 1, 1, tzinfo=UTC),
        source_end_utc=datetime(2024, 1, 1, 1, tzinfo=UTC),
    )
    store = RawStore(tmp_path)
    first = store.write([b'{"synthetic":true,"source_time":"2024-01-01T00:00:00Z"}\n'], spec)
    next_hour = BatchSpec(
        **{
            **spec.model_dump(),
            "received_at": datetime(2026, 10, 11, 20, tzinfo=UTC),
        }
    )
    repeated = store.write([(tmp_path / first.local_path).read_bytes()], next_hour)
    assert first == repeated
    assert "/live/2026/10/11/19/" in first.local_path
    assert first.source_start_utc.year == 2024


def test_concurrent_duplicate_writers(tmp_path: Path, spec: BatchSpec) -> None:
    store = RawStore(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.write([SYNTHETIC], spec), range(2)))
    assert results[0] == results[1]
    assert len(store.inspect()) == 1


def test_quarantine_preserves_original_rejected_bytes(tmp_path: Path, spec: BatchSpec) -> None:
    batch = RawStore(tmp_path / "raw").write([SYNTHETIC], spec)
    path = quarantine_record(
        tmp_path / "quarantine",
        b"synthetic invalid record\x00",
        batch_id=batch.batch_id,
        record_offset=42,
        reason="invalid_geometry",
    )
    assert path.read_bytes() == b"synthetic invalid record\x00"
    sidecar = json.loads(Path(str(path) + ".manifest.json").read_text())
    assert sidecar["record_offset"] == 42
    assert sidecar["reason"] == "invalid_geometry"
    assert sidecar["batch_id"] == batch.batch_id


def test_invalid_manifest_or_unmanifested_file_is_not_input(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    (tmp_path / "synthetic.manifest.json").write_text('{"schema_version":99}')
    (tmp_path / "orphan.bin").write_bytes(SYNTHETIC)
    assert {item.state for item in store.inspect()} == {"invalid", "unmanifested"}
