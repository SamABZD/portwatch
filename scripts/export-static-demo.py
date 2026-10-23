"""Build public static responses from checksum-verified, private prepared exports.

Run from the repository root. The destination must be new; never publish raw backups.
"""

import argparse
import gzip
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from portwatch.demo.replay import load_prepared, stamp

PORTS = {"los_angeles", "oakland"}
STALE = (5, 15, 30, 60)
LIMIT = 25 * 1024 * 1024


def write_json(root, relative, value):
    path = root / relative
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if relative.endswith(".gz"):
        payload = gzip.compress(payload, compresslevel=6, mtime=0)
    if len(payload) > LIMIT:
        raise ValueError(f"static_asset_too_large: {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
    return len(payload), hashlib.sha256(payload).hexdigest()


def pack_frames(frames):
    base = frames[15]
    vessel_index = {v["mmsi"]: i for i, v in enumerate(base["vessels"])}
    variants = {}
    for stale, frame in frames.items():
        references = []
        for vessel in frame["vessels"]:
            index = vessel_index[vessel["mmsi"]]
            original = base["vessels"][index]
            changes = {key: value for key, value in vessel.items() if value != original[key]}
            references.append(dict(index=index, changes=changes))
        extra = {
            key: value
            for key, value in frame.items()
            if key not in {"vessels", "trails", "metrics", "staleness_minutes"}
            and value != base[key]
        }
        reconstructed = dict(
            base,
            **extra,
            staleness_minutes=stale,
            metrics=frame["metrics"],
            vessels=[dict(base["vessels"][ref["index"]], **ref["changes"]) for ref in references],
            trails=[
                dict(v, stale=w["stale"])
                for v, w in zip(base["trails"], frame["trails"], strict=True)
            ],
        )
        if reconstructed != frame:
            raise ValueError("frame_pack_would_change_data")
        variants[str(stale)] = dict(
            metrics=frame["metrics"],
            vessels=references,
            extra=extra,
            trail_stale=[v["stale"] for v in frame["trails"]],
        )
    return dict(base=base, variants=variants)


def frame_batch(arguments):
    snapshot, output, first, last = arguments
    dataset = load_prepared(snapshot)
    port = dataset.metadata["port_id"]
    start = stamp(dataset.metadata["start_utc"])
    result = {}
    for minute in range(first, last + 1, 5):
        relative = f"{port}/frames/{minute}.json.gz"
        frames = {
            stale: dataset.frame(start + timedelta(minutes=minute), stale, 30) for stale in STALE
        }
        result[relative] = write_json(Path(output), relative, pack_frames(frames))
    return result


def export_static(snapshots, output, workers=4):
    output = Path(output)
    if output.exists():
        raise ValueError("choose_new_static_output")
    datasets = [load_prepared(path) for path in snapshots]
    ids = [item.metadata.get("port_id") for item in datasets]
    if not ids or len(set(ids)) != len(ids) or not set(ids) <= PORTS:
        raise ValueError("unique_known_ports_required")
    created = datetime.now(UTC).isoformat()
    entries, reports, hashes = [], [], {}
    total_bytes = 0

    def save(relative, value):
        nonlocal total_bytes
        size, digest = write_json(output, relative, value)
        hashes[relative] = digest
        total_bytes += size

    for dataset in datasets:
        meta = dataset.metadata
        port = meta["port_id"]
        start = stamp(meta["start_utc"])
        if stamp(meta["end_utc"]) - start != timedelta(days=1):
            raise ValueError("static_demo_requires_one_day_snapshot")
        entries.append(
            dict(
                port_id=port,
                name=meta["port_name"],
                dataset=dict(meta, available=True, zones=dataset.zones),
            )
        )
        health = dict(
            overall="prepared_snapshot",
            checked_at_utc=created,
            database=dict(status="not_used"),
            collector=dict(status="unavailable_in_prepared_mode"),
            historical=dict(
                status="prepared_historical", canonical_positions=meta.get("observation_count")
            ),
            live=dict(status="unavailable_in_prepared_mode"),
            context=None,
            issues=["Historical snapshot only; no current pipeline health is connected."],
        )
        save(f"{port}/health.json", health)
        reports.append(
            dict(health, port_id=port, name=meta["port_name"], runs=None, operations=None)
        )
    jobs = [
        (str(path), str(output), first, last)
        for path in snapshots
        for first, last in [(0, 360), (365, 720), (725, 1080), (1085, 1440)]
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(frame_batch, jobs):
            for relative, (size, digest) in result.items():
                hashes[relative] = digest
                total_bytes += size
            print(f"Completed {len(hashes)} snapshot assets", flush=True)
    save(
        "pipeline.json",
        dict(
            ports=reports,
            repository="https://github.com/SamABZD/portwatch",
            semantics="Archived historical snapshot; no running collectors or database",
        ),
    )
    save(
        "manifest.json",
        dict(
            format="portwatch-static-v2",
            created_at_utc=created,
            ports=entries,
            attribution=dict(
                ais="NOAA OCM / MarineCadastre historical AIS",
                weather="Open-Meteo / ERA5 retrospective reanalysis",
                zones="PortWatch analytical approximations, not official boundaries",
            ),
        ),
    )
    # Keep headroom for Next.js, map and framework assets in the complete upload.
    if len(hashes) > 10000:
        raise ValueError("static_snapshot_file_budget_exceeded")
    save("checksums.json", hashes.copy())
    return dict(files=len(hashes), bytes=total_bytes, ports=ids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="append", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    print(json.dumps(export_static(arguments.snapshot, arguments.output)))
