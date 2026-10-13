"""Bound owned diagnostic logs and terminal Prefect run metadata; preserve all source bytes."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta

from portwatch.live_spool import collector_lock
from portwatch.settings import Settings, configured_ports


def backup_databases(settings):
    """Two logical copies on the existing second volume; never delete raw inputs."""
    root = settings.project_path(settings.raw_dir).parent / "backups"
    root.mkdir(parents=True, exist_ok=True)
    with collector_lock(root / "backup-lock"):
        return _backup_databases(settings, root)


def _backup_databases(settings, root):
    result = []
    for name in dict.fromkeys([settings.db_name, settings.history_db_name]):
        if not name:
            continue
        if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
            raise ValueError("unsupported_backup_database_name")
        copies = sorted(root.glob(f"{name}-*.dump"), key=lambda path: path.stat().st_mtime)
        if copies and time.time() - copies[-1].stat().st_mtime < 86400:
            continue
        if shutil.disk_usage(root).free < settings.storage_min_free_bytes + 512 * 1024 * 1024:
            result.append(dict(database=name, status="disk_reserve"))
            continue
        path = root / f"{name}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.dump"
        temporary = path.with_suffix(".part")
        env = os.environ.copy()
        env.pop("PORTWATCH_AIS_API_KEY", None)
        env["PGPASSWORD"] = settings.db_password.get_secret_value()
        process = subprocess.Popen(
            [
                "pg_dump",
                "-h",
                settings.db_host,
                "-p",
                str(settings.db_port),
                "-U",
                settings.db_user,
                "-d",
                name,
                "-Fc",
                "--no-owner",
                "--no-privileges",
                "--exclude-table-data=public.vessel_activity_states",
                "--file",
                str(temporary),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                if (
                    time.monotonic() - started > 600
                    or (temporary.exists() and temporary.stat().st_size > 512 * 1024 * 1024)
                    or shutil.disk_usage(root).free < settings.storage_min_free_bytes
                ):
                    process.terminate()
                    process.wait(timeout=10)
                    raise RuntimeError("backup_resource_bound")
                time.sleep(2)
            if process.returncode:
                raise RuntimeError("backup_dump_failed")
            with temporary.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            temporary.replace(path)
            record = dict(
                database=name,
                status="complete",
                bytes=path.stat().st_size,
                sha256=digest,
                created_at_utc=datetime.now(UTC).isoformat(),
            )
            path.with_suffix(".json").write_text(json.dumps(record, indent=2), "utf-8")
            for old in copies[:-1]:
                old.unlink()
                old.with_suffix(".json").unlink(missing_ok=True)
            result.append(record)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            temporary.unlink(missing_ok=True)
    return result


def retain_logs(settings):
    root = settings.project_path(settings.live_status_dir)
    for name in ("prefect-server", "prefect-runner", "prefect-recovery", "maintenance"):
        path = root / f"{name}.log"
        if path.is_file() and path.stat().st_size > 5 * 1024 * 1024:
            with path.open("r+b") as stream:
                stream.seek(-1024 * 1024, 2)
                (root / f"{name}.previous.log").write_bytes(stream.read())
                stream.truncate(0)


async def retain_runs(settings):
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import (
        FlowRunFilter,
        FlowRunFilterDeploymentId,
        FlowRunFilterEndTime,
        FlowRunFilterState,
        FlowRunFilterStateType,
    )
    from prefect.exceptions import ObjectNotFound
    from prefect.states import StateType

    cutoff = datetime.now(UTC) - timedelta(days=7)
    deleted = 0
    async with get_client() as client:
        ids = []
        for port in configured_ports(settings):
            for name, suffix in (
                ("portwatch-live", "minute"),
                ("portwatch-weather", "hourly"),
                ("portwatch-news", "hourly"),
            ):
                try:
                    deployment = await client.read_deployment_by_name(f"{name}/{port}-{suffix}")
                except ObjectNotFound:
                    continue
                flow = await client.read_flow(deployment.flow_id)
                if flow.name == name and deployment.parameters == {"port": port}:
                    ids.append(deployment.id)
        if ids:
            runs = await client.read_flow_runs(
                flow_run_filter=FlowRunFilter(
                    deployment_id=FlowRunFilterDeploymentId(any_=ids),
                    end_time=FlowRunFilterEndTime(before_=cutoff),
                    state=FlowRunFilterState(
                        type=FlowRunFilterStateType(
                            any_=[
                                StateType.COMPLETED,
                                StateType.FAILED,
                                StateType.CRASHED,
                                StateType.CANCELLED,
                            ]
                        )
                    ),
                ),
                limit=200,
            )
            for run in runs:
                await client.delete_flow_run(run.id)
                deleted += 1
    return dict(checked_at_utc=datetime.now(UTC).isoformat(), metadata_days=7, deleted_runs=deleted)


def maintenance_report(settings):
    try:
        report = asyncio.run(retain_runs(settings))
    except Exception as error:
        report = dict(
            checked_at_utc=datetime.now(UTC).isoformat(),
            status="degraded",
            metadata_error_class=type(error).__name__,
        )
    if os.environ.get("PORTWATCH_CLOUD_LIVE") == "1":
        try:
            report["backups"] = backup_databases(settings)
        except Exception as error:
            report.update(status="degraded", backup_error_class=type(error).__name__)
    return report


def main():
    settings = Settings()
    retain_logs(settings)
    from portwatch.orchestration import configure_local

    configure_local()
    report = maintenance_report(settings)
    (settings.project_path(settings.live_status_dir) / "maintenance.json").write_text(
        json.dumps(report, indent=2), "utf-8"
    )


if __name__ == "__main__":
    main()
