"""Pure source-state semantics; saved receipts remain independent of display age."""

from datetime import UTC, datetime, timedelta

import pytest

from portwatch.demo.replay import public_json
from portwatch.news import news_identity, news_state

NOW = datetime(2024, 1, 2, 12, tzinfo=UTC)


def snapshot(**changes):
    attempt = dict(
        source_id="gdelt_doc_v2",
        status="valid_nonempty",
        started_at=NOW - timedelta(seconds=1),
        retrieved_at=NOW,
        http_status=200,
        attempt_id="synthetic-success",
        batch_id="synthetic-batch",
    )
    return (
        dict(
            product="recent_news",
            port_id="los_angeles",
            source_id="gdelt_doc_v2",
            provider="GDELT DOC 2.0",
            saved_batch_id="synthetic-batch",
            request_key="synthetic-request",
            last_successful_fetch_utc=NOW,
            using_fallback=False,
            data_status="available",
            saved_row_count=1,
            valid_start_utc=NOW - timedelta(hours=24),
            valid_end_utc=NOW,
            latest_attempt=attempt,
            primary_attempt=attempt,
            schedule=None,
        )
        | changes
    )


@pytest.mark.parametrize(
    "changes,advance,expected,freshness",
    [
        ({}, 0, "available", "fresh"),
        ({"data_status": "valid_empty", "saved_row_count": 0}, 0, "valid_empty", "fresh"),
        ({"using_fallback": True}, 0, "fallback", "fresh"),
        ({}, 7201, "stale", "stale"),
        (
            {"last_successful_fetch_utc": None, "data_status": "unavailable"},
            0,
            "unavailable",
            "unavailable",
        ),
        (
            {"schedule": {"status": "deferred", "started_at": NOW}},
            0,
            "deferred",
            "fresh",
        ),
    ],
)
def test_saved_source_states(changes, advance, expected, freshness):
    result = news_state(snapshot(**changes), NOW + timedelta(seconds=advance), 7200)
    assert result["status"] == expected
    assert result["freshness"] == freshness


def test_failed_refresh_retains_receipt_and_marks_last_good_stale():
    saved = snapshot(
        latest_attempt=dict(
            status="failed",
            started_at=NOW,
            http_status=429,
            next_allowed_at=NOW + timedelta(minutes=15),
        ),
    )
    result = news_state(saved, NOW + timedelta(seconds=20), 7200)
    assert result["status"] == result["freshness"] == "stale"
    assert result["saved_batch_id"] == "synthetic-batch"
    assert result["last_successful_fetch_utc"] == NOW
    assert result["success_age_seconds"] == 20
    assert result["data_status"] == "available"


def test_primary_rate_limit_remains_separate_from_valid_empty_fallback():
    primary = dict(status="failed", started_at=NOW - timedelta(seconds=3), http_status=429)
    saved = snapshot(
        using_fallback=True,
        source_id="port_los_angeles_releases",
        provider="Port of Los Angeles",
        data_status="valid_empty",
        saved_row_count=0,
        primary_attempt=primary,
    )
    result = news_state(saved, NOW + timedelta(seconds=5), 7200)
    assert result["status"] == "fallback"
    assert result["data_status"] == "valid_empty"
    assert result["primary_attempt"] == primary
    assert result["latest_attempt"]["http_status"] == 200
    assert result["success_age_seconds"] == 5


def test_failed_primary_and_fallback_keep_last_good_primary_stale():
    selected = dict(source_id="gdelt_doc_v2", status="failed", started_at=NOW, http_status=503)
    fallback = dict(
        source_id="port_los_angeles_releases",
        status="failed",
        started_at=NOW + timedelta(seconds=1),
        http_status=503,
    )
    result = news_state(
        snapshot(selected_attempt=selected, primary_attempt=selected, latest_attempt=fallback),
        NOW + timedelta(seconds=20),
        7200,
    )
    assert result["status"] == result["freshness"] == "stale"
    assert result["saved_batch_id"] == "synthetic-batch"
    assert result["last_successful_fetch_utc"] == NOW
    assert result["selected_attempt"] == selected
    assert result["latest_attempt"] == fallback


def test_rate_limited_without_saved_response_is_deferred_until_allowed():
    attempt = dict(
        status="failed",
        started_at=NOW,
        http_status=429,
        next_allowed_at=NOW + timedelta(minutes=15),
    )
    saved = snapshot(
        last_successful_fetch_utc=None,
        saved_batch_id=None,
        data_status="unavailable",
        latest_attempt=attempt,
        primary_attempt=attempt,
    )
    result = news_state(saved, NOW + timedelta(minutes=1), 7200)
    assert result["status"] == "deferred"
    assert result["data_status"] == "unavailable"
    assert result["primary_attempt"]["http_status"] == 429
    assert result["success_age_seconds"] is None
    assert news_state(saved, NOW + timedelta(minutes=16), 7200)["status"] == "unavailable"


def test_json_frame_metadata_keeps_attempt_and_schedule_time_comparisons_valid():
    saved = public_json(
        snapshot(
            latest_attempt=dict(status="failed", started_at=NOW, http_status=503),
            schedule=dict(status="deferred", started_at=NOW),
        )
    )
    result = news_state(saved, NOW + timedelta(seconds=30), 7200)
    assert result["status"] == result["freshness"] == "stale"
    assert result["last_successful_fetch_utc"] == NOW.isoformat()
    assert result["success_age_seconds"] == 30


def test_identity_ignores_clock_age_but_includes_actual_refetch_receipt():
    saved = snapshot()
    fresh = news_state(saved, NOW + timedelta(seconds=1), 7200)
    stale = news_state(saved, NOW + timedelta(hours=3), 7200)
    assert fresh["status"] != stale["status"]
    assert news_identity(fresh) == news_identity(stale)
    refetched = snapshot(last_successful_fetch_utc=NOW + timedelta(minutes=15))
    assert refetched["saved_batch_id"] == saved["saved_batch_id"]
    assert news_identity(refetched) != news_identity(saved)
