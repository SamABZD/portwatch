"""Bounded snapshots reuse the existing event-time inference; no rendering writes or fetches."""

import hashlib
import json
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

from portwatch.context import plan_context
from portwatch.context_storage import cached_response
from portwatch.db import connect_db
from portwatch.health import json_value
from portwatch.live import live_spec
from portwatch.marts import configured_marts
from portwatch.settings import load_port_config
from portwatch.storage import checksum_file
from portwatch.visit_storage import rules_for_port
from portwatch.visits import TrackPoint, infer_track
from portwatch.zones import ASSIGNMENT_VERSION, configured_geofence

MAX_POSITIONS = 150000
FORMAT = "portwatch-prepared-replay-v1"


class ReplayError(ValueError):
    """Only trusted reason codes may be returned to clients."""


def stamp(value):
    value = (
        datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    )
    if value.utcoffset() is None:
        raise ReplayError("explicit_timezone_required")
    return value.astimezone(UTC)


def public_json(value):
    return json.loads(json.dumps(value, default=json_value, allow_nan=False))


@dataclass(frozen=True, slots=True)
class Observation:
    id: int
    identity: str
    mmsi: str
    at: datetime
    longitude: float
    latitude: float
    speed: float | None
    course: float | None
    vessel_type: int | None
    flags: tuple
    timestamp_quality: str
    zone: str
    memberships: tuple


