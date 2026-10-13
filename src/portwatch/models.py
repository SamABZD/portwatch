"""Finite dbt SQL validation in owned output schemas; Python remains the orchestrator."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from portwatch.bookkeeping import finish_run
from portwatch.db import connect_db
from portwatch.events import emit
from portwatch.ingest import plan
from portwatch.pipeline import PipelineError, begin_workflow, owned_lock
from portwatch.settings import Settings


def dbt_environment(settings, schema):
    env = os.environ.copy()
    env.pop("PORTWATCH_AIS_API_KEY", None)
    env.pop("PORTWATCH_DB_PASSWORD", None)
    env.update(
        PORTWATCH_DB_HOST=settings.db_host,
        PORTWATCH_DB_PORT=str(settings.db_port),
        PORTWATCH_DB_NAME=settings.db_name,
        PORTWATCH_DB_USER=settings.db_user,
        PORTWATCH_DB_SSLMODE=settings.db_sslmode,
        DBT_ENV_SECRET_PORTWATCH_PASSWORD=settings.db_password.get_secret_value(),
        PORTWATCH_DBT_SCHEMA=schema,
        PGOPTIONS="-c timezone=UTC -c statement_timeout=300000 -c lock_timeout=5000",
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
        DO_NOT_TRACK="1",
    )
    return env


def result_summary(path):
    """Project public outcomes, never compiled SQL, database credentials or error bodies."""
    document = json.loads(path.read_text("utf-8"))
    results = document["results"]
    if not results:
        raise PipelineError("dbt_no_results")
    return [
        dict(node=row["unique_id"], status=row["status"], seconds=row.get("execution_time"))
        for row in results
    ]


def run_models(settings: Settings, port: str, start: str, end: str, *, check_live=False):
    requests = plan(settings, port, False, start, end)
    project = settings.project_path(Path("analytics"))
    default = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = Path(os.environ.get("PORTWATCH_DBT_PYTHON", str(default)))
    if not executable.is_absolute() or not executable.is_file():
        raise PipelineError("dbt_environment_missing_run_uv_sync_in_analytics")
    if not (project / "dbt_project.yml").is_file():
        raise PipelineError("dbt_project_missing_use_repository_checkout")
    run_token = uuid4().hex
    schema = "pw_dbt_" + run_token
    folder = settings.project_path(settings.dbt_artifacts_dir) / run_token
    folder.mkdir(parents=True, exist_ok=False)
    variables = dict(
        port_id=port,
        start_utc=requests[0][0].source_start_utc.isoformat(),
        end_utc=requests[-1][0].source_end_utc.isoformat(),
        check_live=check_live,
    )
    env = dbt_environment(settings, schema)
    options = [
        "--no-partial-parse",
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(project),
        "--target-path",
        str(folder / "target"),
        "--log-path",
        str(folder / "logs"),
        "--vars",
        json.dumps(variables),
    ]
    key = f"models-v1:{port}:{start}:{end}"
    with connect_db(settings) as connection:
        # Session locks coordinate existing writers without holding a long SQL transaction.
        # Model builds across ports serialize, since all consume shared source relations.
        with owned_lock(connection, "models", "dbt"), owned_lock(connection, "mutation", port):
            run_id = begin_workflow(connection, key, "pipeline_models")
            report = dict(
                run_id=str(run_id),
                schema=schema,
                port_id=port,
                start=start,
                end=end,
                status="failed",
                artifacts=str(folder.relative_to(settings.project_path(Path()))),
                live_freshness="pending"
                if check_live
                else "not_requested_local_collectors_stopped",
            )
            try:
                for action in (["build"], ["source", "freshness"], ["docs", "generate"]):
                    if action == ["source", "freshness"] and not check_live:
                        continue
                    label = "-".join(action)
                    with (folder / f"{label}.log").open("wb") as log:
                        completed = subprocess.run(
                            [
                                str(executable),
                                "-c",
                                "from dbt.cli.main import cli; cli()",
                                "--no-use-colors",
                                *action,
                                *options,
                                *(["--static"] if action == ["docs", "generate"] else []),
                            ],
                            cwd=project,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=1800,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                    if action == ["build"] and (folder / "target/run_results.json").is_file():
                        report["nodes"] = result_summary(folder / "target/run_results.json")
                    if completed.returncode:
                        raise PipelineError("dbt_" + label.replace("-", "_") + "_failed")
                    if action == ["build"] and (
                        not report.get("nodes")
                        or any(row["status"] not in {"success", "pass"} for row in report["nodes"])
                    ):
                        raise PipelineError("dbt_build_nodes_failed")
                    if action == ["source", "freshness"]:
                        report["live_source_outcomes"] = result_summary(
                            folder / "target/sources.json"
                        )
                        report["live_freshness"] = (
                            "passed"
                            if all(
                                row["status"] == "pass" for row in report["live_source_outcomes"]
                            )
                            else "degraded"
                        )
                # Docs can replace run_results; preserve the build's test outcomes above.
                rows = connection.execute(
                    sql.SQL("SELECT count(*) FROM {}.port_activity_hourly").format(
                        sql.Identifier(schema)
                    )
                ).fetchone()[0]
                report["input_partitions"] = [
                    dict(partition_id=row[0], input_sha256=row[1], weather_batch_id=row[2])
                    for row in connection.execute(
                        sql.SQL(
                            "SELECT partition_id,input_sha256,weather_batch_id "
                            "FROM {}.stg_partitions ORDER BY start_utc,partition_id"
                        ).format(sql.Identifier(schema))
                    )
                ]
                report.update(status="succeeded", hourly_rows=rows)
                connection.execute(
                    "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
                    (Jsonb(report), run_id),
                )
                finish_run(connection, run_id, input_count=len(requests), output_count=rows)
                emit("dbt_build_finished", port_id=port, hours=rows, status="succeeded")
            except Exception as error:
                reason = error.code if isinstance(error, PipelineError) else type(error).__name__
                report["reason"] = reason
                connection.execute(
                    "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
                    (Jsonb(report), run_id),
                )
                finish_run(connection, run_id, error=error)
                emit("dbt_build_finished", port_id=port, status="failed", reason=reason)
                raise PipelineError(reason) from None
            finally:
                (folder / "summary.json").write_text(json.dumps(report, indent=2), "utf-8")
    return report
