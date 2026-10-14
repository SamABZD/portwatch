"""Synthetic news lineage reaches real SQL, source health and the read-only frame API."""

import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from portwatch.bookkeeping import finish_run
from portwatch.context import ContextError, news_fallback, plan_context
from portwatch.context_http import HttpResponse
from portwatch.context_storage import ingest_context
from portwatch.demo.api import ReplayService, create_app
from portwatch.demo.replay import fingerprint, load_dataset, public_json
from portwatch.health import context_health
from portwatch.marts import MartConfig
from portwatch.news import news_identity, read_news_context, read_news_rows
from portwatch.pipeline import PIPELINE_VERSION, begin_workflow
from portwatch.settings import Settings
from portwatch.storage import BatchSpec, RawStore
from portwatch.visits import VisitRules
from portwatch.zones import assign_zones, validate_geofence
from tests.test_bookkeeping import connection as pg_connection
from tests.test_context import NOW, news_payload
from tests.test_news_archive import archive_payload
from tests.test_news_rss import rss_payload
from tests.test_positions import parent as synthetic_parent
from tests.test_visit_storage import VERSION, prepare_track
from tests.test_zones import BOUNDS, synthetic_geofence

connection = pg_connection
parent = synthetic_parent
pytestmark = pytest.mark.integration


def settings_at(tmp_path):
    return Settings(
        raw_dir=tmp_path / "raw",
        quarantine_dir=tmp_path / "quarantine",
        live_status_dir=tmp_path / "live-status",
        ais_api_key=None,
    )


def fetcher(payload, at):
    return lambda _url: HttpResponse(payload, at, 200)


def failed_fetch(_url):
    raise ContextError("source_rate_limited", http_status=429)


def test_provider_failure_and_fallback_keep_separate_receipts(connection, tmp_path):
    settings = settings_at(tmp_path)
    primary = plan_context(settings, "los_angeles", "news", now=NOW)
    with pytest.raises(ContextError, match="source_rate_limited"):
        ingest_context(settings, connection, primary, fetch=failed_fetch, now=NOW)
    fallback = news_fallback(settings, primary)
    received = NOW + timedelta(seconds=2)
    saved = ingest_context(
        settings,
        connection,
        fallback,
        fetch=fetcher(archive_payload(), received),
        now=received,
    )
    result = read_news_context(connection, "los_angeles", received, settings)
    assert result["status"] == "fallback"
    assert result["provider"] == "Port of Los Angeles"
    assert result["saved_batch_id"] == saved.batch_id
    assert result["last_successful_fetch_utc"] == received
    assert result["valid_start_utc"] == primary.start_utc
    assert result["valid_end_utc"] == primary.end_utc
    assert result["window_hours"] == (primary.end_utc - primary.start_utc).total_seconds() / 3600
    assert result["primary_attempt"]["http_status"] == 429
    assert result["latest_attempt"]["http_status"] == 200
    assert context_health(connection, "los_angeles", "recent_news", received, settings) == result


def test_valid_empty_never_becomes_provider_failure(connection, tmp_path):
    settings = settings_at(tmp_path)
    request = plan_context(settings, "los_angeles", "news", now=NOW)
    ingest_context(
        settings, connection, request, fetch=fetcher(news_payload(empty=True), NOW), now=NOW
    )
    result = read_news_context(connection, "los_angeles", NOW, settings)
    assert result["status"] == result["data_status"] == "valid_empty"
    assert result["saved_row_count"] == 0
    assert result["latest_attempt"]["http_status"] == 200
    assert read_news_rows(connection, result, "America/Los_Angeles") == []


def test_deferred_schedule_preserves_primary_429_without_fake_fetch(connection, tmp_path):
    settings = settings_at(tmp_path)
    request = plan_context(settings, "los_angeles", "news", now=NOW)
    with pytest.raises(ContextError):
        ingest_context(settings, connection, request, fetch=failed_fetch, now=NOW)
    key = f"{PIPELINE_VERSION}:los_angeles:news:{request.request_key}"
    run_id = begin_workflow(connection, key, "pipeline_news")
    connection.execute(
        "UPDATE pipeline_runs SET details=%s WHERE run_id=%s",
        (Jsonb(dict(status="deferred", reason="local_poll_cooldown")), run_id),
    )
    finish_run(connection, run_id, input_count=0, output_count=0)
    result = read_news_context(connection, "los_angeles", datetime.now(UTC), settings)
    assert result["status"] == "deferred"
    assert result["schedule"]["reason"] == "local_poll_cooldown"
    assert result["primary_attempt"]["http_status"] == 429
    assert result["saved_batch_id"] is None
    assert connection.execute("SELECT count(*) FROM context_attempts").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM context_responses").fetchone() == (0,)


