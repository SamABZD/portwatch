"""Date-only official releases preserve uncertainty, provenance and bounded parsing."""

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
    result = normalize_official_archive(archive_payload("12/31/2023"), request())
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
