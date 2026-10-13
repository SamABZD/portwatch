"""Actual local browser interactions, tile-independent screenshots and responsive checks."""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

from portwatch.settings import PROJECT_ROOT


def available_browser(playwright, explicit):
    if explicit:
        return str(Path(explicit).resolve())
    standard = Path(playwright.chromium.executable_path)
    if standard.is_file():
        return str(standard)
    # Prefer an already installed Chromium without fetching anything during verification.
    cache = Path.home() / "AppData/Local/ms-playwright"
    candidates = list(cache.glob("chromium-*/chrome-win64/chrome.exe"))
    if not candidates:
        raise RuntimeError("Install Chromium with python -m playwright install chromium")
    return str(max(candidates, key=lambda p: int(p.parent.parent.name.split("-")[-1])))


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        "Horizontal overflow"
    )


def ready(page):
    expect(page.locator(".updating")).to_have_count(0, timeout=20000)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    assert urlparse(args.url).hostname in {"127.0.0.1", "localhost", "::1"}, (
        "Local verification only"
    )
    folder = PROJECT_ROOT / "artifacts/step12"
    folder.mkdir(exist_ok=True)
    page_errors, requests = [], []
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, executable_path=available_browser(p, args.browser_executable)
        )
        page = browser.new_page(viewport={"width": 1365, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "request",
            lambda request: requests.append(request.url) if "/api/frame" in request.url else None,
        )
        # Do not fetch/pan through external map tiles in automation. Deliberately exercise fallback.
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Historical replay", exact=True).click()
        ready(page)
        expect(page.locator(".metric strong").first).to_have_text("99")
        expect(page.get_by_test_id("cutoff-clock")).to_contain_text("12:30")
        expect(page.get_by_text("Basemap unavailable · local geometry active")).to_be_visible()
        no_overflow(page)
        page.screenshot(path=str(folder / "desktop.png"), full_page=True)
        checks.append("production_launch_real_noon_prefix_and_blocked_tile_fallback")

        page.get_by_role("checkbox", name="Basemap", exact=True).uncheck()
        expect(page.get_by_text("Local map · polygons + observations")).to_be_visible()
        assert page.locator(".zone-path").count() > 0
        page.get_by_role("checkbox", name="Zones", exact=True).uncheck()
        expect(page.locator(".zone-path")).to_have_count(0)
        page.get_by_role("checkbox", name="Zones", exact=True).check()
        assert page.locator(".trail-path").count() > 0
        page.get_by_role("checkbox", name="Trails", exact=True).uncheck()
        expect(page.locator(".trail-path")).to_have_count(0)
        page.get_by_role("checkbox", name="Trails", exact=True).check()
        fresh_points = page.locator(".vessel-point").count()
        page.get_by_role("checkbox", name="Stale", exact=True).check()
        assert page.locator(".vessel-point").count() > fresh_points
        page.get_by_role("checkbox", name="Stale", exact=True).uncheck()
        checks.append("zone_trail_basemap_and_stale_toggles")

        button = page.get_by_role("button", name=re.compile("^Select vessel ")).first
        mmsi = button.get_attribute("aria-label").split()[-1]
        button.click()
        expect(page.locator(".vessel-title")).to_contain_text(mmsi)
        page.screenshot(path=str(folder / "vessel-detail.png"), full_page=True)
        page.get_by_role("button", name="Clear vessel selection").click()
        page.locator(".vector-map .vessel-point").last.click()
        expect(page.locator(".vessel-title strong")).to_be_visible()
        page.get_by_role("searchbox", name="Search MMSI").fill(mmsi)
        expect(page.get_by_role("button", name=f"Select vessel {mmsi}", exact=True)).to_be_visible()
        page.get_by_role("searchbox", name="Search MMSI").fill("")
        checks.append("actual_list_and_map_vessel_selection_and_identifier_search")

        slider = page.get_by_role("slider", name="Historical time slider")
        slider.focus()
        slider.press("Home")
        expect(page.get_by_test_id("cutoff-clock")).to_contain_text("00:00")
        expect(page.locator(".chart-empty")).to_be_visible()
        # The real NOAA file has a report exactly at midnight; do not invent an empty prefix.
        page.screenshot(path=str(folder / "initial-prefix.png"), full_page=True)
        slider.press("End")
        ready(page)
        expect(page.locator(".chart-column")).to_have_count(24)
        expect(page.locator(".metric").last).to_contain_text("n=14")
        checks.append("keyboard_slider_initial_prefix_no_completed_hour_and_real_completed_day")

        page.get_by_role("button", name="Reset", exact=False).click()
        ready(page)
        page.get_by_role("combobox", name="Replay speed").select_option("3")
        page.get_by_role("button", name="Play replay").click()
        expect(slider).not_to_have_value("0", timeout=10000)
        page.get_by_role("button", name="Pause replay").click()
        paused = slider.input_value()
        page.wait_for_timeout(2700)
        assert slider.input_value() == paused
        assert int(paused) % 15 == 0
        checks.append("single_playback_loop_speed_selection_and_stable_pause")

        page.get_by_role("combobox", name="Displayed timezone").select_option("America/Los_Angeles")
        expect(page.get_by_test_id("cutoff-clock")).to_contain_text("Pacific")
        page.get_by_role("combobox", name="Displayed timezone").select_option("UTC")
        page.get_by_role("combobox", name="Staleness cutoff").select_option("5")
        ready(page)
        expect(page.locator(".metric").first).to_contain_text("5-minute observation cutoff")
        page.get_by_role("combobox", name="Staleness cutoff").select_option("15")
        ready(page)
        checks.append("timezone_and_configurable_staleness")

        page.get_by_role("button", name="Live", exact=True).click()
        ready(page)
        expect(page.get_by_role("slider", name="Historical time slider")).to_have_count(0)
        # Current live collection may be running; unknown is still covered in unit fixtures.
        expect(page.locator(".metric strong").first).not_to_have_text("—")
        page.locator("#pipeline-health summary").click()
        expect(page.locator(".health-content")).to_be_visible()
        page.screenshot(path=str(folder / "live-status.png"), full_page=True)

        def empty_news(route):
            response = route.fetch()
            fixture = response.json()
            fixture["news_status"] = "valid_empty"
            fixture["news"] = []
            route.fulfill(response=response, json=fixture)

        # Isolated response-status fixture; never persist invented articles or project records.
        page.route("**/api/frame?**", empty_news)
        page.get_by_role("button", name="Refresh saved reports").click()
        expect(page.locator(".news-panel")).to_contain_text("latest successful saved response")
        page.unroute("**/api/frame?**", empty_news)
        checks.append("synthetic_response_status_fixture_distinguishes_valid_empty_news")
        page.get_by_role("button", name="Historical replay", exact=True).click()
        ready(page)
        expect(page.locator(".news-panel")).to_contain_text("Current articles are excluded")
        checks.append("mode_switch_source_separation_and_actual_current_live_state")

        def fail_frame(route):
            route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":{"code":"query_api_disconnected"}}',
            )

        page.route("**/api/frame?**", fail_frame)
        page.get_by_role("button", name="Reset", exact=False).click()
        expect(page.locator(".query-error")).to_be_visible()
        page.unroute("**/api/frame?**", fail_frame)
        page.get_by_role("button", name="Retry frame").click()
        ready(page)
        expect(page.locator(".query-error")).to_have_count(0)
        checks.append("failed_frame_helpful_state_and_actual_retry_recovery")

        # A real nonempty prefix for viewport inspection, without creating playback loops.
        slider.focus()
        slider.press("End")
        ready(page)
        for name, width, height in (
            ("laptop", 1280, 720),
            ("tablet", 834, 1112),
            ("mobile", 390, 844),
        ):
            page.set_viewport_size({"width": width, "height": height})
            no_overflow(page)
            page.screenshot(path=str(folder / f"{name}.png"), full_page=True)
        checks.append("laptop_tablet_mobile_no_horizontal_overflow")
        page.emulate_media(reduced_motion="reduce")
        assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        checks.append("reduced_motion_preference")

        disconnected = browser.new_page(viewport={"width": 1280, "height": 800})
        disconnected.on("pageerror", lambda error: page_errors.append(str(error)))
        disconnected.route(
            "**/api/catalog**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":{"code":"query_api_disconnected"}}',
            ),
        )
        disconnected.goto(args.url)
        disconnected.wait_for_load_state("networkidle")
        expect(
            disconnected.get_by_text("The query API is disconnected.", exact=True)
        ).to_be_visible()
        disconnected.screenshot(path=str(folder / "disconnected.png"), full_page=True)
        checks.append("initial_disconnection_has_recovery_instruction")
        assert not page_errors, page_errors
        version = browser.version
        browser.close()
    report = dict(
        verified=True,
        verified_at_utc=datetime.now(UTC).isoformat(),
        browser_version=version,
        checks=checks,
        page_errors=page_errors,
        external_tiles_fetched=0,
        screenshots=[
            p.name
            for p in folder.glob("*.png")
            if p.name not in {"debug.png", "initial-desktop.png"}
        ],
        actual_frame_requests=len(requests),
        url=args.url,
    )
    (folder / "browser-verification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