def test_row_limit_orders_known_dates_and_unknown_ties_deterministically(connection, tmp_path):
    settings = settings_at(tmp_path)
    request = plan_context(settings, "los_angeles", "news", now=NOW)
    known = [
        dict(
            url=f"https://example.invalid/dated/{index:02}",
            title=f"Synthetic harbor release {index}",
            published_at=(NOW - timedelta(minutes=index + 1)).isoformat(),
        )
        for index in range(8)
    ]
    unknown = [
        dict(url=f"https://example.invalid/unknown/{index:02}", title="Synthetic harbor release")
        for index in range(5)
    ]
    payload = json.dumps(dict(articles=list(reversed(unknown + known)))).encode()
    ingest_context(settings, connection, request, fetch=fetcher(payload, NOW), now=NOW)
    result = read_news_context(connection, "los_angeles", NOW, settings)
    rows = read_news_rows(connection, result, "America/Los_Angeles")
    assert result["saved_row_count"] == 13
    assert [row["url"] for row in rows] == [item["url"] for item in known + unknown[:2]]
    assert rows[-1]["publication_precision"] == "unknown"
    assert rows[-1]["publication_time_utc"] is None
    assert rows[-1]["first_seen_time_utc"] is None
    assert "publication_missing" in rows[-1]["quality_flags"]
    assert read_news_rows(connection, result, "America/Los_Angeles") == rows


def test_more_than_ten_date_only_releases_keep_calendar_dates(connection, tmp_path):
    settings = settings_at(tmp_path)
    primary = plan_context(settings, "los_angeles", "news", now=NOW)
    request = news_fallback(settings, primary)
    rows = "".join(
        f'<tr><td><a href="/references/2024-News-Releases/synthetic-{index:02}">'
        f"Synthetic harbor release {index}</a></td><td>01/02/2024</td></tr>"
        for index in reversed(range(13))
    )
    payload = f'<table summary="2024 News Releases">{rows}</table>'.encode()
    ingest_context(settings, connection, request, fetch=fetcher(payload, NOW), now=NOW)
    result = read_news_context(connection, "los_angeles", NOW, settings)
    selected = read_news_rows(connection, result, "America/Los_Angeles")
    assert result["saved_row_count"] == 13 and len(selected) == 10
    assert [row["url"].rsplit("-", 1)[1] for row in selected] == [f"{i:02}" for i in range(10)]
    assert all(row["publication_date"] == "2024-01-02" for row in selected)
    assert all(row["publication_time_utc"] is None for row in selected)
    assert all(row["publication_precision"] == "date" for row in selected)
    assert all(row["publication_timezone"] == "America/Los_Angeles" for row in selected)


def test_selection_and_primary_attempts_never_cross_ports(connection, tmp_path):
    settings = settings_at(tmp_path)
    la = plan_context(settings, "los_angeles", "news", now=NOW)
    oakland = plan_context(settings, "oakland", "news", now=NOW)
    la_saved = ingest_context(settings, connection, la, fetch=fetcher(news_payload(), NOW), now=NOW)
    with pytest.raises(ContextError):
        ingest_context(
            settings, connection, oakland, fetch=failed_fetch, now=NOW + timedelta(minutes=16)
        )
    fallback = news_fallback(settings, oakland)
    oakland_saved = ingest_context(
        settings,
        connection,
        fallback,
        fetch=fetcher(rss_payload(NOW - timedelta(hours=1)), NOW + timedelta(minutes=16)),
        now=NOW + timedelta(minutes=16),
    )
    la_result = read_news_context(connection, "los_angeles", NOW + timedelta(minutes=17), settings)
    oakland_result = read_news_context(connection, "oakland", NOW + timedelta(minutes=17), settings)
    assert la_result["saved_batch_id"] == la_saved.batch_id
    assert la_result["primary_attempt"]["http_status"] == 200
    assert la_result["status"] == "available" and not la_result["using_fallback"]
    assert oakland_result["saved_batch_id"] == oakland_saved.batch_id
    assert oakland_result["primary_attempt"]["http_status"] == 429
    assert oakland_result["provider"] == "Port of Oakland"
    assert news_identity(la_result) != news_identity(oakland_result)
    assert all(
        row["domain"] == "example.invalid"
        for row in read_news_rows(connection, la_result, "America/Los_Angeles")
    )
    assert all(
        row["domain"] == "www.portofoakland.com"
        for row in read_news_rows(connection, oakland_result, "America/Los_Angeles")
    )


