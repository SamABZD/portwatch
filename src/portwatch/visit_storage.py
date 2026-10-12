"""Source-separated full-dataset recomputation and read-only event-time prefixes."""

import json
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import timedelta
from itertools import groupby

from psycopg import sql

from portwatch.bookkeeping import finish_run, start_run
from portwatch.settings import Settings, load_port_config
from portwatch.visits import (
    RULE_VERSION,
    InferenceError,
    TrackPoint,
    VisitEpisode,
    VisitRules,
    WaitingEpisode,
    identity,
    infer_track,
    utc,
)
from portwatch.zones import (
    ASSIGNMENT_VERSION,
    AssignmentScope,
    assign_zones,
    configured_geofence,
    demo_scope,
)

DATASET_FILTER = "p.source_id=%s AND p.dataset_id=%s AND p.processing_version=%s"
PARTITION_FILTER = """port_id=%s AND source_id=%s AND dataset_id=%s AND clean_version=%s
    AND geofence_version=%s AND assignment_version=%s AND rule_id=%s"""


def rules_for_port(port):
    return VisitRules(
        port.waiting_speed_knots,
        port.waiting_min_minutes * 60,
        port.max_observation_gap_minutes * 60,
        port.visit_persistence_minutes * 60,
        port.visit_min_consistent_observations,
    )


def partition_parameters(scope, port_id, version, rules):
    return (
        port_id,
        scope.source_id,
        scope.dataset_id,
        scope.clean_version,
        version,
        ASSIGNMENT_VERSION,
        rules.rule_id,
    )


def partition_identity(scope, port_id, version, rules, mmsi):
    return identity([*partition_parameters(scope, port_id, version, rules), mmsi])


def track_groups(connection, scope, port_id, version, cutoff=None):
    """Stream in deterministic vessel/event-time order; a group buffers only one vessel."""
    with connection.cursor(name="visit_tracks") as cursor:
        cursor.itersize = 4096
        cursor.execute(
            """WITH memberships AS (
                SELECT m.position_id,bool_or(z.zone_type='terminal') AS terminal,
                    bool_or(z.zone_type='harbor') AS harbor,
                    array_agg(z.zone_id ORDER BY z.zone_id)
                        FILTER (WHERE z.zone_type='waiting_candidate') AS candidates
                FROM position_zone_memberships m JOIN port_zones z
                    ON z.port_id=m.port_id AND z.version=m.geofence_version AND z.zone_id=m.zone_id
                WHERE m.port_id=%s AND m.geofence_version=%s AND m.assignment_version=%s
                GROUP BY m.position_id)
            SELECT p.id,p.observation_id,p.mmsi,p.effective_at,p.speed_knots,p.quality_flags,
                coalesce(m.terminal,false),coalesce(m.harbor,false),coalesce(m.candidates,'{}'),a.state
            FROM vessel_positions p LEFT JOIN memberships m ON m.position_id=p.id
            LEFT JOIN position_zone_assignments a ON a.position_id=p.id
                AND a.port_id=%s AND a.geofence_version=%s AND a.assignment_version=%s
            WHERE """
            + DATASET_FILTER
            + " AND (%s::timestamptz IS NULL OR p.effective_at<=%s) "
            "ORDER BY p.mmsi,p.effective_at,p.observation_id",
            (port_id, version, ASSIGNMENT_VERSION) * 2
            + (scope.source_id, scope.dataset_id, scope.clean_version, cutoff, cutoff),
        )
        for mmsi, rows in groupby(cursor, key=lambda row: row[2]):
            points = []
            for row in rows:
                if row[9] is None:
                    raise InferenceError(
                        "position is missing this zone version; classify zones first"
                    )
                points.append(
                    TrackPoint(
                        row[0], row[1], row[3], row[4], row[7], row[6], tuple(row[8]), tuple(row[5])
                    )
                )
            yield mmsi, points


def point_hash(points):
    return identity(
        [
            (
                p.observation_id,
                p.at.isoformat(),
                p.speed_knots,
                p.harbor,
                p.terminal,
                p.candidate_zones,
                p.quality_flags,
            )
            for p in points
        ]
    )


@dataclass
class InferenceCounts:
    tracks: int = 0
    positions: int = 0
    distinct_event_times: int = 0
    visits: int = 0
    completed_visits: int = 0
    partial_visits: int = 0
    waiting_episodes: int = 0
    regional_candidates: int = 0
    port_associated_candidates: int = 0
    unknown_current_tracks: int = 0

    def add(self, points, result):
        self.tracks += 1
        self.positions += len(points)
        self.distinct_event_times += len(result.states)
        self.visits += len(result.visits)
        self.completed_visits += sum(v.completed for v in result.visits)
        self.partial_visits += sum(not v.completed for v in result.visits)
        self.waiting_episodes += len(result.waiting)
        self.regional_candidates += sum(w.associated_visit_id is None for w in result.waiting)
        self.port_associated_candidates += sum(
            w.associated_visit_id is not None for w in result.waiting
        )
        self.unknown_current_tracks += result.current_state == "unknown"


def _insert_episodes(connection, table, model, partition_id, episodes):
    names = [field.name for field in fields(model)]
    columns = ["partition_id", *names]
    if model is WaitingEpisode:
        columns.append("association_scope")
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.SQL(",").join(sql.Placeholder() for _ in columns),
    )
    rows = []
    for episode in episodes:
        row = [partition_id]
        for name in names:
            value = getattr(episode, name)
            row.append(list(value) if isinstance(value, tuple) else value)
        if model is WaitingEpisode:
            row.append("port" if episode.associated_visit_id else "region")
        rows.append(row)
    if rows:
        with connection.cursor() as cursor:
            cursor.executemany(statement, rows)


