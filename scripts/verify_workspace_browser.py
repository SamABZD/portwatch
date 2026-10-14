"""Bounded Phase 4 workspace QA against an owned, fixture-only frontend.

Only an owned immutable PortWatch HTTPS preview is accepted. API responses come
from saved genuine Phase 3 captures; empty/failure projections are UI fixtures.
Other origins are blocked, except default OSM tile URLs fulfilled in memory.
Real MapLibre uses an in-memory raster, never a provider or public screenshot.
Public screenshot candidates show unchanged historical frames with tiles off.
"""

import argparse
import asyncio
import hashlib
import io
import json
import math
import os
import re
import struct
import zlib
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright, expect

from scripts.verify_demo_browser import available_browser
from scripts.verify_navigation_browser import PORT_NAMES, Fixtures, scoped_url

WIDTHS = (390, 768, 1024, 1440)
NODE_IDS = (
    "noaa",
    "ais",
    "weather",
    "news",
    "raw",
    "python",
    "postgis",
    "api",
    "web",
    "dbt",
    "prefect",
)
GROUPS = (
    "historical_vector",
    "historical_screenshots",
    "freshness",
    "saved_states",
    "real_maplibre",
    "renderer_fallbacks",
    "mobile_gestures",
    "pipeline",
    "lineage",
)


def test_tile():
    """Deterministic test-only PNG, generated without image/network libraries."""
    size = 256
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend((220, 229, 231) if x % 64 == 0 or y % 64 == 0 else (240, 245, 246))

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class FixtureServer:
    def __init__(self, args, fixtures):
        self.origin = urlparse(args.url)
        self.fixtures = fixtures
        self.lineage = Path(args.lineage).read_bytes() if args.lineage else None
        self.bypass = os.environ.get("PORTWATCH_TEST_VERCEL_BYPASS")
        self.freshness_frames = (
            {
                port: json.loads(
                    (Path(args.freshness_fixtures) / f"historical-{port}-stale30.json").read_text(
                        encoding="utf-8"
                    )
                )
                for port in PORT_NAMES
            }
            if args.freshness_fixtures
            else {}
        )
        self.state = "normal"
        self.tile_state = "normal"
        self.gate = asyncio.Event()
        self.gate.set()
        self.requests = []
        self.tiles = []
        self.blocked = []
        self.errors = []
        self.lineage_asset_requests = []
        self.tile = test_tile()

    async def serve(self, route):
        url = urlparse(route.request.url)
        local_tile = url.netloc == self.origin.netloc and url.path.startswith("/__test_tiles/")
        osm_tile = url.scheme == "https" and url.netloc == "tile.openstreetmap.org"
        if not (local_tile or osm_tile) and (url.scheme, url.netloc) != (
            self.origin.scheme,
            self.origin.netloc,
        ):
            self.blocked.append(route.request.url)
            await route.abort()
            return
        if local_tile or osm_tile:
            tile = re.fullmatch(r"/(?:__test_tiles/)?(\d+)/(\d+)/(\d+)\.png", url.path)
            if not tile:
                self.errors.append("Invalid test tile path")
                await route.abort()
                return
            self.tiles.append(tuple(map(int, tile.groups())))
            if self.tile_state == "loading":
                await self.gate.wait()
            if self.tile_state == "failure":
                await route.fulfill(
                    status=503, content_type="text/plain", body="test-only tile failure"
                )
            else:
                await route.fulfill(content_type="image/png", body=self.tile)
            return
        if not url.path.startswith("/api/"):
            if self.bypass:
                await route.continue_(
                    headers={**route.request.headers, "x-vercel-protection-bypass": self.bypass}
                )
            else:
                await route.continue_()
            return
        endpoint = url.path.rsplit("/", 1)[-1]
        # The standalone dbt document briefly resolves Angular template image URLs
        # before bootstrap. They are missing decorative assets, not API contracts.
        if (
            endpoint
            in {
                "%7B%7B%20getIcon(item.type,%20'on')%20%7D%7D",
                "%7B%7B%20getIcon(item.type,%20'off')%20%7D%7D",
                "favicon.ico')%7D",
            }
            and urlparse(route.request.frame.url).path == "/api/history-lineage"
        ):
            self.lineage_asset_requests.append(endpoint)
            await route.fulfill(status=404, content_type="text/plain", body="Missing dbt asset")
            return
        query = parse_qs(url.query)
        self.requests.append({"endpoint": endpoint, "query": query, "state": self.state})
        if endpoint == "history-lineage":
            if self.lineage:
                await route.fulfill(content_type="text/html", body=self.lineage)
            else:
                await route.fulfill(
                    status=503, json={"detail": {"code": "saved_lineage_not_supplied"}}
                )
            return
        if self.state == "catalog_loading" and endpoint == "catalog":
            await self.gate.wait()
        if self.state == "frame_loading" and endpoint == "frame":
            await self.gate.wait()
        if self.state == "frame_failure" and endpoint == "frame":
            await route.fulfill(status=503, json={"detail": {"code": "query_api_disconnected"}})
            return
        if self.state == "pipeline_failure" and endpoint == "pipeline":
            await route.fulfill(status=503, json={"detail": {"code": "query_api_disconnected"}})
            return
        try:
            view = parse_qs(urlparse(route.request.frame.url).query)
            body = self.fixtures.response(endpoint, query, view)
            if self.state == "freshness" and endpoint == "frame":
                port = query.get("port", ["los_angeles"])[0]
                stale = query.get("stale", ["15"])[0]
                if stale not in {"15", "30"}:
                    raise ValueError("Missing genuine freshness capture")
                body = (
                    self.freshness_frames[port]
                    if stale == "30"
                    else self.fixtures.prepared_frames[port]
                )
                body = json.loads(json.dumps(body))
            if self.state == "freshness" and endpoint == "catalog":
                port = query.get("port", ["los_angeles"])[0]
                body["datasets"] = [
                    {**dataset, **self.fixtures.prepared_frames[port]["dataset"]}
                    if dataset["mode"] == "historical"
                    else dataset
                    for dataset in body["datasets"]
                ]
            if self.state == "catalog_empty" and endpoint == "catalog":
                body["datasets"] = []
            if self.state == "frame_empty" and endpoint == "frame":
                body["vessels"] = []
                body["trails"] = []
                for key in (
                    "seen_vessels",
                    "fresh_vessels",
                    "fresh_port_occupancy",
                    "candidate_waiting",
                ):
                    body["metrics"][key] = 0
            if self.state == "frame_stale" and endpoint == "frame":
                for vessel in body["vessels"]:
                    vessel["stale"] = True
                for trail in body["trails"]:
                    trail["stale"] = True
                body["metrics"]["fresh_vessels"] = 0
            await route.fulfill(json=body)
        except Exception as error:
            self.errors.append(f"{endpoint}: {type(error).__name__}: {error}")
            await route.fulfill(status=500, json={"detail": {"code": "fixture_error"}})


