"""Verify navigation and scope using saved, genuine API responses.

Requires an already running local frontend or a verified immutable PortWatch HTTPS
preview. API routes use local fixture files only; all other-origin requests and
unexpected API endpoints are blocked. Historical AIS frames are verified only at
their recorded cutoff, without changing analytics.
"""

import argparse
import asyncio
import base64
import copy
import json
import os
import re
import struct
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from playwright.async_api import async_playwright, expect

from scripts.verify_demo_browser import available_browser

PORT_NAMES = {"los_angeles": "Port of Los Angeles", "oakland": "Port of Oakland"}
WIDTHS = (390, 768, 1024, 1440)


class Fixtures:
    def __init__(self, directory):
        self.directory = directory
        self.prepared = False
        self.frames = {
            (port, mode): self.read(f"{mode}-{port}")
            for port in PORT_NAMES
            for mode in ("live", "historical")
        }
        self.nondefault = {port: self.read(f"historical-{port}-600") for port in PORT_NAMES}
        self.prepared_frames = {
            port: self.read(f"prepared-historical-{port}") for port in PORT_NAMES
        }

    def read(self, name):
        return json.loads((self.directory / f"{name}.json").read_text(encoding="utf-8"))

    def frame(self, port, mode, minute=750):
        if mode == "historical" and minute == 600:
            return self.nondefault[port]
        if mode == "historical" and self.prepared:
            return self.prepared_frames[port]
        return self.frames[(port, mode)]

    def response(self, endpoint, query, view_query=None):
        port = query.get("port", ["los_angeles"])[0]
        if port not in PORT_NAMES:
            raise ValueError(f"Unsafe port query: {port}")
        if endpoint == "frame":
            mode = query.get("mode", ["live"])[0]
            if mode not in {"live", "historical"}:
                raise ValueError(f"Unsafe mode query: {mode}")
            frame = self.frame(port, mode)
            if mode == "historical":
                requested = datetime.fromisoformat(query["at"][0].replace("Z", "+00:00"))
                if requested == datetime.fromisoformat(self.nondefault[port]["cutoff_utc"]):
                    frame = self.frame(port, mode, 600)
                recorded = datetime.fromisoformat(frame["cutoff_utc"].replace("Z", "+00:00"))
                if requested != recorded:
                    raise ValueError("Missing genuine fixture for the requested replay cutoff")
            return copy.deepcopy(frame)
        if endpoint == "catalog":
            if self.prepared:
                return self.read("prepared-catalog")
            minute = 600 if (view_query or {}).get("minute") == ["600"] else 750
            body = self.read(f"catalog-{port}")
            body["datasets"] = [
                {**dataset, **copy.deepcopy(self.frame(port, dataset["mode"], minute)["dataset"])}
                for dataset in body["datasets"]
            ]
            return body
        if endpoint == "health":
            return self.read(f"health-{port}")
        if endpoint == "pipeline" and self.prepared:
            return self.read("prepared-pipeline")
        if endpoint in {"pipeline", "history"}:
            return self.read(endpoint)
        raise ValueError(f"Unexpected API endpoint: {endpoint}")


def scoped_url(base, path, port="los_angeles", mode="live", minute=750):
    return (
        urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        + "?"
        + urlencode(
            {
                "port": port,
                "mode": mode,
                "minute": minute,
            }
        )
    )


async def assert_scope_url(url, port, mode, minute=750):
    query = parse_qs(urlparse(url).query)
    assert query.get("port") == [port], (url, port)
    assert query.get("mode") == [mode], (url, mode)
    assert query.get("minute") == [str(minute)], (url, minute)


async def ready(page, route, port):
    await page.wait_for_load_state("networkidle")
    await expect(page.locator(".app-header")).to_have_count(1)
    if route == "map":
        await expect(page.locator(".news-panel")).to_be_visible(timeout=20_000)
        await expect(page.locator(".metric-strip")).to_have_attribute("aria-busy", "false")
        await expect(page.locator(".updating")).to_have_count(0)
    else:
        selected = page.get_by_role("region", name=f"{PORT_NAMES[port]} pipeline", exact=True)
        await expect(selected).to_be_visible(timeout=20_000)
        await expect(
            selected.get_by_role("heading", name=PORT_NAMES[port], exact=True)
        ).to_be_visible()
        await expect(page.locator("section[aria-label$=' pipeline']")).to_have_count(1)
        await expect(page.locator("#history")).to_contain_text("Los Angeles history")
    await expect(page.locator(".query-error")).to_have_count(0)


