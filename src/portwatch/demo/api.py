"""Loopback query API: bounded prepared data, revision caches, no ingestion side effects."""

import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from portwatch.db import connect_db
from portwatch.demo.replay import ReplayError, fingerprint, load_dataset, load_prepared, stamp
from portwatch.events import emit
from portwatch.health import collect_health
from portwatch.settings import Settings


class ReplayService:
    def __init__(self, settings, prepared=None):
        self.settings = settings
        self.prepared = load_prepared(prepared) if prepared else None
        self.datasets, self.frames = {}, OrderedDict()
        self.lock = RLock()
        self.health_cache = None
        self.health_checked = 0

    def dataset(self, mode):
        with self.lock:
            if self.prepared:
                if mode != "historical":
                    raise ReplayError("prepared_mode_has_no_live_connection")
                return self.prepared
            previous = self.datasets.get(mode)
            if previous and time.monotonic() - previous[0] < 10:
                return previous[1]
            with connect_db(self.settings) as connection, connection.transaction():
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                connection.execute("SELECT set_config('statement_timeout','15s',true)")
                descriptor, fence, port = fingerprint(
                    connection, self.settings, mode, datetime.now(UTC)
                )
                if previous and descriptor["revision"] == previous[1].metadata["revision"]:
                    dataset = previous[1]
                else:
                    dataset = load_dataset(connection, descriptor, fence, port)
            self.datasets[mode] = time.monotonic(), dataset
            return dataset

    def frame(self, mode, at, stale, trail):
        with self.lock:
            dataset = self.dataset(mode)
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

    def health(self):
        with self.lock:
            if self.prepared:
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
            if not self.health_cache or time.monotonic() - self.health_checked > 10:
                report = collect_health(self.settings)
                self.health_cache = {
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
                    )
                }
                self.health_checked = time.monotonic()
            return self.health_cache


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
    def catalog():
        entries = []
        for mode in ("historical", "live"):
            try:
                dataset = service.dataset(mode)
                entries.append(dict(dataset.metadata, available=True, zones=dataset.zones))
            except ReplayError as error:
                entries.append(dict(mode=mode, available=False, reason=str(error)))
        return dict(
            datasets=entries,
            prepared_mode=bool(service.prepared),
            attribution=dict(
                ais="NOAA OCM / MarineCadastre AIS; AISStream saved reports",
                weather="Open-Meteo / ERA5 retrospective reanalysis; separate current forecast",
                zones="PortWatch la-approx-v1 analytical approximations, not official boundaries",
            ),
        )

    @app.get("/api/frame")
    def frame(
        mode: Literal["historical", "live"] = "historical",
        at: str | None = None,
        stale: int = Query(default=15, ge=1, le=60),
        trail: int = Query(default=30, ge=5, le=60),
    ):
        if mode == "live" and at is not None:
            raise HTTPException(422, detail={"code": "live_cutoff_is_server_time"})
        try:
            return service.frame(mode, at, stale, trail)
        except (TypeError, AttributeError, OverflowError) as error:
            raise HTTPException(
                422, detail={"code": "invalid_cutoff", "error_class": type(error).__name__}
            ) from None
        except ValueError as error:
            if isinstance(error, ReplayError):
                raise
            raise HTTPException(422, detail={"code": "invalid_cutoff"}) from None

    @app.get("/api/health")
    def health():
        return service.health()

    return app