@pytest.mark.parametrize("failure", ["malformed", "rate_limited"])
def test_failed_refresh_keeps_last_good_revision_and_rows(connection, tmp_path, failure):
    settings = settings_at(tmp_path)
    request = plan_context(settings, "los_angeles", "news", now=NOW)
    first = ingest_context(
        settings, connection, request, fetch=fetcher(news_payload(), NOW), now=NOW
    )
    later = NOW + timedelta(minutes=16)
    fetch = fetcher(news_payload(bad=True), later) if failure == "malformed" else failed_fetch
    with pytest.raises(ContextError):
        ingest_context(settings, connection, request, fetch=fetch, refresh=True, now=later)
    result = read_news_context(connection, "los_angeles", later, settings)
    assert result["saved_batch_id"] == first.batch_id
    assert result["last_successful_fetch_utc"] == NOW
    assert result["status"] == result["freshness"] == "stale"
    assert result["data_status"] == "available"
    assert result["latest_attempt"]["status"] == "failed"
    assert len(read_news_rows(connection, result, "America/Los_Angeles")) == 1
    assert connection.execute("SELECT count(*) FROM context_responses").fetchone() == (1,)
    assert len(RawStore(settings.raw_dir).inspect()) == (2 if failure == "malformed" else 1)


def test_identical_bytes_keep_raw_identity_but_refresh_receipt_and_revision(connection, tmp_path):
    settings = settings_at(tmp_path)
    request = plan_context(settings, "los_angeles", "news", now=NOW)
    first = ingest_context(
        settings, connection, request, fetch=fetcher(news_payload(), NOW), now=NOW
    )
    before = read_news_context(connection, "los_angeles", NOW, settings)
    later = NOW + timedelta(minutes=16)
    second = ingest_context(
        settings, connection, request, fetch=fetcher(news_payload(), later), refresh=True, now=later
    )
    after = read_news_context(connection, "los_angeles", later, settings)
    assert first.batch_id == second.batch_id and second.reused
    assert before["last_successful_fetch_utc"] == NOW
    assert after["last_successful_fetch_utc"] == later
    assert news_identity(before) != news_identity(after)
    assert connection.execute("SELECT received_at FROM ingestion_batches").fetchone() == (NOW,)
    assert connection.execute("SELECT count(*) FROM context_responses").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM context_attempts").fetchone() == (2,)


def test_failed_primary_and_official_refresh_keep_selected_primary_stale(connection, tmp_path):
    settings = settings_at(tmp_path)
    primary = plan_context(settings, "los_angeles", "news", now=NOW)
    first = ingest_context(
        settings, connection, primary, fetch=fetcher(news_payload(), NOW), now=NOW
    )
    later = NOW + timedelta(minutes=16)
    with pytest.raises(ContextError, match="source_rate_limited"):
        ingest_context(settings, connection, primary, fetch=failed_fetch, refresh=True, now=later)
    official = news_fallback(settings, primary)
    with pytest.raises(ContextError, match="source_rate_limited"):
        ingest_context(
            settings, connection, official, fetch=failed_fetch, now=later + timedelta(seconds=1)
        )
    result = read_news_context(connection, "los_angeles", later + timedelta(seconds=2), settings)
    assert result["saved_batch_id"] == first.batch_id
    assert result["last_successful_fetch_utc"] == NOW
    assert result["source_id"] == "gdelt_doc_v2"
    assert result["data_status"] == "available" and result["saved_row_count"] == 1
    assert result["latest_attempt"]["source_id"] == official.source_id
    assert result["latest_attempt"]["status"] == "failed"
    assert result["selected_attempt"]["source_id"] == "gdelt_doc_v2"
    assert result["selected_attempt"]["status"] == "failed"
    assert result["selected_attempt"]["started_at"] == later
    assert result["status"] == result["freshness"] == "stale"
    assert len(read_news_rows(connection, result, "America/Los_Angeles")) == 1


def test_unregistered_news_raw_recovers_without_fetch_or_fake_attempt(connection, tmp_path):
    settings = settings_at(tmp_path)
    request = plan_context(settings, "los_angeles", "news", now=NOW)
    manifest = RawStore(settings.raw_dir).write(
        [news_payload()],
        BatchSpec(
            source_id=request.source_id,
            dataset_id="ctx-" + request.request_key,
            source_url=request.endpoint,
            mode="live",
            received_at=NOW,
            source_start_utc=request.start_utc,
            source_end_utc=request.end_utc,
            processing_version=request.parser_version,
            file_suffix=".json",
        ),
    )

    def forbidden(_url):
        pytest.fail("verified saved news must recover without provider request")

    saved = ingest_context(
        settings, connection, request, fetch=forbidden, now=NOW + timedelta(minutes=1)
    )
    result = read_news_context(connection, "los_angeles", NOW + timedelta(minutes=1), settings)
    assert saved.batch_id == result["saved_batch_id"] == manifest.batch_id
    assert saved.retrieved_at == result["last_successful_fetch_utc"] == NOW
    assert result["status"] == "available" and result["latest_attempt"] is None
    assert len(read_news_rows(connection, result, "America/Los_Angeles")) == 1
    assert connection.execute("SELECT count(*) FROM context_attempts").fetchone() == (0,)


