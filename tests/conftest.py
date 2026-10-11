"""Shared, explicitly synthetic acquisition metadata."""

from datetime import UTC, date, datetime

import pytest

from portwatch.storage import BatchSpec


@pytest.fixture
def spec() -> BatchSpec:
    return BatchSpec(
        source_id="synthetic_fixture", dataset_id="synthetic_step02",
        source_url="https://example.invalid/synthetic.csv", mode="historical",
        source_date=date(2024, 1, 1), received_at=datetime(2026, 10, 11, tzinfo=UTC),
        record_count=2, file_suffix=".csv",
    )
