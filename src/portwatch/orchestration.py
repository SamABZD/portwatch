"""Local Prefect 3.8.8 adapter. Business functions and recovery remain independent."""

import os
from datetime import UTC, datetime, timedelta

from portwatch.pipeline import (
    PipelineError,
    execute_step,
    run_context,
    run_historical,
    run_live,
    sanitized,
)
from portwatch.settings import PROJECT_ROOT


def configure_local():
    home = PROJECT_ROOT / "data/prefect"
    home.mkdir(parents=True, exist_ok=True)
    # Never inherit a Cloud profile or place Prefect metadata in analytical PostgreSQL.
    os.environ.update(
        PREFECT_HOME=str(home),
        PREFECT_PROFILES_PATH=str(home / "profiles.toml"),
        PREFECT_API_URL="http://127.0.0.1:4200/api",
        PREFECT_SERVER_DATABASE_CONNECTION_URL="sqlite+aiosqlite:///"
        + (home / "prefect.db").as_posix(),
        PREFECT_SERVER_ANALYTICS_ENABLED="false",
        PREFECT_CLOUD_ENABLE_ORCHESTRATION_TELEMETRY="false",
        PREFECT_SERVER_EPHEMERAL_ENABLED="false",
        PREFECT_LOGGING_LOG_PRINTS="false",
        DO_NOT_TRACK="1",
    )
    os.environ.pop("PREFECT_API_KEY", None)
    return home


# Imported only by the orchestration commands, never ordinary ingestion/doctor/help.
configure_local()
from prefect import flow, get_run_logger, serve, task  # noqa: E402
from prefect.cache_policies import NO_CACHE  # noqa: E402
from prefect.schedules import Interval  # noqa: E402
from prefect.task_runners import ThreadPoolTaskRunner  # noqa: E402


def retry_transient(task, task_run, state):
    try:
        state.result()
    except PipelineError as error:
        return error.transient
    return False


@task(
    name="committed-pipeline-step",
    retries=2,
    retry_delay_seconds=[1, 3],
    retry_condition_fn=retry_transient,
    persist_result=False,
    cache_policy=NO_CACHE,
)
def committed_step(port, day, step, key, parent, *, force=False):
    try:
        result = execute_step(port, day, step, key, parent, force=force)
        get_run_logger().info(
            "%s %s: input=%s output=%s checkpoint_reused=%s",
            day,
            step,
            result.get("input_count"),
            result.get("output_count"),
            result.get("checkpoint_reused"),
        )
        return result
    except Exception as error:
        raise sanitized(error) from None


@task(
    name="finite-live-processing",
    retries=2,
    retry_delay_seconds=[1, 3],
    retry_condition_fn=retry_transient,
    persist_result=False,
    cache_policy=NO_CACHE,
)
def live_task(port):
    try:
        report = run_live(port)
        get_run_logger().info(
            "New batches=%s positions=%s observation lag seconds=%s",
            report["new_batches"],
            report["new_positions"],
            report["observation_lag_seconds"],
        )
        return report
    except Exception as error:
        raise sanitized(error) from None


@task(
    name="independent-context",
    retries=2,
    retry_delay_seconds=[1, 3],
    retry_condition_fn=retry_transient,
    persist_result=False,
    cache_policy=NO_CACHE,
)
def context_task(kind, port):
    try:
        return run_context(kind, port)
    except Exception as error:
        raise sanitized(error) from None


def checked_historical(**arguments):
    try:
        result = run_historical(**arguments, step_runner=committed_step)
        if result["status"] == "failed":
            # Each day's durable audit retains its own safe reason and checkpoints.
            raise PipelineError("historical_partitions_failed")
        if result["status"] == "busy":
            raise PipelineError("overlapping_request_busy")
        return result
    except Exception as error:
        raise sanitized(error) from None


@flow(
    name="portwatch-historical-demo",
    persist_result=False,
    task_runner=ThreadPoolTaskRunner(max_workers=1),
)
def historical_flow(port: str = "los_angeles", stop_after_raw: bool = False, force: bool = False):
    return checked_historical(port=port, demo=True, stop_after_raw=stop_after_raw, force=force)


@flow(
    name="portwatch-backfill", persist_result=False, task_runner=ThreadPoolTaskRunner(max_workers=1)
)
def backfill_flow(port: str, start: str, end: str, force: bool = False):
    return checked_historical(port=port, demo=False, start=start, end=end, force=force)


@flow(name="portwatch-live", persist_result=False, task_runner=ThreadPoolTaskRunner(max_workers=1))
def live_flow(port: str = "los_angeles"):
    return live_task(port)


@flow(
    name="portwatch-weather", persist_result=False, task_runner=ThreadPoolTaskRunner(max_workers=1)
)
def weather_flow(port: str = "los_angeles"):
    return context_task("weather", port)


@flow(name="portwatch-news", persist_result=False, task_runner=ThreadPoolTaskRunner(max_workers=1))
def news_flow(port: str = "los_angeles"):
    return context_task("news", port)


def deployments(port="los_angeles"):
    # Hourly providers start at the next UTC hour; no burst of refreshes on runner startup.
    next_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return [
        live_flow.to_deployment(
            name=f"{port}-minute", interval=60, parameters={"port": port}, concurrency_limit=1
        ),
        weather_flow.to_deployment(
            name=f"{port}-hourly",
            schedule=Interval(timedelta(hours=1), anchor_date=next_hour),
            parameters={"port": port},
            concurrency_limit=1,
        ),
        news_flow.to_deployment(
            name=f"{port}-hourly",
            schedule=Interval(timedelta(hours=1), anchor_date=next_hour),
            parameters={"port": port},
            concurrency_limit=1,
        ),
    ]


def serve_local(port="los_angeles"):
    from portwatch.live import live_spec
    from portwatch.settings import Settings

    live_spec(Settings(), port)  # Reject a wrong port before registering anything.
    serve(*deployments(port), limit=3, pause_on_shutdown=True)


def start_local_server():
    import sys

    from prefect.cli import app

    previous = sys.argv
    try:
        sys.argv = [
            "prefect",
            "server",
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            "4200",
            "--workers",
            "1",
        ]
        app()
    finally:
        sys.argv = previous


def pause_local():
    """Pause only our three deployments, also usable after an abrupt runner termination."""
    import asyncio

    from prefect.client.orchestration import get_client

    async def pause():
        async with get_client() as client:
            for flow_name, deployment_name in (
                ("portwatch-live", "los_angeles-minute"),
                ("portwatch-weather", "los_angeles-hourly"),
                ("portwatch-news", "los_angeles-hourly"),
            ):
                deployment = await client.read_deployment_by_name(f"{flow_name}/{deployment_name}")
                await client.set_deployment_paused_state(deployment.id, True)

    asyncio.run(pause())
