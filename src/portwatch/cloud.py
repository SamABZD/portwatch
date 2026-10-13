"""Railway entry point: persistent data, historical bootstrap and owned live runtime."""

import os
import subprocess
import sys
import threading
import time

import uvicorn
from fastapi.responses import JSONResponse

from portwatch.db import connect_db, migrate
from portwatch.demo.api import create_app
from portwatch.pipeline import run_context, run_historical
from portwatch.settings import Settings, configured_ports


def bootstrap(settings):
    """Migrate and create missing genuine historical partitions; never reset stored data."""
    for attempt in range(24):
        try:
            with connect_db(settings) as connection:
                migrate(connection)
            break
        except Exception:
            if attempt == 23:
                raise
            time.sleep(5)
    for port in configured_ports(settings):
        with connect_db(settings) as connection:
            ready = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM port_activity_hourly WHERE port_id=%s "
                "AND source_id='noaa_ocm_ais_2024_geoparquet')",
                (port,),
            ).fetchone()[0]
        if not ready:
            print(f"Bootstrapping genuine historical data: {port}", flush=True)
            result = run_historical(port=port, demo=True)
            if result["status"] != "succeeded":
                raise RuntimeError("historical_bootstrap_incomplete")


def public_app(settings):
    application = create_app(settings)

    @application.get("/readyz", include_in_schema=False)
    def ready():
        try:
            with connect_db(settings) as connection:
                connection.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
            return {"status": "ready"}
        except Exception:
            # Readiness never exposes connection strings, exception text or provider keys.
            return JSONResponse({"status": "unavailable"}, status_code=503)

    return application


def refresh_context(settings):
    """Recover current context once at startup; respect global failure backoff and spacing."""
    for kind in ("weather", "news"):
        for port in configured_ports(settings):
            for attempt in range(2):
                try:
                    result = run_context(kind, port)
                    if result.get("status") != "deferred":
                        break
                    from datetime import UTC, datetime

                    next_allowed = result.get("next_allowed_utc")
                    delay = (
                        (datetime.fromisoformat(next_allowed) - datetime.now(UTC)).total_seconds()
                        if next_allowed
                        else 5
                    )
                    # Long provider throttles belong to the hourly schedule, not a hot retry loop.
                    if attempt or delay > 65:
                        break
                    time.sleep(max(1, delay + 1))
                except Exception as error:
                    print(f"Context pending: {kind}/{port} ({type(error).__name__})", flush=True)
                    break


def main():
    settings = Settings()
    if os.environ.get("PORTWATCH_CLOUD_BOOTSTRAP", "1") == "1":
        bootstrap(settings)
    runtime = None
    try:
        if os.environ.get("PORTWATCH_CLOUD_LIVE", "0") == "1":
            runtime = subprocess.Popen([sys.executable, "-m", "portwatch.runtime"])
            threading.Thread(target=refresh_context, args=(settings,), daemon=True).start()
        port = int(os.environ.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("invalid_listen_port")
        uvicorn.run(
            public_app(settings),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=port,
            access_log=False,
        )
    finally:
        if runtime and runtime.poll() is None:
            subprocess.run(
                [sys.executable, "-m", "portwatch.runtime", "--stop"],
                check=False,
                timeout=10,
            )
            try:
                runtime.wait(timeout=40)
            except subprocess.TimeoutExpired:
                runtime.terminate()
                runtime.wait(timeout=10)


if __name__ == "__main__":
    main()
