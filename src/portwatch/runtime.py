"""One owned local live runtime: independent collectors and the existing Prefect runner.

Run with ``python -m portwatch.runtime``; request a graceful stop with ``--stop``.
No boot task, hosting account, public listener or paid resource is created.
"""

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
from contextlib import suppress
from datetime import UTC, datetime
from urllib.request import urlopen

import psutil

from portwatch.bookkeeping import finish_run, register_batch, start_run
from portwatch.db import connect_db
from portwatch.events import emit
from portwatch.live import LiveCollector, live_spec
from portwatch.live_spool import collector_lock
from portwatch.settings import PROJECT_ROOT, Settings, for_port
from portwatch.storage import RawStore


async def collect(settings, port, stop):
    delay = 1
    while not stop.is_set():
        try:
            spec, bounds = live_spec(settings, port)
            store = RawStore(settings.project_path(settings.raw_dir))
            with connect_db(settings) as connection:
                run_id = start_run(connection, "live_collect")
                collector = LiveCollector(
                    settings.ais_api_key,
                    bounds,
                    spec,
                    store,
                    settings.project_path(settings.live_spool_dir) / spec.dataset_id,
                    settings.project_path(settings.live_status_dir) / f"{port}-status.json",
                    register=lambda batch, connection=connection, store=store: register_batch(
                        connection, store, batch
                    ),
                    rotate_seconds=settings.live_rotate_seconds,
                    rotate_bytes=settings.live_rotate_bytes,
                    queue_records=settings.live_queue_records,
                    flush_seconds=settings.live_flush_seconds,
                )
                try:
                    await collector.run(stop_event=stop)
                    finish_run(
                        connection,
                        run_id,
                        input_count=collector.status.received_records,
                        output_count=collector.status.finalized_records,
                    )
                except Exception as error:
                    finish_run(connection, run_id, error=error)
                    raise
        except Exception as error:
            emit(
                "runtime_collector_retry",
                port=port,
                error_class=type(error).__name__,
                backoff_seconds=delay,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 60)