async def header(page, route, port, mode, minute=750):
    nav = page.get_by_role("navigation", name="Primary navigation", exact=True)
    await expect(nav).to_have_count(1)
    selector = page.get_by_role("combobox", name="Select port", exact=True)
    await expect(selector).to_have_value(port)
    assert await selector.evaluate(
        "el => [...el.labels].some(label => /Port/.test(label.textContent))"
    )
    active_name = "Vessel map" if route == "map" else "Data pipeline"
    inactive_name = "Data pipeline" if route == "map" else "Vessel map"
    active = nav.get_by_role("link", name=active_name, exact=True)
    inactive = nav.get_by_role("link", name=inactive_name, exact=True)
    await expect(active).to_have_attribute("aria-current", "page")
    assert await inactive.get_attribute("aria-current") is None
    for name, path in (("Vessel map", "/"), ("Data pipeline", "/pipeline")):
        link = nav.get_by_role("link", name=name, exact=True)
        await expect(link).to_be_visible()
        target = urljoin(page.url, await link.get_attribute("href"))
        assert urlparse(target).path == path
        await assert_scope_url(target, port, mode, minute)
    styles = await active.evaluate(
        """(el, other) => {
        const active = getComputedStyle(el), inactive = getComputedStyle(
          [...el.closest('nav').querySelectorAll('a')].find(a => a.textContent.trim() === other));
        return {
          weight: active.fontWeight !== inactive.fontWeight,
          underline: active.textDecorationLine.includes('underline') &&
            !inactive.textDecorationLine.includes('underline'),
          border: ['Top', 'Right', 'Bottom', 'Left'].some(side =>
            parseFloat(active['border' + side + 'Width']) >
            parseFloat(inactive['border' + side + 'Width']))
        };
    }""",
        inactive_name,
    )
    assert any(styles.values()), f"Active navigation needs a cue beyond color: {styles}"
    source = page.get_by_role("link", name="View source", exact=True)
    if route == "map":
        await expect(source).to_have_count(1)
        await expect(source).to_be_visible()
        await expect(source).to_have_attribute("href", "https://github.com/SamABZD/portwatch")
    else:
        await expect(source).to_have_count(0)


async def layout(page):
    measurements = await page.evaluate("""() => {
      const controls = [...document.querySelectorAll(
        '.app-header nav a, .app-header select, .intro-row a')];
      const brand = document.querySelector('.brand'), b = brand.getBoundingClientRect(),
        style = getComputedStyle(brand), header = document.querySelector('.app-header'),
        h = header.getBoundingClientRect(), headerStyle = getComputedStyle(header);
      return {width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        zoom: getComputedStyle(document.documentElement).zoom,
        brand: {left:b.left, top:b.top, width:b.width, height:b.height,
          font:style.fontFamily, size:style.fontSize, weight:style.fontWeight,
          leading:style.lineHeight, tracking:style.letterSpacing,
          symbol:brand.querySelector('svg').outerHTML},
        header: {left:h.left, top:h.top, width:h.width, height:h.height,
          columnGap:headerStyle.columnGap, rowGap:headerStyle.rowGap},
        controls: controls.map(el => { const r = el.getBoundingClientRect(); return {
          text: el.textContent.trim(), left:r.left, right:r.right, top:r.top, bottom:r.bottom
        }; })};
    }""")
    assert measurements["scrollWidth"] <= measurements["width"], measurements
    for control in measurements["controls"]:
        assert control["left"] >= 0 and control["right"] <= measurements["width"] + 1, control
        assert control["top"] >= 0, control
        assert control["bottom"] <= await page.evaluate("innerHeight"), control
    heading = await page.get_by_role("heading", level=1).bounding_box()
    assert heading and heading["y"] >= 0, "Page purpose is above the viewport"
    assert heading["y"] + heading["height"] <= await page.evaluate("innerHeight"), (
        "Page purpose does not fit in the first viewport"
    )
    return measurements


async def keyboard_navigation(page, name):
    link = page.get_by_role("navigation", name="Primary navigation").get_by_role(
        "link", name=name, exact=True
    )
    await link.focus()
    await page.keyboard.press("Shift+Tab")
    await page.keyboard.press("Tab")
    await expect(link).to_be_focused()
    focus = await link.evaluate("""el => {
      const style = getComputedStyle(el);
      return (parseFloat(style.outlineWidth) > 0 && style.outlineStyle !== 'none') ||
        style.boxShadow !== 'none';
    }""")
    assert focus, "Keyboard navigation link has no visible focus indicator"
    await page.keyboard.press("Enter")


