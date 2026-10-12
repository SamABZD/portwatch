"""One owned local live runtime: independent collectors and the existing Prefect runner.

Run with ``python -m portwatch.runtime``; request a graceful stop with ``--stop``.
No boot task, hosting account, public listener or paid resource is created.
"""

import argparse
import asyncio
import json
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

    with collector_lock(root / "runtime-lock"):
        stop_file.unlink(missing_ok=True)
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            try:
                with urlopen("http://127.0.0.1:4200/api/health", timeout=3) as response:
                    if response.status != 200:
                        raise OSError("prefect_unavailable")
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
            runner = launch(["orchestrate", "serve", "--port", ",".join(ports)], "prefect-runner")
            state = dict(
                pid=psutil.Process().pid,
                process_created_at=psutil.Process().create_time(),
                ports=ports,
                started_at_utc=datetime.now(UTC).isoformat(),
                scope="local_machine_must_remain_awake",
                status="running",
            )
            (root / "runtime.json").write_text(json.dumps(state, indent=2), "utf-8")
            collectors = [asyncio.create_task(collect(settings, port, stop)) for port in ports]
            emit("runtime_started", ports=ports)
            while not stop.is_set():
                await asyncio.sleep(1)
                if stop_file.exists():
                    stop.set()
                elif (
                    shutil.disk_usage(settings.project_path(settings.raw_dir)).free
                    < settings.storage_min_free_bytes
                ):
                    emit("runtime_disk_reserve_reached")
                    stop.set()
                elif runner.poll() is not None:
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
