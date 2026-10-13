"""Verify a real prepared snapshot opens directly in historical mode without a DB/AIS key."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from verify_demo_browser import available_browser, no_overflow, ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3002")
    parser.add_argument("--port", choices=["los_angeles", "oakland"], default="los_angeles")
    args = parser.parse_args()
    folder = Path("artifacts/step13/prepared-browser")
    folder.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=available_browser(playwright, None)
        )
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        ready(page)
        expect(page.get_by_role("slider", name="Historical time slider")).to_be_visible()
        expect(page.get_by_role("button", name="Live", exact=True)).to_be_disabled()
        expect(page.get_by_role("combobox", name="Select port")).to_have_value(args.port)
        expect(page.locator(".metric strong").first).to_have_text(
            "99" if args.port == "los_angeles" else "75"
        )
        response = page.request.get(args.url + "/api/health?port=" + args.port)
        assert response.status == 200 and response.json()["database"]["status"] == "not_used"
        response.dispose()
        no_overflow(page)
        page.screenshot(path=str(folder / f"{args.port}-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        no_overflow(page)
        page.screenshot(path=str(folder / f"{args.port}-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    report = dict(
        verified=True,
        verified_at_utc=datetime.now(UTC).isoformat(),
        port=args.port,
        checks=[
            "automatic_historical_mode_and_snapshot_port",
            "real_noon_metrics_without_database",
            "desktop_mobile_and_no_page_errors",
        ],
        page_errors=errors,
    )
    (folder / f"{args.port}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
