"""Immutable approximate geofences and additive, boundary-inclusive memberships."""

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import Point, shape
from shapely.validation import explain_validity

from portwatch.bookkeeping import finish_run, start_run
from portwatch.ingest import plan
from portwatch.positions import PROCESSING_VERSION
from portwatch.settings import Settings, load_port_config

ASSIGNMENT_VERSION = "covers-v1"
PRIORITIES = {"terminal": 10, "harbor": 20, "waiting_candidate": 30}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reference(StrictModel):
    title: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    reviewed_on: date


class ZoneProperties(StrictModel):
    zone_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    zone_type: Literal["terminal", "harbor", "waiting_candidate"]
    attribution_scope: Literal["port", "region"]
    uncertainty: str = Field(min_length=1)

    @model_validator(mode="after")
    def attribution(self):
        expected = "region" if self.zone_type == "waiting_candidate" else "port"
        if self.attribution_scope != expected:
            raise ValueError(
                "candidate waiting areas must be regional; harbor/terminals port scoped"
            )
        return self


class ZoneFeature(StrictModel):
    type: Literal["Feature"]
    properties: ZoneProperties
    geometry: dict


class ReviewPoint(StrictModel):
    label: str = Field(min_length=1)
    coordinates: tuple[float, float]
    expected_zone_ids: list[str]


class GeofenceDocument(StrictModel):
    type: Literal["FeatureCollection"]
    schema_version: Literal[1]
    port_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=100)
    authored_on: date
    coordinate_order: Literal["longitude,latitude"]
    srid: Literal[4326]
    approximate: Literal[True]
    uncertainty: str = Field(min_length=1)
    validity: dict
    references: list[Reference] = Field(min_length=1)
    review_points: list[ReviewPoint] = Field(default_factory=list)
    features: list[ZoneFeature] = Field(min_length=1, max_length=100)


@dataclass(frozen=True)
class Geofence:
    document: GeofenceDocument
    canonical_json: str
    content_sha256: str


def _coordinates(geometry: dict, bounds: tuple[float, float, float, float]) -> None:
    if set(geometry) != {"type", "coordinates"}:
        raise ValueError("geometry must contain only type and coordinates; EPSG:4326 is required")
    kind = geometry["type"]
    if kind not in ("Polygon", "MultiPolygon"):
        raise ValueError("zones must be Polygon or MultiPolygon")
    polygons = [geometry["coordinates"]] if kind == "Polygon" else geometry["coordinates"]
    if not isinstance(polygons, list) or not polygons:
        raise ValueError("polygon coordinates must be nonempty arrays")
    west, south, east, north = bounds
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("polygon must contain an exterior ring")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError(
                    "rings must have at least four coordinates and be explicitly closed"
                )
            for coordinate in ring:
                if not isinstance(coordinate, list) or len(coordinate) != 2:
                    raise ValueError("use exactly two coordinates: longitude, latitude")
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in coordinate):
                    raise ValueError("coordinates must be finite numbers")
                longitude, latitude = coordinate
                if not (west <= longitude <= east and south <= latitude <= north):
                    raise ValueError(
                        "zone coordinate outside reviewed collection bounds; check lon/lat"
                    )


