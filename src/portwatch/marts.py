"""Transactional SQL marts and event-time prefix reconstruction over existing contracts."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from importlib.resources import files

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from portwatch.bookkeeping import finish_run, start_run
from portwatch.context import plan_context
from portwatch.context_storage import cached_response
from portwatch.settings import Settings, load_port_config
from portwatch.visit_storage import (
    CONTEXT_FILTER,
    PARTITION_FILTER,
    _insert_episodes,
    context_parameters,
    partition_identity,
    partition_parameters,
    recompute_visits,
    rules_for_port,
    track_groups,
)
from portwatch.visits import VisitEpisode, VisitRules, WaitingEpisode, identity, infer_track, utc
from portwatch.zones import (
    ASSIGNMENT_VERSION,
    AssignmentScope,
    assign_zones,
    configured_geofence,
    demo_scope,
)

METRIC_VERSION = "marts-v1"
HOURLY_SQL = files("portwatch").joinpath("queries/hourly.sql").read_text("utf-8")


class MartError(ValueError):
    """Non-secret actionable analytical validation failure."""


@dataclass(frozen=True)
class MartConfig:
    port_id: str
    geofence_version: str
    visit_rules: VisitRules
    snapshot_staleness_seconds: int = 900
    weather_batch_id: str | None = None
    weather_product: str = "historical_reanalysis"

    def __post_init__(self):
        if not 60 <= self.snapshot_staleness_seconds <= 3600:
            raise MartError("snapshot staleness must be 60 to 3600 seconds")
        if self.weather_product not in {"historical_reanalysis", "current_forecast"}:
            raise MartError("unsupported weather product")

    def document(self, clean_version, context_dataset_ids=()):
        document = dict(
            metric_version=METRIC_VERSION,
            port_id=self.port_id,
            geofence_version=self.geofence_version,
            assignment_version=ASSIGNMENT_VERSION,
            clean_version=clean_version,
            visit_rule_id=self.visit_rules.rule_id,
            snapshot_staleness_seconds=self.snapshot_staleness_seconds,
            snapshot_time="utc_hour_start",
            transition_hour="confirmation_time",
            waiting_duration_hour="last_observed_time",
            dwell="completed_interval_midpoint",
            weather_product=self.weather_product,
            weather_batch_id=self.weather_batch_id,
        )
        if len(context_dataset_ids) > 1:
            document["context_dataset_ids"] = list(context_dataset_ids)
        return document

    def analytical_version(self, scope):
        return identity(self.document(scope.clean_version, scope.context_dataset_ids))

    def partition_id(self, scope):
        return identity(
            [self.port_id, scope.source_id, scope.dataset_id, self.analytical_version(scope)]
        )


@dataclass(frozen=True)
class MartResult:
    partition_id: str
    analytical_version: str
    hours: int
    observations: int
    arrivals: int
    departures: int
    completed_dwell_samples: int
    waiting_vessel_hours: int


def validate_scope(scope):
    for value in (scope.start_utc, scope.end_utc):
        utc(value)
        if value.minute or value.second or value.microsecond:
            raise MartError("mart period must use complete UTC hour boundaries")
    if not timedelta(hours=1) <= scope.end_utc - scope.start_utc <= timedelta(days=7):
        raise MartError("mart period must contain 1 hour to 7 days")


def expanded_scope(connection, scope):
    first, last = connection.execute(
        "SELECT min(p.effective_at),max(p.effective_at) FROM vessel_positions p WHERE "
        + CONTEXT_FILTER,
        context_parameters(scope),
    ).fetchone()
    if first is None:
        return scope
    return replace(
        scope,
        start_utc=min(scope.start_utc, first),
        end_utc=max(scope.end_utc, last + timedelta(microseconds=1)),
    )


def _weather_request(connection, config):
    if config.weather_batch_id is None:
        return None
    row = connection.execute(
        """SELECT r.request_key,r.product,r.port_id,c.source_metadata
        FROM context_responses c JOIN context_requests r USING(request_key)
        WHERE c.batch_id=%s""",
        (config.weather_batch_id,),
    ).fetchone()
    if not row or row[1] != config.weather_product or row[2] != config.port_id:
        raise MartError(f"marts require a matching-port saved {config.weather_product} revision")
    units = row[3].get("hourly_units", {})
    if units.get("wind_speed_10m") not in {None, "m/s"} or units.get("precipitation") not in {
        None,
        "mm",
    }:
        raise MartError("selected weather revision has unexpected units")
    return row[0]


def _prepare_points(connection, scope, config, cutoff):
    connection.execute(
        """CREATE TEMP TABLE pw_mart_points ON COMMIT DROP AS
        SELECT p.id,p.mmsi,p.effective_at,p.timestamp_quality,p.quality_flags,
            CASE WHEN a.state='outside' THEN 'outside' ELSE a.primary_zone_id END AS primary_zone,
            z.zone_type,a.state AS assignment_state
        FROM vessel_positions p LEFT JOIN position_zone_assignments a ON a.position_id=p.id
            AND a.port_id=%s AND a.geofence_version=%s AND a.assignment_version=%s
        LEFT JOIN port_zones z ON z.port_id=a.port_id AND z.version=a.geofence_version
            AND z.zone_id=a.primary_zone_id WHERE """
        + CONTEXT_FILTER
        + " AND (%s::timestamptz IS NULL OR p.effective_at<=%s)",
        (
            config.port_id,
            config.geofence_version,
            ASSIGNMENT_VERSION,
            scope.source_id,
            list(scope.dataset_ids),
            scope.clean_version,
            cutoff,
            cutoff,
        ),
    )
    if connection.execute(
        "SELECT count(*) FROM pw_mart_points WHERE assignment_state IS NULL"
    ).fetchone()[0]:
        raise MartError("positions are missing the selected spatial assignment")
    connection.execute("CREATE INDEX ON pw_mart_points(effective_at,mmsi)")
    connection.execute("CREATE INDEX ON pw_mart_points(mmsi,effective_at)")
    connection.execute("ANALYZE pw_mart_points")
    connection.execute(
        """CREATE TEMP TABLE pw_mart_zone_ids ON COMMIT DROP AS
        SELECT zone_id FROM port_zones WHERE port_id=%s AND version=%s
        UNION SELECT 'outside' UNION SELECT 'unknown'""",
        (config.port_id, config.geofence_version),
    )


def _prepare_health(connection, scope):
    connection.execute(
        """CREATE TEMP TABLE pw_mart_coverage ON COMMIT DROP AS
        SELECT range_agg(tstzrange(b.source_start_utc,b.source_end_utc,'[)')) FILTER
            (WHERE cb.status='processed' AND b.source_start_utc IS NOT NULL
                AND b.source_end_utc IS NOT NULL) AS available
        FROM ingestion_batches b JOIN clean_batches cb ON cb.raw_batch_id=b.batch_id
        WHERE b.source_id=%s AND b.dataset_id=ANY(%s) AND cb.processing_version=%s""",
        context_parameters(scope),
    )
    count, processed, modes = connection.execute(
        """SELECT count(*),count(*) FILTER (WHERE cb.status='processed'),array_agg(DISTINCT b.mode)
        FROM ingestion_batches b JOIN clean_batches cb ON cb.raw_batch_id=b.batch_id
        WHERE b.source_id=%s AND b.dataset_id=%s AND cb.processing_version=%s""",
        (scope.source_id, scope.dataset_id, scope.clean_version),
    ).fetchone()
    if modes and len(modes) > 1:
        raise MartError("historical and live inputs cannot share a mart series")
    rejected, duplicates = connection.execute(
        """SELECT count(*) FILTER (WHERE cr.outcome='quarantined'),
            count(*) FILTER (WHERE cr.outcome='duplicate')
        FROM clean_records cr JOIN ingestion_batches b ON b.batch_id=cr.raw_batch_id
        WHERE b.source_id=%s AND b.dataset_id=%s AND cr.processing_version=%s""",
        (scope.source_id, scope.dataset_id, scope.clean_version),
    ).fetchone()
    complete = count > 0 and count == processed
    health = dict(
        clean_inputs=count,
        processed_inputs=processed,
        pending_or_failed_inputs=count - processed,
        quarantined_records=rejected if complete else None,
        duplicate_records=duplicates,
        news_context="excluded_recent_source",
        coverage="processed_input_period_not_total_ais",
    )
    collector = (
        "not_applicable_historical"
        if modes == ["historical"]
        else "unknown_historical_collector_state"
    )
    return health, rejected if complete else None, collector


def _prepare_episodes(connection, scope, config, cutoff):
    target = partition_parameters(
        scope, config.port_id, config.geofence_version, config.visit_rules
    )
    if cutoff is None:
        connection.execute(
            "CREATE TEMP TABLE pw_mart_selected_partitions ON COMMIT DROP AS "
            "SELECT partition_id FROM visit_inference_partitions WHERE " + PARTITION_FILTER,
            target,
        )
        connection.execute(
            "CREATE TEMP TABLE pw_mart_visits ON COMMIT DROP AS SELECT v.* FROM vessel_visits v "
            "JOIN pw_mart_selected_partitions p USING(partition_id)"
        )
        connection.execute(
            "CREATE TEMP TABLE pw_mart_waits ON COMMIT DROP AS SELECT w.* FROM waiting_episodes w "
            "JOIN pw_mart_selected_partitions p USING(partition_id)"
        )
    else:
        connection.execute(
            "CREATE TEMP TABLE pw_mart_visits "
            "(LIKE vessel_visits INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        connection.execute(
            "CREATE TEMP TABLE pw_mart_waits "
            "(LIKE waiting_episodes INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        for mmsi, points in track_groups(
            connection, scope, config.port_id, config.geofence_version, cutoff
        ):
            partition_id = partition_identity(
                scope, config.port_id, config.geofence_version, config.visit_rules, mmsi
            )
            result = infer_track(points, config.visit_rules, partition_id, cutoff)
            _insert_episodes(
                connection, "pw_mart_visits", VisitEpisode, partition_id, result.visits
            )
            _insert_episodes(
                connection, "pw_mart_waits", WaitingEpisode, partition_id, result.waiting
            )
    connection.execute("ANALYZE pw_mart_visits")
    connection.execute("ANALYZE pw_mart_waits")


def _hour_rows(connection, scope, config, cutoff=None):
    _prepare_points(connection, scope, config, cutoff)
    health, rejected, collector = _prepare_health(connection, scope)
    _prepare_episodes(connection, scope, config, cutoff)
    options = dict(
        start=scope.start_utc,
        end=scope.end_utc,
        cutoff=cutoff or scope.end_utc,
        stale=config.snapshot_staleness_seconds,
        rejected=rejected,
        collector=collector,
        weather=config.weather_batch_id,
        weather_product=config.weather_product,
        processing_complete=health["clean_inputs"] > 0 and health["pending_or_failed_inputs"] == 0,
    )
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(HOURLY_SQL, options)
        rows = cursor.fetchall()
    return rows, health, rejected


def validate_rows(rows, *, expected_observations=None, expected_hours=None):
    if expected_hours is not None and len(rows) != expected_hours:
        raise MartError("hour grid is incomplete")
    if len({row["hour_utc"] for row in rows}) != len(rows):
        raise MartError("analytical grain is not unique")
    if (
        expected_observations is not None
        and sum(row["observation_count"] or 0 for row in rows) != expected_observations
    ):
        raise MartError("hourly observations do not reconcile to source positions")
    for row in rows:
        snapshot = row["snapshot_vessel_count"]
        zones = list(row["snapshot_zone_counts"].values())
        if snapshot is not None and (
            any(value is None for value in zones) or sum(zones) != snapshot
        ):
            raise MartError("snapshot zone counts multiply or omit vessels")
        if snapshot is None and any(value is not None for value in zones):
            raise MartError("unknown snapshot has numeric zone counts")
        if (
            row["completed_dwell_sample_count"] == 0
            and row["completed_dwell_median_seconds"] is not None
        ):
            raise MartError("dwell value has no completed samples")


def recompute_marts(
    connection,
    scope: AssignmentScope,
    config: MartConfig,
    *,
    before_commit: Callable[[], None] | None = None,
) -> MartResult:
    validate_scope(scope)
    # Append classifications separately; publication of visits and marts is atomic together.
    expanded = expanded_scope(connection, scope)
    for dataset in expanded.dataset_ids:
        single = replace(expanded, dataset_id=dataset, context_dataset_ids=())
        if connection.execute(
            "SELECT 1 FROM vessel_positions p WHERE " + CONTEXT_FILTER + " LIMIT 1",
            context_parameters(single),
        ).fetchone():
            assign_zones(connection, config.port_id, config.geofence_version, single)
    run_id = start_run(connection, "marts")
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute("SELECT set_config('statement_timeout','120s',true)")
            connection.execute("SELECT set_config('lock_timeout','15s',true)")
            connection.execute("SELECT pg_advisory_xact_lock(734920028)")
            recompute_visits(
                connection, expanded, config.port_id, config.geofence_version, config.visit_rules
            )
            weather_request = _weather_request(connection, config)
            version = config.analytical_version(scope)
            partition_id = config.partition_id(scope)
            document = config.document(scope.clean_version, scope.context_dataset_ids)
            connection.execute(
                "INSERT INTO mart_rule_sets VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (version, METRIC_VERSION, config.visit_rules.rule_id, Jsonb(document)),
            )
            if (
                connection.execute(
                    "SELECT parameters FROM mart_rule_sets WHERE analytical_version=%s", (version,)
                ).fetchone()[0]
                != document
            ):
                raise MartError("analytical rules do not match their content identity")
            rows, health, rejected = _hour_rows(connection, scope, config)
            expected = connection.execute(
                "SELECT count(*) FROM pw_mart_points WHERE effective_at>=%s AND effective_at<%s",
                (scope.start_utc, scope.end_utc),
            ).fetchone()[0]
            validate_rows(
                rows,
                expected_observations=expected,
                expected_hours=int((scope.end_utc - scope.start_utc).total_seconds() / 3600),
            )
            inputs = connection.execute(
                "SELECT mmsi,input_sha256,result_as_of_utc FROM visit_inference_partitions WHERE "
                + PARTITION_FILTER
                + " ORDER BY mmsi",
                partition_parameters(
                    scope, config.port_id, config.geofence_version, config.visit_rules
                ),
            ).fetchall()
            input_hash = identity([inputs, health, document, scope.start_utc, scope.end_utc])
            input_as_of = max((row[2] for row in inputs), default=scope.end_utc)
            connection.execute("DELETE FROM mart_partitions WHERE partition_id=%s", (partition_id,))
            connection.execute(
                """INSERT INTO mart_partitions (
                partition_id,port_id,source_id,dataset_id,clean_version,
                geofence_version,assignment_version,rule_id,analytical_version,start_utc,end_utc,
                input_as_of_utc,input_sha256,source_health,unattributed_rejected_record_count,
                weather_batch_id,weather_request_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    partition_id,
                    config.port_id,
                    scope.source_id,
                    scope.dataset_id,
                    scope.clean_version,
                    config.geofence_version,
                    ASSIGNMENT_VERSION,
                    config.visit_rules.rule_id,
                    version,
                    scope.start_utc,
                    scope.end_utc,
                    input_as_of,
                    input_hash,
                    Jsonb(health),
                    rejected,
                    config.weather_batch_id,
                    weather_request,
                ),
            )
            names = [
                "partition_id",
                "port_id",
                "source_id",
                "dataset_id",
                "analytical_version",
                *rows[0],
            ]
            statement = sql.SQL("INSERT INTO port_activity_hourly ({}) VALUES ({})").format(
                sql.SQL(",").join(map(sql.Identifier, names)),
                sql.SQL(",").join(sql.Placeholder() for _ in names),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    statement,
                    [
                        (
                            partition_id,
                            config.port_id,
                            scope.source_id,
                            scope.dataset_id,
                            version,
                            *(
                                Jsonb(value) if isinstance(value, dict) else value
                                for value in row.values()
                            ),
                        )
                        for row in rows
                    ],
                )
            if before_commit:
                before_commit()
        finish_run(connection, run_id, input_count=expected, output_count=len(rows))
    except Exception as error:
        finish_run(connection, run_id, error=error)
        raise
    return MartResult(
        partition_id,
        version,
        len(rows),
        expected,
        sum(row["confirmed_arrival_count"] or 0 for row in rows),
        sum(row["confirmed_departure_count"] or 0 for row in rows),
        sum(row["completed_dwell_sample_count"] or 0 for row in rows),
        sum(row["waiting_candidate_count"] or 0 for row in rows),
    )


