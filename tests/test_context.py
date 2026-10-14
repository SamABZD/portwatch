"""Synthetic context semantics; no provider requests or copied article content."""

import json
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from portwatch.cli import app
from portwatch.context import (
    ContextError,
    canonical_url,
    normalize_news,
    normalize_weather,
    plan_context,
)
from portwatch.context_http import fetch_context, retry_after
from portwatch.settings import Settings

NOW = datetime(2024, 1, 2, 12, tzinfo=UTC)


def request(kind="weather", demo=True):
    return plan_context(Settings(), "los_angeles", kind, demo=demo, now=NOW)


def weather_payload(req=None, *, speed=3.2, missing=False):
    req = req or request()
    stamps = list(range(int(req.start_utc.timestamp()), int(req.end_utc.timestamp()), 3600))
    body = dict(
        latitude=33.75,
        longitude=-118.25,
        utc_offset_seconds=0,
        timezone="GMT",
        generationtime_ms=0.4,
        hourly_units=dict(time="unixtime", wind_speed_10m="m/s", precipitation="mm"),
        hourly=dict(
            time=stamps, wind_speed_10m=[speed] * len(stamps), precipitation=[0.0] * len(stamps)
        ),
    )
    if missing:
        body["hourly"].pop("wind_speed_10m")
        body["hourly_units"].pop("wind_speed_10m")
    return json.dumps(body).encode()


def news_payload(*, empty=False, duplicates=False, bad=False):
    article = dict(
        url="https://example.invalid/article?id=42&utm_source=synthetic",
        title="Synthetic harbor closure mention",
        domain="example.invalid",
        language="English",
        seendate="20240102T110000Z",
    )
    if bad:
        article["url"] = "not-a-url"
    articles = [] if empty else [article]
    if duplicates:
        articles.append(dict(article, url="https://example.invalid/article?id=42&utm_medium=test"))
    return json.dumps(dict(articles=articles)).encode()


def test_demo_weather_matches_frozen_ais_and_excludes_unsupported_gusts():
    req = request()
    assert req.start_utc == datetime(2024, 1, 1, tzinfo=UTC)
    assert req.end_utc == datetime(2024, 1, 2, tzinfo=UTC)
    assert req.parameters["models"] == "era5"
    assert "wind_gusts_10m" not in req.parameters["hourly"]
    assert req.parameters["timeformat"] == "unixtime"
    result = normalize_weather(weather_payload(), req)
    assert len(result.rows) == 24
    assert result.rows[0]["precipitation"] == 0
    assert result.rows[0]["wind_gusts_10m"] is None
    assert result.metadata["issue_time_utc"] is None
    assert "generationtime_ms" in result.metadata


def test_current_weather_and_recent_news_have_distinct_partitions():
    current = request("weather", False)
    news = request("news", False)
    assert current.product == "current_forecast"
    assert current.end_utc - current.start_utc == timedelta(days=2)
    assert "wind_gusts_10m" in current.parameters["hourly"]
    assert current.request_key != request().request_key
    assert news.parameters["startdatetime"] == "20231226120000"
    assert news.end_utc - news.start_utc == timedelta(days=7)
    assert news.parser_version == "news-v2"
    assert current.parser_version == "context-v1"
    assert news.parameters["enddatetime"] == "20240102120000"
    assert news.product == "recent_news"
    with pytest.raises(ContextError, match="recent_news_excluded"):
        request("news", True)


def test_missing_weather_variable_remains_unknown_and_units_are_validated():
    result = normalize_weather(weather_payload(missing=True), request())
    assert all(row["wind_speed_10m"] is None for row in result.rows)
    assert "wind_speed_10m_missing" in result.quality["missing_variables"]
    body = json.loads(weather_payload())
    body["hourly_units"]["wind_speed_10m"] = "km/h"
    with pytest.raises(ContextError, match="shape_or_units"):
        normalize_weather(json.dumps(body).encode(), request())


@pytest.mark.parametrize("change", ["gap", "timezone", "negative", "bool", "overflow"])
def test_invalid_weather_coverage_and_values_fail(change):
    body = json.loads(weather_payload())
    if change == "gap":
        body["hourly"]["time"].pop()
    elif change == "timezone":
        body["utc_offset_seconds"] = 3600
    else:
        body["hourly"]["wind_speed_10m"][0] = {"negative": -1, "bool": True, "overflow": 10**400}[
            change
        ]
    with pytest.raises(ContextError):
        normalize_weather(json.dumps(body).encode(), request())


def test_weather_period_mismatch_refused(tmp_path):
    config = tmp_path / "demo.yaml"
    config.write_text(
        'port_id: los_angeles\nstart_utc: "2024-01-01T00:00:00Z"\n'
        'end_utc: "2024-01-02T00:00:00Z"\nweather_period:\n'
        '  start_utc: "2024-01-02T00:00:00Z"\n'
        '  end_utc: "2024-01-03T00:00:00Z"\n'
    )
    with pytest.raises(ContextError, match="must_match_ais"):
        plan_context(Settings(demo_config=config), "los_angeles", "weather", demo=True)