def validate_geofence(data: dict, port_id: str, bounds: tuple[float, float, float, float]):
    document = GeofenceDocument.model_validate(data)
    if document.port_id != port_id:
        raise ValueError("geofence port_id does not match the configured port")
    ids = [feature.properties.zone_id for feature in document.features]
    if len(set(ids)) != len(ids):
        raise ValueError("zone IDs must be unique within a geofence version")
    shapes = {}
    for feature in document.features:
        _coordinates(feature.geometry, bounds)
        polygon = shape(feature.geometry)
        if polygon.is_empty or not polygon.is_valid:
            raise ValueError(
                f"invalid zone {feature.properties.zone_id}: {explain_validity(polygon)}"
            )
        shapes[feature.properties.zone_id] = polygon
    for review in document.review_points:
        lon, lat = review.coordinates
        if not (
            math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90
        ):
            raise ValueError("review point must use finite longitude,latitude")
        actual = {zone_id for zone_id, polygon in shapes.items() if polygon.covers(Point(lon, lat))}
        if actual != set(review.expected_zone_ids):
            raise ValueError(f"review point {review.label} has unexpected memberships")
    canonical = json.dumps(
        document.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return Geofence(document, canonical, hashlib.sha256(canonical.encode()).hexdigest())


def read_geofence(path: Path, port_id: str, bounds: tuple[float, float, float, float]) -> Geofence:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("geofence exceeds the 2 MiB review limit")

    def nonfinite(value):
        raise ValueError(f"nonstandard JSON number: {value}")

    return validate_geofence(
        json.loads(path.read_text("utf-8"), parse_constant=nonfinite), port_id, bounds
    )


def configured_geofence(settings: Settings, port_id: str, path: Path | None = None) -> Geofence:
    port = load_port_config(settings.project_path(settings.port_config))
    if port.port_id != port_id or port.bounding_box is None:
        raise ValueError("use the configured port with reviewed collection bounds")
    if path is None and port.geofence_path is None:
        raise ValueError("configure geofence_path or provide --file")
    return read_geofence(
        settings.project_path(path or Path(port.geofence_path)), port_id, port.bounding_box
    )


def load_geofence(connection, geofence: Geofence) -> bool:
    """Publish one immutable version transactionally; a changed same-version file fails."""
    document = geofence.document
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(734920026)")
        existing = connection.execute(
            "SELECT content_sha256 FROM geofence_versions WHERE port_id=%s AND version=%s",
            (document.port_id, document.version),
        ).fetchone()
        if existing:
            if existing[0] != geofence.content_sha256:
                raise ValueError(
                    "geofence version already exists with changed content; use a new version"
                )
            return False
        connection.execute(
            """INSERT INTO geofence_versions(port_id,version,content_sha256,authored_on,
                approximate,uncertainty,validity,reference,document)
                VALUES (%s,%s,%s,%s,true,%s,%s::jsonb,%s::jsonb,%s::jsonb)""",
            (
                document.port_id,
                document.version,
                geofence.content_sha256,
                document.authored_on,
                document.uncertainty,
                json.dumps(document.validity),
                json.dumps(
                    [reference.model_dump(mode="json") for reference in document.references]
                ),
                geofence.canonical_json,
            ),
        )
        for feature in document.features:
            properties = feature.properties
            connection.execute(
                """INSERT INTO port_zones(port_id,version,zone_id,name,zone_type,attribution_scope,
                    priority,geometry,reference,validity)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,
                        public.ST_Multi(public.ST_SetSRID(public.ST_GeomFromGeoJSON(%s),4326)),
                        %s::jsonb,%s::jsonb)""",
                (
                    document.port_id,
                    document.version,
                    properties.zone_id,
                    properties.name,
                    properties.zone_type,
                    properties.attribution_scope,
                    PRIORITIES[properties.zone_type],
                    json.dumps(feature.geometry),
                    json.dumps(
                        [reference.model_dump(mode="json") for reference in document.references]
                    ),
                    json.dumps({**document.validity, "uncertainty": properties.uncertainty}),
                ),
            )
        connection.execute("ANALYZE port_zones")
    return True


@dataclass(frozen=True)
class AssignmentScope:
    source_id: str
    dataset_id: str
    start_utc: datetime
    end_utc: datetime
    clean_version: str = PROCESSING_VERSION

    def __post_init__(self):
        if (
            self.start_utc.utcoffset() != timedelta(0)
            or self.end_utc.utcoffset() != timedelta(0)
            or self.start_utc >= self.end_utc
        ):
            raise ValueError("assignment scope requires ordered, explicit UTC boundaries")
        if not self.source_id or not self.dataset_id or not self.clean_version:
            raise ValueError("assignment scope requires source, dataset and cleaning version")

    @property
    def parameters(self):
        return (self.source_id, self.dataset_id, self.clean_version, self.start_utc, self.end_utc)


SCOPE_FILTER = """p.source_id=%s AND p.dataset_id=%s AND p.processing_version=%s
    AND p.effective_at >= %s AND p.effective_at < %s"""


@dataclass(frozen=True)
class AssignmentCounts:
    input_positions: int
    newly_assigned: int
    inside: int
    outside: int
    memberships: int
    overlaps: int


def assign_zones(
    connection,
    port_id: str,
    version: str,
    scope: AssignmentScope,
    *,
    before_commit: Callable[[], None] | None = None,
) -> AssignmentCounts:
    """Append missing assignments only; preserve all overlaps and explicit outside rows."""
    run_id = start_run(connection, "zones")
    try:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(734920027)")
            connection.execute("SELECT set_config('statement_timeout','120s',true)")
            if not connection.execute(
                "SELECT 1 FROM geofence_versions WHERE port_id=%s AND version=%s",
                (port_id, version),
            ).fetchone():
                raise ValueError("geofence version is not loaded; run geo load first")
            connection.execute(
                "CREATE TEMP TABLE pw_zone_selected(position_id bigint PRIMARY KEY) ON COMMIT DROP"
            )
            connection.execute(
                "INSERT INTO pw_zone_selected SELECT p.id FROM vessel_positions p WHERE "
                + SCOPE_FILTER,
                scope.parameters,
            )
            input_count = connection.execute("SELECT count(*) FROM pw_zone_selected").fetchone()[0]
            if input_count == 0:
                raise ValueError("no cleaned positions in the selected source/dataset/UTC interval")
            connection.execute("ANALYZE pw_zone_selected")
            connection.execute(
                """CREATE TEMP TABLE pw_zone_pending ON COMMIT DROP AS
                SELECT s.position_id FROM pw_zone_selected s WHERE NOT EXISTS (
                    SELECT 1 FROM position_zone_assignments a WHERE a.position_id=s.position_id
                    AND a.port_id=%s AND a.geofence_version=%s AND a.assignment_version=%s)""",
                (port_id, version, ASSIGNMENT_VERSION),
            )
            new_count = connection.execute("SELECT count(*) FROM pw_zone_pending").fetchone()[0]
            connection.execute("ANALYZE pw_zone_pending")
            connection.execute(
                """CREATE TEMP TABLE pw_zone_matches ON COMMIT DROP AS
                SELECT p.id AS position_id,z.zone_id,z.priority FROM pw_zone_pending s
                JOIN vessel_positions p ON p.id=s.position_id
                JOIN port_zones z ON public.ST_Covers(z.geometry,p.position)
                WHERE z.port_id=%s AND z.version=%s""",
                (port_id, version),
            )
            connection.execute("ANALYZE pw_zone_matches")
            connection.execute(
                """INSERT INTO position_zone_assignments(position_id,port_id,geofence_version,
                    assignment_version,state,primary_zone_id,membership_count)
                SELECT s.position_id,%s,%s,%s,
                    CASE WHEN count(m.zone_id)=0 THEN 'outside' ELSE 'inside' END,
                    (array_agg(m.zone_id ORDER BY m.priority,m.zone_id)
                        FILTER (WHERE m.zone_id IS NOT NULL))[1],count(m.zone_id)
                FROM pw_zone_pending s LEFT JOIN pw_zone_matches m USING(position_id)
                GROUP BY s.position_id""",
                (port_id, version, ASSIGNMENT_VERSION),
            )
            # FK probes need current cardinalities after this bulk insert. A fresh table's
            # zero-row statistics can select a broad port/version index for every probe.
            connection.execute("ANALYZE position_zone_assignments")
            connection.execute(
                """INSERT INTO position_zone_memberships(position_id,port_id,geofence_version,
                    assignment_version,zone_id) SELECT position_id,%s,%s,%s,zone_id
                    FROM pw_zone_matches""",
                (port_id, version, ASSIGNMENT_VERSION),
            )
            connection.execute("ANALYZE position_zone_memberships")
            inside, outside, memberships, overlaps = connection.execute(
                """SELECT count(*) FILTER (WHERE a.state='inside'),
                    count(*) FILTER (WHERE a.state='outside'),sum(a.membership_count),
                    count(*) FILTER (WHERE a.membership_count>1)
                FROM position_zone_assignments a JOIN pw_zone_selected s USING(position_id)
                WHERE a.port_id=%s AND a.geofence_version=%s AND a.assignment_version=%s""",
                (port_id, version, ASSIGNMENT_VERSION),
            ).fetchone()
            if inside + outside != input_count:
                raise ValueError("zone assignment reconciliation failed")
            if before_commit:
                before_commit()
            result = AssignmentCounts(
                input_count, new_count, inside, outside, memberships, overlaps
            )
        finish_run(connection, run_id, input_count=input_count, output_count=input_count)
        return result
    except Exception as error:
        finish_run(connection, run_id, error=error)
        raise


def demo_scope(settings: Settings, port_id: str) -> AssignmentScope:
    requests = plan(settings, port_id, True, None, None)
    spec, parameters = requests[0]
    return AssignmentScope(
        spec.source_id, spec.dataset_id, parameters.start_utc, parameters.end_utc
    )


def transform_zones(settings: Settings, connection, port_id: str, version: str | None = None):
    port = load_port_config(settings.project_path(settings.port_config))
    if port.port_id != port_id:
        raise ValueError("use the configured port")
    if version is None:
        geofence = configured_geofence(settings, port_id)
        version = geofence.document.version
        saved = connection.execute(
            "SELECT content_sha256 FROM geofence_versions WHERE port_id=%s AND version=%s",
            (port_id, version),
        ).fetchone()
        if not saved or saved[0] != geofence.content_sha256:
            raise ValueError("configured geofence is not loaded or changed; run geo load")
    scope = demo_scope(settings, port_id)
    return version, assign_zones(connection, port_id, version, scope)