class ReplayDataset:
    def __init__(self, metadata, observations, zones, rules, weather, coverage, news=None):
        self.metadata, self.zones, self.rules = metadata, zones, rules
        self.weather, self.coverage, self.news = weather, coverage, news or []
        self.groups = defaultdict(list)
        for point in observations:
            self.groups[point.mmsi].append(point)
        self.times = {}
        self.zone_types = {
            f["properties"]["zone_id"]: f["properties"]["zone_type"] for f in zones["features"]
        }
        for mmsi, points in self.groups.items():
            points.sort(key=lambda p: (p.at, p.identity))
            self.times[mmsi] = [p.at for p in points]

    def covered(self, start, end):
        return any(left <= start and right >= end for left, right in self.coverage)

    def frame(self, cutoff, stale_minutes=15, trail_minutes=30):
        cutoff = stamp(cutoff)
        start, end = stamp(self.metadata["start_utc"]), stamp(self.metadata["end_utc"])
        if self.metadata["mode"] == "historical" and not start <= cutoff <= end:
            raise ReplayError("cutoff_outside_prepared_period")
        if not 1 <= stale_minutes <= 60 or not 5 <= trail_minutes <= 60:
            raise ReplayError("display_limits_out_of_range")
        fresh_limit = cutoff - timedelta(minutes=stale_minutes)
        trail_limit = cutoff - timedelta(minutes=trail_minutes)
        vessels, trails, visits, active_waits = [], [], [], []
        hours = defaultdict(set)
        reports = Counter()
        zone_counts = Counter()
        prefix_count = 0
        for mmsi, saved in self.groups.items():
            points = saved[: bisect_right(self.times[mmsi], cutoff)]
            if not points:
                continue
            prefix_count += len(points)
            for point in points:
                if start <= point.at < end:
                    hour = point.at.replace(minute=0, second=0, microsecond=0)
                    hours[hour].add(mmsi)
                    reports[hour] += 1
            track = [
                TrackPoint(
                    p.id,
                    p.identity,
                    p.at,
                    p.speed,
                    any(self.zone_types.get(z) == "harbor" for z in p.memberships),
                    any(self.zone_types.get(z) == "terminal" for z in p.memberships),
                    tuple(
                        z for z in p.memberships if self.zone_types.get(z) == "waiting_candidate"
                    ),
                    p.flags,
                )
                for p in points
            ]
            inferred = infer_track(track, self.rules, f"{self.metadata['revision']}:{mmsi}", cutoff)
            visits.extend(inferred.visits)
            latest = points[-1]
            same_time = [p for p in points if p.at == latest.at]
            ambiguous = len({(p.longitude, p.latitude) for p in same_time}) > 1
            stale = latest.at < fresh_limit
            zone = latest.zone if len({p.zone for p in same_time}) == 1 else "unknown"
            active_episodes = [
                w
                for w in inferred.waiting
                if w.qualification_known_at <= cutoff
                and w.observed_end == latest.at
                and w.end_upper is None
            ]
            candidate = bool(active_episodes) and inferred.current_state == "candidate_waiting"
            if candidate and not stale:
                active_waits.append(mmsi)
            if not stale:
                zone_counts["unknown" if ambiguous else zone] += 1
            partial = [v for v in inferred.visits if not v.completed]
            associated = any(
                w.association_known_at and w.association_known_at <= cutoff for w in active_episodes
            )
            vessels.append(
                dict(
                    mmsi=mmsi,
                    longitude=None if ambiguous else latest.longitude,
                    latitude=None if ambiguous else latest.latitude,
                    speed_knots=latest.speed,
                    course_degrees=latest.course,
                    vessel_type=latest.vessel_type,
                    last_observation_utc=latest.at,
                    observation_age_seconds=(cutoff - latest.at).total_seconds(),
                    stale=stale,
                    zone=zone,
                    waiting="port_associated_candidate"
                    if candidate and associated
                    else "regional_candidate"
                    if candidate
                    else "not_currently_qualified",
                    waiting_history=dict(
                        qualified_episodes=len(inferred.waiting),
                        associated_episodes=sum(
                            bool(w.association_known_at and w.association_known_at <= cutoff)
                            for w in inferred.waiting
                        ),
                    ),
                    visit_status="partial"
                    if partial
                    else "completed"
                    if inferred.visits
                    else "not_observed",
                    partial_reasons=sorted(
                        {reason for v in partial for reason in v.completeness_reasons}
                    ),
                    timestamp_quality=latest.timestamp_quality,
                    ambiguous_position=ambiguous,
                    quality_flags=list(latest.flags),
                    source_id=self.metadata["source_id"],
                )
            )
            segment, previous = [], None
            by_id = {p.id: p for p in points}
            for state in inferred.states:
                candidates = (
                    [p for p in points if p.at == state.at] if len(state.points) > 1 else None
                )
                point = candidates[0] if candidates else by_id[state.representative.position_id]
                if point.at < trail_limit:
                    continue
                conflict = candidates and len({(p.longitude, p.latitude) for p in candidates}) > 1
                if conflict or (
                    previous
                    and (point.at - previous.at).total_seconds() > self.rules.max_gap_seconds
                ):
                    self._trail(trails, mmsi, segment, stale)
                    segment = []
                if conflict:
                    previous = None
                    continue
                segment.append([point.longitude, point.latitude, point.at.isoformat()])
                previous = point
            self._trail(trails, mmsi, segment, stale)
        vessels.sort(key=lambda v: (v["stale"], v["mmsi"]))
        fresh = [v for v in vessels if not v["stale"]]
        snapshot_known = bool(fresh) or self.covered(fresh_limit, cutoff)
        completed = [v for v in visits if v.completed and v.departure_confirmed_at <= cutoff]
        dwell = [(v.duration_min_seconds + v.duration_max_seconds) / 2 for v in completed]
        chart = []
        hour = start
        while hour + timedelta(hours=1) <= min(cutoff, end):
            known = bool(reports[hour]) or self.covered(hour, hour + timedelta(hours=1))
            chart.append(
                dict(
                    hour_utc=hour,
                    hour_end_utc=hour + timedelta(hours=1),
                    observed_vessels=len(hours[hour]) if known else None,
                    observations=reports[hour] if known else None,
                    complete_hour=True,
                )
            )
            hour += timedelta(hours=1)
        weather = [
            w
            for w in self.weather
            if stamp(w["valid_time_utc"]) <= cutoff
            and stamp(w["valid_time_utc"])
            <= cutoff
            < stamp(w["valid_time_utc"]) + timedelta(hours=1)
        ]
        return public_json(
            dict(
                cutoff_utc=cutoff,
                dataset=self.metadata,
                vessels=vessels,
                trails=trails,
                prefix_observations=prefix_count,
                metrics=dict(
                    seen_vessels=len(vessels),
                    fresh_vessels=len(fresh) if snapshot_known else None,
                    fresh_port_occupancy=sum(
                        zone_counts[z]
                        for z, kind in self.zone_types.items()
                        if kind in {"harbor", "terminal"}
                    )
                    if snapshot_known
                    else None,
                    zone_counts=dict(zone_counts) if snapshot_known else None,
                    candidate_waiting=len(active_waits) if snapshot_known else None,
                    completed_dwell_sample_count=len(completed),
                    completed_dwell_median_seconds=median(dwell) if dwell else None,
                    partial_visit_count=sum(not v.completed for v in visits),
                ),
                chart=chart,
                weather=weather[0] if weather else None,
                news=[] if self.metadata["mode"] == "historical" else self.news,
                news_status="excluded_recent_source"
                if self.metadata["mode"] == "historical"
                else self.metadata.get("news_status", "unavailable"),
                staleness_minutes=stale_minutes,
                trail_minutes=trail_minutes,
                max_trail_gap_seconds=self.rules.max_gap_seconds,
                semantics="actual_observation_positions_and_event_time_prefix_inference",
            )
        )

    @staticmethod
    def _trail(trails, mmsi, points, stale):
        if len(points) < 2:
            return
        # Break gaps before downsampling. Keep observed endpoints, never fabricate coordinates.
        stride = max(1, (len(points) + 30) // 31)
        selected = points[::stride]
        if selected[-1] != points[-1]:
            selected.append(points[-1])
        trails.append(dict(mmsi=mmsi, points=selected, stale=stale))


def fingerprint(connection, settings, mode, now):
    port = load_port_config(settings.project_path(settings.port_config))
    fence = configured_geofence(settings, port.port_id)
    if mode == "historical":
        scope, config = configured_marts(settings, connection, port.port_id)
        source, dataset, start, end = (
            scope.source_id,
            scope.dataset_id,
            scope.start_utc,
            scope.end_utc,
        )
        weather = config.weather_batch_id
        version = config.analytical_version(scope)
    else:
        spec, _ = live_spec(settings, port.port_id)
        source, dataset = spec.source_id, spec.dataset_id
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        start = end - timedelta(days=2)
        weather_response = cached_response(
            connection, plan_context(settings, port.port_id, "weather")
        )
        weather = weather_response.batch_id if weather_response else None
        version = "live-prefix-" + port.port_id + "-" + rules_for_port(port).rule_id
    ledger = connection.execute(
        "SELECT c.input_id,c.input_sha256,c.status,c.completed_at FROM "
        "clean_batches c JOIN ingestion_batches b ON b.batch_id=c.raw_batch_id "
        "WHERE b.source_id=%s AND b.dataset_id=%s AND c.processing_version='positions-v1' "
        "ORDER BY c.input_id",
        (source, dataset),
    ).fetchall()
    news = (
        connection.execute(
            "SELECT selected_batch_id FROM context_requests WHERE port_id=%s "
            "AND product='recent_news' ORDER BY selected_retrieved_at DESC NULLS LAST LIMIT 1",
            (port.port_id,),
        ).fetchone()
        if mode == "live"
        else None
    )
    revision = hashlib.sha256(
        json.dumps(
            [ledger, weather, news, fence.content_sha256, version, start, end],
            default=json_value,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return (
        dict(
            port_id=port.port_id,
            port_name=port.display_name,
            mode=mode,
            source_id=source,
            dataset_id=dataset,
            start_utc=start,
            end_utc=end,
            clean_version="positions-v1",
            geofence_version=fence.document.version,
            analytical_version=version,
            revision=revision,
            weather_batch_id=weather,
            news_batch_id=news[0] if news else None,
            bounds=list(port.bounding_box),
            display_timezone="UTC",
            observation_count=None,
        ),
        fence,
        port,
    )


def load_dataset(connection, metadata, fence, port):
    source, dataset = metadata["source_id"], metadata["dataset_id"]
    start, end = metadata["start_utc"], metadata["end_utc"]
    rows = connection.execute(
        """SELECT p.id,p.observation_id,p.mmsi,p.effective_at,p.longitude,
        p.latitude,p.speed_knots,p.course_degrees,p.vessel_type,p.quality_flags,p.timestamp_quality,
        CASE WHEN a.state='outside' THEN 'outside' ELSE a.primary_zone_id END,
        ARRAY(SELECT m.zone_id FROM position_zone_memberships m WHERE m.position_id=p.id
        AND m.port_id=%s AND m.geofence_version=%s AND m.assignment_version=%s ORDER BY m.zone_id)
        FROM vessel_positions p LEFT JOIN position_zone_assignments a ON a.position_id=p.id
        AND a.port_id=%s AND a.geofence_version=%s AND a.assignment_version=%s
        WHERE p.source_id=%s AND p.dataset_id=%s AND p.processing_version='positions-v1'
        AND p.effective_at>=%s AND p.effective_at<%s ORDER BY p.mmsi,p.effective_at,p.observation_id
        LIMIT %s""",
        (port.port_id, fence.document.version, ASSIGNMENT_VERSION) * 2
        + (source, dataset, start, end, MAX_POSITIONS + 1),
    ).fetchall()
    if len(rows) > MAX_POSITIONS:
        raise ReplayError("prepared_position_limit_exceeded")
    if any(row[11] is None for row in rows):
        raise ReplayError("spatial_assignment_missing_run_saved_pipeline")
    observations = [
        Observation(*row[:9], tuple(row[9]), row[10], row[11], tuple(row[12])) for row in rows
    ]
    metadata = public_json(
        dict(
            metadata,
            observation_count=len(rows),
            latest_observation_utc=max((r.at for r in observations), default=None),
            timestamp_semantics="provider_documented_utc"
            if metadata["mode"] == "historical"
            else "local_receipt_fallback_not_verified_event_time",
        )
    )
    weather_rows = connection.execute(
        "SELECT valid_time_utc,wind_speed_10m,wind_gusts_10m,"
        "precipitation FROM weather_values WHERE batch_id=%s AND valid_time_utc>=%s "
        "AND valid_time_utc<%s ORDER BY valid_time_utc",
        (metadata["weather_batch_id"], start, end),
    ).fetchall()
    weather = [
        dict(
            zip(
                (
                    "valid_time_utc",
                    "wind_speed_mps",
                    "gusts_mps",
                    "preceding_hour_precipitation_mm",
                ),
                r,
                strict=True,
            )
        )
        for r in weather_rows
    ]
    coverage = connection.execute(
        "SELECT b.source_start_utc,b.source_end_utc FROM ingestion_batches b "
        "JOIN clean_batches c ON c.raw_batch_id=b.batch_id "
        "WHERE b.source_id=%s AND b.dataset_id=%s "
        "AND c.processing_version='positions-v1' AND c.status='processed' "
        "AND b.source_start_utc IS NOT NULL AND b.source_end_utc IS NOT NULL",
        (source, dataset),
    ).fetchall()
    news = []
    if metadata["mode"] == "live":
        news_rows = connection.execute(
            "SELECT title,original_url,source_domain,publication_time_utc,"
            "first_seen_time_utc FROM news_mentions WHERE batch_id=%s LIMIT 10",
            (metadata["news_batch_id"],),
        ).fetchall()
        news = [
            dict(
                zip(
                    ("title", "url", "domain", "publication_time_utc", "first_seen_time_utc"),
                    r,
                    strict=True,
                )
            )
            for r in news_rows
        ]
        metadata["news_status"] = (
            "available" if news else "valid_empty" if metadata["news_batch_id"] else "unavailable"
        )
    zones = dict(
        type="FeatureCollection",
        features=[feature.model_dump(mode="json") for feature in fence.document.features],
    )
    return ReplayDataset(
        metadata, observations, zones, rules_for_port(port), weather, coverage, news
    )


def export_prepared(settings, path):
    path = settings.project_path(Path(path))
    if path.exists() or Path(str(path) + ".sha256").exists():
        raise ReplayError("choose_new_prepared_path")
    with connect_db(settings) as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        metadata, fence, port = fingerprint(connection, settings, "historical", datetime.now(UTC))
        dataset = load_dataset(connection, metadata, fence, port)
    document = public_json(
        dict(
            format=FORMAT,
            metadata=dataset.metadata,
            zones=dataset.zones,
            rules=asdict(dataset.rules),
            weather=dataset.weather,
            coverage=dataset.coverage,
            observations=[asdict(p) for points in dataset.groups.values() for p in points],
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, separators=(",", ":"), allow_nan=False)
    checksum, size = checksum_file(path)
    with Path(str(path) + ".sha256").open("x", encoding="ascii") as checksum_stream:
        checksum_stream.write(checksum + "\n")
    return dict(
        path=str(path),
        sha256=checksum,
        byte_count=size,
        observations=metadata.get("observation_count") or dataset.metadata["observation_count"],
        revision=dataset.metadata["revision"],
    )


def load_prepared(path):
    from portwatch.visits import VisitRules

    path = Path(path)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ReplayError("prepared_file_too_large")
    if checksum_file(path)[0] != Path(str(path) + ".sha256").read_text("ascii").strip():
        raise ReplayError("prepared_checksum_mismatch")
    document = json.loads(path.read_text("utf-8"))
    if document["format"] != FORMAT or document["metadata"]["mode"] != "historical":
        raise ReplayError("unsupported_prepared_format")
    if len(document["observations"]) > MAX_POSITIONS:
        raise ReplayError("prepared_position_limit_exceeded")
    observations = [
        Observation(
            **dict(
                p, at=stamp(p["at"]), flags=tuple(p["flags"]), memberships=tuple(p["memberships"])
            )
        )
        for p in document["observations"]
    ]
    return ReplayDataset(
        document["metadata"],
        observations,
        document["zones"],
        VisitRules(**document["rules"]),
        document["weather"],
        [(stamp(a), stamp(b)) for a, b in document["coverage"]],
    )
