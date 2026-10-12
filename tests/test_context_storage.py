"""Actual PostgreSQL context revisions and replay isolation; synthetic loopback-free bytes."""

from datetime import timedelta

import pytest

from portwatch.context import ContextError, plan_context
from portwatch.context_http import HttpResponse
from portwatch.context_storage import ingest_context, read_demo_context
from portwatch.settings import Settings
from portwatch.storage import RawStore
from tests.test_bookkeeping import connection as pg_connection
from tests.test_context import NOW, news_payload, request, weather_payload

connection = pg_connection
pytestmark = pytest.mark.integration


def setup(tmp_path):
    return Settings(raw_dir=tmp_path)


def fetcher(payload, at=NOW):
    return lambda url: HttpResponse(payload, at, 200)


def test_duplicate_weather_replay_and_changed_revision_preserved(connection, tmp_path):
    settings, req = setup(tmp_path), request()
    first = ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)
    second = ingest_context(
        settings,
        connection,
        req,
        fetch=fetcher(weather_payload()),
        refresh=True,
        now=NOW + timedelta(minutes=2),
    )
    assert first.batch_id == second.batch_id and second.reused
    revised = ingest_context(
        settings,
        connection,
        req,
        fetch=fetcher(weather_payload(speed=9)),
        refresh=True,
        now=NOW + timedelta(minutes=4),
    )
    assert revised.batch_id != first.batch_id
    assert connection.execute("SELECT count(*) FROM weather_values").fetchone() == (48,)
    assert connection.execute("SELECT count(*) FROM ingestion_batches").fetchone() == (2,)
    assert len(RawStore(tmp_path).inspect()) == 2
    selected = read_demo_context(settings, connection, "los_angeles", req.end_utc)
    pinned = read_demo_context(
        settings, connection, "los_angeles", req.end_utc, batch_id=first.batch_id
    )
    assert selected["weather"][0][1] == 9
    assert pinned["weather"][0][1] == 3.2
    assert selected["batch_id"] == revised.batch_id


def test_cached_demo_never_calls_provider_and_prefix_has_no_future_weather(connection, tmp_path):
    settings, req = setup(tmp_path), request()
    ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)

    def unexpected(url):
        raise AssertionError("cached replay must not use network")

    cached = ingest_context(
        settings, connection, req, fetch=unexpected, now=NOW + timedelta(days=100)
    )
    assert cached.reused
    prefix = read_demo_context(
        settings, connection, "los_angeles", req.start_utc + timedelta(hours=3, minutes=30)
    )
    assert len(prefix["weather"]) == 4
    assert prefix["news"] == [] and prefix["news_status"] == "excluded_recent_source"


def test_news_empty_and_missing_publication_times_are_distinct_from_failures(connection, tmp_path):
    settings, req = setup(tmp_path), request("news", False)
    empty = ingest_context(
        settings, connection, req, fetch=fetcher(news_payload(empty=True)), now=NOW
    )
    assert empty.rows == 0
    assert connection.execute("SELECT status FROM context_attempts").fetchone() == ("valid_empty",)
    full = ingest_context(
        settings,
        connection,
        req,
        fetch=fetcher(news_payload(duplicates=True)),
        refresh=True,
        now=NOW + timedelta(minutes=16),
    )
    assert full.rows == 1
    assert connection.execute(
        "SELECT publication_time_utc, first_seen_time_utc FROM news_mentions"
    ).fetchone() == (None, NOW - timedelta(hours=1))
    reused = ingest_context(
        settings,
        connection,
        req,
        fetch=fetcher(news_payload(duplicates=True)),
        refresh=True,
        now=NOW + timedelta(minutes=32),
    )
    assert reused.reused and reused.batch_id == full.batch_id
    assert connection.execute("SELECT count(*) FROM news_mentions").fetchone() == (1,)
    demo = read_demo_context(settings, connection, "los_angeles", request().end_utc)
    assert demo["weather_status"] == "unavailable" and demo["news"] == []


def test_failed_normalization_retains_raw_and_prior_selected_revision(connection, tmp_path):
    settings, req = setup(tmp_path), request()
    first = ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)
    with pytest.raises(ContextError, match="invalid_json"):
        ingest_context(
            settings,
            connection,
            req,
            fetch=fetcher(b"not JSON"),
            refresh=True,
            now=NOW + timedelta(minutes=2),
        )
    assert len(RawStore(tmp_path).inspect()) == 2
    assert connection.execute("SELECT count(*) FROM weather_values").fetchone() == (24,)
    assert connection.execute(
        "SELECT status, error_code FROM context_attempts ORDER BY started_at"
    ).fetchall() == [("valid_nonempty", None), ("failed", "invalid_json_response")]
    assert (
        read_demo_context(settings, connection, "los_angeles", req.end_utc)["batch_id"]
        == first.batch_id
    )
    assert connection.execute("SELECT status FROM ingestion_batches ORDER BY id").fetchall() == [
        ("processed",),
        ("failed",),
    ]


