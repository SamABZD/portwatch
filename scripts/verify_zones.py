"""Independently check every real demo membership and render approximate zone coverage."""

import argparse
import hashlib
import json
import math
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import matplotlib
import numpy as np
import shapely

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as PlotPolygon
from matplotlib.patches import Rectangle
from verify_positions import business_hash

from portwatch.db import connect_db
from portwatch.settings import Settings, load_port_config
from portwatch.zones import (
    ASSIGNMENT_VERSION,
    PRIORITIES,
    SCOPE_FILTER,
    configured_geofence,
    demo_scope,
    transform_zones,
)


def inspect_memberships(connection, geofence, scope):
    doc = geofence.document
    expected_polygons = [shapely.geometry.shape(f.geometry) for f in doc.features]
    ids = [f.properties.zone_id for f in doc.features]
    priority = {f.properties.zone_id: PRIORITIES[f.properties.zone_type] for f in doc.features}
    digest = hashlib.sha256()
    mismatches = checked = 0
    sample = []
    # Evenly sample the time-ordered observations; never load all business rows into RAM.
    count = connection.execute(
        "SELECT count(*) FROM vessel_positions p WHERE " + SCOPE_FILTER, scope.parameters
    ).fetchone()[0]
    stride = max(1, math.ceil(count / 5000))
    with connection.transaction(), connection.cursor(name="zone_verify") as cursor:
        cursor.execute(
            """SELECT p.observation_id,p.longitude,p.latitude,a.state,a.primary_zone_id,
                a.membership_count,ARRAY(SELECT m.zone_id FROM position_zone_memberships m
                    WHERE m.position_id=p.id AND m.port_id=%s AND m.geofence_version=%s
                    AND m.assignment_version=%s ORDER BY m.zone_id)
            FROM vessel_positions p LEFT JOIN position_zone_assignments a ON a.position_id=p.id
                AND a.port_id=%s AND a.geofence_version=%s AND a.assignment_version=%s
            WHERE """
            + SCOPE_FILTER
            + " ORDER BY p.effective_at,p.observation_id",
            (doc.port_id, doc.version, ASSIGNMENT_VERSION) * 2 + scope.parameters,
        )
        while rows := cursor.fetchmany(2048):
            points = shapely.points(np.array([(row[1], row[2]) for row in rows]))
            covered = [shapely.covers(polygon, points) for polygon in expected_polygons]
            for i, row in enumerate(rows):
                observation, lon, lat, state, primary, membership_count, members = row
                expected = sorted(ids[j] for j, mask in enumerate(covered) if mask[i])
                chosen = (
                    min(expected, key=lambda zone_id: (priority[zone_id], zone_id))
                    if expected
                    else None
                )
                valid = (
                    members == expected
                    and membership_count == len(expected)
                    and state == ("inside" if expected else "outside")
                    and primary == chosen
                )
                mismatches += not valid
                digest.update(
                    json.dumps(
                        [observation, state, primary, members], separators=(",", ":")
                    ).encode()
                )
                digest.update(b"\n")
                if checked % stride == 0:
                    sample.append((lon, lat, state))
                checked += 1
    if checked != count or mismatches:
        raise ValueError(
            f"independent spatial check failed: {checked}/{count}; mismatches={mismatches}"
        )
    return {
        "checked": checked,
        "membership_mismatches": mismatches,
        "business_sha256": digest.hexdigest(),
    }, sample


