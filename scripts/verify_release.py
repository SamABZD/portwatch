"""Offline real-data reproducibility in an owned database; optionally start a fresh native PG.

Requires the genuine frozen NOAA/ERA5 artifacts already saved by the documented pipeline.
No provider calls, live credentials, deletion of source evidence or main-database writes.
"""

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

import psutil
import pyarrow.parquet as pq
from pydantic import SecretStr
from verify_marts import mart_hash
from verify_positions import business_hash

from portwatch.backup import owned_database
from portwatch.db import connect_db, migrate
from portwatch.demo.replay import export_prepared
from portwatch.marts import configured_marts
from portwatch.pipeline import run_historical
from portwatch.settings import PROJECT_ROOT, Settings, for_port

FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def native_server(settings, folder, enabled):
    if not enabled:
        yield settings
        return
    binary = PROJECT_ROOT / "pg-runtime/bin"
    cluster = (folder / "pgdata").resolve()
    assert cluster.is_relative_to(folder.resolve()) and not cluster.exists()
    port = free_port()
    subprocess.run(
        [
            str(binary / "initdb.exe"),
            "-D",
            str(cluster),
            "-U",
            "pw_audit",
            "--auth-local=trust",
            "--auth-host=trust",
            "--encoding=UTF8",
            "--no-locale",
        ],
        check=True,
        capture_output=True,
        timeout=60,
        creationflags=FLAGS,
    )
    control = [str(binary / "pg_ctl.exe"), "-D", str(cluster)]
    try:
        subprocess.run(
            [
                *control,
                "-l",
                str(folder / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {port}",
                "-w",
                "start",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            creationflags=FLAGS,
        )
        yield settings.model_copy(
            update=dict(
                db_host="127.0.0.1",
                db_port=port,
                db_name="postgres",
                db_user="pw_audit",
                db_sslmode="disable",
                db_password=SecretStr("ephemeral_audit_only"),
                ais_api_key=None,
            )
        )
    finally:
        result = subprocess.run(
            [*control, "-m", "fast", "-w", "stop"],
            capture_output=True,
            timeout=60,
            creationflags=FLAGS,
        )
        (folder / "native-stop.json").write_text(json.dumps(dict(exit_code=result.returncode)))
        result.check_returncode()


@contextmanager
def isolated_environment(settings, folder):
    changes = dict(
        PORTWATCH_DB_HOST=settings.db_host,
        PORTWATCH_DB_PORT=str(settings.db_port),
        PORTWATCH_DB_NAME=settings.db_name,
        PORTWATCH_DB_USER=settings.db_user,
        PORTWATCH_DB_PASSWORD=settings.db_password.get_secret_value(),
        PORTWATCH_DB_SSLMODE=settings.db_sslmode,
        PORTWATCH_AIS_API_KEY="",
        PORTWATCH_LIVE_STATUS_DIR=str(folder / "status"),
    )
    previous = {key: os.environ.get(key) for key in changes}
    os.environ.update(changes)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def no_external_network():
    original = socket.getaddrinfo

    def lookup(host, *args, **kwargs):
        if host != "localhost" and not ip_address(host).is_loopback:
            raise AssertionError("release reproduction permits only loopback networking")
        return original(host, *args, **kwargs)

    socket.getaddrinfo = lookup
    try:
        yield
    finally:
        socket.getaddrinfo = original


@contextmanager
def api_server(folder, prepared=None):
    port = free_port()
    arguments = [sys.executable, "-m", "portwatch", "demo", "serve", "--listen-port", str(port)]
    env = os.environ.copy()
    if prepared:
        arguments.extend(["--prepared", str(prepared)])
        env.update(PORTWATCH_DB_PORT="9", PORTWATCH_AIS_API_KEY="")
    with (folder / ("prepared-api.log" if prepared else "query-api.log")).open("wb") as log:
        child = subprocess.Popen(
            arguments, env=env, cwd=PROJECT_ROOT, stdout=log, stderr=log, creationflags=FLAGS
        )
        base = f"http://127.0.0.1:{port}/api/"
        try:
            for _ in range(100):
                try:
                    with urlopen(base + "catalog", timeout=1):
                        break
                except OSError:
                    if child.poll() is not None:
                        raise RuntimeError("owned query API exited") from None
                    time.sleep(0.1)
            else:
                raise RuntimeError("owned query API startup timed out")
            yield base
        finally:
            child.terminate()
            child.wait(timeout=15)


def query(base, endpoint, **parameters):
    tick = time.perf_counter()
    with urlopen(base + endpoint + "?" + urlencode(parameters), timeout=30) as response:
        value = json.load(response)
    return value, time.perf_counter() - tick


def snapshot(settings):
    with connect_db(settings) as connection:
        result = dict(business_sha256=business_hash(connection), ports={})
        for port in ("los_angeles", "oakland"):
            scope, config = configured_marts(for_port(settings, port), connection, port)
            counts = connection.execute(
                "SELECT count(*),count(DISTINCT mmsi),min(effective_at),max(effective_at) "
                "FROM vessel_positions WHERE source_id=%s AND dataset_id=%s",
                (scope.source_id, scope.dataset_id),
            ).fetchone()
            quality = connection.execute(
                "SELECT sum(c.input_records),sum(c.duplicate_records),sum(c.quarantined_records) "
                "FROM clean_batches c JOIN ingestion_batches b ON b.batch_id=c.raw_batch_id "
                "WHERE b.source_id=%s AND b.dataset_id=%s",
                (scope.source_id, scope.dataset_id),
            ).fetchone()
            local = connection.execute(
                "SELECT local_path FROM ingestion_batches WHERE source_id=%s AND dataset_id=%s",
                (scope.source_id, scope.dataset_id),
            ).fetchone()[0]
            with pq.ParquetFile(settings.project_path(settings.raw_dir) / local) as source:
                national_rows = source.metadata.num_rows
            result["ports"][port] = dict(
                positions=counts[0],
                vessels=counts[1],
                first_utc=counts[2].isoformat(),
                last_utc=counts[3].isoformat(),
                national_input_rows=national_rows,
                extracted=int(quality[0]),
                duplicates=int(quality[1]),
                quarantined=int(quality[2]),
                mart_sha256=mart_hash(connection, config.partition_id(scope)),
            )
        profile_rows = connection.execute(
            "SELECT source_record_id,processing_version,source_id,dataset_id,mmsi,"
            "available_at,fields "
            "FROM vessel_profile_reports ORDER BY source_record_id,processing_version"
        ).fetchall()
        result["profile_sha256"] = hashlib.sha256(
            json.dumps(profile_rows, default=str, sort_keys=True).encode()
        ).hexdigest()
        return result


def verify(settings, folder):
    with owned_database(settings) as scoped, isolated_environment(scoped, folder):
        with connect_db(scoped) as connection:
            migrations = migrate(connection)
            assert len(migrations) == 8 and migrate(connection) == []
            version = connection.execute("SELECT version(),postgis_lib_version()").fetchone()
        passes = []
        with no_external_network():
            for repeat in (False, True):
                times = {}
                for port in ("los_angeles", "oakland"):
                    print(f"Historical {port}: {'repeat' if repeat else 'fresh'}", flush=True)
                    tick = time.perf_counter()
                    result = run_historical(port, force=repeat)
                    assert result["status"] == "succeeded", result["failures"]
                    times[port] = time.perf_counter() - tick
                passes.append(dict(elapsed_seconds=times, snapshot=snapshot(scoped)))
        assert passes[0]["snapshot"] == passes[1]["snapshot"]
        assert [p["positions"] for p in passes[0]["snapshot"]["ports"].values()] == [62157, 52783]
        (folder / "historical-verification.json").write_text(
            json.dumps(dict(verified=True, passes=passes), indent=2), encoding="utf-8"
        )
        cutoff = "2024-01-01T12:30:00Z"
        with api_server(folder) as base:
            noon, cold = query(base, "frame", mode="historical", at=cutoff, port="los_angeles")
            again, warm = query(base, "frame", mode="historical", at=cutoff, port="los_angeles")
            assert again["cache_hit"] and warm < 2 and noon["metrics"]["fresh_vessels"] == 99
        prepared = folder / "prepared.json"
        export_prepared(scoped, prepared)
        with api_server(folder, prepared) as base:
            portable, portable_seconds = query(base, "frame", mode="historical", at=cutoff)
            health, _ = query(base, "health")
            assert portable["vessels"] == noon["vessels"]
            assert portable["metrics"] == noon["metrics"]
            assert health["database"]["status"] == "not_used"
        with (folder / "recovery.txt").open("w") as output:
            subprocess.run(
                [
                    sys.executable,
                    "scripts/verify_recovery.py",
                    "--output",
                    str(folder / "recovery-verification.json"),
                ],
                check=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=300,
                creationflags=FLAGS,
            )
        assert snapshot(scoped) == passes[1]["snapshot"]
        return dict(
            verified=True,
            passes=passes,
            database_version=version,
            cold_query_seconds=cold,
            cached_query_seconds=warm,
            prepared_query_seconds=portable_seconds,
            without_live_credentials=True,
            prepared_without_database=True,
            provider_requests=0,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--native", action="store_true", help="Start/stop a new private Windows PG17 cluster"
    )
    args = parser.parse_args()
    folder = PROJECT_ROOT / "artifacts/step13" / uuid4().hex
    folder.mkdir(parents=True)
    started = time.perf_counter()
    with native_server(Settings(ais_api_key=None), folder, args.native) as settings:
        report = verify(settings, folder)
    report.update(
        verified_at_utc=datetime.now(UTC).isoformat(),
        elapsed_seconds=time.perf_counter() - started,
        hardware=dict(
            os=platform.platform(),
            python=platform.python_version(),
            logical_cpus=psutil.cpu_count(),
            ram_bytes=psutil.virtual_memory().total,
        ),
        owned_database_removed=True,
        native_cluster_stopped=args.native,
    )
    (folder / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (PROJECT_ROOT / "artifacts/step13/latest.json").write_text(
        json.dumps(dict(folder=str(folder.relative_to(PROJECT_ROOT)), **report), indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