async def loaded(page):
    await expect(page.locator(".metric-strip")).to_have_attribute(
        "aria-busy", "false", timeout=20_000
    )
    await expect(page.locator(".updating")).to_have_count(0)
    await expect(page.locator(".query-error")).to_have_count(0)
    await expect(page.locator(".news-panel")).to_be_visible()


async def tiles_loaded(page):
    await expect(page.locator(".maplibregl-canvas")).to_be_visible(timeout=20_000)
    await expect(page.locator(".vector-map")).to_have_count(0, timeout=20_000)
    await page.wait_for_load_state("networkidle")


async def canvas_bitmap(page):
    return await page.locator(".maplibregl-canvas").screenshot(animations="disabled")


def rendered_landmarks(bitmap):
    """Read fresh vessel fill centroids from the actual composited canvas capture."""
    with Image.open(io.BytesIO(bitmap)) as image:
        rgb = image.convert("RGB")
        pixels = rgb.load()
        remaining = {
            (x, y)
            for y in range(rgb.height)
            for x in range(rgb.width)
            if pixels[x, y][0] < 35
            and 100 <= pixels[x, y][1] <= 140
            and 105 <= pixels[x, y][2] <= 145
        }
    landmarks = []
    while remaining:
        pending = [remaining.pop()]
        cluster = []
        while pending:
            x, y = pending.pop()
            cluster.append((x, y))
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        if len(cluster) >= 10:
            landmarks.append(
                [
                    sum(p[0] for p in cluster) / len(cluster),
                    sum(p[1] for p in cluster) / len(cluster),
                ]
            )
    assert len(landmarks) >= 3, f"Only {len(landmarks)} rendered vessel landmarks"
    return sorted(landmarks)


def landmark_distance(first, second):
    """Symmetric nearest-centroid error; imagery/hover/fading cannot stand in for camera."""
    distances = [
        min(math.dist(point, candidate) for candidate in target)
        for source, target in ((first, second), (second, first))
        for point in source
    ]
    return max(distances)


async def stable_landmarks(page):
    previous = None
    for _ in range(10):
        current = rendered_landmarks(await canvas_bitmap(page))
        if (
            previous
            and len(previous) == len(current)
            and landmark_distance(previous, current) < 0.75
        ):
            return current
        previous = current
        await page.wait_for_timeout(100)
    raise AssertionError("Rendered vessel landmarks did not settle in bounded sampling")


async def layout(page, vector=False):
    if vector:
        try:
            await page.wait_for_function(
                """() => {
                  const stage = document.querySelector('.map-stage');
                  const svg = document.querySelector('.vector-map');
                  if (!stage || !svg) return false;
                  const r = stage.getBoundingClientRect(), v = svg.viewBox.baseVal;
                  return Math.abs(v.width-r.width)<=1 && Math.abs(v.height-r.height)<=1;
                }""",
                timeout=3000,
                polling="raf",
            )
        except PlaywrightTimeoutError:
            # Collect concrete dimensions below; timeout alone is not defect evidence.
            pass
    measurement = await page.evaluate("""() => {
      const rect = selector => {const element = document.querySelector(selector);
        const r = element.getBoundingClientRect(), s = getComputedStyle(element);
        return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right,
          position:s.position,fontSize:parseFloat(s.fontSize),scrollHeight:element.scrollHeight,
          clientHeight:element.clientHeight};};
      const svg = document.querySelector('.vector-map');
      return {width:innerWidth, scrollWidth:document.documentElement.scrollWidth,
        stage:rect('.map-stage'), tile:rect('.tile-map'),
        footer:rect('.map-footer'), legend:rect('.map-legend'),
        credit:rect('.map-credit'), column:rect('.map-column'), inspector:rect('.vessel-inspector'),
        viewBox:svg ? [svg.viewBox.baseVal.width,svg.viewBox.baseVal.height] : null,
        points:[...document.querySelectorAll('.vector-map .vessel-point')].map(el => ({
          x:parseFloat(el.getAttribute('cx')),y:parseFloat(el.getAttribute('cy'))})),
        finitePaths:[...document.querySelectorAll('.vector-map path,.vector-map polyline')]
          .every(el => !/NaN|Infinity/.test(
            el.getAttribute('d') || el.getAttribute('points') || ''))};
    }""")
    summary = {
        key: measurement[key]
        for key in (
            "width",
            "scrollWidth",
            "stage",
            "tile",
            "footer",
            "legend",
            "credit",
            "viewBox",
        )
    }
    assert measurement["scrollWidth"] <= measurement["width"], summary
    stage, footer = measurement["stage"], measurement["footer"]
    assert stage["height"] >= 280 and stage["width"] >= 200, stage
    assert footer["y"] >= stage["bottom"] - 1, summary
    assert measurement["legend"]["position"] not in {"absolute", "fixed"}, summary
    assert measurement["credit"]["position"] not in {"absolute", "fixed"}, summary
    assert measurement["credit"]["y"] >= measurement["legend"]["bottom"] - 1, summary
    assert measurement["legend"]["fontSize"] >= 11 and measurement["credit"]["fontSize"] >= 11, (
        summary
    )
    inspector = measurement["inspector"]
    if measurement["width"] <= 720:
        assert inspector["y"] >= footer["bottom"] - 1, summary
    else:
        assert stage["height"] <= inspector["height"] <= measurement["column"]["height"] + 1, (
            summary
        )
    if vector:
        assert measurement["viewBox"], summary
        mismatch = {
            "width": measurement["width"],
            "stage": [stage["width"], stage["height"]],
            "tile": [measurement["tile"]["width"], measurement["tile"]["height"]],
            "viewBox": measurement["viewBox"],
        }
        assert abs(measurement["viewBox"][0] - stage["width"]) <= 1, mismatch
        assert abs(measurement["viewBox"][1] - stage["height"]) <= 1, mismatch
        assert measurement["finitePaths"], measurement
        for point in measurement["points"]:
            assert math.isfinite(point["x"]) and math.isfinite(point["y"]), point
            assert 0 <= point["x"] <= stage["width"] and 0 <= point["y"] <= stage["height"], (
                point,
                stage,
            )
    return measurement