def render_plot(destination, geofence, port, sample, counts):
    colors = {"harbor": "#257a96", "terminal": "#cd7134", "waiting_candidate": "#8275a5"}
    fig, ax = plt.subplots(figsize=(11, 10))
    fig.subplots_adjust(left=0.10, right=0.96, bottom=0.14, top=0.88)
    ax.set_facecolor("#f4f7f7")
    x, y, _ = zip(*sample, strict=True)
    ax.scatter(
        x, y, s=3, color="#28434b", alpha=0.35, label=f"{len(sample):,} real observations sampled"
    )
    shown = set()
    for feature in geofence.document.features:
        properties = feature.properties
        polygons = (
            [feature.geometry["coordinates"]]
            if feature.geometry["type"] == "Polygon"
            else feature.geometry["coordinates"]
        )
        for polygon in polygons:
            label = {
                "harbor": "Approximate harbor",
                "terminal": "Terminal vicinity",
                "waiting_candidate": "Regional candidate area",
            }[properties.zone_type]
            ax.add_patch(
                PlotPolygon(
                    polygon[0],
                    facecolor=colors[properties.zone_type],
                    edgecolor=colors[properties.zone_type],
                    alpha=0.17,
                    linewidth=2,
                    label=label if properties.zone_type not in shown else None,
                )
            )
            # Holes are unsupported by this diagnostic only; loaded geometries may have them.
            for hole in polygon[1:]:
                ax.add_patch(
                    PlotPolygon(
                        hole, facecolor=ax.get_facecolor(), edgecolor=colors[properties.zone_type]
                    )
                )
            shown.add(properties.zone_type)
    west, south, east, north = port.bounding_box
    ax.add_patch(
        Rectangle(
            (west, south),
            east - west,
            north - south,
            fill=False,
            linestyle="--",
            edgecolor="#647d81",
            label="Collection filter",
        )
    )
    for review in geofence.document.review_points:
        lon, lat = review.coordinates
        ax.scatter([lon], [lat], marker="x", color="#a33943", s=48)
        label = review.label.replace(" reference", "").replace(" vicinity", "")
        if "Long Beach" in label:
            label = "Long Beach Pier F\nexclusion reference"
            offset, alignment = (-8, 12), "right"
        else:
            offset, alignment = (7, 7), "left"
        ax.annotate(
            label,
            (lon, lat),
            xytext=offset,
            textcoords="offset points",
            fontsize=8,
            horizontalalignment=alignment,
        )
    ax.set(
        xlim=(west - 0.007, -118.197),
        ylim=(south - 0.004, north + 0.004),
        xlabel="Longitude (EPSG:4326)",
        ylabel="Latitude (EPSG:4326)",
    )
    ax.set_aspect(1 / math.cos(math.radians((south + north) / 2)))
    ax.grid(alpha=0.16)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.95)
    ax.set_title(
        "PortWatch: approximate Los Angeles analytical zones\n"
        f"2024-01-01 UTC | {geofence.document.version} | "
        f"{counts.input_positions:,} clean positions",
        loc="left",
        fontsize=13,
        pad=15,
    )
    fig.text(
        0.10,
        0.025,
        "Diagnostic only. Not official boundaries or navigation. "
        "Offshore membership does not prove waiting.\n"
        f"{counts.inside:,} inside / {counts.outside:,} outside; "
        f"{counts.overlaps:,} with overlapping memberships. "
        "Reference: Port of Los Angeles terminal schematic (2023).",
        fontsize=8,
        color="#425963",
    )
    fig.savefig(destination, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/step06"))
    args = parser.parse_args()
    settings = Settings()
    destination = settings.project_path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    geofence = configured_geofence(settings, "los_angeles")
    port = load_port_config(settings.project_path(settings.port_config))
    scope = demo_scope(settings, "los_angeles")
    started = time.perf_counter()
    with connect_db(settings) as connection:
        original_before = business_hash(connection)
        passes = []
        for _ in range(2):
            _, counts = transform_zones(settings, connection, "los_angeles")
            inspection, sample = inspect_memberships(connection, geofence, scope)
            passes.append({"counts": asdict(counts), **inspection})
        original_after = business_hash(connection)
        if (
            original_before != original_after
            or passes[0]["business_sha256"] != passes[1]["business_sha256"]
        ):
            raise ValueError("replay changed original positions or zone assignments")
        baseline = settings.project_path(Path("artifacts/step06/positions-before.sha256"))
        if baseline.exists() and baseline.read_text().strip() != original_after:
            raise ValueError("Step06 changed the pre-stage clean business hash")
        if passes[1]["counts"]["newly_assigned"] != 0:
            raise ValueError("second replay created unexpected assignments")
        areas = connection.execute(
            "WITH selected AS (SELECT p.id FROM vessel_positions p WHERE " + SCOPE_FILTER + ") "
            """SELECT z.zone_id,z.zone_type,z.attribution_scope,
                public.ST_Area(z.geometry::public.geography)/1000000,
                (SELECT count(*) FROM position_zone_memberships m JOIN selected s
                    ON s.id=m.position_id WHERE m.port_id=z.port_id
                    AND m.geofence_version=z.version AND m.zone_id=z.zone_id
                    AND m.assignment_version=%s),
                (SELECT count(*) FROM position_zone_assignments a JOIN selected s
                    ON s.id=a.position_id WHERE a.port_id=z.port_id
                    AND a.geofence_version=z.version AND a.primary_zone_id=z.zone_id
                    AND a.assignment_version=%s)
            FROM port_zones z WHERE z.port_id=%s AND z.version=%s ORDER BY z.zone_id""",
            scope.parameters
            + (ASSIGNMENT_VERSION, ASSIGNMENT_VERSION, "los_angeles", geofence.document.version),
        ).fetchall()
        connection.execute("ANALYZE vessel_positions")
        connection.execute("ANALYZE port_zones")
        plan = connection.execute(
            """EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT p.id FROM port_zones z
            JOIN vessel_positions p ON public.ST_Covers(z.geometry,p.position)
            WHERE z.port_id=%s AND z.version=%s AND """
            + SCOPE_FILTER,
            ("los_angeles", geofence.document.version) + scope.parameters,
        ).fetchone()[0]
        long_beach_distance = connection.execute(
            """SELECT min(public.ST_Distance(geometry::public.geography,
                public.ST_SetSRID(public.ST_MakePoint(-118.209,33.754),4326)::public.geography))
            FROM port_zones WHERE port_id=%s AND version=%s AND attribution_scope='port'""",
            ("los_angeles", geofence.document.version),
        ).fetchone()[0]
    report = {
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "geofence_version": geofence.document.version,
        "geofence_sha256": geofence.content_sha256,
        "assignment_version": ASSIGNMENT_VERSION,
        "approximate": True,
        "scope": {
            "source": scope.source_id,
            "dataset": scope.dataset_id,
            "clean_version": scope.clean_version,
            "start_utc": scope.start_utc.isoformat(),
            "end_utc": scope.end_utc.isoformat(),
        },
        "replays": passes,
        "clean_business_sha256_before": original_before,
        "clean_business_sha256_after": original_after,
        "clean_business_unchanged": True,
        "long_beach_reference_distance_to_port_zones_m": long_beach_distance,
        "zones": [
            {
                "zone_id": z,
                "type": kind,
                "scope": attribution,
                "area_km2": area,
                "memberships": matches,
                "primary_assignments": primary,
            }
            for z, kind, attribution, area, matches, primary in areas
        ],
        "spatial_query_plan": plan,
        "sampled_points": len(sample),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    report_path = destination / "verification.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    plot_path = destination / "los-angeles-zones.png"
    render_plot(plot_path, geofence, port, sample, counts)
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"spatial_query_plan", "zones"}
            },
            indent=2,
        )
    )
    print(f"Report: {report_path}\nDiagnostic: {plot_path}")


if __name__ == "__main__":
    main()
