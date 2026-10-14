"""Verify saved news UI with recorded API fixtures and isolated test-only controls.

The existing local frontend must already be running. Every API response is served
from a fixture; other origins and unknown API endpoints are blocked. This script
never starts a server, queries a database, or fetches a news provider.
"""

import argparse
import asyncio
import copy
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect

from scripts.verify_demo_browser import available_browser

PORTS = ("los_angeles", "oakland")
STATUS_TEXT = {
    "available": "Saved port news is available.",
    "valid_empty": "No matching articles in the saved window.",
    "fallback": "Official port releases shown as fallback coverage.",
    "stale": "Last good response shown; refresh failed or is overdue.",
    "deferred": "Scheduled news fetch deferred; saved dates remain unchanged.",
    "unavailable": "No usable saved news response is connected.",
}
CASES = (
    "available",
    "valid_empty",
    "fallback_429",
    "stale",
    "deferred_saved",
    "deferred_no_saved",
    "unavailable",
    "primary_parser_200",
    "frame_independent_of_health",
)
NEXT_FETCH = "2026-10-14T01:00:00+00:00"


def read_fixture(folder, name):
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))


def test_articles(port):
    """Synthetic date/control rows, labeled test-only in every visible headline."""
    rows = []
    for kind in ("timestamp", "date only", "first seen", "unknown dates"):
        rows.append(
            {
                "title": f"TEST ONLY - {port} - {kind}",
                "url": f"https://example.test/news/{port}/{kind.replace(' ', '-')}",
                "domain": "example.test",
                "publication_time_utc": None,
                "first_seen_time_utc": None,
                "publication_date": None,
                "publication_timezone": None,
                "publication_precision": "unknown",
                "relevance_tags": [],
                "quality_flags": ["test_only_synthetic_control"],
            }
        )
    rows[0].update(
        publication_time_utc="2026-10-13T19:00:00+00:00",
        publication_precision="instant",
        relevance_tags=["storm"],
    )
    rows[1].update(
        publication_date="2026-10-09",
        publication_precision="date",
        publication_timezone="America/Los_Angeles",
    )
    rows[2]["first_seen_time_utc"] = "2026-10-13T19:15:00+00:00"
    return rows


def test_news(port, case):
    """Change only news presentation controls, never AIS analytics or receipts."""
    rows = test_articles(port)
    context = {
        "status": "available",
        "data_status": "available",
        "freshness": "fresh",
        "provider": f"TEST ONLY - frame source - {port}",
        "source_id": "test_only_frame_source",
        "source_url": f"https://example.test/sources/{port}",
        "using_fallback": False,
        "coverage": "TEST ONLY - synthetic news states; not deployed observations",
        "valid_start_utc": "2026-10-06T20:00:00+00:00",
        "valid_end_utc": "2026-10-13T20:00:00+00:00",
        "window_hours": 168,
        "configured_window_hours": 168,
        "last_successful_fetch_utc": "2026-10-13T20:00:00+00:00",
        "success_age_seconds": 600,
        "saved_batch_id": "test_only_saved_news",
        "saved_row_count": len(rows),
        "quality": {},
        "primary_attempt": None,
        "schedule": None,
    }
    if case == "valid_empty":
        rows = []
        context.update(status="valid_empty", data_status="valid_empty", saved_row_count=0)
    if case in {"fallback_429", "primary_parser_200"}:
        context.update(status="fallback", using_fallback=True)
        context["primary_attempt"] = {
            "status": "failed",
            "http_status": 429 if case == "fallback_429" else 200,
            "error_code": (
                "source_rate_limited" if case == "fallback_429" else "invalid_json_response"
            ),
            "next_allowed_at": NEXT_FETCH if case == "fallback_429" else None,
        }
        context["quality"] = {"unknown_date_count": 2}
    if case == "stale":
        context.update(status="stale", freshness="stale", success_age_seconds=10_800)
    if case in {"deferred_saved", "deferred_no_saved"}:
        context.update(
            status="deferred",
            schedule={
                "status": "deferred",
                "next_allowed_utc": NEXT_FETCH,
            },
        )
    if case in {"deferred_no_saved", "unavailable"}:
        rows = []
        context.update(
            data_status="unavailable",
            freshness="unavailable",
            saved_batch_id=None,
            saved_row_count=None,
            last_successful_fetch_utc=None,
            success_age_seconds=None,
            valid_start_utc=None,
            valid_end_utc=None,
            window_hours=None,
        )
        if case == "unavailable":
            context["status"] = "unavailable"
    if case == "frame_independent_of_health":
        context["saved_row_count"] = 12
    return context, rows


