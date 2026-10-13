"""Frozen-demo planning and historical ingestion orchestration."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import yaml

from portwatch.bookkeeping import finish_run, register_batch, start_run
from portwatch.download import acquire
from portwatch.historical import DerivedManifest, ExtractParameters, extract
from portwatch.settings import Settings, for_port, load_port_config
from portwatch.storage import BatchSpec, RawStore

SOURCE_ID = "noaa_ocm_ais_2024_geoparquet"
SOURCE_ROOT = "https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/ais2024/"
MAX_BACKFILL_DAYS = 7


@dataclass(frozen=True)
class HistoricalResult:
    manifest: DerivedManifest
    downloaded: bool
    reused_extract: bool
    elapsed_seconds: float


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise ValueError("dates must use YYYY-MM-DD") from None


def plan(
    settings: Settings, port_id: str, demo: bool, start: str | None, end: str | None
) -> list[tuple[BatchSpec, ExtractParameters]]:
    settings = for_port(settings, port_id)
    port = load_port_config(settings.project_path(settings.port_config))
    if port_id != port.port_id:
        raise ValueError("select a configured port")
    if port.bounding_box is None:
        raise ValueError("collection bounding box is required")
    configured = yaml.safe_load(settings.project_path(settings.demo_config).read_text("utf-8"))
    if configured["port_id"] != port_id or configured["source_id"] != SOURCE_ID:
        raise ValueError("unsupported demo source or port")
    if demo:
        if start is not None or end is not None:
            raise ValueError("--demo cannot be combined with --start or --end")
        start_time = datetime.fromisoformat(configured["start_utc"].replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(configured["end_utc"].replace("Z", "+00:00"))
        if start_time.utcoffset() != timedelta(0) or end_time.utcoffset() != timedelta(0):
            raise ValueError("demo boundaries must explicitly use UTC")
        if start_time.time() != datetime.min.time() or end_time.time() != datetime.min.time():
            raise ValueError("demo boundaries must use UTC midnight")
        first, stop = start_time.date(), end_time.date()
        if (stop - first).days != 1:
            raise ValueError("frozen demo must be exactly one day")
    else:
        if start is None or end is None:
            raise ValueError("use --demo or both --start and --end (exclusive)")
        first, stop = _date(start), _date(end)
    if first < date(2024, 1, 1) or stop > date(2025, 1, 1):
        raise ValueError("only the audited NOAA 2024 daily source is supported")
    if not 1 <= (stop - first).days <= MAX_BACKFILL_DAYS:
        raise ValueError("select 1 to 7 UTC days; --end is exclusive")
    requests = []
    for offset in range((stop - first).days):
        day = first + timedelta(days=offset)
        url = SOURCE_ROOT + f"ais-{day.isoformat()}.parquet"
        if demo and configured["noaa_url"] != url:
            raise ValueError("demo URL must match its selected NOAA day")
        dataset = (
            configured["dataset_id"]
            if demo
            else (f"{port_id}_{day:%Y_%m_%d}_noaa_ocm_geoparquet_v1")
        )
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        spec = BatchSpec(
            source_id=SOURCE_ID,
            dataset_id=dataset,
            source_url=url,
            mode="historical",
            source_date=day,
            source_start_utc=day_start,
            source_end_utc=day_end,
            file_suffix=".parquet",
        )
        parameters = ExtractParameters(
            port_id=port_id,
            bounding_box=port.bounding_box,
            start_utc=day_start,
            end_utc=day_end,
        )
        requests.append((spec, parameters))
    return requests


def ingest_historical(
    settings: Settings,
    connection,
    requests: list[tuple[BatchSpec, ExtractParameters]],
    *,
    refresh: bool = False,
    progress: Callable[[int], None] | None = None,
) -> list[HistoricalResult]:
    store = RawStore(settings.project_path(settings.raw_dir))
    derived_root = settings.project_path(settings.derived_dir)
    run_id = start_run(connection, "historical_ingest")
    results = []
    inspected = selected = 0
    try:
        for spec, parameters in requests:
            started = time.perf_counter()
            transfer = acquire(store, spec, refresh=refresh, progress=progress)
            raw_path = store._path(transfer.manifest.local_path)
            with pq.ParquetFile(raw_path, pre_buffer=False) as source:
                if source.metadata.num_rows == 0:
                    raise ValueError("NOAA daily source contains no rows")
            register_batch(connection, store, transfer.manifest)
            derived, reused = extract(raw_path, transfer.manifest, parameters, derived_root)
            inspected += derived.source_rows_inspected
            selected += derived.rows_selected
            results.append(
                HistoricalResult(
                    manifest=derived,
                    downloaded=transfer.downloaded,
                    reused_extract=reused,
                    elapsed_seconds=time.perf_counter() - started,
                )
            )
        finish_run(connection, run_id, input_count=inspected, output_count=selected)
    except Exception as error:
        finish_run(connection, run_id, input_count=inspected, output_count=selected, error=error)
        raise
    return results


def artifact_path(settings: Settings, manifest: DerivedManifest) -> Path:
    return settings.project_path(settings.derived_dir) / manifest.artifact_path