def read_prefix_hours(connection, scope, config, as_of_utc, *, completed_only=False):
    """Only temp objects; no audit or persistent rows, and no event times after cutoff."""
    validate_scope(scope)
    utc(as_of_utc)
    if not scope.start_utc <= as_of_utc <= scope.end_utc:
        raise MartError("replay cutoff is outside the selected period")
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        connection.execute("SELECT set_config('statement_timeout','120s',true)")
        _weather_request(connection, config)
        rows, _, _ = _hour_rows(connection, scope, config, as_of_utc)
        validate_rows(rows)
    return [
        dict(
            row,
            port_id=config.port_id,
            source_id=scope.source_id,
            dataset_id=scope.dataset_id,
            analytical_version=config.analytical_version(scope),
            clean_version=scope.clean_version,
            weather_batch_id=config.weather_batch_id,
            result_semantics="event_time_prefix_retrospective_reanalysis",
        )
        for row in rows
        if not completed_only or row["complete_hour"]
    ]


def read_completed_hours(connection, scope, config, as_of_utc):
    """Stored retrospective output is exposed only once its whole input prefix is known."""
    validate_scope(scope)
    utc(as_of_utc)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """SELECT h.* FROM port_activity_hourly h JOIN mart_partitions p USING(partition_id)
            WHERE h.port_id=%s AND h.source_id=%s AND h.dataset_id=%s AND h.analytical_version=%s
            AND p.clean_version=%s AND h.hour_end_utc<=%s AND p.input_as_of_utc<=%s
            AND h.hour_utc>=%s AND h.hour_utc<%s
            ORDER BY h.hour_utc""",
            (
                config.port_id,
                scope.source_id,
                scope.dataset_id,
                config.analytical_version(scope),
                scope.clean_version,
                as_of_utc,
                as_of_utc,
                scope.start_utc,
                scope.end_utc,
            ),
        )
        return cursor.fetchall()


def configured_marts(settings: Settings, connection, port_id):
    port = load_port_config(settings.project_path(settings.port_config))
    if port_id != port.port_id:
        raise MartError("select the configured port")
    geofence = configured_geofence(settings, port_id)
    version = geofence.document.version
    loaded = connection.execute(
        "SELECT content_sha256 FROM geofence_versions WHERE port_id=%s AND version=%s",
        (port_id, version),
    ).fetchone()
    if not loaded or loaded[0] != geofence.content_sha256:
        raise MartError("configured geofence is not loaded or changed; run geo load")
    scope = demo_scope(settings, port_id)
    weather = cached_response(connection, plan_context(settings, port_id, "weather", demo=True))
    return scope, MartConfig(
        port_id,
        version,
        rules_for_port(port),
        port.snapshot_staleness_minutes * 60,
        weather.batch_id if weather else None,
    )


def transform_marts(settings, connection, port_id, *, as_of_utc=None):
    scope, config = configured_marts(settings, connection, port_id)
    if as_of_utc is not None:
        return config, read_prefix_hours(connection, scope, config, as_of_utc)
    return config, recompute_marts(connection, scope, config)
