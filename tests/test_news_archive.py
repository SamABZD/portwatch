"""Date-only official releases preserve uncertainty, provenance and bounded parsing."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html import escape

import pytest

from portwatch.context import ContextError, news_fallback, plan_context
from portwatch.news_archive import normalize_official_archive
from portwatch.settings import Settings
from tests.test_context import NOW


def archive_payload(day="01/02/2024", *, href="/references/2024-News-Releases/synthetic"):
    return (
        '<html><table summary="2024 News Releases"><tr>'
        f'<td><a href="{href}">Synthetic Port of Los Angeles storm advisory</a></td>'
        f"<td>{day}</td></tr></table></html>"
    ).encode()


def request():
    return news_fallback(Settings(), plan_context(Settings(), "los_angeles", "news", now=NOW))


def archive_entries(entries, *, marker='summary="2024 News Releases"'):
    rows = [
        "<tr><td>"
        f'<a href="{escape(url, quote=True)}">Synthetic Port of Los Angeles update</a>'
        f"</td><td>{escape(day)}</td></tr>"
        for url, day in entries
    ]
    return (f"<html><table {marker}>" + "".join(rows) + "</table></html>").encode()


def test_release_date_is_not_an_invented_publication_instant_or_first_seen():
    result = normalize_official_archive(archive_payload(), request())
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["publication_time_utc"] is None and row["first_seen_time_utc"] is None
    assert "publication_date_only_window_overlap" in row["quality_flags"]
    assert result.metadata["publication_dates"][row["original_url"]] == "2024-01-02"
    assert result.metadata["provider"] == "Port of Los Angeles"
    assert not result.quality["exhaustive"]


def test_dates_wholly_outside_requested_window_produce_a_valid_empty_archive():
    result = normalize_official_archive(archive_payload("12/20/2023"), request())
    assert result.rows == [] and result.quality["outside_window"] == 1


@pytest.mark.parametrize(
    "payload",
    [b"<html>unavailable</html>", archive_payload("unknown"), b"\xff", b"x" * 2097153],
    ids=["unavailable", "undated", "invalid_utf8", "oversized"],
)
def test_unrecognized_or_undated_archive_is_not_empty_success(payload):
    with pytest.raises(ContextError):
        normalize_official_archive(payload, request())


def test_external_link_cannot_become_an_official_release():
    with pytest.raises(ContextError, match="article_metadata_missing"):
        normalize_official_archive(
            archive_payload(href="https://example.invalid/synthetic"), request()
        )


@pytest.mark.parametrize(
    "day,day_start,day_end,hours",
    [
        (
            "01/02/2024",
            datetime(2024, 1, 2, 8, tzinfo=UTC),
            datetime(2024, 1, 3, 8, tzinfo=UTC),
            24,
        ),
        (
            "03/10/2024",
            datetime(2024, 3, 10, 8, tzinfo=UTC),
            datetime(2024, 3, 11, 7, tzinfo=UTC),
            23,
        ),
        (
            "11/03/2024",
            datetime(2024, 11, 3, 7, tzinfo=UTC),
            datetime(2024, 11, 4, 8, tzinfo=UTC),
            25,
        ),
    ],
)
def test_pacific_date_only_overlap_obeys_day_boundaries_and_dst(day, day_start, day_end, hours):
    assert day_end - day_start == timedelta(hours=hours)
    payload = archive_payload(day)
    windows = [
        (day_start - timedelta(hours=1), day_start, False),
        (day_start - timedelta(hours=1), day_start + timedelta(seconds=1), True),
        (day_end - timedelta(seconds=1), day_end + timedelta(hours=1), True),
        (day_end, day_end + timedelta(hours=1), False),
    ]
    for start, end, included in windows:
        req = replace(request(), start_utc=start, end_utc=end)
        result = normalize_official_archive(payload, req)
        assert len(result.rows) == int(included)
        assert result.quality["excluded_by_date"] == int(not included)
        if included:
            row = result.rows[0]
            assert row["publication_time_utc"] is None and row["first_seen_time_utc"] is None
            assert result.metadata["publication_dates"] == {
                row["original_url"]: datetime.strptime(day, "%m/%d/%Y").date().isoformat()
            }


def test_archive_cap_follows_canonical_dedup_and_retains_only_stored_publication_dates():
    req = replace(request(), parameters={"maxrecords": 2})
    entries = [
        ("/news/first?utm_source=one", "01/02/2024"),
        ("/news/first?utm_source=two", "01/02/2024"),
        ("/news/second", "01/01/2024"),
        ("/news/third", "12/31/2023"),
    ]
    result = normalize_official_archive(archive_entries(entries), req)
    assert [row["canonical_url"] for row in result.rows] == [
        "https://portoflosangeles.org/news/first",
        "https://portoflosangeles.org/news/second",
    ]
    assert result.metadata["publication_dates"] == {
        "https://portoflosangeles.org/news/first?utm_source=one": "2024-01-02",
        "https://portoflosangeles.org/news/second": "2024-01-01",
    }
    assert all(row["publication_time_utc"] is None for row in result.rows)
    assert all(row["first_seen_time_utc"] is None for row in result.rows)
    assert result.quality["parsed_item_count"] == 4
    assert result.quality["eligible_item_count"] == 4
    assert result.quality["duplicate_urls"] == 1
    assert result.quality["deduplicated_count"] == 3
    assert result.quality["stored_item_count"] == 2
    assert result.quality["truncated"]


def test_archive_harmless_table_label_drift_preserves_attributed_release_metadata():
    payload = archive_entries(
        [("/news/synthetic", "01/02/2024")],
        marker='aria-label=" 2024   NEWS-RELEASES "',
    )
    result = normalize_official_archive(payload, request())
    assert len(result.rows) == 1
    assert result.metadata["provider"] == "Port of Los Angeles"
    assert result.metadata["response_bytes"] == len(payload)


def test_archive_unrelated_table_label_is_not_an_empty_success():
    payload = archive_entries(
        [("/news/synthetic", "01/02/2024")], marker='aria-label="Cargo totals"'
    )
    with pytest.raises(ContextError, match="news_archive_structure_unavailable"):
        normalize_official_archive(payload, request())


def test_archive_mixed_unknown_and_outside_dates_disclose_incomplete_empty_coverage():
    payload = archive_entries([("/news/unknown", "unknown"), ("/news/old", "12/20/2023")])
    result = normalize_official_archive(payload, request())
    assert result.rows == []
    assert result.quality["parsed_item_count"] == 2
    assert result.quality["eligible_item_count"] == 0
    assert result.quality["excluded_by_date"] == 1
    assert result.quality["unknown_date_count"] == 1
    assert result.quality["unknown_publication_rows"] == 1
    assert not result.quality["date_coverage_complete"]
