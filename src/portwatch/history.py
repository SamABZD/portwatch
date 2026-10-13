"""Read only the explicitly published, bounded dbt history snapshot."""

import json
import re

from psycopg import sql

from portwatch.db import connect_db
from portwatch.health import json_value


def published_history(settings):
    if not settings.history_db_name:
        raise ValueError("history_not_deployed")
    path = settings.project_path(settings.live_status_dir) / "history-models.json"
    report = json.loads(path.read_text("utf-8"))
    if (
        report.get("status") != "succeeded"
        or report.get("hourly_rows") != 168
        or report.get("port_id") != "los_angeles"
        or not re.fullmatch(r"pw_dbt_[a-f0-9]{32}", report.get("schema", ""))
    ):
        raise ValueError("history_publication_invalid")
    history = settings.model_copy(update={"db_name": settings.history_db_name})
    with connect_db(history) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout='5s'")
        cursor = connection.execute(
            sql.SQL(
                "SELECT hour_utc,observation_count,distinct_vessels_observed,"
                "confirmed_arrival_count,confirmed_departure_count,baseline_hours,baseline_days,"
                "baseline_eligible,anomaly_label,observation_p50,source_interval_available "
                "FROM {}.activity_baselines WHERE port_id='los_angeles' ORDER BY hour_utc LIMIT 169"
            ).format(sql.Identifier(report["schema"]))
        )
        columns = [column.name for column in cursor.description]
        hours = [dict(zip(columns, row, strict=True)) for row in cursor]
    if len(hours) != 168:
        raise ValueError("history_hour_grid_incomplete")
    result = dict(
        port_id="los_angeles",
        start=report["start"],
        end=report["end"],
        hours=hours,
        models=sum(row["status"] == "success" for row in report["nodes"]),
        tests=sum(row["status"] == "pass" for row in report["nodes"]),
        limitations="Observed AIS reports and experimental visits; "
        "not total traffic or official calls. "
        "Every baseline excludes its current hour. Seven days are insufficient for anomaly labels.",
    )
    return json.loads(json.dumps(result, default=json_value))