def tile_cover(tiles, bounds):
    """Check actual requested tile union contains every selected-port corner."""
    zooms = Counter(z for z, _, _ in tiles)
    assert zooms, "No real MapLibre raster requests"
    zoom = zooms.most_common(1)[0][0]
    points = {(x, y) for z, x, y in tiles if z == zoom}
    west, south, east, north = bounds
    corners = []
    for longitude in (west, east):
        for latitude in (south, north):
            x = math.floor((longitude + 180) / 360 * 2**zoom)
            y = math.floor(
                (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2 * 2**zoom
            )
            assert (x, y) in points, (bounds, zoom, (x, y), sorted(points))
            corners.append([x, y])
    return {"zoom": zoom, "requested_tiles": len(points), "port_corners": corners}


async def verify(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    fixtures = Fixtures(Path(args.fixtures))
    server = FixtureServer(args, fixtures)
    checks, screenshots, page_errors, unsupported, failures = [], [], [], [], []
    resume = None
    if args.resume:
        previous_bytes = Path(args.resume).read_bytes()
        previous = json.loads(previous_bytes)
        unsupported.extend(previous.get("unsupported", []))
        prior_revision = previous.get("source_revision") or args.resume_source_revision
        if not prior_revision:
            raise ValueError("Resume requires explicit prior source revision")
        changed_preview = previous.get("url", "").rstrip("/") != args.url.rstrip("/")
        resume_hash = hashlib.sha256(previous_bytes).hexdigest()
        archive = output / f"resume-evidence-{resume_hash[:12]}"
        archive.mkdir(exist_ok=True)
        snapshot = archive / "verification.json"
        snapshot.write_bytes(previous_bytes)
        for shot in previous.get("screenshots", []):
            frame = Path(args.fixtures) / f"historical-{shot['port']}.json"
            if shot["frame_sha256"] != hashlib.sha256(frame.read_bytes()).hexdigest():
                raise ValueError("Resume screenshot fixture identity changed")
            bitmap = output / shot["file"]
            if not bitmap.is_file():
                raise ValueError("Resume screenshot is missing")
            old_bitmap = archive / shot["file"]
            old_bitmap.parent.mkdir(parents=True, exist_ok=True)
            old_bitmap.write_bytes(bitmap.read_bytes())
            screenshots.append(
                {
                    **shot,
                    "file": str(old_bitmap.relative_to(output)),
                    "bitmap_sha256": hashlib.sha256(bitmap.read_bytes()).hexdigest(),
                    "evidence_reused": True,
                    "source_url": shot.get("source_url", previous["url"]),
                    "source_revision": shot.get("source_revision", prior_revision),
                }
            )
        checks.extend(
            {
                **check,
                "evidence_reused": True,
                "source_url": check.get("source_url", previous["url"]),
                "source_revision": check.get("source_revision", prior_revision),
            }
            for check in previous.get("checks", [])
            if check.get("geometry", True) is not None
            and not (
                check.get("case") == "actual_mobile_one_finger_page_scroll_two_finger_map_pan"
                and check.get("scroll_delta", 0) <= 30
            )
        )
        resume = {
            "report_sha256": resume_hash,
            "snapshot": str(snapshot.relative_to(output)),
            "source_url": previous["url"],
            "source_revision": prior_revision,
            "different_preview": changed_preview,
            "superseded": "Prior mobile scroll_delta reused overwritten variable; reverify gesture",
            "checks_reused": len(checks),
            "screenshots_reused": len(screenshots),
        }
    report = {
        "verified": False,
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "url": args.url,
        "source_revision": args.source_revision,
        "targeted_groups": args.only or list(GROUPS),
        "checks": checks,
        "screenshots": screenshots,
        "unsupported": unsupported,
        "page_errors": page_errors,
        "failures": failures,
        "resume": resume,
        "external_requests_sent": 0,
        "provider_requests_sent": 0,
        "fixtures": "Unchanged genuine Phase 3 frames, saved operational/history responses",
        "test_only_projections": [
            "empty catalog",
            "empty frame",
            "all stale flags",
            "HTTP503",
            "gated loading",
            "local raster PNG",
            "denied WebGL context",
        ],
    }

    def record(name, **evidence):
        checks.append(
            {
                "case": name,
                "source_url": args.url,
                "source_revision": args.source_revision,
                **evidence,
            }
        )

    def enabled(name):
        return not args.only or name in args.only

    def already(name, **scope):
        return any(
            check.get("case", check.get("check")) == name
            and all(check.get(key) == value for key, value in scope.items())
            for check in checks
        )

    async def checked_layout(page, port, width, vector=False):
        try:
            return await layout(page, vector=vector)
        except AssertionError as error:
            failures.append(
                {
                    "case": "vector_layout" if vector else "tile_layout",
                    "port": port,
                    "width": width,
                    "detail": str(error)[:1500],
                }
            )
            return None

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            executable_path=available_browser(playwright, args.browser_executable),
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            service_workers="block",
        )
        await context.route("**/*", server.serve)
        page = await context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        @asynccontextmanager
        async def guard(name):
            try:
                yield
            except Exception as error:
                detail = f"{type(error).__name__}: {error}"
                failures.append({"case": name, "detail": detail[:1500]})
            finally:
                server.state = "normal"
                server.tile_state = "normal"
                server.gate.set()
                for owned in list(browser.contexts):
                    if owned is not context:
                        await owned.close()

        async def capture(name, target=None, port="los_angeles"):
            path = output / name
            if target:
                await page.locator(target).screenshot(path=str(path), animations="disabled")
            else:
                await page.screenshot(path=str(path), full_page=True, animations="disabled")
            frame_file = Path(args.fixtures) / f"historical-{port}.json"
            screenshots.append(
                {
                    "file": name,
                    "port": port,
                    "mode": "historical",
                    "cutoff_utc": fixtures.frame(port, "historical")["cutoff_utc"],
                    "frame_sha256": hashlib.sha256(frame_file.read_bytes()).hexdigest(),
                    "tiles": "off",
                    "synthetic_state": False,
                    "source_url": args.url,
                    "source_revision": args.source_revision,
                }
            )

        try:
            if enabled("historical_vector"):
                for port in PORT_NAMES:
                    async with guard(f"historical_vector_{port}"):
                        await page.goto(scoped_url(args.url, "/", port, "historical"))
                        await loaded(page)
                        await page.get_by_role("checkbox", name="Basemap", exact=True).uncheck()
                        await expect(page.locator(".map-caption")).to_contain_text("Basemap off")
                        frame = fixtures.frame(port, "historical")
                        fresh = [
                            v
                            for v in frame["vessels"]
                            if not v["stale"]
                            and v["longitude"] is not None
                            and v["latitude"] is not None
                        ]
                        await expect(page.locator(".vessel-point")).to_have_count(len(fresh))
                        await expect(page.locator(".metric strong").first).to_have_text(
                            f"{frame['metrics']['fresh_vessels']:,}"
                        )
                        await expect(page.locator(".inspector-scope")).to_contain_text("stale")
                        await expect(page.get_by_test_id("cutoff-clock")).to_contain_text("12:30")
                        await expect(
                            page.get_by_role("combobox", name="Staleness cutoff")
                        ).to_have_value("15")
                        for width in WIDTHS:
                            if already(
                                "selected_port_vector_bounds_resize_layout", port=port, width=width
                            ):
                                continue
                            await page.set_viewport_size({"width": width, "height": 900})
                            await expect(page.locator(".vector-map")).to_be_visible()
                            await page.wait_for_timeout(100)
                            geometry = await checked_layout(page, port, width, vector=True)
                            record(
                                "selected_port_vector_bounds_resize_layout",
                                port=port,
                                width=width,
                                bounds=frame["dataset"]["bounds"],
                                geometry=geometry,
                            )
                            if width == 1440 and geometry is not None:
                                await capture(
                                    f"map-{port.replace('_', '-')}-historical.png", port=port
                                )
                            if width == 390 and port == "los_angeles" and geometry is not None:
                                await capture("map-mobile-historical.png", port=port)

                        chosen = fresh[0]
                        search = page.get_by_role("searchbox", name="Search MMSI")
                        await search.fill(chosen["mmsi"])
                        button = page.get_by_role(
                            "button", name=f"Select vessel {chosen['mmsi']}", exact=True
                        )
                        await expect(button).to_have_count(1)
                        await button.click()
                        await expect(page.locator(".vessel-title")).to_contain_text(chosen["mmsi"])
                        await expect(button).to_have_attribute("aria-pressed", "true")
                        await expect(page.locator(".selected-point")).to_have_count(1)
                        clear = page.get_by_role("button", name="Clear vessel selection")
                        box = await clear.bounding_box()
                        assert box and box["width"] >= 44 and box["height"] >= 44, box
                        await clear.click()
                        await expect(page.locator(".selected-point")).to_have_count(0)
                        name = (chosen.get("reported_profile") or {}).get("name")
                        assert name, "Saved fixture has no name-search evidence"
                        await search.fill(name)
                        await expect(button).to_be_visible()
                        await search.fill("__no_matching_vessel__")
                        await expect(page.locator(".vessel-list button")).to_have_count(0)
                        await expect(page.locator(".empty-list")).to_contain_text("No matching")
                        await search.fill("")
                        point = page.locator(".vessel-point").last
                        identifier = re.search(
                            r"MMSI (\d+)", await point.locator("title").text_content()
                        ).group(1)
                        await point.click()
                        await expect(page.locator(".vessel-title")).to_contain_text(identifier)
                        # Search restores list access beyond the bounded first100 vessels.
                        await search.fill(identifier)
                        await expect(
                            page.get_by_role(
                                "button", name=f"Select vessel {identifier}", exact=True
                            )
                        ).to_have_attribute("aria-pressed", "true")
                        await clear.click()
                        await search.fill("")
                        record(
                            "list_map_selection_name_mmsi_search_clear", port=port, clear_target=box
                        )

                        for label, selector in (("Zones", ".zone-path"), ("Trails", ".trail-path")):
                            toggle = page.get_by_role("checkbox", name=label, exact=True)
                            before = await page.locator(selector).count()
                            assert before > 0, (port, label)
                            await toggle.uncheck()
                            await expect(page.locator(selector)).to_have_count(0)
                            await toggle.check()
                            await expect(page.locator(selector)).to_have_count(before)
                        trails = [trail for trail in frame["trails"] if not trail["stale"]]
                        counts = await page.locator(".trail-path").evaluate_all(
                            """els => els.map(el =>
                            el.getAttribute('points').trim().split(/\\s+/).length)"""
                        )
                        assert counts == [len(trail["points"]) for trail in trails], counts
                        await page.get_by_role("checkbox", name="Stale", exact=True).check()
                        await expect(page.locator(".vessel-point")).to_have_count(
                            len(frame["vessels"])
                        )
                        await expect(page.locator(".inspector-scope")).to_contain_text(
                            "fresh and stale"
                        )
                        await page.get_by_role("checkbox", name="Stale", exact=True).uncheck()
                        await expect(page.locator(".vessel-point")).to_have_count(len(fresh))
                        await page.get_by_role("combobox", name="Displayed timezone").select_option(
                            "America/Los_Angeles"
                        )
                        await expect(page.get_by_test_id("cutoff-clock")).to_contain_text("Pacific")
                        await page.get_by_role("combobox", name="Displayed timezone").select_option(
                            "UTC"
                        )
                        record(
                            "overlays_saved_trail_segments_stale_toggle_timezone",
                            port=port,
                            fresh_points=len(fresh),
                            saved_vessels=len(frame["vessels"]),
                            saved_segments=len(trails),
                        )

            if enabled("historical_screenshots"):
                async with guard("historical_screenshots"):
                    for port in PORT_NAMES:
                        await page.goto(scoped_url(args.url, "/", port, "historical"))
                        await loaded(page)
                        await page.get_by_role("checkbox", name="Basemap", exact=True).uncheck()
                        for width in [390, 1440] if port == "los_angeles" else [1440]:
                            await page.set_viewport_size({"width": width, "height": 900})
                            await layout(page, vector=True)
                            await expect(
                                page.get_by_role("link", name="Open-Meteo", exact=True)
                            ).to_be_visible()
                            name = (
                                "map-mobile-historical.png"
                                if width == 390
                                else f"map-{port.replace('_', '-')}-historical.png"
                            )
                            await capture(name, port=port)
                    record("historical_capture_weather_attribution_link")

            if enabled("freshness"):
                async with guard("freshness"):
                    if server.freshness_frames:
                        server.state = "freshness"
                        for port in PORT_NAMES:
                            await page.goto(scoped_url(args.url, "/", port, "historical"))
                            await loaded(page)
                            control = page.get_by_role("combobox", name="Staleness cutoff")
                            await control.select_option("30")
                            await loaded(page)
                            fresh_frame = server.freshness_frames[port]
                            assert fresh_frame["staleness_minutes"] == 30
                            assert (
                                fresh_frame["cutoff_utc"]
                                == fixtures.prepared_frames[port]["cutoff_utc"]
                            )
                            await expect(page.locator(".metric strong").first).to_have_text(
                                f"{fresh_frame['metrics']['fresh_vessels']:,}"
                            )
                            frames = [r for r in server.requests if r["endpoint"] == "frame"]
                            assert frames[-1]["query"].get("stale") == ["30"]
                            await control.select_option("15")
                            await loaded(page)
                            record(
                                "genuine_prepared_freshness_control",
                                port=port,
                                cutoff_utc=fresh_frame["cutoff_utc"],
                                fresh_vessels_30=fresh_frame["metrics"]["fresh_vessels"],
                            )
                        server.state = "normal"
                    else:
                        unsupported.append(
                            "Nondefault freshness values: no matching genuine saved frame; "
                            "default 15-minute cutoff verified."
                        )
                    unsupported.append(
                        "Captured historical trails contain no same-MMSI multi-segment gap; "
                        "saved segment/point boundaries verified without inventing tracks."
                    )

            if enabled("saved_states"):
                async with guard("saved_states"):
                    server.state = "catalog_loading"
                    server.gate.clear()
                    await page.goto(
                        scoped_url(args.url, "/", mode="historical"), wait_until="domcontentloaded"
                    )
                    await expect(page.locator(".initial-loading")).to_contain_text("Reading")
                    server.state = "normal"
                    server.gate.set()
                    await loaded(page)
                    record("catalog_loading_has_explicit_state")
                    server.state = "catalog_empty"
                    await page.reload()
                    await expect(page.locator(".unavailable")).to_contain_text("No datasets")
                    await expect(page.locator(".initial-loading")).to_have_count(0)
                    server.state = "normal"
                    await page.get_by_role("button", name="Retry datasets").click()
                    await loaded(page)
                    record("successful_empty_catalog_recovers_without_permanent_loading")
                    server.state = "frame_loading"
                    server.gate.clear()
                    await page.reload(wait_until="domcontentloaded")
                    await expect(page.locator(".updating")).to_contain_text("Updating")
                    server.state = "normal"
                    server.gate.set()
                    await loaded(page)
                    record("frame_loading_is_distinct_from_empty")
                    server.state = "frame_failure"
                    await page.reload()
                    await expect(page.locator(".query-error")).to_contain_text("disconnected")
                    await expect(page.locator(".metric strong").first).to_have_text("â€”")
                    await expect(page.locator(".map-empty")).to_have_count(0)
                    server.state = "normal"
                    await page.get_by_role("button", name="Retry frame").click()
                    await loaded(page)
                    record("api_failure_unknown_metrics_retry_separate_from_zero")
                    for state, expected in (
                        ("frame_empty", "No observations at this cutoff"),
                        ("frame_stale", "No fresh observations"),
                    ):
                        server.state = state
                        await page.reload()
                        await loaded(page)
                        await page.get_by_role("checkbox", name="Basemap", exact=True).uncheck()
                        await expect(page.locator(".map-empty")).to_contain_text(expected)
                        await expect(page.locator(".vessel-point")).to_have_count(0)
                        await expect(page.locator(".query-error")).to_have_count(0)
                        if state == "frame_stale":
                            await page.get_by_role("checkbox", name="Stale", exact=True).check()
                            await expect(page.locator(".vessel-point")).to_have_count(
                                len(fixtures.frame("los_angeles", "historical")["vessels"])
                            )
                        record(state + "_distinct_saved_state")
                    server.state = "normal"

            if enabled("real_maplibre"):
                async with guard("real_maplibre"):
                    server.tile_state = "loading"
                    server.gate.clear()
                    await page.goto(
                        scoped_url(args.url, "/", mode="live"), wait_until="domcontentloaded"
                    )
                    await loaded(page)
                    await expect(page.locator(".map-caption")).to_contain_text("Loading basemap")
                    await expect(page.locator(".vector-map")).to_be_visible()
                    server.tile_state = "normal"
                    server.gate.set()
                    await tiles_loaded(page)
                    record("basemap_loading_local_geometry_until_tiles_ready")
                    for port in PORT_NAMES:
                        await page.set_viewport_size({"width": 1440, "height": 900})
                        start = len(server.tiles)
                        await page.goto(scoped_url(args.url, "/", port, "live"))
                        await loaded(page)
                        await tiles_loaded(page)
                        coverage = tile_cover(
                            server.tiles[start:], fixtures.frame(port, "live")["dataset"]["bounds"]
                        )
                        before = await stable_landmarks(page)
                        canvas = page.locator(".maplibregl-canvas")
                        box = await canvas.bounding_box()
                        assert box
                        await canvas.scroll_into_view_if_needed()
                        box = await canvas.bounding_box()
                        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                        await page.mouse.move(x, y)
                        await page.mouse.down()
                        await page.mouse.move(x + 100, y + 30, steps=12)
                        await page.mouse.up()
                        await page.wait_for_timeout(800)
                        await page.wait_for_load_state("networkidle")
                        panned = await stable_landmarks(page)
                        assert landmark_distance(before, panned) > 20, (
                            "Actual rendered vessels did not respond to pan"
                        )
                        count = len(server.requests)
                        await page.get_by_role(
                            "button", name="Refresh saved reports", exact=True
                        ).click()
                        await loaded(page)
                        await page.wait_for_timeout(300)
                        after = await stable_landmarks(page)
                        assert landmark_distance(panned, after) < 1, (
                            "Data refresh moved rendered vessel landmarks",
                            panned,
                            after,
                        )
                        assert any(
                            request["endpoint"] == "frame" for request in server.requests[count:]
                        )
                        for width in WIDTHS:
                            await page.set_viewport_size({"width": width, "height": 900})
                            await page.wait_for_timeout(150)
                            measured = await checked_layout(page, port, width)
                            if measured is None:
                                continue
                            dimensions = await canvas.evaluate(
                                """el => ({width:el.clientWidth,height:el.clientHeight,
                                backingWidth:el.width,backingHeight:el.height})"""
                            )
                            assert abs(dimensions["width"] - measured["stage"]["width"]) <= 1, (
                                dimensions
                            )
                            assert abs(dimensions["height"] - measured["stage"]["height"]) <= 1, (
                                dimensions
                            )
                        record(
                            "real_maplibre_bounds_pan_refresh_resize",
                            port=port,
                            tile_coverage=coverage,
                            rendered_landmarks_after_refresh=after,
                            camera_error_pixels=landmark_distance(panned, after),
                            camera_proof=(
                                "Pan moved real rendered vessels; "
                                "refresh retains landmark centroids within1px"
                            ),
                        )

            if enabled("renderer_fallbacks"):
                async with guard("renderer_fallbacks"):
                    server.tile_state = "failure"
                    await page.goto(scoped_url(args.url, "/", mode="historical"))
                    await loaded(page)
                    await expect(page.locator(".map-caption")).to_contain_text(
                        "Basemap tiles unavailable", timeout=20_000
                    )
                    await expect(page.locator(".vector-map")).to_be_visible()
                    record("tile_failure_local_geometry_actual_source_error")
                    server.tile_state = "normal"
                    denied = await browser.new_context(
                        viewport={"width": 390, "height": 900},
                        service_workers="block",
                    )
                    await denied.route("**/*", server.serve)
                    await denied.add_init_script("""(() => {
                      const get = HTMLCanvasElement.prototype.getContext;
                      HTMLCanvasElement.prototype.getContext = function(kind, ...args) {
                        return /webgl/i.test(kind) ? null : get.call(this, kind, ...args);
                      };
                    })();""")
                    denied_page = await denied.new_page()
                    denied_page.on("pageerror", lambda error: page_errors.append(str(error)))
                    await denied_page.goto(scoped_url(args.url, "/", mode="historical"))
                    await loaded(denied_page)
                    await expect(denied_page.locator(".map-caption")).to_contain_text(
                        "Map renderer unavailable", timeout=20_000
                    )
                    await expect(denied_page.locator(".vector-map")).to_be_visible()
                    await denied.close()
                    record("denied_webgl_distinct_renderer_fallback")

            if enabled("mobile_gestures"):
                async with guard("mobile_gestures"):
                    mobile = await browser.new_context(
                        viewport={"width": 390, "height": 844},
                        is_mobile=True,
                        has_touch=True,
                        device_scale_factor=1,
                        service_workers="block",
                    )
                    await mobile.route("**/*", server.serve)
                    mobile_page = await mobile.new_page()
                    mobile_page.on("pageerror", lambda error: page_errors.append(str(error)))
                    await mobile_page.goto(scoped_url(args.url, "/", mode="live"))
                    await loaded(mobile_page)
                    await tiles_loaded(mobile_page)
                    await mobile_page.locator(".map-stage").scroll_into_view_if_needed()
                    canvas = mobile_page.locator(".maplibregl-canvas")
                    assert (
                        await canvas.evaluate("el => getComputedStyle(el).touchAction")
                        == "pan-x pan-y"
                    )
                    session = await mobile.new_cdp_session(mobile_page)

                    async def swipe(points, dx, dy):
                        await session.send(
                            "Input.dispatchTouchEvent",
                            {"type": "touchStart", "touchPoints": points},
                        )
                        for step in range(1, 13):
                            moved = [
                                {
                                    **point,
                                    "x": point["x"] + dx * step / 12,
                                    "y": point["y"] + dy * step / 12,
                                }
                                for point in points
                            ]
                            await session.send(
                                "Input.dispatchTouchEvent",
                                {"type": "touchMove", "touchPoints": moved},
                            )
                            await asyncio.sleep(0.025)
                        await session.send(
                            "Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}
                        )
                        await mobile_page.wait_for_timeout(700)

                    box = await canvas.bounding_box()
                    scroll_before = await mobile_page.evaluate("scrollY")
                    await swipe(
                        [
                            {
                                "x": box["x"] + box["width"] / 2,
                                "y": box["y"] + box["height"] * 0.7,
                                "id": 0,
                            }
                        ],
                        0,
                        -110,
                    )
                    scroll_after = await mobile_page.evaluate("scrollY")
                    one_finger_scroll_delta = scroll_after - scroll_before
                    assert one_finger_scroll_delta > 30, (scroll_before, scroll_after)
                    await mobile_page.locator(".map-stage").scroll_into_view_if_needed()
                    await expect(
                        mobile_page.locator(".maplibregl-cooperative-gesture-screen")
                    ).to_have_css("opacity", "0", timeout=5000)
                    before = await stable_landmarks(mobile_page)
                    box = await canvas.bounding_box()
                    scroll_before = await mobile_page.evaluate("scrollY")
                    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    await swipe(
                        [{"x": cx - 20, "y": cy, "id": 0}, {"x": cx + 20, "y": cy, "id": 1}], 55, 0
                    )
                    await mobile_page.wait_for_timeout(1500)
                    after = await stable_landmarks(mobile_page)
                    assert landmark_distance(before, after) > 20, (
                        "Two-finger gesture did not move rendered vessel positions"
                    )
                    assert abs(await mobile_page.evaluate("scrollY") - scroll_before) < 3
                    await session.detach()
                    await mobile.close()
                    record(
                        "actual_mobile_one_finger_page_scroll_two_finger_map_pan",
                        scroll_delta=one_finger_scroll_delta,
                    )

            if enabled("pipeline"):
                async with guard("pipeline"):
                    await page.goto(scoped_url(args.url, "/pipeline", mode="historical"))
                    await expect(page.locator("#history")).to_contain_text("Los Angeles history")
                    await expect(page.locator("#quality-heading")).to_be_visible()
                    await expect(page.locator(".architecture-node")).to_have_count(len(NODE_IDS))
                    source_order = await page.locator(
                        ".architecture-sources [data-architecture-node]"
                    ).evaluate_all("els => els.map(el => el.dataset.architectureNode)")
                    process_order = await page.locator(
                        ".architecture-processing [data-architecture-node]"
                    ).evaluate_all("els => els.map(el => el.dataset.architectureNode)")
                    assert source_order == ["noaa", "ais", "weather", "news"], source_order
                    assert process_order == ["raw", "python", "postgis", "api", "web"], (
                        process_order
                    )
                    await expect(page.locator(".architecture-history")).to_contain_text(
                        "Historical branch from PostGIS"
                    )
                    await expect(page.locator(".architecture-scheduler")).to_contain_text(
                        "Continuous AIS collectors run independently"
                    )
                    for node_id in NODE_IDS:
                        node = page.locator(f"[data-architecture-node='{node_id}']")
                        await node.focus()
                        await expect(node).to_be_focused()
                        assert await node.evaluate(
                            """el => {const s=getComputedStyle(el);return s.outlineStyle !== 'none'
                            && parseFloat(s.outlineWidth)>0}"""
                        )
                        await page.keyboard.press("Enter")
                        await expect(node).to_have_attribute("aria-pressed", "true")
                        await expect(page.locator("#architecture-details h3")).to_contain_text(
                            (await node.locator("strong").text_content()).strip()
                        )
                        href = await page.locator(".architecture-source-link a").get_attribute(
                            "href"
                        )
                        assert href.startswith("https://github.com/SamABZD/portwatch/blob/main/"), (
                            href
                        )
                        if node_id == "dbt":
                            await expect(page.locator("#architecture-details")).to_contain_text(
                                "current live"
                            )
                    assert await page.evaluate(
                        """document.querySelector('#quality-heading').compareDocumentPosition(
                        document.querySelector('#history')) & Node.DOCUMENT_POSITION_FOLLOWING"""
                    )
                    assert await page.evaluate(
                        """document.querySelector('#history').compareDocumentPosition(
                        document.querySelector('#operations-heading'))
                        & Node.DOCUMENT_POSITION_FOLLOWING"""
                    )
                    tables = page.locator(".table-scroll")
                    for index in range(await tables.count()):
                        table = tables.nth(index)
                        await expect(table).to_have_attribute("role", "region")
                        assert await table.get_attribute("aria-label"), "Unlabeled scroll table"
                        await expect(table).to_have_attribute("tabindex", "0")
                    links = page.get_by_role(
                        "link", name="Explore SQL models and lineage", exact=True
                    )
                    assert await links.count() >= 1
                    for index in range(await links.count()):
                        assert (
                            await links.nth(index).get_attribute("href")
                            == "/api/history-lineage?view=static"
                        )
                        await expect(links.nth(index)).to_have_attribute("target", "_blank")
                    if server.lineage:
                        async with page.expect_popup() as popup:
                            await links.first.click()
                        lineage = await popup.value
                        await lineage.wait_for_load_state("domcontentloaded")
                        assert urlparse(lineage.url).path == "/api/history-lineage"
                        html = await lineage.content()
                        assert "dbt" in html.lower() and len(html) > 10_000
                        await expect(lineage).to_have_title("dbt Docs")
                        await lineage.wait_for_function(
                            "document.body.innerText.toLowerCase().includes('portwatch')",
                            timeout=20_000,
                        )
                        await lineage.close()
                        record(
                            "genuine_standalone_dbt_lineage_document",
                            sha256=hashlib.sha256(server.lineage).hexdigest(),
                        )
                    else:
                        unsupported.append(
                            "Standalone lineage document not supplied; real local href/target "
                            "and historical scope verified, document loading untested."
                        )
                    for width in WIDTHS:
                        await page.set_viewport_size({"width": width, "height": 900})
                        await page.wait_for_timeout(100)
                        assert await page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        )
                        wire_geometry = await page.locator(".architecture-wires").evaluate(
                            """el => ({viewBox:el.getAttribute('viewBox'),
                            paths:[...el.querySelectorAll('[data-architecture-edge]')].map(p => ({
                              id:p.dataset.architectureEdge,d:p.getAttribute('d')}))})"""
                        )
                        assert wire_geometry["paths"] and all(
                            "NaN" not in p["d"] and "Infinity" not in p["d"]
                            for p in wire_geometry["paths"]
                        )
                        record(
                            "pipeline_ordered_flow_all_widths", width=width, geometry=wire_geometry
                        )
                        if width == 1440:
                            await capture("pipeline-desktop.png")
                            await capture("pipeline-architecture.png", target=".architecture")
                        if width == 390:
                            await capture("pipeline-mobile.png")
                    record(
                        "architecture_keyboard_details_source_links_historical_scope_tables_order"
                    )
                    server.state = "pipeline_failure"
                    await page.reload()
                    await expect(page.locator(".query-error")).to_contain_text("disconnected")
                    server.state = "normal"
                    await page.get_by_role("button", name="Retry pipeline").click()
                    await expect(page.locator(".query-error")).to_have_count(0)
                    record("pipeline_api_failure_retry")
                    server.state = "pipeline_failure"
                    await expect(page.locator(".query-error")).to_contain_text(
                        "stale", timeout=35_000
                    )
                    await expect(
                        page.get_by_role("region", name="Port of Los Angeles pipeline", exact=True)
                    ).to_be_visible()
                    server.state = "normal"
                    await page.get_by_role("button", name="Retry pipeline").click()
                    await expect(page.locator(".query-error")).to_have_count(0)
                    record("pipeline_poll_failure_retains_saved_results_with_stale_label")
            if enabled("lineage"):
                async with guard("lineage"):
                    await page.goto(scoped_url(args.url, "/pipeline", mode="historical"))
                    await expect(page.locator("#history")).to_contain_text("Los Angeles history")
                    link = page.get_by_role(
                        "link", name="Explore SQL models and lineage", exact=True
                    ).first
                    async with page.expect_popup() as popup:
                        await link.click()
                    lineage = await popup.value
                    await lineage.wait_for_load_state("domcontentloaded")
                    await expect(lineage).to_have_title("dbt Docs")
                    await lineage.wait_for_function(
                        "document.body.innerText.toLowerCase().includes('portwatch')",
                        timeout=20_000,
                    )
                    await lineage.close()
                    record(
                        "lineage_template_asset_requests_separate_from_api_contract",
                        missing_decorative_assets=len(server.lineage_asset_requests),
                        genuine_document_sha256=hashlib.sha256(server.lineage).hexdigest(),
                    )
            assert not server.errors, server.errors
            assert not page_errors, page_errors
            report["verified"] = not failures
        except Exception as error:
            report["failure"] = f"{type(error).__name__}: {error}"
            failures.append({"case": "blocking_assertion", "detail": report["failure"][:1500]})
            server.gate.set()
            await page.screenshot(path=str(output / "workspace-failure.png"), full_page=True)
        finally:
            report.update(
                browser_version=browser.version,
                fixture_errors=server.errors,
                api_call_counts=dict(Counter(r["endpoint"] for r in server.requests)),
                frame_requests=[r for r in server.requests if r["endpoint"] == "frame"],
                local_test_tile_requests=len(server.tiles),
                external_requests_blocked=len(server.blocked),
                lineage_missing_decorative_assets=len(server.lineage_asset_requests),
            )
            await browser.close()
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "checks": len(checks),
                "screenshots": len(screenshots),
                "unsupported": unsupported,
                "failure": (
                    report.get("failure", "")
                    or (f"{failures[0]['case']}: {failures[0]['detail']}" if failures else "")
                )[:300],
                "failed_checks": len(failures),
                "report": str(output / "verification.json"),
            }
        )
    )
    return 0 if report["verified"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Verified immutable PortWatch HTTPS preview")
    parser.add_argument("--fixtures", required=True, help="Saved Phase 3 fixture directory")
    parser.add_argument("--output", default="artifacts/phase4")
    parser.add_argument("--lineage", help="Genuine saved standalone dbt HTML; never fetched")
    parser.add_argument("--freshness-fixtures", help="Genuine prepared30-minute frames directory")
    parser.add_argument("--browser-executable")
    parser.add_argument(
        "--resume", help="Reuse prior evidence with its explicit preview and source revision"
    )
    parser.add_argument(
        "--source-revision", required=True, help="Full verified source commit of this preview"
    )
    parser.add_argument(
        "--resume-source-revision", help="Prior report source commit when not recorded"
    )
    parser.add_argument(
        "--only", nargs="+", choices=GROUPS, help="Run only named independent scopes"
    )
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if (
        parsed.scheme != "https"
        or not re.fullmatch(r"portwatch-[a-z0-9]+-abzd\.vercel\.app", parsed.hostname or "")
        or parsed.port not in {None, 443}
    ):
        parser.error(
            "Only a verified immutable portwatch-*-abzd.vercel.app HTTPS preview is supported"
        )
    raise SystemExit(asyncio.run(verify(args)))


if __name__ == "__main__":
    main()