def recompute_visits(
    connection,
    scope: AssignmentScope,
    port_id: str,
    version: str,
    rules: VisitRules,
    *,
    before_commit: Callable[[], None] | None = None,
) -> InferenceCounts:
    """Atomically replace this dataset/rule/geofence's complete affected vessel histories."""
    run_id = start_run(connection, "visits")
    counts = InferenceCounts()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute("SELECT set_config('statement_timeout','120s',true)")
            connection.execute("SELECT set_config('lock_timeout','15s',true)")
            connection.execute("SELECT pg_advisory_xact_lock(734920028)")
            connection.execute(
                "INSERT INTO visit_rule_sets(rule_id,rule_version,parameters) "
                "VALUES (%s,%s,%s::jsonb) "
                "ON CONFLICT(rule_id) DO NOTHING",
                (rules.rule_id, RULE_VERSION, json.dumps(rules.document)),
            )
            saved = connection.execute(
                "SELECT parameters FROM visit_rule_sets WHERE rule_id=%s", (rules.rule_id,)
            ).fetchone()[0]
            if saved != rules.document:
                raise InferenceError("stored rules do not match their content identity")
            target = partition_parameters(scope, port_id, version, rules)
            connection.execute(
                "DELETE FROM visit_inference_partitions WHERE " + PARTITION_FILTER, target
            )
            connection.execute(
                "CREATE TEMP TABLE pw_visit_states "
                "(LIKE vessel_activity_states INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            for mmsi, points in track_groups(connection, scope, port_id, version):
                partition_id = partition_identity(scope, port_id, version, rules, mmsi)
                cutoff = max(scope.end_utc, max(p.at for p in points))
                result = infer_track(points, rules, partition_id, cutoff)
                counts.add(points, result)
                connection.execute(
                    """INSERT INTO visit_inference_partitions(partition_id,port_id,source_id,
                        dataset_id,
                        clean_version,geofence_version,assignment_version,rule_id,mmsi,input_sha256,
                        position_count,distinct_event_times,result_as_of_utc,current_state)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        partition_id,
                        *target,
                        mmsi,
                        point_hash(points),
                        len(points),
                        len(result.states),
                        cutoff,
                        result.current_state,
                    ),
                )
                _insert_episodes(
                    connection, "vessel_visits", VisitEpisode, partition_id, result.visits
                )
                _insert_episodes(
                    connection, "waiting_episodes", WaitingEpisode, partition_id, result.waiting
                )
                with (
                    connection.cursor() as cursor,
                    cursor.copy(
                        "COPY pw_visit_states(partition_id,position_id,observed_at,observed_state,"
                        "port_inside,waiting_candidate,gap_before_seconds,interval_state_before,"
                        "quality_flags) FROM STDIN"
                    ) as copy,
                ):
                    for state in result.states:
                        for point in state.points:
                            copy.write_row(
                                (
                                    partition_id,
                                    point.position_id,
                                    state.at,
                                    state.state,
                                    state.port_inside,
                                    state.waiting,
                                    state.gap_before_seconds,
                                    state.interval_state_before,
                                    list(state.flags),
                                )
                            )
            if counts.positions == 0:
                raise InferenceError("no cleaned positions in the selected dataset")
            connection.execute("ANALYZE visit_inference_partitions")
            connection.execute("INSERT INTO vessel_activity_states SELECT * FROM pw_visit_states")
            connection.execute("ANALYZE vessel_activity_states")
            if before_commit:
                before_commit()
        finish_run(connection, run_id, input_count=counts.positions, output_count=counts.positions)
    except Exception as error:
        finish_run(connection, run_id, error=error)
        raise
    return counts


def preview_visits(connection, scope, port_id, version, rules, cutoff):
    utc(cutoff)
    counts = InferenceCounts()
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SELECT set_config('statement_timeout','120s',true)")
        for mmsi, points in track_groups(connection, scope, port_id, version, cutoff):
            partition_id = partition_identity(scope, port_id, version, rules, mmsi)
            counts.add(points, infer_track(points, rules, partition_id, cutoff))
    return counts


def transform_visits(settings: Settings, connection, port_id: str, *, as_of_utc=None, version=None):
    port = load_port_config(settings.project_path(settings.port_config))
    if port.port_id != port_id:
        raise InferenceError("select the configured port")
    if version is None:
        geofence = configured_geofence(settings, port_id)
        version = geofence.document.version
        saved = connection.execute(
            "SELECT content_sha256 FROM geofence_versions WHERE port_id=%s AND version=%s",
            (port_id, version),
        ).fetchone()
        if not saved or saved[0] != geofence.content_sha256:
            raise InferenceError("configured geofence is not loaded or changed; run geo load")
    rules = rules_for_port(port)
    scope = demo_scope(settings, port_id)
    if as_of_utc is not None:
        return version, rules, preview_visits(connection, scope, port_id, version, rules, as_of_utc)
    first, last = connection.execute(
        "SELECT min(p.effective_at),max(p.effective_at) FROM vessel_positions p WHERE "
        + DATASET_FILTER,
        (scope.source_id, scope.dataset_id, scope.clean_version),
    ).fetchone()
    if first is None:
        raise InferenceError("clean the saved historical positions first")
    expanded = AssignmentScope(
        scope.source_id,
        scope.dataset_id,
        min(scope.start_utc, first),
        max(scope.end_utc, last + timedelta(microseconds=1)),
        scope.clean_version,
    )
    assign_zones(connection, port_id, version, expanded)
    return version, rules, recompute_visits(connection, expanded, port_id, version, rules)
