"""NOAA daily GeoParquet extraction in bounded record batches."""

import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import psutil
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from portwatch.storage import (
    SHA256,
    SLUG,
    Manifest,
    _publication_lock,
    _publish_json,
    _sync_directory,
    checksum_file,
)

EXTRACTION_VERSION = "noaa-la-subset-v2"
BATCH_ROWS = 65_536
MAX_DISTINCT_VESSELS = 50_000
RUNTIME_VERSIONS = {
    "pyarrow": pa.__version__, "shapely": shapely.__version__, "numpy": np.__version__,
}


class ExtractParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    port_id: str = Field(pattern=SLUG)
    bounding_box: tuple[float, float, float, float]
    start_utc: datetime
    end_utc: datetime
    batch_rows: int = Field(default=BATCH_ROWS, gt=0, le=BATCH_ROWS)
    code_version: str = EXTRACTION_VERSION

    @field_validator("start_utc", "end_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("UTC timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def valid_range(self):
        west, south, east, north = self.bounding_box
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("invalid collection bounds")
        if self.end_utc <= self.start_utc:
            raise ValueError("empty or reversed extraction time window")
        if self.code_version != EXTRACTION_VERSION:
            raise ValueError("unsupported extraction version")
        return self


def derivation_identity(parent: Manifest, parameters: ExtractParameters) -> str:
    body = {
        "parent_batch_id": parent.batch_id, "parent_raw_sha256": parent.content_sha256,
        "parameters": parameters.model_dump(mode="json"), "runtime_versions": RUNTIME_VERSIONS,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class DerivedManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    derivation_id: str = Field(pattern=SHA256)
    parent_batch_id: str = Field(pattern=SHA256)
    parent_raw_sha256: str = Field(pattern=SHA256)
    parent_raw_path: str
    source_id: str = Field(pattern=SLUG)
    dataset_id: str = Field(pattern=SLUG)
    parameters: ExtractParameters
    runtime_versions: dict[str, str]
    artifact_path: str
    content_sha256: str = Field(pattern=SHA256)
    byte_count: int = Field(ge=0)
    source_rows_inspected: int = Field(ge=0)
    region_rows: int = Field(ge=0)
    unusable_coordinate_rows: int = Field(ge=0)
    missing_timestamp_rows: int = Field(ge=0)
    rows_selected: int = Field(ge=0)
    distinct_vessels: int = Field(ge=0)
    earliest_utc: datetime | None
    latest_utc: datetime | None
    source_earliest_utc: datetime | None
    source_latest_utc: datetime | None
    preview: list[dict]
    max_batch_rows_observed: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    peak_process_memory_bytes: int | None
    memory_measurement: str
    created_at: datetime


def peak_process_memory() -> tuple[int | None, str]:
    if os.name == "nt":
        info = psutil.Process().memory_info()
        if hasattr(info, "peak_wset"):
            return info.peak_wset, "Windows lifetime peak working set (bytes)"
    try:
        import resource
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        value = int(peak if sys.platform == "darwin" else peak * 1024)
        return value, "OS lifetime peak RSS (bytes)"
    except ImportError:
        return None, "unavailable; batch size remains bounded"


def _schema(source: pq.ParquetFile, parent: Manifest, identity: str) -> pa.Schema:
    required = {"mmsi", "base_date_time", "sog", "cog", "heading", "geometry"}
    if not required.issubset(source.schema_arrow.names):
        raise ValueError("NOAA required position columns are missing")
    if not pa.types.is_timestamp(source.schema_arrow.field("base_date_time").type):
        raise ValueError("NOAA source timestamp must use a Parquet timestamp type")
    metadata = source.schema_arrow.metadata or {}
    geo = json.loads(metadata.get(b"geo", b"{}"))
    geometry = geo.get("columns", {}).get("geometry", {})
    # GeoParquet defaults to WGS84 when the CRS key is absent; explicit null means unknown.
    crs = geometry.get("crs")
    crs_id = crs.get("id", {}) if isinstance(crs, dict) else {}
    supported_crs = "crs" not in geometry or (
        crs_id.get("authority"), str(crs_id.get("code"))
    ) == ("EPSG", "4326")
    if geometry.get("encoding") != "WKB" or not supported_crs:
        raise ValueError("unsupported geometry encoding or CRS")
    geometry.pop("bbox", None)  # National extent must not describe a regional subset.
    geometry.pop("covering", None)
    fields = [
        pa.field("longitude", pa.float64()), pa.field("latitude", pa.float64()),
        pa.field("observed_at_utc", pa.timestamp("ms", tz="UTC")),
        pa.field("source_row_offset", pa.uint64()), pa.field("source_batch_id", pa.string()),
        pa.field("source_id", pa.string()), pa.field("dataset_id", pa.string()),
        pa.field("derivation_id", pa.string()), pa.field("source_time_quality", pa.string()),
    ]
    if any(field.name in source.schema_arrow.names for field in fields):
        raise ValueError("source columns conflict with lineage fields")
    schema = pa.schema(list(source.schema_arrow) + fields)
    return schema.with_metadata({
        b"geo": json.dumps(geo).encode(), b"parent_raw_sha256": parent.content_sha256.encode(),
        b"derivation_id": identity.encode(),
    })


def _extent(timestamps):
    extent = pc.min_max(timestamps).as_py()
    return extent["min"], extent["max"]


def extract(
    raw_path: Path,
    parent: Manifest,
    parameters: ExtractParameters,
    derived_root: Path,
) -> tuple[DerivedManifest, bool]:
    """Return metadata and reuse flag. No national dataframe or full table is created."""
    started = time.perf_counter()
    if checksum_file(raw_path) != (parent.content_sha256, parent.byte_count):
        raise ValueError("parent raw checksum or size mismatch")
    identity = derivation_identity(parent, parameters)
    derived_root = derived_root.resolve()
    directory = derived_root / parent.source_id / parent.dataset_id / parameters.port_id / identity
    directory.mkdir(parents=True, exist_ok=True)
    artifact = directory / "positions.parquet"
    sidecar = directory / "positions.parquet.manifest.json"
    if artifact.exists() and sidecar.exists():
        saved = DerivedManifest.model_validate_json(sidecar.read_text("utf-8"))
        if saved.derivation_id != identity or saved.parameters != parameters:
            raise ValueError("existing derivation manifest identity mismatch")
        if checksum_file(artifact) != (saved.content_sha256, saved.byte_count):
            raise ValueError("existing regional artifact is corrupt")
        return saved, True
    if artifact.exists():
        raise ValueError("regional artifact has no recovery manifest")
    source = pq.ParquetFile(raw_path, pre_buffer=False)
    total_rows = source.metadata.num_rows
    try:
        schema = _schema(source, parent, identity)
    except BaseException:
        source.close()
        raise
    handle, name = tempfile.mkstemp(dir=directory, suffix=".part")
    os.close(handle)
    temporary = Path(name)
    rows = selected_count = region_count = bad_coordinates = missing_times = max_batch = 0
    vessels = set()
    earliest = latest = source_earliest = source_latest = None
    preview = []
    west, south, east, north = parameters.bounding_box
    try:
        with pq.ParquetWriter(temporary, schema, compression="snappy", version="2.6") as writer:
            for batch in source.iter_batches(batch_size=parameters.batch_rows, use_threads=False):
                offset = rows
                rows += batch.num_rows
                max_batch = max(max_batch, batch.num_rows)
                table = pa.Table.from_batches([batch])
                points = shapely.from_wkb(table["geometry"].to_pylist(), on_invalid="ignore")
                longitude, latitude = shapely.get_x(points), shapely.get_y(points)
                valid = (
                    (shapely.get_type_id(points) == 0) & np.isfinite(longitude)
                    & np.isfinite(latitude) & (longitude >= -180) & (longitude <= 180)
                    & (latitude >= -90) & (latitude <= 90)
                )
                bad_coordinates += int(np.count_nonzero(~valid))
                # Named latitude/longitude fields are decoded before this collection filter.
                region = valid & (longitude >= west) & (longitude <= east)
                region &= (latitude >= south) & (latitude <= north)
                region_count += int(np.count_nonzero(region))
                times = pc.cast(table["base_date_time"], pa.timestamp("ms", tz="UTC"))
                missing_times += times.null_count
                low, high = _extent(times)
                if low is not None:
                    source_earliest = low if source_earliest is None else min(source_earliest, low)
                    source_latest = high if source_latest is None else max(source_latest, high)
                time_mask = pc.fill_null(pc.and_(
                    pc.greater_equal(times, pa.scalar(parameters.start_utc, type=times.type)),
                    pc.less(times, pa.scalar(parameters.end_utc, type=times.type)),
                ), False).to_numpy(zero_copy_only=False)
                selected = region & time_mask
                regional = table.filter(pa.array(selected))
                count = regional.num_rows
                regional_times = times.filter(pa.array(selected))
                added = [
                    pa.array(longitude[selected]), pa.array(latitude[selected]), regional_times,
                    pa.array(np.arange(offset, rows, dtype=np.uint64)[selected]),
                    pa.array([parent.batch_id] * count, type=pa.string()),
                    pa.array([parent.source_id] * count, type=pa.string()),
                    pa.array([parent.dataset_id] * count, type=pa.string()),
                    pa.array([identity] * count, type=pa.string()),
                    pa.array(["provider_documented_utc"] * count, type=pa.string()),
                ]
                output = pa.Table.from_arrays(list(regional.columns) + added, schema=schema)
                if count:
                    writer.write_table(output, row_group_size=parameters.batch_rows)
                    selected_count += count
                    vessels.update(
                        item for item in regional["mmsi"].to_pylist() if item is not None
                    )
                    if len(vessels) > MAX_DISTINCT_VESSELS:
                        raise ValueError("regional vessel cardinality exceeds memory safety limit")
                    low, high = _extent(regional_times)
                    earliest = low if earliest is None else min(earliest, low)
                    latest = high if latest is None else max(latest, high)
                    for index in range(min(count, 5 - len(preview))):
                        preview.append({
                            "timestamp_utc": regional_times[index].as_py().isoformat(),
                            "longitude": float(longitude[selected][index]),
                            "latitude": float(latitude[selected][index]),
                        })
        source.close()
        if rows != total_rows:
            raise ValueError("not all source rows were inspected")
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        checksum, size = checksum_file(temporary)
        peak, measurement = peak_process_memory()
        manifest = DerivedManifest(
            derivation_id=identity, parent_batch_id=parent.batch_id,
            parent_raw_sha256=parent.content_sha256, parent_raw_path=parent.local_path,
            source_id=parent.source_id, dataset_id=parent.dataset_id, parameters=parameters,
            runtime_versions=RUNTIME_VERSIONS,
            artifact_path=artifact.relative_to(derived_root).as_posix(),
            content_sha256=checksum, byte_count=size, source_rows_inspected=rows,
            region_rows=region_count, unusable_coordinate_rows=bad_coordinates,
            missing_timestamp_rows=missing_times, rows_selected=selected_count,
            distinct_vessels=len(vessels), earliest_utc=earliest, latest_utc=latest,
            source_earliest_utc=source_earliest, source_latest_utc=source_latest, preview=preview,
            max_batch_rows_observed=max_batch, elapsed_seconds=time.perf_counter() - started,
            peak_process_memory_bytes=peak, memory_measurement=measurement,
            created_at=datetime.now(UTC),
        )
        with _publication_lock(directory):
            if artifact.exists():
                saved = DerivedManifest.model_validate_json(sidecar.read_text("utf-8"))
                if checksum_file(artifact) != (saved.content_sha256, saved.byte_count):
                    raise ValueError("concurrent regional artifact is corrupt")
                return saved, True
            if sidecar.exists():
                saved = DerivedManifest.model_validate_json(sidecar.read_text("utf-8"))
                if saved.content_sha256 != checksum or saved.derivation_id != identity:
                    raise ValueError("pending regional derivation content mismatch")
                manifest = saved
            else:
                _publish_json(sidecar, manifest.model_dump_json(indent=2))
            os.rename(temporary, artifact)
            _sync_directory(directory)
        return manifest, False
    finally:
        source.close()
        temporary.unlink(missing_ok=True)
