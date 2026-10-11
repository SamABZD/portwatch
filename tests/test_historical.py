"""Synthetic position fixtures and local HTTP only; never acquired NOAA rows."""

import json
import struct
import threading
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from portwatch.download import DownloadError, acquire
from portwatch.historical import ExtractParameters, _schema, extract
from portwatch.ingest import plan
from portwatch.settings import Settings
from portwatch.storage import BatchSpec, RawStore


@pytest.fixture
def source(tmp_path):
    def point(longitude, latitude):
        return struct.pack("<BIdd", 1, 1, longitude, latitude)

    table = pa.table({
        "mmsi": pa.array([111, 222, 333, 444, 555, 666, 777, 888], type=pa.int32()),
        "base_date_time": pa.array([
            datetime(2024, 1, 1, 1), datetime(2024, 1, 1, 2), datetime(2024, 1, 1, 3),
            datetime(2024, 1, 1, 4), datetime(2024, 1, 1, 5), datetime(2024, 1, 1, 6),
            datetime(2024, 1, 2), None,
        ], type=pa.timestamp("ms")),
        "sog": pa.array([1.0] * 8, type=pa.float32()),
        "cog": pa.array([0.0] * 8, type=pa.float32()),
        "heading": pa.array([0] * 8, type=pa.int32()),
        "geometry": pa.array([
            point(-118.25, 33.73), point(-118.30, 33.70), point(-118.20, 33.75), None,
            point(300, 33.7), b"bad", point(-118.25, 33.73), point(-118.25, 33.73),
        ], type=pa.binary()),
    }).replace_schema_metadata({b"geo": json.dumps({
        "version": "1.0.0", "primary_column": "geometry",
        "columns": {"geometry": {"encoding": "WKB", "geometry_types": ["Point"]}},
    }).encode()})
    path = tmp_path / "synthetic.parquet"
    pq.write_table(table, path, row_group_size=3)
    spec = BatchSpec(
        source_id="synthetic_fixture", dataset_id="synthetic_step03",
        source_url="https://example.invalid/synthetic.parquet", mode="historical",
        source_date=date(2024, 1, 1), file_suffix=".parquet",
    )
    store = RawStore(tmp_path / "raw")
    parent = store.write([path.read_bytes()], spec)
    parameters = ExtractParameters(
        port_id="los_angeles", bounding_box=(-118.34, 33.66, -118.225, 33.80),
        start_utc=datetime(2024, 1, 1, tzinfo=UTC), end_utc=datetime(2024, 1, 2, tzinfo=UTC),
        batch_rows=2,
    )
    return store, parent, parameters


def test_bounded_filter_coordinates_time_and_lineage(tmp_path, source):
    store, parent, parameters = source
    report, reused = extract(
        store._path(parent.local_path), parent, parameters, tmp_path / "derived"
    )
    assert not reused
    assert report.source_rows_inspected == 8
    assert report.unusable_coordinate_rows == 3
    assert report.region_rows == 4
    assert report.missing_timestamp_rows == 1
    assert report.rows_selected == 2 and report.distinct_vessels == 2
    assert report.max_batch_rows_observed == 2
    assert report.earliest_utc == datetime(2024, 1, 1, 1, tzinfo=UTC)
    assert report.latest_utc == datetime(2024, 1, 1, 2, tzinfo=UTC)
    output = pq.read_table(tmp_path / "derived" / report.artifact_path)
    assert output["source_row_offset"].to_pylist() == [0, 1]
    assert output["source_batch_id"].to_pylist() == [parent.batch_id] * 2
    assert output["observed_at_utc"].type.tz == "UTC"
    assert output["source_time_quality"].to_pylist() == ["provider_documented_utc"] * 2
    assert "mmsi" not in report.preview[0]


def test_rerun_reuses_and_changed_parameters_create_new_identity(tmp_path, source):
    store, parent, parameters = source
    root = tmp_path / "derived"
    first, _ = extract(store._path(parent.local_path), parent, parameters, root)
    repeated, reused = extract(store._path(parent.local_path), parent, parameters, root)
    assert reused and repeated == first
    changed = ExtractParameters(**{
        **parameters.model_dump(), "bounding_box": (-118.27, 33.66, -118.225, 33.80),
    })
    other, reused = extract(store._path(parent.local_path), parent, changed, root)
    assert not reused and other.derivation_id != first.derivation_id
    assert other.rows_selected == 1
    rebuilt, _ = extract(store._path(parent.local_path), parent, parameters, tmp_path / "rebuild")
    assert rebuilt.content_sha256 == first.content_sha256


def test_corrupt_derived_artifact_is_not_reused(tmp_path, source):
    store, parent, parameters = source
    root = tmp_path / "derived"
    report, _ = extract(store._path(parent.local_path), parent, parameters, root)
    (root / report.artifact_path).write_bytes(b"synthetic corruption")
    with pytest.raises(ValueError, match="corrupt"):
        extract(store._path(parent.local_path), parent, parameters, root)


