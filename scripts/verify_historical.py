"""Verify real demo lineage, optional raw-only rebuild, and a bounded scatter sample."""

import argparse
import json
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely
from matplotlib.patches import Rectangle

from portwatch.download import acquire
from portwatch.historical import DerivedManifest, derivation_identity, extract
from portwatch.ingest import plan
from portwatch.settings import PROJECT_ROOT, Settings
from portwatch.storage import RawStore, checksum_file


def verify(rebuild: bool = False) -> dict:
    settings = Settings()
    spec, parameters = plan(settings, "los_angeles", True, None, None)[0]
    store = RawStore(settings.project_path(settings.raw_dir))
    parents = [item.manifest for item in store.inspect() if item.state == "complete"
               and (item.manifest.source_id, item.manifest.dataset_id,
                    item.manifest.source_url, item.manifest.source_date)
               == (spec.source_id, spec.dataset_id, spec.source_url, spec.source_date)]
    if not parents:
        raise ValueError("no completed demo raw source")
    # The matching cache exists, so default acquisition performs no HTTP. Honor the
    # current-source pointer even when a provider rolls back to older identical bytes.
    parent = acquire(store, spec).manifest
    identity = derivation_identity(parent, parameters)
    root = settings.project_path(settings.derived_dir)
    path = root / parent.source_id / parent.dataset_id / parameters.port_id / identity
    report = DerivedManifest.model_validate_json(
        (path / "positions.parquet.manifest.json").read_text("utf-8")
    )
    artifact = path / "positions.parquet"
    assert checksum_file(artifact) == (report.content_sha256, report.byte_count)
    assert report.parent_raw_sha256 == parent.content_sha256
    west, south, east, north = parameters.bounding_box
    total = 0
    vessels = set()
    earliest = latest = None
    previous_offset = -1
    non_minute = 0
    non_second = 0
    stride = max(1, math.ceil(report.rows_selected / 5000))
    sample_longitude, sample_latitude = [], []
    with pq.ParquetFile(artifact) as source:
        for batch in source.iter_batches(batch_size=65_536, use_threads=False):
            longitude = batch.column("longitude").to_numpy()
            latitude = batch.column("latitude").to_numpy()
            assert np.all(np.isfinite(longitude) & np.isfinite(latitude))
            assert np.all((longitude >= west) & (longitude <= east))
            assert np.all((latitude >= south) & (latitude <= north))
            points = shapely.from_wkb(batch.column("geometry").to_pylist())
            assert np.array_equal(longitude, shapely.get_x(points))
            assert np.array_equal(latitude, shapely.get_y(points))
            times = batch.column("observed_at_utc")
            assert times.type.tz == "UTC" and times.null_count == 0
            assert pc.all(pc.greater_equal(times, parameters.start_utc)).as_py()
            assert pc.all(pc.less(times, parameters.end_utc)).as_py()
            extent = pc.min_max(times).as_py()
            earliest = extent["min"] if earliest is None else min(earliest, extent["min"])
            latest = extent["max"] if latest is None else max(latest, extent["max"])
            milliseconds = pc.cast(times, "int64").to_numpy()
            non_minute += int(np.count_nonzero(milliseconds % 60000))
            non_second += int(np.count_nonzero(milliseconds % 1000))
            for field, expected in (
                ("source_batch_id", parent.batch_id), ("dataset_id", parent.dataset_id),
                ("source_id", parent.source_id), ("derivation_id", identity),
            ):
                assert pc.all(pc.equal(batch.column(field), expected)).as_py()
            offsets = batch.column("source_row_offset").to_numpy()
            assert np.all(np.diff(offsets.astype(np.int64)) > 0)
            assert offsets[0] > previous_offset
            previous_offset = int(offsets[-1])
            assert previous_offset < report.source_rows_inspected
            vessels.update(value for value in batch.column("mmsi").to_pylist() if value is not None)
            selected = np.arange(total, total + batch.num_rows) % stride == 0
            sample_longitude.extend(longitude[selected].tolist())
            sample_latitude.extend(latitude[selected].tolist())
            total += batch.num_rows
    assert total == report.rows_selected > 0
    assert len(vessels) == report.distinct_vessels
    assert earliest == report.earliest_utc and latest == report.latest_utc
    result = {
        "verified_rows": total, "distinct_vessels": len(vessels),
        "earliest_utc": earliest.isoformat(), "latest_utc": latest.isoformat(),
        "not_on_whole_minute": non_minute, "scatter_sample_rows": len(sample_longitude),
        "not_on_whole_second": non_second,
        "artifact_sha256": report.content_sha256, "raw_sha256": parent.content_sha256,
        "rebuild_matching_checksum": None,
    }
    if rebuild:
        rebuilt, _ = extract(
            store._path(parent.local_path), parent, parameters,
            PROJECT_ROOT / "artifacts/step03-rebuild",
        )
        assert rebuilt.content_sha256 == report.content_sha256
        assert rebuilt.rows_selected == report.rows_selected
        result["rebuild_matching_checksum"] = True
    output = PROJECT_ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 8), dpi=150)
    ax.scatter(sample_longitude, sample_latitude, s=5, color="#195681", alpha=0.35,
               linewidths=0, label=f"NOAA positions ({len(sample_longitude):,} sampled)")
    ax.add_patch(Rectangle((west, south), east-west, north-south, fill=False,
                          edgecolor="#ba6c28", linestyle="--", linewidth=1.5))
    for label, longitude, latitude in (
        ("Pier 400 reference", -118.24131, 33.73519),
        ("Outer harbor reference", -118.24239, 33.71466),
        ("Long Beach Pier F reference", -118.2111, 33.7515),
    ):
        ax.scatter([longitude], [latitude], marker="x", color="#76332b", s=45)
        ax.annotate(label, (longitude, latitude), xytext=(5, 7), textcoords="offset points",
                    fontsize=8)
    ax.set_xlim(west-0.01, -118.19)
    ax.set_ylim(south-0.01, north+0.01)
    ax.set_aspect(1/np.cos(np.deg2rad((south+north)/2)))
    ax.set_xlabel("Longitude (WGS84 degrees)")
    ax.set_ylabel("Latitude (WGS84 degrees)")
    ax.set_title("Port of Los Angeles collection area\nNOAA AIS, January 1, 2024 UTC")
    ax.grid(alpha=0.2)
    ax.legend(loc="lower left", fontsize=8)
    fig.text(
        0.5, 0.015, "Dashed box: heuristic filter. Reference points from source audit. "
        "No basemap or official port boundary.", ha="center", fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(output / "step03-los-angeles.png")
    plt.close(fig)
    (output / "step03-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    print(json.dumps(verify(parser.parse_args().rebuild), indent=2))