def test_stored_fallback_reaches_sql_frame_api_and_matching_health(
    connection, tmp_path, parent, monkeypatch
):
    import portwatch.demo.api as api
    import portwatch.demo.replay as replay
    import portwatch.health as health
    import tests.test_visit_storage as visit_fixtures

    settings = settings_at(tmp_path)
    received = datetime.now(UTC) - timedelta(minutes=30)
    primary = plan_context(settings, "los_angeles", "news", now=received)
    with pytest.raises(ContextError):
        ingest_context(settings, connection, primary, fetch=failed_fetch, now=received)
    fallback = news_fallback(settings, primary)
    receipt = received + timedelta(seconds=2)
    publication_day = receipt.astimezone(ZoneInfo("America/Los_Angeles"))
    saved = ingest_context(
        settings,
        connection,
        fallback,
        fetch=fetcher(archive_payload(publication_day.strftime("%m/%d/%Y")), receipt),
        now=receipt,
    )
    base = received - timedelta(minutes=30)
    monkeypatch.setattr(visit_fixtures, "BASE", base)
    original = prepare_track(connection, tmp_path, parent, base=base)
    scope = replace(original, start_utc=base, end_utc=base + timedelta(hours=2))
    assign_zones(connection, "los_angeles", VERSION, scope)
    fence = validate_geofence(synthetic_geofence(), "los_angeles", BOUNDS)
    config = MartConfig("los_angeles", VERSION, VisitRules())
    monkeypatch.setattr(replay, "configured_geofence", lambda *_: fence)
    monkeypatch.setattr(replay, "configured_marts", lambda *_: (scope, config))
    monkeypatch.setattr(
        replay,
        "live_spec",
        lambda *_: (
            SimpleNamespace(source_id=parent.source_id, dataset_id=parent.dataset_id),
            None,
        ),
    )
    monkeypatch.setattr(api, "connect_db", lambda *_: nullcontext(connection))
    monkeypatch.setattr(health, "connect_db", lambda *_: nullcontext(connection))
    descriptor, _, port = fingerprint(connection, settings, "live", datetime.now(UTC))
    loaded = load_dataset(connection, descriptor, fence, port)
    assert loaded.metadata["observation_count"] == 5
    assert loaded.news[0]["publication_date"] == publication_day.date().isoformat()
    assert loaded.news[0]["publication_time_utc"] is None
    aged = loaded.frame(receipt + timedelta(hours=3))
    assert aged["news_status"] == aged["dataset"]["news_status"] == "stale"
    assert aged["dataset"]["news_context"] == aged["news_context"]
    assert aged["news_context"]["freshness"] == "stale"
    with TestClient(create_app(service=ReplayService(settings))) as client:
        response = client.get("/api/frame?mode=live&port=los_angeles")
        assert response.status_code == 200
        frame = response.json()
        context = frame["news_context"]
        assert frame["dataset"]["news_context"] == context
        assert frame["dataset"]["news_status"] == frame["news_status"]
        assert frame["news_status"] == context["status"] == "fallback"
        assert frame["prefix_observations"] == 5
        assert context["provider"] == "Port of Los Angeles"
        assert context["saved_batch_id"] == saved.batch_id
        assert context["last_successful_fetch_utc"] == receipt.isoformat()
        assert context["primary_attempt"]["http_status"] == 429
        assert frame["news"][0]["publication_precision"] == "date"
        report_response = client.get("/api/health?port=los_angeles")
        assert report_response.status_code == 200
        source_health = report_response.json()["context"]["recent_news"]
        assert news_identity(source_health) == news_identity(context)
        historical_response = client.get(
            "/api/frame",
            params=dict(
                mode="historical", port="los_angeles", at=(base + timedelta(hours=1)).isoformat()
            ),
        )
        assert historical_response.status_code == 200
        historical = historical_response.json()
        assert historical["prefix_observations"] == 5
        assert historical["news"] == []
        assert historical["news_context"] is None
        assert historical["news_status"] == "excluded_recent_source"
    assert (
        public_json(context_health(connection, "los_angeles", "recent_news", receipt, settings))[
            "saved_batch_id"
        ]
        == saved.batch_id
    )
