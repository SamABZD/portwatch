"""Actual two-port browser/API verification; no synthetic vessel data or external tiles."""

import json
from datetime import UTC, datetime
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

from portwatch.settings import PROJECT_ROOT, Settings
from scripts.verify_demo_browser import available_browser, no_overflow, ready


def main():
    folder = PROJECT_ROOT / "artifacts/multiport"
    folder.mkdir(parents=True, exist_ok=True)
    secret = Settings().ais_api_key
    errors, checks, measurements = [], [], {}

    def query(path):
        with urlopen("http://127.0.0.1:3000/api/" + path, timeout=30) as response:
            body = response.read().decode()
        assert not secret or secret.get_secret_value() not in body, "Secret in public response"
        return json.loads(body)

    for port in ("los_angeles", "oakland"):
        live = query(f"frame?mode=live&port={port}")
        historical = query(f"frame?mode=historical&port={port}&at=2024-01-01T12:30:00Z")
        assert live["dataset"]["port_id"] == historical["dataset"]["port_id"] == port
        assert live["dataset"]["source_id"] == "aisstream_v0"
        assert historical["dataset"]["source_id"] != "aisstream_v0"
        assert live["metrics"]["fresh_vessels"] and live["metrics"]["fresh_vessels"] > 0
        assert all(v["last_observation_utc"] <= live["cutoff_utc"] for v in live["vessels"])
        measurements[port] = dict(
            live_positions=live["prefix_observations"],
            fresh_vessels=live["metrics"]["fresh_vessels"],
            noon_positions=historical["prefix_observations"],
        )
    pipeline = query("pipeline")
    assert {p["port_id"] for p in pipeline["ports"]} == {"los_angeles", "oakland"}
    assert all(p["collector"]["status"] == "healthy_connected" for p in pipeline["ports"])
    checks.append("actual_live_collection_both_ports_source_isolation_and_secret_projection")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=available_browser(playwright, None)
        )
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto("http://127.0.0.1:3000")
        page.wait_for_load_state("networkidle")
        ready(page)
        expect(page.get_by_role("slider", name="Historical time slider")).to_have_count(0)
        expect(page.locator(".metric strong").first).not_to_have_text("Unknown")
        no_overflow(page)
        page.screenshot(path=str(folder / "live-los-angeles.png"), full_page=True)
        page.get_by_role("combobox", name="Select port").select_option("oakland")
        page.wait_for_load_state("networkidle")
        ready(page)
        expect(page.get_by_role("combobox", name="Select port")).to_have_value("oakland")
        expect(page.locator(".metric strong").first).not_to_have_text("Unknown")
        page.screenshot(path=str(folder / "live-oakland.png"), full_page=True)
        checks.append("live_default_and_port_switch_without_historical_mixing")
        page.get_by_role("button", name="Historical replay", exact=True).click()
        ready(page)
        page.get_by_role("button", name="Select vessel", exact=False).first.click()
        expect(page.locator(".vessel-title strong")).to_be_visible()
        page.get_by_text("Reported vessel & voyage details", exact=True).click()
        expect(page.get_by_text("Reported destination", exact=True)).to_be_visible()
        expect(page.locator(".reported-profile")).to_contain_text("Not received")
        page.screenshot(path=str(folder / "oakland-vessel.png"), full_page=True)
        checks.append("reported_vessel_details_and_explicit_missing_voyage_data")
        search = page.get_by_role("searchbox", name="Search MMSI")
        search.fill("__no_such_vessel__")
        expect(page.get_by_role("button", name="Select vessel", exact=False)).to_have_count(0)
        search.fill("")
        page.get_by_role("combobox", name="Select port").select_option("los_angeles")
        page.wait_for_load_state("networkidle")
        ready(page)
        expect(page.locator(".metric strong").first).to_have_text("99")
        checks.append("los_angeles_original_noon_metrics_preserved")
        page.get_by_role("link", name="Data pipeline", exact=True).click()
        expect(page.get_by_role("heading", name="Port of Oakland", exact=True)).to_be_visible(
            timeout=20000
        )
        expect(page.get_by_role("heading", name="Port of Los Angeles", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="How the data travels")).to_be_visible()
        expect(page.locator(".pipeline-table")).to_have_count(4)
        no_overflow(page)
        page.screenshot(path=str(folder / "pipeline-desktop.png"), full_page=True)
        for name, width, height in (
            ("laptop", 1280, 720),
            ("tablet", 834, 1112),
            ("mobile", 390, 844),
        ):
            page.set_viewport_size({"width": width, "height": height})
            no_overflow(page)
            if name == "mobile":
                page.screenshot(path=str(folder / "pipeline-mobile.png"), full_page=True)
        checks.append("pipeline_lineage_real_measurements_run_history_and_four_viewports")
        page.route(
            "**/api/pipeline",
            lambda route: route.fulfill(status=503, json={"detail": {"code": "disconnected"}}),
        )
        page.reload()
        expect(page.locator(".query-error[role=alert]")).to_contain_text("disconnected")
        page.unroute("**/api/pipeline")
        page.get_by_role("button", name="Retry pipeline").click()
        expect(page.get_by_role("heading", name="Port of Oakland", exact=True)).to_be_visible(
            timeout=20000
        )
        expect(page.locator(".query-error[role=alert]")).to_have_count(0)
        checks.append("pipeline_disconnection_and_retry_recovery")
        page.get_by_role("link", name="Return to the vessel map").click()
        page.wait_for_load_state("networkidle")
        ready(page)
        no_overflow(page)
        page.screenshot(path=str(folder / "map-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    report = dict(
        verified=True,
        verified_at_utc=datetime.now(UTC).isoformat(),
        checks=checks,
        measurements=measurements,
        page_errors=errors,
        external_tiles_fetched=0,
    )
    (folder / "browser-verification.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