def analytical_payload(frame):
    """News substitutions must leave every other recorded frame value intact."""
    return {
        key: value
        for key, value in frame.items()
        if key not in {"news", "news_context", "news_status"}
    }


class RecordedResponses:
    def __init__(self, folder):
        self.folder = folder
        self.case = "recorded"
        self.prepared = False
        self.frames = {port: read_fixture(folder, f"live-{port}") for port in PORTS}
        self.history = {port: read_fixture(folder, f"historical-{port}") for port in PORTS}
        self.health = {port: read_fixture(folder, f"health-{port}") for port in PORTS}
        self.catalog = {port: read_fixture(folder, f"catalog-{port}") for port in PORTS}

    def response(self, endpoint, port, query):
        if port not in PORTS:
            raise ValueError(f"Unexpected port: {port}")
        if endpoint == "catalog":
            body = copy.deepcopy(self.catalog[port])
            # Match recorded dataset revisions without altering analytical values.
            for index, dataset in enumerate(body["datasets"]):
                frame = self.frames[port] if dataset["mode"] == "live" else self.history[port]
                body["datasets"][index] = {
                    **copy.deepcopy(dataset),
                    **copy.deepcopy(frame["dataset"]),
                }
            if self.prepared:
                body["prepared_mode"] = True  # TEST ONLY: live-unavailable presentation control.
            return body
        if endpoint == "health":
            body = copy.deepcopy(self.health[port])
            if self.case == "frame_independent_of_health":
                body.setdefault("context", {})["recent_news"] = {
                    "status": "unavailable",
                    "last_http_status": 503,
                    "provider": "TEST ONLY - health source must not label frame rows",
                    "source_id": "test_only_health_source",
                    "saved_row_count": 99,
                }
            return body
        if endpoint == "frame":
            mode = query.get("mode", ["live"])[0]
            saved = self.frames[port] if mode == "live" else self.history[port]
            body = copy.deepcopy(saved)
            if mode == "historical":
                expected = datetime.fromisoformat(saved["cutoff_utc"].replace("Z", "+00:00"))
                requested = datetime.fromisoformat(query["at"][0].replace("Z", "+00:00"))
                if requested != expected:
                    raise ValueError("Only the recorded historical cutoff can be verified")
                # TEST ONLY: deliberately contaminated news tests historical exclusion.
                body["news_context"], body["news"] = test_news(port, "available")
                body["news_status"] = "available"
            elif self.case != "recorded":
                body["news_context"], body["news"] = test_news(port, self.case)
                body["news_status"] = body["news_context"]["status"]
            assert analytical_payload(body) == analytical_payload(saved), "AIS fixture altered"
            return body
        if endpoint in {"pipeline", "history"}:
            return read_fixture(self.folder, endpoint)
        raise ValueError(f"Unexpected API endpoint: {endpoint}")


async def no_overflow(page):
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        "Horizontal page overflow"
    )


async def ready(page):
    await expect(page.locator(".updating")).to_have_count(0, timeout=20_000)
    await expect(page.locator(".news-panel")).to_be_visible(timeout=20_000)
    await expect(page.locator(".metric-strip")).to_have_attribute(
        "aria-busy", "false", timeout=20_000
    )
    await page.wait_for_load_state("networkidle")


async def verify_news_rows(panel, rows, context):
    await expect(panel.locator("li")).to_have_count(len(rows))
    for index, row in enumerate(rows):
        link = panel.locator("li").nth(index).get_by_role("link")
        await expect(link).to_have_text(row["title"])
        await expect(link).to_have_attribute("href", row["url"])
        await expect(link).to_have_attribute("rel", "noreferrer")
        await expect(panel.locator("li").nth(index).locator("small")).to_contain_text(row["domain"])
    if context and context.get("source_url"):
        source = panel.get_by_role("link", name=context["provider"], exact=True)
        await expect(source).to_have_attribute("href", context["source_url"])