async def run(ports):
    settings = Settings()
    if not settings.ais_api_key or not settings.ais_api_key.get_secret_value().strip():
        raise ValueError("missing_api_key")
    for port in ports:
        for_port(settings, port)
    root = settings.project_path(settings.live_status_dir)
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < settings.storage_min_free_bytes:
        raise ValueError("disk_reserve_reached")
    stop_file = root / "runtime-stop"
    stop = asyncio.Event()
    owned = []
    logs = []
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    cloud = os.environ.get("PORTWATCH_CLOUD_LIVE") == "1"
    cutoff = datetime.now(UTC).isoformat()

    def launch(arguments, name):
        log = (root / f"{name}.log").open("ab")
        logs.append(log)
        child = subprocess.Popen(
            [sys.executable, "-m", "portwatch", *arguments],
            cwd=PROJECT_ROOT,
            stdout=log,
            stderr=log,
            creationflags=flags,
        )
        owned.append(child)
        return child

    def maintenance():
        # Popen duplicates stdout for the child; keep no hourly parent file descriptor.
        with (root / "maintenance.log").open("ab") as log:
            child = subprocess.Popen(
                [sys.executable, "-m", "portwatch.maintenance"],
                cwd=PROJECT_ROOT,
                stdout=log,
                stderr=log,
                creationflags=flags,
            )
        owned[:] = [process for process in owned if process.poll() is None]
        owned.append(child)
        return child

    with collector_lock(root / "runtime-lock"):
        stop_file.unlink(missing_ok=True)
        for name in ("prefect-server", "prefect-runner", "prefect-recovery"):
            path = root / f"{name}.log"
            if path.is_file() and path.stat().st_size > 1024 * 1024:
                path.replace(root / f"{name}.previous.log")
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            try:
                with urlopen("http://127.0.0.1:4200/api/health", timeout=3) as response:
                    if response.status != 200:
                        raise OSError("prefect_unavailable")
                if cloud:
                    raise RuntimeError("unexpected_existing_cloud_server")
            except OSError:
                server = launch(["orchestrate", "server"], "prefect-server")
                for _ in range(60):
                    await asyncio.sleep(1)
                    try:
                        with urlopen("http://127.0.0.1:4200/api/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("prefect_start_failed") from None
                else:
                    raise RuntimeError("prefect_start_timeout")
            if cloud:
                with (root / "prefect-recovery.log").open("ab") as log:
                    result = await asyncio.to_thread(
                        subprocess.run,
                        [
                            sys.executable,
                            "-m",
                            "portwatch.recovery",
                            "--ports",
                            ",".join(ports),
                            "--cutoff",
                            cutoff,
                        ],
                        stdout=log,
                        stderr=log,
                        timeout=180,
                        cwd=PROJECT_ROOT,
                    )
                if result.returncode:
                    raise RuntimeError("cloud_schedule_recovery_failed")
            runner = launch(["orchestrate", "serve", "--port", ",".join(ports)], "prefect-runner")
            state = dict(
                pid=psutil.Process().pid,
                process_created_at=psutil.Process().create_time(),
                ports=ports,
                started_at_utc=datetime.now(UTC).isoformat(),
                scope="railway_singleton" if cloud else "local_machine_must_remain_awake",
                status="running",
            )
            (root / "runtime.json").write_text(json.dumps(state, indent=2), "utf-8")
            collectors = [asyncio.create_task(collect(settings, port, stop)) for port in ports]
            emit("runtime_started", ports=ports)
            server_failures = 0
            tick = 0
            cleaner = None
            while not stop.is_set():
                await asyncio.sleep(1)
                tick += 1
                if cloud and (tick == 60 or tick % 3600 == 0):
                    if cleaner is None or cleaner.poll() is not None:
                        cleaner = maintenance()
                if cloud and tick % 15 == 0:
                    try:
                        with urlopen("http://127.0.0.1:4200/api/health", timeout=3) as response:
                            if response.status != 200:
                                raise OSError("prefect_unavailable")
                        server_failures = 0
                    except OSError:
                        server_failures += 1
                    if server_failures >= 3 or server.poll() is not None:
                        raise RuntimeError("owned_prefect_server_unavailable")
                if stop_file.exists():
                    stop.set()
                elif (
                    shutil.disk_usage(settings.project_path(settings.raw_dir)).free
                    < settings.storage_min_free_bytes
                ):
                    emit("runtime_disk_reserve_reached")
                    stop.set()
                elif runner.poll() is not None:
                    if cloud:
                        raise RuntimeError("owned_prefect_runner_exited")
                    emit("runtime_runner_restart", exit_code=runner.returncode)
                    await asyncio.sleep(5)
                    runner = launch(
                        ["orchestrate", "serve", "--port", ",".join(ports)], "prefect-runner"
                    )
            await asyncio.gather(*collectors)
        finally:
            stop.set()
            if "collectors" in locals():
                await asyncio.gather(*collectors, return_exceptions=True)
            with suppress(Exception):
                await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-m", "portwatch", "orchestrate", "pause"],
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    creationflags=flags,
                )
            for child in reversed(owned):
                if child.poll() is None:
                    child.terminate()
                    with suppress(subprocess.TimeoutExpired):
                        await asyncio.to_thread(child.wait, timeout=10)
            for log in logs:
                log.close()
            if "state" in locals():
                state.update(status="stopped", ended_at_utc=datetime.now(UTC).isoformat())
                (root / "runtime.json").write_text(json.dumps(state, indent=2), "utf-8")
            stop_file.unlink(missing_ok=True)
            emit("runtime_stopped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ports", default="los_angeles,oakland")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        path = Settings().project_path(Settings().live_status_dir) / "runtime-stop"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        print("Graceful live runtime stop requested.")
        return
    ports = list(dict.fromkeys(args.ports.split(",")))
    if not 1 <= len(ports) <= 3:
        parser.error("select one to three configured ports")
    try:
        asyncio.run(run(ports))
    except Exception as error:
        print(
            f"Live runtime unavailable ({type(error).__name__}); see the runbook.", file=sys.stderr
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