async def capture_physical_viewport(context, page, path):
    """Capture the browser surface directly, preserving the native zoom bitmap."""
    viewport = await page.evaluate(
        "() => ({width:innerWidth, height:innerHeight, dpr:devicePixelRatio})"
    )
    session = await context.new_cdp_session(page)
    try:
        screenshot = await session.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
            },
        )
    finally:
        await session.detach()
    bitmap = base64.b64decode(screenshot["data"], validate=True)
    assert bitmap[:8] == b"\x89PNG\r\n\x1a\n", "Browser capture is not PNG"
    width, height = struct.unpack(">II", bitmap[16:24])
    expected = (
        round(viewport["width"] * viewport["dpr"]),
        round(viewport["height"] * viewport["dpr"]),
    )
    assert (width, height) == expected, (
        "Browser capture cropped the physical viewport",
        (width, height),
        expected,
    )
    path.write_bytes(bitmap)
    return {"method": "CDP Page.captureScreenshot", "width": width, "height": height}


async def native_zoom(playwright, args, serve, errors, checks, screenshots, output):
    """Use Chrome's zoom API in an owned temporary profile, not CSS or pinch zoom."""
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(
        prefix="portwatch-navigation-zoom-", dir=temporary_parent
    ) as directory:
        owned = Path(directory).resolve()
        assert owned.parent == temporary_parent
        assert owned.name.startswith("portwatch-navigation-zoom-")
        extension, profile = owned / "extension", owned / "profile"
        assert extension.resolve().is_relative_to(owned)
        assert profile.resolve().is_relative_to(owned)
        extension.mkdir()
        (extension / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 3,
                    "name": "PortWatch local zoom QA",
                    "version": "1.0",
                    "permissions": ["tabs"],
                    "background": {"service_worker": "background.js"},
                }
            ),
            encoding="utf-8",
        )
        (extension / "background.js").write_text(
            "chrome.runtime.onInstalled.addListener(() => {});\n", encoding="utf-8"
        )
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            headless=True,
            channel="chromium",
            executable_path=available_browser(playwright, args.browser_executable),
            no_viewport=True,
            args=[
                f"--disable-extensions-except={extension}",
                f"--load-extension={extension}",
                "--window-size=1458,900",
                "--force-device-scale-factor=1",
            ],
        )
        await context.route("**/*", serve)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            worker = (
                context.service_workers[0]
                if context.service_workers
                else (await context.wait_for_event("serviceworker", timeout=20_000))
            )
            for route, path in (("map", "/"), ("pipeline", "/pipeline")):
                await page.goto(scoped_url(args.url, path, mode="historical"))
                await ready(page, route, "los_angeles")

                async def set_zoom(factor):
                    return await worker.evaluate(
                        """async ({url, factor}) => {
                      const tab = (await chrome.tabs.query({})).find(tab => tab.url === url);
                      if (!tab) throw new Error('Target QA tab not found');
                      await chrome.tabs.setZoomSettings(tab.id, {
                        mode:'automatic', scope:'per-tab'
                      });
                      await chrome.tabs.setZoom(tab.id, factor);
                      return await chrome.tabs.getZoom(tab.id);
                    }""",
                        {"url": page.url, "factor": factor},
                    )

                assert await set_zoom(1) == 1
                await page.wait_for_function("innerWidth === 1440 && devicePixelRatio === 1")
                before = await page.evaluate("""() => ({width:innerWidth, dpr:devicePixelRatio,
                  outerWidth, cssZoom:getComputedStyle(document.documentElement).zoom,
                  visualScale:visualViewport.scale,
                  compact:matchMedia('(max-width:760px)').matches})
                """)
                zoom = await set_zoom(2)
                assert zoom == 2, zoom
                await page.wait_for_function("innerWidth === 720 && devicePixelRatio === 2")
                await page.evaluate("scrollTo(0, 0)")
                after = await page.evaluate("""() => ({width:innerWidth, dpr:devicePixelRatio,
                  outerWidth, cssZoom:getComputedStyle(document.documentElement).zoom,
                  visualScale:visualViewport.scale,
                  compact:matchMedia('(max-width:760px)').matches})
                """)
                assert before["outerWidth"] == after["outerWidth"]
                assert float(after["cssZoom"]) == 1 and after["visualScale"] == 1, after
                assert not before["compact"] and after["compact"], (before, after)
                await header(page, route, "los_angeles", "historical")
                measurements = await layout(page)
                name = f"{route}-browser-zoom-200.png"
                capture = await capture_physical_viewport(context, page, output / name)
                screenshots.append(name)
                checks.append(
                    {
                        "route": route,
                        "browser_zoom_percent": 200,
                        "chrome_tabs_get_zoom": zoom,
                        "before": before,
                        "after": after,
                        "header": measurements,
                        "capture": capture,
                    }
                )
        except Exception:
            await page.screenshot(path=str(output / "native-zoom-failure.png"), full_page=True)
            raise
        finally:
            await context.close()