async def verify(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    responses = RecordedResponses(Path(args.fixtures))
    parsed_base = urlparse(args.url)
    origin = (parsed_base.scheme, parsed_base.netloc)
    requests, blocked, route_errors, page_errors, screenshots, checks = [], [], [], [], [], []
    report = {
        "verified": False,
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "url": args.url,
        "fixture_scope": "Recorded AIS and official saved articles; TEST ONLY news edge controls",
        "checks": checks,
        "screenshots": screenshots,
        "page_errors": page_errors,
        "route_errors": route_errors,
        "provider_requests_sent": 0,
    }

    async def serve(route):
        url = urlparse(route.request.url)
        if (url.scheme, url.netloc) != origin:
            blocked.append(route.request.url)
            await route.abort()
            return
        if not url.path.startswith("/api/"):
            await route.continue_()
            return
        endpoint = url.path.rsplit("/", 1)[-1]
        query = parse_qs(url.query)
        port = query.get("port", ["los_angeles"])[0]
        requests.append((endpoint, port, responses.case))
        try:
            body = responses.response(endpoint, port, query)
            await route.fulfill(json=body)
        except Exception as error:
            route_errors.append(f"{endpoint}/{port}: {error}")
            await route.fulfill(status=500, json={"detail": {"code": "test_fixture_error"}})

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=available_browser(playwright, args.browser_executable)
        )
        context = await browser.new_context(
            viewport={"width": 1365, "height": 900},
            service_workers="block",
        )
        await context.route("**/*", serve)
        page = await context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        try:
            await page.goto(args.url)
            await ready(page)
            panel = page.locator(".news-panel")
            for port in PORTS:
                responses.case = "recorded"
                selector = page.get_by_role("combobox", name="Select port")
                if await selector.input_value() != port:
                    await selector.select_option(port)
                live_mode = page.get_by_role("button", name="Live", exact=True)
                if await live_mode.get_attribute("aria-pressed") != "true":
                    await live_mode.click()
                await page.get_by_role("button", name="Refresh saved reports", exact=True).click()
                await ready(page)
                recorded = responses.frames[port]
                await verify_news_rows(panel, recorded["news"], recorded.get("news_context"))
                recorded_status = (recorded.get("news_context") or {}).get(
                    "status", recorded["news_status"]
                )
                await expect(panel.locator("strong").first).to_have_text(
                    STATUS_TEXT[recorded_status]
                )
                if port == "los_angeles":
                    recorded_row = recorded["news"][0]
                    assert recorded_row["publication_time_utc"] is None
                    assert recorded_row["first_seen_time_utc"] is None
                    assert recorded_row["publication_date"] == "2026-10-09"
                    recorded_date = panel.locator("li").first.locator("small")
                    await expect(recorded_date).to_contain_text("2026-10-09 · date only")
                    await expect(recorded_date).to_contain_text("time unknown")
                    assert not re.search(r"\d{2}:\d{2}", await recorded_date.inner_text())
                else:
                    assert recorded["news"][0]["publication_time_utc"] is not None
                    await expect(panel.locator("li").first.locator("small")).not_to_contain_text(
                        "time unknown"
                    )
                checks.append(
                    {
                        "port": port,
                        "case": "recorded_official_news",
                        "rows": len(recorded["news"]),
                        "passed": True,
                    }
                )

                if port == "los_angeles":
                    for name, width, height in (
                        ("news-desktop.png", 1365, 900),
                        ("news-mobile.png", 390, 844),
                    ):
                        await page.set_viewport_size({"width": width, "height": height})
                        await no_overflow(page)
                        await panel.scroll_into_view_if_needed()
                        await page.screenshot(path=str(output / name), full_page=True)
                        screenshots.append(name)
                    await page.set_viewport_size({"width": 1365, "height": 900})
                else:
                    await page.set_viewport_size({"width": 1365, "height": 900})
                    await no_overflow(page)
                    await panel.scroll_into_view_if_needed()
                    await page.screenshot(
                        path=str(output / "news-oakland-desktop.png"), full_page=True
                    )
                    screenshots.append("news-oakland-desktop.png")

                for case in CASES:
                    responses.case = case
                    if case == "frame_independent_of_health":
                        # Reload ensures both contradictory health and frame responses arrive.
                        await page.reload()
                        await ready(page)
                        selector = page.get_by_role("combobox", name="Select port")
                        if await selector.input_value() != port:
                            await selector.select_option(port)
                    await page.get_by_role(
                        "button", name="Refresh saved reports", exact=True
                    ).click()
                    await ready(page)
                    news_context, rows = test_news(port, case)
                    await expect(panel.locator("strong").first).to_have_text(
                        STATUS_TEXT[news_context["status"]]
                    )
                    await verify_news_rows(panel, rows, news_context)
                    await expect(panel).to_contain_text("context, not evidence")
                    if case == "valid_empty":
                        await expect(panel).to_contain_text("does not establish absence")
                    if case == "fallback_429":
                        await expect(panel).to_contain_text("rate limited (HTTP 429)")
                        await expect(panel).to_contain_text("No primary retry before")
                        await expect(panel).to_contain_text("2 undated source items excluded")
                    if case == "primary_parser_200":
                        await expect(panel).to_contain_text(
                            "unavailable (HTTP 200; invalid json response)"
                        )
                        await expect(panel).not_to_contain_text("rate limited")
                    if case == "stale":
                        await expect(panel).to_contain_text("stale saved response")
                        await expect(panel).to_contain_text("3 hours ago")
                    if case.startswith("deferred_"):
                        await expect(panel).to_contain_text("Next eligible scheduled fetch")
                    if case in {"deferred_no_saved", "unavailable"}:
                        await expect(panel).to_contain_text("Last successful fetch: Unknown")
                        await expect(panel).not_to_contain_text("successful response contained no")
                    if case == "frame_independent_of_health":
                        await expect(panel).to_contain_text(
                            "Showing 4 of 12 saved matching articles"
                        )
                        await expect(panel).not_to_contain_text("health source")
                        await expect(panel).not_to_contain_text("99")
                    await no_overflow(page)
                    checks.append({"port": port, "case": case, "rows": len(rows), "passed": True})

                responses.case = "available"
                await page.get_by_role("button", name="Refresh saved reports", exact=True).click()
                await ready(page)
                await page.get_by_role("combobox", name="Displayed timezone").select_option(
                    "America/Los_Angeles"
                )
                date_only = panel.locator("li").nth(1).locator("small")
                await expect(date_only).to_have_text(
                    "example.test · 2026-10-09 · date only (America/Los_Angeles); time unknown"
                )
                assert not re.search(r"\d{2}:\d{2}", await date_only.inner_text()), "Invented time"
                await expect(panel.locator("li").nth(0)).to_contain_text("12:00 Pacific")
                await expect(panel.locator("li").nth(2)).to_contain_text("First seen Oct 13, 12:15")
                await expect(panel.locator("li").nth(2)).to_contain_text("publication time unknown")
                await expect(panel.locator("li").nth(3)).to_contain_text(
                    "Publication and first-seen times unknown"
                )
                checks.append({"port": port, "case": "pacific_date_precision", "passed": True})

                article = panel.locator("li").first.get_by_role("link")
                await article.focus()
                await expect(article).to_be_focused()
                async with context.expect_page() as popup_event:
                    await article.press("Enter")
                popup = await popup_event.value
                await popup.wait_for_load_state("domcontentloaded")
                await popup.close()
                assert any(f"example.test/news/{port}/timestamp" in url for url in blocked), (
                    "Keyboard article link did not reach the external-request blocker"
                )
                checks.append(
                    {"port": port, "case": "keyboard_article_link_blocked", "passed": True}
                )

                for width, height in ((1365, 900), (390, 844)):
                    await page.set_viewport_size({"width": width, "height": height})
                    await no_overflow(page)
                    await expect(
                        panel.get_by_role("heading", name="Port news & context")
                    ).to_be_visible()
                checks.append({"port": port, "case": "desktop_mobile_no_overflow", "passed": True})

                await page.get_by_role("button", name="Historical replay", exact=True).click()
                await ready(page)
                await expect(panel).to_contain_text("Current articles are excluded.")
                await expect(panel.locator("li")).to_have_count(0)
                await expect(panel).not_to_contain_text("TEST ONLY")
                live_button = panel.get_by_role(
                    "button", name="View current port context", exact=True
                )
                await expect(live_button).to_be_visible()
                await live_button.focus()
                await live_button.press("Enter")
                await ready(page)
                await expect(page.get_by_role("button", name="Live", exact=True)).to_have_attribute(
                    "aria-pressed", "true"
                )
                await expect(panel.locator("li")).to_have_count(4)
                checks.append(
                    {"port": port, "case": "historical_exclusion_current_context", "passed": True}
                )

            responses.prepared = True
            await page.reload()
            await ready(page)
            await expect(page.get_by_role("button", name="Live", exact=True)).to_be_disabled()
            await expect(panel).to_contain_text("Current articles are excluded.")
            await expect(
                panel.get_by_role("button", name="View current port context")
            ).to_have_count(0)
            await expect(panel.locator("li")).to_have_count(0)
            await no_overflow(page)
            await panel.scroll_into_view_if_needed()
            await page.screenshot(path=str(output / "news-historical.png"), full_page=True)
            screenshots.append("news-historical.png")
            checks.append({"case": "prepared_historical_no_current_context_button", "passed": True})
            assert not route_errors, route_errors
            assert not page_errors, page_errors
            report["verified"] = True
        except Exception as error:
            report["failure"] = f"{type(error).__name__}: {error}"
            await page.screenshot(path=str(output / "news-failure.png"), full_page=True)
        finally:
            report.update(
                browser_version=browser.version,
                api_requests=dict(Counter(item[0] for item in requests)),
                external_requests_blocked=len(blocked),
            )
            await browser.close()
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["verified"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3160")
    parser.add_argument(
        "--fixtures", required=True, help="Recorded/composed API fixture directory."
    )
    parser.add_argument("--output", default="artifacts/phase2-news")
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    if urlparse(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Local verification only")
    raise SystemExit(asyncio.run(verify(args)))


if __name__ == "__main__":
    main()