def test_429_and_timeout_have_failed_runs_and_no_fabricated_empty_rows(connection, tmp_path):
    settings, req = setup(tmp_path), request("news", False)

    def rate_limited(url):
        raise ContextError(
            "source_rate_limited", http_status=429, next_allowed_at=NOW + timedelta(hours=1)
        )

    with pytest.raises(ContextError, match="rate_limited"):
        ingest_context(settings, connection, req, fetch=rate_limited, now=NOW)
    with pytest.raises(ContextError, match="local_poll_cooldown"):
        ingest_context(
            settings,
            connection,
            req,
            fetch=fetcher(news_payload()),
            now=NOW + timedelta(minutes=16),
        )

    def timeout(url):
        raise ContextError("source_timeout")

    with pytest.raises(ContextError, match="source_timeout"):
        ingest_context(settings, connection, req, fetch=timeout, now=NOW + timedelta(hours=2))
    assert connection.execute("SELECT count(*) FROM context_responses").fetchone() == (0,)
    assert connection.execute("SELECT status FROM pipeline_runs").fetchall() == [
        ("failed",),
        ("failed",),
    ]
    assert connection.execute(
        "SELECT http_status, error_code FROM context_attempts ORDER BY started_at"
    ).fetchall() == [(429, "source_rate_limited"), (None, "source_timeout")]


def test_current_news_and_forecast_cannot_enter_historical_partition(connection, tmp_path):
    settings = setup(tmp_path)
    historical, current = request(), request("weather", False)
    hist = ingest_context(
        settings, connection, historical, fetch=fetcher(weather_payload()), now=NOW
    )
    forecast = ingest_context(
        settings,
        connection,
        current,
        fetch=fetcher(weather_payload(current, speed=50)),
        now=NOW + timedelta(minutes=2),
    )
    news = plan_context(settings, "los_angeles", "news", now=NOW)
    ingest_context(settings, connection, news, fetch=fetcher(news_payload()), now=NOW)
    demo = read_demo_context(settings, connection, "los_angeles", historical.end_utc)
    assert demo["batch_id"] == hist.batch_id and demo["news"] == []
    assert all(row[1] == 3.2 for row in demo["weather"])
    with pytest.raises(ContextError, match="not_in_demo_partition"):
        read_demo_context(
            settings, connection, "los_angeles", historical.end_utc, batch_id=forecast.batch_id
        )


def test_missing_weather_values_persist_as_nulls_not_zero(connection, tmp_path):
    settings, req = setup(tmp_path), request()
    result = ingest_context(
        settings, connection, req, fetch=fetcher(weather_payload(missing=True)), now=NOW
    )
    assert result.rows == 24
    assert connection.execute(
        "SELECT count(*) FROM weather_values WHERE wind_speed_10m IS NULL AND precipitation = 0"
    ).fetchone() == (24,)


def test_interrupted_attempt_recovered_under_connector_lock(connection, tmp_path):
    settings, req = setup(tmp_path), request()
    ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)
    connection.execute("UPDATE context_attempts SET status = 'running', ended_at = NULL")
    connection.execute("UPDATE pipeline_runs SET status = 'running', ended_at = NULL")
    # Reading the cached response still detects that the previous writer has terminated.
    cached = ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)
    assert cached.reused
    assert connection.execute("SELECT status, error_code FROM context_attempts").fetchone() == (
        "failed",
        "source_interrupted",
    )
    assert connection.execute("SELECT status FROM pipeline_runs").fetchone() == ("interrupted",)


def test_corrupt_cached_context_refused_without_fetching(connection, tmp_path):
    settings, req = setup(tmp_path), request()
    result = ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)
    local = connection.execute(
        "SELECT local_path FROM ingestion_batches WHERE batch_id = %s", (result.batch_id,)
    ).fetchone()[0]
    (tmp_path / local).write_bytes(b"synthetic corruption")
    with pytest.raises(ValueError, match="verified"):
        ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)


def test_normalization_publish_rolls_back_rows_and_preserves_selection(
    connection, tmp_path, monkeypatch
):
    from portwatch.context import NormalizedContext, normalize_weather

    settings, req = setup(tmp_path), request()
    first = ingest_context(settings, connection, req, fetch=fetcher(weather_payload()), now=NOW)

    def invalid_grain(payload, req):
        normalized = normalize_weather(payload, req)
        return NormalizedContext(
            normalized.rows + [normalized.rows[0]], normalized.quality, normalized.metadata
        )

    monkeypatch.setattr("portwatch.context_storage.normalize_weather", invalid_grain)
    with pytest.raises(Exception, match="duplicate key"):
        ingest_context(
            settings,
            connection,
            req,
            fetch=fetcher(weather_payload(speed=10)),
            refresh=True,
            now=NOW + timedelta(minutes=2),
        )
    assert connection.execute("SELECT count(*) FROM weather_values").fetchone() == (24,)
    assert connection.execute("SELECT count(*) FROM context_responses").fetchone() == (1,)
    selected = read_demo_context(settings, connection, "los_angeles", req.end_utc)
    assert selected["batch_id"] == first.batch_id