async def verify(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    fixtures = Fixtures(Path(args.fixtures))
    origin = urlparse(args.url)
    bypass = (
        os.environ.get("PORTWATCH_TEST_VERCEL_BYPASS")
        if origin.scheme == "https"
        and re.fullmatch(r"portwatch-[a-z0-9]+-abzd\.vercel\.app", origin.hostname or "")
        else None
    )
    requests, errors, blocked, checks, screenshots = [], [], [], [], []
    report = {
        "verified": False,
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "url": args.url,
        "fixtures": "Genuine saved AIS/news and checksummed prepared exports; analytics unchanged",
        "checks": checks,
        "errors": errors,
        "screenshots": screenshots,
        "external_requests_sent": 0,
        "upstream_api_requests_sent": 0,
        "vercel_bypass_scope": "Selected preview origin static requests only" if bypass else None,
    }

    async def serve(route):
        url = urlparse(route.request.url)
        if (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
            blocked.append(route.request.url)
            await route.abort()
            return
        if not url.path.startswith("/api/"):
            if bypass:
                await route.continue_(
                    headers={**route.request.headers, "x-vercel-protection-bypass": bypass}
                )
            else:
                await route.continue_()
            return
        endpoint = url.path.rsplit("/", 1)[-1]
        query = parse_qs(url.query)
        requests.append({"endpoint": endpoint, "query": query})
        try:
            view_query = parse_qs(urlparse(route.request.frame.url).query)
            await route.fulfill(json=fixtures.response(endpoint, query, view_query))
        except Exception as error:
            errors.append(f"{endpoint}: {error}")
            await route.fulfill(status=500, json={"detail": {"code": "fixture_error"}})

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=available_browser(playwright, args.browser_executable)
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            service_workers="block",
        )
        await context.route("**/*", serve)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def counts():
            return Counter(request["endpoint"] for request in requests)

        async def map_scope(port, mode, minute=750):
            await ready(page, "map", port)
            await header(page, "map", port, mode, minute)
            saved = fixtures.frame(port, mode, minute)
            metric = saved["metrics"]["fresh_vessels"]
            expected = "—" if metric is None else f"{metric:,}"
            await expect(page.locator(".metric strong").first).to_have_text(expected)
            matching = [request for request in requests if request["endpoint"] == "frame"]
            assert matching[-1]["query"]["port"] == [port]
            assert matching[-1]["query"]["mode"] == [mode]
            if mode == "historical":
                await expect(
                    page.get_by_role("slider", name="Historical time slider")
                ).to_have_value(str(minute))
                await expect(page.get_by_test_id("cutoff-clock")).to_contain_text(
                    "10:00" if minute == 600 else "12:30"
                )
                requested = datetime.fromisoformat(matching[-1]["query"]["at"][0])
                assert requested == datetime.fromisoformat(saved["cutoff_utc"])

        try:
            await page.goto(scoped_url(args.url, "/"))
            await map_scope("los_angeles", "live")
            await expect(page.get_by_role("heading", level=1)).to_contain_text("data engineering")
            assert "data engineering" in (await page.title()).lower()
            description = await page.locator("meta[name='description']").get_attribute("content")
            assert "data engineering" in description.lower(), description
            intro_link = page.get_by_role("link", name="Explore the data pipeline", exact=True)
            await expect(intro_link).to_be_visible()
            await assert_scope_url(
                urljoin(page.url, await intro_link.get_attribute("href")), "los_angeles", "live"
            )
            source_link = page.get_by_role("link", name="View source", exact=True)
            await expect(source_link).to_be_visible()
            await expect(source_link).to_have_attribute(
                "href", "https://github.com/SamABZD/portwatch"
            )
            checks.append("first_viewport_purpose_metadata_pipeline_and_public_source_links")

            before = counts()
            await page.get_by_role("combobox", name="Select port").select_option("los_angeles")
            await page.get_by_role("button", name="Live", exact=True).click()
            await map_scope("los_angeles", "live")
            assert counts() == before, (before, counts())
            checks.append("same_port_same_mode_leave_loaded_state_and_requests_unchanged")

            await page.get_by_role("combobox", name="Select port").select_option("oakland")
            await map_scope("oakland", "live")
            await assert_scope_url(page.url, "oakland", "live")
            await page.get_by_role("button", name="Historical replay", exact=True).click()
            await map_scope("oakland", "historical")
            await assert_scope_url(page.url, "oakland", "historical")
            before = counts()
            await page.get_by_role("button", name="Historical replay", exact=True).click()
            await map_scope("oakland", "historical")
            assert counts() == before
            checks.append("both_port_frame_queries_historical_scope_and_same_mode")

            await keyboard_navigation(page, "Data pipeline")
            await ready(page, "pipeline", "oakland")
            await header(page, "pipeline", "oakland", "historical")
            await assert_scope_url(page.url, "oakland", "historical")
            await page.go_back()
            await map_scope("oakland", "historical")
            await page.go_forward()
            await ready(page, "pipeline", "oakland")
            await header(page, "pipeline", "oakland", "historical")
            checks.append("keyboard_route_navigation_browser_back_forward_preserve_scope")

            await page.get_by_role("combobox", name="Select port").select_option("los_angeles")
            await ready(page, "pipeline", "los_angeles")
            await header(page, "pipeline", "los_angeles", "historical")
            await assert_scope_url(page.url, "los_angeles", "historical")
            await keyboard_navigation(page, "Vessel map")
            await map_scope("los_angeles", "historical")
            await page.reload()
            await map_scope("los_angeles", "historical")
            checks.append("pipeline_selector_changes_operational_scope_map_return_reload")

            header_geometry = {}
            for route in ("map", "pipeline"):
                if route == "pipeline":
                    await (
                        page.get_by_role("navigation", name="Primary navigation")
                        .get_by_role("link", name="Data pipeline", exact=True)
                        .click()
                    )
                    await ready(page, route, "los_angeles")
                for width in WIDTHS:
                    await page.set_viewport_size({"width": width, "height": 900})
                    await page.evaluate("scrollTo(0, 0)")
                    await header(page, route, "los_angeles", "historical")
                    measurements = await layout(page)
                    geometry = {key: measurements[key] for key in ("brand", "header")}
                    if route == "map":
                        header_geometry[width] = geometry
                    else:
                        for element in ("brand", "header"):
                            for key, value in geometry[element].items():
                                original = header_geometry[width][element][key]
                                if isinstance(value, (int, float)):
                                    assert abs(value - original) <= 0.5, (
                                        width,
                                        key,
                                        original,
                                        value,
                                    )
                                else:
                                    assert value == original, (width, key, original, value)
                    checks.append({"route": route, "viewport": width, "header": measurements})
                    if width in {390, 1440}:
                        name = f"{route}-{'mobile' if width == 390 else 'desktop'}.png"
                        await page.screenshot(path=str(output / name), full_page=False)
                        screenshots.append(name)
            checks.append("brand_geometry_typography_header_spacing_match_both_routes_all_widths")
            await native_zoom(playwright, args, serve, errors, checks, screenshots, output)

            direct_cases = (
                ("/", "oakland", "live", 750, "oakland", "live", 750),
                ("/pipeline", "los_angeles", "historical", 0, "los_angeles", "historical", 0),
                ("/pipeline", "oakland", "live", 1440, "oakland", "live", 1440),
                ("/", "unknown", "invalid", "NaN", "los_angeles", "live", 750),
                ("/", "oakland", "historical", -1, "oakland", "historical", 750),
                ("/pipeline", "oakland", "historical", 1441, "oakland", "historical", 750),
                ("/", "oakland", "historical", "750.5", "oakland", "historical", 750),
            )
            for path, raw_port, raw_mode, raw_minute, port, mode, minute in direct_cases:
                await page.goto(scoped_url(args.url, path, raw_port, raw_mode, raw_minute))
                route = "map" if path == "/" else "pipeline"
                await ready(page, route, port)
                await header(page, route, port, mode, minute)
                if route == "map":
                    await map_scope(port, mode, minute)
                checks.append(
                    {
                        "direct_url": path,
                        "input": [raw_port, raw_mode, raw_minute],
                        "validated_scope": [port, mode, minute],
                    }
                )

            for port in PORT_NAMES:
                await page.goto(scoped_url(args.url, "/", port, "historical", 600))
                await map_scope(port, "historical", 600)
                await assert_scope_url(page.url, port, "historical", 600)
                await keyboard_navigation(page, "Data pipeline")
                await ready(page, "pipeline", port)
                await header(page, "pipeline", port, "historical", 600)
                await page.go_back()
                await map_scope(port, "historical", 600)
                await page.reload()
                await map_scope(port, "historical", 600)
                await keyboard_navigation(page, "Data pipeline")
                await ready(page, "pipeline", port)
                await keyboard_navigation(page, "Vessel map")
                await map_scope(port, "historical", 600)
                checks.append(
                    {
                        "port": port,
                        "case": "genuine_nondefault_replay_roundtrip_reload",
                        "minute": 600,
                        "cutoff_utc": fixtures.nondefault[port]["cutoff_utc"],
                        "prefix_observations": fixtures.nondefault[port]["prefix_observations"],
                    }
                )

            fixtures.prepared = True
            await page.goto(scoped_url(args.url, "/pipeline", "los_angeles", "live"))
            await ready(page, "pipeline", "los_angeles")
            await header(page, "pipeline", "los_angeles", "historical")
            await assert_scope_url(page.url, "los_angeles", "historical")
            await expect(page.locator(".pipeline-intro")).to_contain_text("Historical")
            await expect(page.locator(".pipeline-intro")).to_contain_text("unavailable")
            footer = page.locator(".app-footer").get_by_role(
                "link", name="Return to the vessel map"
            )
            await assert_scope_url(
                urljoin(page.url, await footer.get_attribute("href")), "los_angeles", "historical"
            )
            await footer.click()
            await map_scope("los_angeles", "historical")
            live = page.get_by_role("button", name="Live", exact=True)
            await expect(live).to_be_disabled()
            await expect(live).to_have_attribute("aria-describedby", "prepared-mode-note")
            await expect(page.locator("#prepared-mode-note")).to_contain_text("unavailable")
            checks.append("genuine_prepared_pipeline_normalizes_scope_map_links_disable_live")
            await page.goto(scoped_url(args.url, "/", "oakland", "live"))
            await map_scope("los_angeles", "historical")
            await assert_scope_url(page.url, "los_angeles", "historical")
            await expect(
                page.get_by_role("combobox", name="Select port").locator(
                    "option[value='oakland']:not([disabled])"
                )
            ).to_have_count(0)
            checks.append(
                "prepared_catalog_restricts_ports_incoming_oakland_falls_back_los_angeles"
            )
            assert not errors, errors
            report["verified"] = True
        except Exception as error:
            report["failure"] = f"{type(error).__name__}: {error}"
            await page.screenshot(path=str(output / "navigation-failure.png"), full_page=True)
        finally:
            report.update(
                resource_call_counts=dict(counts()),
                external_requests_blocked=len(blocked),
                frame_scopes=[
                    request["query"] for request in requests if request["endpoint"] == "frame"
                ],
                browser_version=browser.version,
            )
            await browser.close()
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {
        "verified": report["verified"],
        "checks": len(checks),
        "errors": len(errors),
        "screenshots": len(screenshots),
        "resource_call_counts": report["resource_call_counts"],
        "report": str(output / "verification.json"),
    }
    if "failure" in report:
        summary["failure"] = report["failure"].splitlines()[0][:300]
    print(json.dumps(summary))
    return 0 if report["verified"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:3160",
        help="Already running local frontend or verified immutable PortWatch HTTPS preview",
    )
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--output", default="artifacts/phase3")
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    parsed = urlparse(args.url)
    local = parsed.scheme in {"http", "https"} and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    preview = (
        parsed.scheme == "https"
        and re.fullmatch(r"portwatch-[a-z0-9]+-abzd\.vercel\.app", parsed.hostname or "")
        and parsed.port in {None, 443}
    )
    if parsed.username or parsed.password or not (local or preview):
        parser.error("Only local frontends or immutable PortWatch HTTPS previews are supported")
    raise SystemExit(asyncio.run(verify(args)))


if __name__ == "__main__":
    main()
