"""Geofence validation uses labeled synthetic polygons, never provider data."""

import copy
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from portwatch.cli import app
from portwatch.zones import AssignmentScope, validate_geofence

BOUNDS = (-118.34, 33.66, -118.225, 33.80)


def rectangle(west, south, east, north):
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def synthetic_geofence(version="synthetic-v1"):
    features = []
    for zone_id, kind, scope, ring in (
        ("harbor", "harbor", "port", rectangle(-118.32, 33.70, -118.27, 33.76)),
        ("terminal", "terminal", "port", rectangle(-118.31, 33.71, -118.29, 33.73)),
        ("candidate", "waiting_candidate", "region", rectangle(-118.33, 33.69, -118.30, 33.715)),
    ):
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "zone_id": zone_id,
                    "name": "Synthetic " + zone_id,
                    "zone_type": kind,
                    "attribution_scope": scope,
                    "uncertainty": "Synthetic test only",
                },
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    return {
        "type": "FeatureCollection",
        "schema_version": 1,
        "port_id": "los_angeles",
        "version": version,
        "authored_on": "2026-10-12",
        "coordinate_order": "longitude,latitude",
        "srid": 4326,
        "approximate": True,
        "uncertainty": "Synthetic fixture only",
        "validity": {"status": "synthetic"},
        "references": [
            {
                "title": "Synthetic reference",
                "url": "https://example.invalid/synthetic",
                "reviewed_on": "2026-10-12",
            }
        ],
        "features": features,
    }


def test_invalid_self_intersection_rejected_without_repair():
    data = synthetic_geofence()
    data["features"][0]["geometry"]["coordinates"] = [
        [[-118.32, 33.70], [-118.27, 33.76], [-118.32, 33.76], [-118.27, 33.70], [-118.32, 33.70]]
    ]
    with pytest.raises(ValueError, match="Self-intersection"):
        validate_geofence(data, "los_angeles", BOUNDS)


@pytest.mark.parametrize("mode", ["swapped", "unclosed", "altitude", "nan", "boolean"])
def test_coordinate_errors_are_actionable(mode):
    data = synthetic_geofence()
    ring = data["features"][0]["geometry"]["coordinates"][0]
    if mode == "swapped":
        for vertex in ring:
            vertex.reverse()
    elif mode == "unclosed":
        ring.pop()
    elif mode == "altitude":
        for vertex in ring:
            vertex.append(0)
    else:
        ring[1][0] = float("nan") if mode == "nan" else True
    with pytest.raises(ValueError, match="bounds|closed|coordinates|finite"):
        validate_geofence(data, "los_angeles", BOUNDS)


def test_regional_waiting_and_coordinate_system_required():
    data = synthetic_geofence()
    data["features"][2]["properties"]["attribution_scope"] = "port"
    with pytest.raises(ValueError, match="regional"):
        validate_geofence(data, "los_angeles", BOUNDS)
    data = synthetic_geofence()
    data["srid"] = 3857
    with pytest.raises(ValueError):
        validate_geofence(data, "los_angeles", BOUNDS)


def test_duplicate_ids_and_failed_reference_review_rejected():
    data = synthetic_geofence()
    data["features"].append(copy.deepcopy(data["features"][0]))
    with pytest.raises(ValueError, match="unique"):
        validate_geofence(data, "los_angeles", BOUNDS)
    data = synthetic_geofence()
    data["review_points"] = [
        {
            "label": "Synthetic wrong reference",
            "coordinates": [-118.3, 33.72],
            "expected_zone_ids": [],
        }
    ]
    with pytest.raises(ValueError, match="unexpected memberships"):
        validate_geofence(data, "los_angeles", BOUNDS)


def test_demo_required_and_explicit_utc_scope():
    result = CliRunner().invoke(app, ["transform", "zones"])
    assert result.exit_code == 2 and "--demo" in result.output
    with pytest.raises(ValueError, match="UTC"):
        AssignmentScope("synthetic", "synthetic", datetime(2024, 1, 1), datetime(2024, 1, 2))
    with pytest.raises(ValueError, match="UTC"):
        AssignmentScope(
            "synthetic",
            "synthetic",
            datetime(2024, 1, 2, tzinfo=UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
        )