def test_unknown_crs_rejected_instead_of_assuming_degrees(tmp_path, source):
    store, parent, _ = source
    table = pq.read_table(store._path(parent.local_path))
    geo = json.loads(table.schema.metadata[b"geo"])
    geo["columns"]["geometry"]["crs"] = None
    table = table.replace_schema_metadata({b"geo": json.dumps(geo).encode()})
    invalid = tmp_path / "synthetic_unknown_crs.parquet"
    pq.write_table(table, invalid)
    with pq.ParquetFile(invalid) as reader, pytest.raises(ValueError, match="CRS"):
        _schema(reader, parent, "0"*64)


@pytest.fixture
def server():
    state = {"body": b"synthetic download bytes", "etag": '"synthetic-v1"', "get_count": 0,
             "status": 200, "truncate": False, "precondition_fail_once": False}

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(state["body"])))
            self.send_header("ETag", state["etag"])
            self.end_headers()

        def do_GET(self):
            state["get_count"] += 1
            if state["precondition_fail_once"]:
                state["precondition_fail_once"] = False
                state["etag"] = '"synthetic-revised"'
                self.send_response(412)
                self.end_headers()
                return
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(state["body"])))
            self.send_header("ETag", state["etag"])
            self.end_headers()
            self.wfile.write(state["body"][:4] if state["truncate"] else state["body"])

        def log_message(self, *_args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}/synthetic.parquet", state
    finally:
        http.shutdown()
        http.server_close()
        worker.join()


def download_spec(url):
    return BatchSpec(
        source_id="synthetic_http", dataset_id="synthetic_step03_http", source_url=url,
        mode="historical", source_date=date(2024, 1, 1), file_suffix=".parquet",
    )


def test_download_reuse_refresh_and_revision(tmp_path, server):
    url, state = server
    store = RawStore(tmp_path)
    spec = download_spec(url)
    first = acquire(store, spec)
    assert first.downloaded
    assert acquire(store, spec).manifest == first.manifest
    assert not acquire(store, spec, refresh=True).downloaded
    assert state["get_count"] == 1
    state["etag"] = '"synthetic-v2"'
    state["body"] += b"revision"
    second = acquire(store, spec, refresh=True)
    assert second.downloaded and second.manifest.batch_id != first.manifest.batch_id
    assert (tmp_path / first.manifest.local_path).read_bytes() == b"synthetic download bytes"


def test_partial_transfer_never_published_and_retries_bounded(tmp_path, server, monkeypatch):
    url, state = server
    monkeypatch.setattr("portwatch.download.time.sleep", lambda _: None)
    state["truncate"] = True
    store = RawStore(tmp_path)
    with pytest.raises(DownloadError, match="after_3_attempts"):
        acquire(store, download_spec(url))
    assert state["get_count"] == 3
    assert store.inspect() == []
    assert list(tmp_path.glob("*.part")) == []


def test_provider_rollback_keeps_current_selection_without_rewriting_receipts(tmp_path, server):
    url, state = server
    original = state["body"]
    store = RawStore(tmp_path)
    spec = download_spec(url)
    first = acquire(store, spec)
    state["body"] += b"revision"
    state["etag"] = '"synthetic-v2"'
    acquire(store, spec, refresh=True)
    state["body"] = original
    state["etag"] = '"synthetic-v3-same-bytes-as-v1"'
    reverted = acquire(store, spec, refresh=True)
    assert reverted.manifest == first.manifest
    assert acquire(store, spec).manifest == first.manifest
    assert not acquire(store, spec, refresh=True).downloaded
    assert state["get_count"] == 3
    assert len(store.inspect()) == 2


def test_if_match_failure_retries_fresh_head(tmp_path, server, monkeypatch):
    url, state = server
    state["precondition_fail_once"] = True
    monkeypatch.setattr("portwatch.download.time.sleep", lambda _: None)
    result = acquire(RawStore(tmp_path), download_spec(url))
    assert result.downloaded and state["get_count"] == 2
    assert result.manifest.remote_etag == '"synthetic-revised"'


def test_http_failure_and_size_ceiling_do_not_publish(tmp_path, server):
    url, state = server
    store = RawStore(tmp_path)
    with pytest.raises(DownloadError, match="size_limit"):
        acquire(store, download_spec(url), max_bytes=4)
    assert state["get_count"] == 0
    state["status"] = 404
    with pytest.raises(DownloadError, match="http_404"):
        acquire(store, download_spec(url))
    assert store.inspect() == []


@pytest.mark.parametrize(("demo", "start", "end"), [
    (True, "2024-01-01", None), (False, None, None),
    (False, "2023-12-31", "2024-01-01"), (False, "2024-01-01", "2024-01-09"),
    (False, "2024-01-02", "2024-01-01"), (False, "bad", "2024-01-02"),
])
def test_unsupported_backfill_rejected_before_network(demo, start, end):
    with pytest.raises(ValueError):
        plan(Settings(), "los_angeles", demo, start, end)


def test_demo_and_explicit_day_have_same_source_and_dataset():
    demo = plan(Settings(), "los_angeles", True, None, None)[0]
    explicit = plan(Settings(), "los_angeles", False, "2024-01-01", "2024-01-02")[0]
    assert demo[0].dataset_id == explicit[0].dataset_id
    assert demo[0].source_url == explicit[0].source_url
    assert demo[1] == explicit[1]
