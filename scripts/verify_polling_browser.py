"""Local polling regression using saved API responses; no provider or DB calls."""

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect

from scripts.verify_demo_browser import available_browser


async def verify(args):
    fixtures = Path(args.fixtures)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    requests, errors, aborted, checks = [], [], [], []
    held = {}
    fail_pipeline = False

    async def serve(route):
        nonlocal fail_pipeline
        url = urlparse(route.request.url)
        if url.hostname not in {"127.0.0.1", "localhost"}:
            await route.abort()
            return
        if not url.path.startswith("/api/"):
            await route.continue_()
            return
        endpoint = url.path.rsplit("/", 1)[-1]
        query = parse_qs(url.query)
        port = query.get("port", ["los_angeles"])[0]
        requests.append((endpoint, port, query.get("mode", [""])[0]))
        gate = held.get((endpoint, port))
        if gate:
            await gate.wait()
        try:
            if endpoint == "pipeline" and fail_pipeline:
                fail_pipeline = False
                await route.fulfill(status=503, json={"detail": {"code": "disconnected"}})
                return
            name = (
                f"{query.get('mode', ['live'])[0]}-{port}"
                if endpoint == "frame"
                else f"{endpoint}-{port}"
                if endpoint in {"catalog", "health"}
                else endpoint
            )
            body = json.loads((fixtures / f"{name}.json").read_text("utf-8"))
            # These response controls are synthetic test fixtures only.
            if endpoint == "frame":
                body["staleness_minutes"] = int(query.get("stale", ["15"])[0])
                if query.get("mode") == ["historical"]:
                    body["cutoff_utc"] = query["at"][0]
            await route.fulfill(json=body)
        except Exception:
            if gate is None:
                raise
            # An obsolete browser request can already have been cancelled.

    def counts():
        return Counter(item[0] for item in requests)

    async def requested(endpoint, target):
        async with asyncio.timeout(5):
            while counts()[endpoint] < target:
                await asyncio.sleep(0.01)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=available_browser(playwright, None)
        )
        try:
            page = await browser.new_page(viewport={"width": 1365, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("requestfailed", lambda request: aborted.append(request.url))
            await page.route("**/*", serve)
            await page.add_init_script("""
                window.pollingVisibility = 'visible';
                Object.defineProperty(document, 'visibilityState', {
                  get: () => window.pollingVisibility
                });
                window.setPollingVisibility = (state) => {
                  window.pollingVisibility = state;
                  document.dispatchEvent(new Event('visibilitychange'));
                };
            """)
            await page.clock.install(time=datetime(2026, 10, 13, 20, 0, tzinfo=UTC))
            await page.clock.pause_at(datetime(2026, 10, 13, 20, 0, 1, tzinfo=UTC))

            async def ready():
                await expect(page.locator(".updating")).to_have_count(0)
                await page.wait_for_load_state("networkidle")

            async def visibility(state):
                await page.evaluate("state => window.setPollingVisibility(state)", state)

            await page.goto(args.url)
            await ready()
            initial = counts()
            assert initial["frame"] == initial["health"] == initial["catalog"] == 1
            await page.clock.run_for(30000)
            await ready()
            assert counts()["frame"] == 2 and counts()["health"] == 2
            checks.append("visible_live_cadence_preserved")

            await visibility("hidden")
            before = counts()
            await page.clock.run_for(90000)
            assert counts() == before
            await visibility("visible")
            await ready()
            resumed = counts()
            assert all(resumed[key] == before[key] + 1 for key in ("frame", "health"))
            assert resumed["catalog"] == before["catalog"]
            await visibility("visible")
            await page.wait_for_load_state("networkidle")
            assert counts() == resumed
            checks.append("hidden_dashboard_zero_requests_single_resume")

            gate = asyncio.Event()
            held[("frame", "los_angeles")] = gate
            frame_count = counts()["frame"]
            await page.get_by_role("button", name="Refresh saved reports", exact=True).click()
            await page.wait_for_function("document.querySelector('.updating') !== null")
            await requested("frame", frame_count + 1)
            await page.get_by_role("combobox", name="Select port").select_option("oakland")
            await ready()
            gate.set()
            held.clear()
            await page.wait_for_load_state("networkidle")
            await expect(page.get_by_role("combobox", name="Select port")).to_have_value("oakland")
            assert any("port=los_angeles" in url and "/api/frame" in url for url in aborted)
            checks.append("manual_refresh_and_obsolete_port_request_cancelled")

            await page.get_by_role("button", name="Historical replay", exact=True).click()
            await ready()
            before = counts()["frame"]
            await page.clock.run_for(60000)
            await ready()
            assert counts()["frame"] == before
            await page.get_by_role("button", name="Play replay", exact=True).click()
            await visibility("hidden")
            minute = await page.get_by_role("slider", name="Historical time slider").input_value()
            before = counts()
            await page.clock.run_for(90000)
            assert counts() == before
            assert (
                await page.get_by_role("slider", name="Historical time slider").input_value()
                == minute
            )
            await visibility("visible")
            await ready()
            await page.get_by_role("button", name="Pause replay", exact=True).click()
            checks.append("historical_no_periodic_frame_requests_hidden_playback_paused")
            await page.screenshot(path=str(output / "dashboard-desktop.png"), full_page=True)
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.screenshot(path=str(output / "dashboard-mobile.png"), full_page=True)

            await page.goto(args.url.rstrip("/") + "/pipeline")
            await page.wait_for_load_state("networkidle")
            # Re-anchor the paused clock after this full document navigation.
            await page.clock.pause_at(
                datetime.fromtimestamp(await page.evaluate("Date.now()") / 1000 + 1, UTC)
            )
            await expect(
                page.get_by_role("heading", name="Port of Oakland", exact=True)
            ).to_be_visible()
            before = counts()
            await page.clock.run_for(30000)
            await requested("pipeline", before["pipeline"] + 1)
            await page.wait_for_load_state("networkidle")
            assert counts()["pipeline"] == before["pipeline"] + 1, (before, counts())
            assert counts()["frame"] == before["frame"] and counts()["health"] == before["health"]
            await visibility("hidden")
            before = counts()
            await page.clock.run_for(90000)
            assert counts() == before
            await visibility("visible")
            await requested("pipeline", before["pipeline"] + 1)
            await page.wait_for_load_state("networkidle")
            assert counts()["pipeline"] == before["pipeline"] + 1
            checks.append("route_cleanup_hidden_pipeline_zero_requests_single_resume")

            gate = asyncio.Event()
            held[("pipeline", "los_angeles")] = gate
            await page.clock.run_for(30000)
            await requested("pipeline", before["pipeline"] + 2)
            before_slow = counts()
            await page.clock.run_for(90000)
            assert counts() == before_slow
            await visibility("hidden")
            await visibility("visible")
            gate.set()
            held.clear()
            await requested("pipeline", before_slow["pipeline"] + 1)
            await page.wait_for_load_state("networkidle")
            assert counts()["pipeline"] == before_slow["pipeline"] + 1
            checks.append("slow_pipeline_no_overlap_aborted_resume_recovery")

            fail_pipeline = True
            await page.clock.run_for(30000)
            await expect(page.locator(".query-error")).to_contain_text("disconnected")
            await page.get_by_role("button", name="Retry pipeline", exact=True).click()
            await expect(page.locator(".query-error")).to_have_count(0)
            checks.append("pipeline_failure_manual_retry_recovers")
            await page.screenshot(path=str(output / "pipeline-mobile.png"), full_page=True)
            await page.set_viewport_size({"width": 1365, "height": 900})
            await page.screenshot(path=str(output / "pipeline-desktop.png"), full_page=True)
            assert not errors, errors
            report = {
                "checked_at_utc": datetime.now(UTC).isoformat(),
                "url": args.url,
                "fixtures": "saved genuine API responses with isolated test controls",
                "checks": checks,
                "requests": dict(counts()),
                "page_errors": errors,
            }
            (output / "verification.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3160")
    parser.add_argument("--fixtures", required=True, help="Directory of saved baseline API JSON.")
    parser.add_argument("--output", default="artifacts/phase1")
    args = parser.parse_args()
    assert urlparse(args.url).hostname in {"localhost", "127.0.0.1"}, "Local verification only"
    asyncio.run(verify(args))


if __name__ == "__main__":
    main()
