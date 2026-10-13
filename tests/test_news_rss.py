"""Official RSS metadata preserves times and rejects unsafe XML; no provider calls."""

from datetime import timedelta
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