def test_news_missing_publication_time_and_conservative_deduplication():
    result = normalize_news(news_payload(duplicates=True), request("news", False))
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["publication_time_utc"] is None
    assert row["first_seen_time_utc"] == datetime(2024, 1, 2, 11, tzinfo=UTC)
    assert row["canonical_url"] == "https://example.invalid/article?id=42"
    assert "utm_source" in row["original_url"]
    assert row["relevance_tags"] == ["closure"]
    assert result.quality["duplicate_urls"] == 1
    assert not result.quality["exhaustive"]


def test_url_ids_encoding_order_and_fragments_remain_distinct():
    first = "HTTPS://EXAMPLE.INVALID/Article?a=%2F&article_id=1&ref=42&utm_source=x#section"
    assert (
        canonical_url(first) == "https://example.invalid/Article?a=%2F&article_id=1&ref=42#section"
    )
    assert canonical_url(first) != canonical_url(first.replace("article_id=1", "article_id=2"))
    assert canonical_url(first) != canonical_url(first.replace("#section", "#other"))


@pytest.mark.parametrize("payload", [b"{}", b'{"articles":[]}'])
def test_valid_empty_news(payload):
    result = normalize_news(payload, request("news", False))
    assert result.rows == []
    assert result.quality["provider_article_count"] == 0
    assert not result.quality["truncated"]


@pytest.mark.parametrize("payload", [b"", b"[]", b"null", b'{"articles":null}', b"upstream error"])
def test_missing_or_bad_news_is_not_empty_success(payload):
    with pytest.raises(ContextError):
        normalize_news(payload, request("news", False))


def test_news_result_cap_is_explicitly_truncated():
    req = request("news", False)
    req = replace(req, parameters={**req.parameters, "maxrecords": 1})
    result = normalize_news(news_payload(), req)
    assert result.quality["truncated"]
    assert not result.quality["exhaustive"]


def test_separate_optional_publication_time_and_keyword_config():
    body = json.loads(news_payload())
    body["articles"][0]["published_at"] = "2024-01-01T22:30:00Z"
    req = replace(request("news", False), keywords=("harbor", "earthquake"))
    result = normalize_news(json.dumps(body).encode(), req)
    assert result.rows[0]["publication_time_utc"] == datetime(2024, 1, 1, 22, 30, tzinfo=UTC)
    assert result.rows[0]["relevance_tags"] == ["harbor"]


@pytest.fixture
def http_server():
    state = dict(
        statuses=[200], count=0, delay=0, payload=b'{"articles":[]}', retry=None, length_offset=0
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["count"] += 1
            time.sleep(state["delay"])
            self.send_response(
                state["statuses"][min(state["count"] - 1, len(state["statuses"]) - 1)]
            )
            if state["retry"]:
                self.send_header("Retry-After", state["retry"])
            self.send_header("Content-Length", str(len(state["payload"]) + state["length_offset"]))
            self.end_headers()
            try:
                self.wfile.write(state["payload"])
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/context", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_http_success_and_transient_retry(http_server):
    url, state = http_server
    state["statuses"] = [503, 200]
    result = fetch_context(url, timeout=1, deadline=3)
    assert result.payload == state["payload"]
    assert result.status == 200 and state["count"] == 2


def test_429_never_immediately_retried_and_preserves_cooldown(http_server):
    url, state = http_server
    state.update(statuses=[429], retry="1800")
    with pytest.raises(ContextError, match="source_rate_limited") as caught:
        fetch_context(url)
    assert state["count"] == 1
    assert caught.value.http_status == 429
    assert caught.value.next_allowed_at > datetime.now(UTC) + timedelta(minutes=29)


def test_timeout_and_oversize_are_bounded_failures(http_server):
    url, state = http_server
    state["delay"] = 0.15
    with pytest.raises(ContextError, match="source_timeout"):
        fetch_context(url, attempts=1, timeout=0.03, deadline=0.2)
    state["delay"] = 0
    with pytest.raises(ContextError, match="too_large"):
        fetch_context(url, max_bytes=1)


def test_partial_transfer_is_failure_even_if_partial_bytes_form_valid_json(http_server):
    url, state = http_server
    state["length_offset"] = 10
    with pytest.raises(ContextError, match="source_incomplete_response"):
        fetch_context(url, attempts=1, timeout=0.1)
    assert state["count"] == 1


def test_retry_after_supports_dates_and_cli_failure_is_safe(monkeypatch):
    assert retry_after("Tue, 02 Jan 2024 13:00:00 GMT", NOW) == NOW + timedelta(hours=1)
    assert retry_after("garbage", NOW) is None

    def fail(*args, **kwargs):
        raise ContextError("source_timeout")

    monkeypatch.setattr("portwatch.cli.plan_context", fail)
    result = CliRunner().invoke(app, ["ingest", "weather", "--demo"])
    assert result.exit_code == 1
    assert "source_timeout" in result.output
