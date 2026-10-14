"""Official RSS metadata preserves times and rejects unsafe XML; no provider calls."""

from dataclasses import replace
from datetime import timedelta, timezone
from email.utils import format_datetime
from html import escape

import pytest

from portwatch.context import ContextError, news_fallback, normalize_news_rss, plan_context
from portwatch.settings import Settings
from tests.test_context import NOW


def rss_payload(at=NOW - timedelta(hours=1), *, duplicate=False, date=True):
    item = (
        "<item><title>Port of Oakland storm advisory</title>"
        "<link>https://www.portofoakland.com/news/synthetic-advisory</link>"
        + (f"<pubDate>{escape(format_datetime(at))}</pubDate>" if date else "")
        + "<description>Unrelated body content is not normalized</description></item>"
    )
    return (
        '<rss version="2.0"><channel>' + item * (2 if duplicate else 1) + "</channel></rss>"
    ).encode()


def fallback_request():
    return news_fallback(Settings(), plan_context(Settings(), "oakland", "news", now=NOW))


def rss_entries(entries):
    items = []
    for url, at in entries:
        stamp = f"<pubDate>{escape(format_datetime(at))}</pubDate>" if at is not None else ""
        items.append(
            "<item><title>Synthetic Port of Oakland update</title>"
            f"<link>{escape(url)}</link>{stamp}</item>"
        )
    return ("<rss><channel>" + "".join(items) + "</channel></rss>").encode()


def test_official_rss_metadata_deduplicates_and_preserves_publication_time():
    result = normalize_news_rss(rss_payload(duplicate=True), fallback_request())
    assert len(result.rows) == 1 and result.quality["duplicate_urls"] == 1
    assert result.rows[0]["publication_time_utc"] == NOW - timedelta(hours=1)
    assert result.rows[0]["first_seen_time_utc"] is None
    assert "body content" not in str(result.rows)
    assert not result.quality["exhaustive"]
    assert result.metadata["provider"] == "Port of Oakland"


def test_only_dated_feed_items_in_the_requested_window_are_selected():
    result = normalize_news_rss(rss_payload(NOW - timedelta(days=10)), fallback_request())
    assert result.rows == [] and result.quality["outside_window"] == 1
    with pytest.raises(ContextError, match="publication_dates_unavailable"):
        normalize_news_rss(rss_payload(date=False), fallback_request())


@pytest.mark.parametrize(
    "payload",
    [
        b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss><channel/></rss>',
        b"<html>not RSS</html>",
        b"<rss>",
    ],
)
def test_unsafe_or_malformed_feed_is_not_empty_success(payload):
    with pytest.raises(ContextError):
        normalize_news_rss(payload, fallback_request())


def test_fallback_is_only_the_explicit_reviewed_official_source():
    request = plan_context(Settings(), "los_angeles", "news", now=NOW)
    assert news_fallback(Settings(), request).source_id == "port_los_angeles_releases"
    official = fallback_request()
    assert official.source_id == "port_oakland_rss"
    assert official.endpoint == "https://www.portofoakland.com/feed/"


def test_rss_offset_timestamps_obey_inclusive_start_and_exclusive_end():
    req = fallback_request()
    offset = timezone(timedelta(hours=5, minutes=30))
    times = [
        req.start_utc - timedelta(seconds=1),
        req.start_utc,
        req.end_utc - timedelta(seconds=1),
        req.end_utc,
    ]
    payload = rss_entries(
        [
            (f"https://www.portofoakland.com/news/{index}", at.astimezone(offset))
            for index, at in enumerate(times)
        ]
    )
    result = normalize_news_rss(payload, req)
    assert [row["publication_time_utc"] for row in result.rows] == times[1:3]
    assert all(row["first_seen_time_utc"] is None for row in result.rows)
    assert result.quality["parsed_item_count"] == 4
    assert result.quality["eligible_item_count"] == 2
    assert result.quality["excluded_by_date"] == 2
    assert result.quality["unknown_date_count"] == 0
    assert result.quality["stored_item_count"] == 2
    assert result.quality["response_bytes"] == len(payload)
    assert result.metadata["format"] == "RSS 2.0"


def test_rss_storage_cap_applies_after_full_canonical_deduplication():
    req = replace(fallback_request(), parameters={"maxrecords": 2})
    at = req.end_utc - timedelta(hours=1)
    urls = [
        "https://www.portofoakland.com/news/first?utm_source=one",
        "https://www.portofoakland.com/news/first?utm_source=two",
        "https://www.portofoakland.com/news/second",
        "https://www.portofoakland.com/news/third",
    ]
    result = normalize_news_rss(rss_entries([(url, at) for url in urls]), req)
    assert [row["canonical_url"] for row in result.rows] == [
        "https://www.portofoakland.com/news/first",
        urls[2],
    ]
    assert result.quality["parsed_item_count"] == 4
    assert result.quality["eligible_item_count"] == 4
    assert result.quality["duplicate_urls"] == 1
    assert result.quality["deduplicated_count"] == 3
    assert result.quality["stored_item_count"] == 2
    assert result.quality["truncated"]


@pytest.mark.parametrize("include_current", [False, True])
def test_rss_mixed_unknown_and_outside_dates_disclose_incomplete_coverage(include_current):
    req = fallback_request()
    entries = [
        ("https://www.portofoakland.com/news/unknown", None),
        ("https://www.portofoakland.com/news/old", req.start_utc - timedelta(seconds=1)),
    ]
    if include_current:
        entries.append(("https://www.portofoakland.com/news/current", req.start_utc))
    result = normalize_news_rss(rss_entries(entries), req)
    assert len(result.rows) == int(include_current)
    assert result.quality["parsed_item_count"] == len(entries)
    assert result.quality["eligible_item_count"] == int(include_current)
    assert result.quality["excluded_by_date"] == 1
    assert result.quality["unknown_date_count"] == 1
    assert result.quality["unknown_publication_items"] == 1
    assert not result.quality["date_coverage_complete"]
