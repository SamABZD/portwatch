"""Loopback query API: bounded prepared data, revision caches, no ingestion side effects."""

import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from portwatch.db import connect_db
from portwatch.demo.replay import ReplayError, fingerprint, load_dataset, load_prepared, stamp
from portwatch.events import emit
from portwatch.health import collect_health
from portwatch.history import published_history
from portwatch.settings import Settings, configured_ports, for_port, load_port_config


class ReplayService:
    def __init__(self, settings, prepared=None):
        self.settings = settings
        self.prepared = load_prepared(prepared) if prepared else None
        self.datasets, self.frames = {}, OrderedDict()
        self.lock = RLock()
        self.health_cache = {}
        self.health_checked = {}

    def dataset(self, mode, port=None):
        with self.lock:
            if self.prepared:
                if port and port != self.prepared.metadata.get("port_id", "los_angeles"):
                    raise ReplayError("port_unavailable_in_prepared_snapshot")
                if mode != "historical":
                    raise ReplayError("prepared_mode_has_no_live_connection")
                return self.prepared
            settings = for_port(self.settings, port) if port else self.settings
            key = (str(settings.port_config), mode)
            previous = self.datasets.get(key)
            if previous and time.monotonic() - previous[0] < 10:
                return previous[1]
            with connect_db(settings) as connection, connection.transaction():
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                connection.execute("SELECT set_config('statement_timeout','15s',true)")
                descriptor, fence, port = fingerprint(connection, settings, mode, datetime.now(UTC))
                if previous and descriptor["revision"] == previous[1].metadata["revision"]:
                    dataset = previous[1]
                else:
                    dataset = load_dataset(connection, descriptor, fence, port)
            self.datasets[key] = time.monotonic(), dataset
            return dataset

    def frame(self, mode, at, stale, trail, port=None):
        with self.lock:
            dataset = self.dataset(mode, port)
            cutoff = stamp(at) if mode == "historical" and at else datetime.now(UTC)
            if mode == "historical" and not at:
                cutoff = stamp(dataset.metadata["start_utc"]) + timedelta(hours=12, minutes=30)
            if mode == "live":
                # A bounded ten-second display clock makes repeat live polls cacheable.
                cutoff = cutoff.replace(second=cutoff.second // 10 * 10, microsecond=0)
            key = (dataset.metadata["revision"], cutoff.isoformat(), stale, trail)
            if key in self.frames:
                self.frames.move_to_end(key)
                return dict(self.frames[key], cache_hit=True)
            result = dataset.frame(cutoff, stale, trail)
            self.frames[key] = result
            while len(self.frames) > 128:
                self.frames.popitem(last=False)
            return dict(result, cache_hit=False)

    def health(self, port=None):
        with self.lock:
            if self.prepared:
                if port and port != self.prepared.metadata.get("port_id", "los_angeles"):
                    raise ReplayError("port_unavailable_in_prepared_snapshot")
                return dict(
                    overall="prepared_snapshot",
                    checked_at_utc=datetime.now(UTC).isoformat(),
                    database=dict(status="not_used"),
                    collector=dict(status="unavailable_in_prepared_mode"),
                    historical=dict(status="prepared_historical", last_clean_commit_utc=None),
                    live=dict(status="unavailable_in_prepared_mode"),
                    context=None,
                    issues=["Historical snapshot only; current pipeline health is not connected."],
                )
            port = (
                port
                or load_port_config(self.settings.project_path(self.settings.port_config)).port_id
            )
            if (
                port not in self.health_cache
                or time.monotonic() - self.health_checked.get(port, 0) > 10
            ):
                report = collect_health(self.settings, port, include_unscoped=False, polling=True)
                self.health_cache[port] = {
                    key: report[key]
                    for key in (
                        "overall",
                        "checked_at_utc",
                        "database",
                        "collector",
                        "historical",
                        "live",
                        "context",
                        "issues",
                        "runs",
                        "operations",
                    )
                }
                self.health_checked[port] = time.monotonic()
            return self.health_cache[port]


def create_app(settings=None, *, prepared=None, service=None):
    app = FastAPI(title="PortWatch read-only replay", docs_url=None, redoc_url=None)
    service = service or ReplayService(settings or Settings(), prepared)
    app.state.replay = service

    @app.middleware("http")
    async def safe_query_boundary(request, call_next):
        # Catch before ServerErrorMiddleware: its generic exception handler re-raises the
        # original exception to Uvicorn, which could otherwise log private exception text.
        try:
            return await call_next(request)
        except Exception as error:
            emit("replay_query_unavailable", error_class=type(error).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": dict(
                        code="prepared_data_unavailable",
                        error_class=type(error).__name__,
                        action="Check the database and saved data. No ingestion runs here.",
                    )
                },
            )

    @app.exception_handler(ReplayError)
    async def invalid(_request, error):
        return JSONResponse(status_code=422, content={"detail": dict(code=str(error))})

    @app.get("/api/catalog")
    def catalog(port: str | None = None):
        if port and port not in configured_ports(service.settings):
            raise HTTPException(404, detail={"code": "unknown_port"})
        entries = []
        for mode in ("historical", "live"):
            try:
                dataset = service.dataset(mode, port)
                entries.append(dict(dataset.metadata, available=True, zones=dataset.zones))
            except ReplayError as error:
                entries.append(dict(mode=mode, available=False, reason=str(error)))
        return dict(
            datasets=entries,
            ports=[
                dict(
                    port_id=p,
                    name=load_port_config(
                        for_port(service.settings, p).project_path(
                            for_port(service.settings, p).port_config
                        )
                    ).display_name,
                )
                for p in configured_ports(service.settings)
                if not service.prepared
                or p == service.prepared.metadata.get("port_id", "los_angeles")
            ],
            prepared_mode=bool(service.prepared),
            attribution=dict(
                ais="NOAA OCM / MarineCadastre AIS; AISStream saved reports",
                weather="Open-Meteo / ERA5 retrospective reanalysis; separate current forecast",
                zones="PortWatch versioned analytical approximations, not official boundaries",
            ),
        )

    @app.get("/api/frame")
    def frame(
        mode: Literal["historical", "live"] = "historical",
        at: str | None = None,
        stale: int = Query(default=15, ge=1, le=60),
        trail: int = Query(default=30, ge=5, le=60),
        port: str | None = None,
    ):
        if port and port not in configured_ports(service.settings):
            raise HTTPException(404, detail={"code": "unknown_port"})
        if mode == "live" and at is not None:
            raise HTTPException(422, detail={"code": "live_cutoff_is_server_time"})
        try:
            return service.frame(mode, at, stale, trail, port)
        except (TypeError, AttributeError, OverflowError) as error:
            raise HTTPException(
                422, detail={"code": "invalid_cutoff", "error_class": type(error).__name__}
            ) from None
        except ValueError as error:
            if isinstance(error, ReplayError):
                raise
            raise HTTPException(422, detail={"code": "invalid_cutoff"}) from None

    @app.get("/api/health")
    def health(port: str | None = None):
        if port and port not in configured_ports(service.settings):
            raise HTTPException(404, detail={"code": "unknown_port"})
        return service.health(port)

    @app.get("/api/history")
    def history():
        return published_history(service.settings)

    @app.get("/api/history-lineage")
    def history_lineage():
        path = service.settings.project_path(service.settings.raw_dir).parent / "history/index.html"
        if not path.is_file():
            raise HTTPException(404, detail={"code": "history_lineage_not_deployed"})
        return FileResponse(path, media_type="text/html")

    @app.get("/api/pipeline")
    def pipeline():
        """Deliberately narrow public projection; no raw events, paths or task controls."""
        ports = []
        for port in configured_ports(service.settings):
            if service.prepared and port != service.prepared.metadata.get("port_id", "los_angeles"):
                continue
            report = service.health(port)
            ports.append(
                dict(
                    port_id=port,
                    name=load_port_config(
                        for_port(service.settings, port).project_path(
                            for_port(service.settings, port).port_config
                        )
                    ).display_name,
                    checked_at_utc=report["checked_at_utc"],
                    overall=report["overall"],
                    collector={
                        k: report["collector"].get(k)
                        for k in ("status", "last_observed_ais_message_utc", "observation_state")
                    },
                    historical=report["historical"],
                    live=report["live"],
                    context=report["context"],
                    runs=report.get("runs"),
                    issues=report["issues"],
                    operations=report.get("operations"),
                )
            )
        return dict(
            ports=ports,
            repository="https://github.com/SamABZD/portwatch",
            semantics="Current operational measurements, not historical replay metrics",
            stages=[
                dict(name=name, description=description, path=path)
                for name, description, path in (
                    (
                        "Collect",
                        "NOAA daily history and independent AISStream live collectors",
                        "src/portwatch/live.py",
                    ),
                    (
                        "Preserve",
                        "Immutable raw bytes, checksums, batch receipts and resumable spooling",
                        "src/portwatch/storage.py",
                    ),
                    (
                        "Validate",
                        "Canonical positions, duplicates, quarantine and timed reported profiles",
                        "src/portwatch/cleaning.py",
                    ),
                    (
                        "Transform",
                        "Versioned PostGIS zones, experimental visits and hourly analytical marts",
                        "src/portwatch/pipeline.py",
                    ),
                    (
                        "Serve",
                        "Bounded read-only queries and event-time replay for this website",
                        "src/portwatch/demo/replay.py",
                    ),
                )
            ],
        )

    return app
