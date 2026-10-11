"""Source semantics tested with explicitly synthetic records, outside analytical storage."""

import base64
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from portwatch.live_spool import LiveEnvelope
from portwatch.positions import RejectedRecord, live_record, noaa_position, provider_time
from portwatch.storage import BatchSpec, RawStore


@pytest.fixture
def parent(tmp_path):
    spec = BatchSpec(
        source_id="synthetic_noaa",
        dataset_id="synthetic_la",
        mode="historical",
        source_url="https://example.test/synthetic.parquet",
        source_date=date(2024, 1, 1),
        file_suffix=".parquet",
        received_at=datetime(2026, 10, 11, tzinfo=UTC),
    )
    return RawStore(tmp_path / "raw").write([b"synthetic bytes"], spec)


def noaa_row(parent, offset=0, timestamp=None, **changes):
    observed = timestamp or datetime(2024, 1, 1, tzinfo=UTC)
    row = dict(
        source_id=parent.source_id,
        dataset_id=parent.dataset_id,
        source_batch_id=parent.batch_id,
        source_time_quality="provider_documented_utc",
        source_row_offset=offset,
        base_date_time=observed.replace(tzinfo=None),
        observed_at_utc=observed,
        mmsi=123456789,
        longitude=-118.3,
        latitude=33.7,
        sog=0.0,
        cog=360.0,
        heading=511,
        vessel_type=None,
        status=15,
    )
    return row | changes


def live_line(parent, *, message_type="PositionReport", time=None, sequence=1, **changes):
    body = (
        dict(
            UserID=123456789,
            Valid=True,
            Longitude=-118.3,
            Latitude=33.7,
            Sog=102.3,
            Cog=360,
            TrueHeading=511,
            Timestamp=5,
            NavigationalStatus=15,
        )
        | changes
    )
    payload = {
        "MessageType": message_type,
        "Message": {message_type: body},
        "MetaData": {"MMSI": 123456789},
    }
    return (
        LiveEnvelope(
            source_id=parent.source_id,
            dataset_id=parent.dataset_id,
            connection_session=UUID(int=1),
            received_at=time or datetime(2026, 10, 11, tzinfo=UTC),
            record_sequence=sequence,
            source_timestamp="2026-10-11 00:00:00.123456789 +0000 UTC",
            kind="ais_observation",
            frame_type="binary",
            payload_base64=base64.b64encode(json.dumps(payload).encode()).decode(),
        )
        .model_dump_json()
        .encode()
        + b"\n"
    )


def test_noaa_utc_explicit_and_stationary_time_preserved(parent):
    first = noaa_position(noaa_row(parent), parent)
    second = noaa_position(noaa_row(parent, 1, datetime(2024, 1, 1, 0, 1, tzinfo=UTC)), parent)
    duplicate = noaa_position(noaa_row(parent, 2), parent)
    assert first.observed_at == datetime(2024, 1, 1, tzinfo=UTC)
    assert first.effective_at == first.observed_at != first.received_at
    assert first.mmsi == "123456789"
    assert first.observation_id != second.observation_id
    assert first.observation_id == duplicate.observation_id
    assert first.source_record_id != duplicate.source_record_id
    assert first.course_degrees is None and first.heading_degrees is None
    assert first.vessel_type is None and first.navigation_status == 15


def test_aware_timezone_conversion_and_unknown_metadata(parent):
    timestamp = datetime(2024, 1, 1, 2, tzinfo=timezone(timedelta(hours=2)))
    row = noaa_row(parent, base_date_time=timestamp, vessel_type=1024, status=99)
    result = noaa_position(row, parent)
    assert result.observed_at == datetime(2024, 1, 1, tzinfo=UTC)
    assert result.vessel_type == 1024 and result.navigation_status == 99
    assert "vessel_type_unrecognized" in result.quality_flags


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"latitude": 91}, "unavailable_position"),
        ({"longitude": 181}, "unavailable_position"),
        ({"latitude": None}, "missing_position"),
        ({"latitude": float("inf")}, "invalid_coordinates"),
        ({"longitude": -181}, "invalid_coordinates"),
        ({"mmsi": 0}, "invalid_mmsi"),
        ({"mmsi": True}, "invalid_mmsi"),
        ({"mmsi": 123.0}, "invalid_mmsi"),
        ({"base_date_time": None}, "missing_observation_time"),
        ({"source_id": "wrong"}, "invalid_lineage"),
    ],
)
def test_required_values_rejected(parent, changes, reason):
    with pytest.raises(RejectedRecord, match=reason):
        noaa_position(noaa_row(parent, **changes), parent)


def test_optional_values_do_not_reject_position(parent):
    result = noaa_position(noaa_row(parent, sog=-5, cog=None, heading=123.4, status=None), parent)
    assert (
        result.speed_knots is None
        and result.course_degrees is None
        and result.heading_degrees is None
    )
    assert "speed_invalid" in result.quality_flags
    assert "course_missing" in result.quality_flags


@pytest.mark.parametrize(
    "message_type",
    ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"],
)
def test_live_unavailable_motion_receipt_fallback(parent, message_type):
    result = live_record(live_line(parent, message_type=message_type), parent, 0).position
    assert result.observed_at is None and result.effective_at == result.received_at
    assert result.provider_reported_at == datetime(2026, 10, 11, 0, 0, 0, 123456, tzinfo=UTC)
    assert result.speed_knots is result.course_degrees is result.heading_degrees is None
    assert result.timestamp_quality == "receipt_fallback"
    again = live_record(live_line(parent, message_type=message_type), parent, 0).position
    later = live_record(
        live_line(parent, message_type=message_type, sequence=2), parent, 50
    ).position
    assert result == again and result.observation_id != later.observation_id


def test_long_range_specific_sentinels(parent):
    result = live_record(
        live_line(parent, message_type="LongRangeAisBroadcastMessage", Sog=63, Cog=511), parent, 0
    ).position
    assert result.speed_knots is None and result.course_degrees is None
    assert "coarse_long_range_position" in result.quality_flags


def test_static_coordinates_do_not_create_positions(parent):
    result = live_record(live_line(parent, message_type="ShipStaticData"), parent, 0)
    assert result.position is None and result.static_message is not None
    assert result.reason == "static_message_preserved"


def test_live_required_shape_and_provider_validity(parent):
    with pytest.raises(RejectedRecord, match="provider_invalid_message"):
        live_record(live_line(parent, Valid=False), parent, 0)
    with pytest.raises(RejectedRecord, match="invalid_live_envelope"):
        live_record(b"synthetic malformed json", parent, 0)
    assert provider_time("2026-10-11T12:00:00") is None


def test_sources_remain_separate(parent):
    first = noaa_position(noaa_row(parent), parent)
    other = parent.model_copy(update={"source_id": "different_source"})
    second = noaa_position(noaa_row(other), other)
    assert first.observation_id != second.observation_id
    assert replace(first, source_id=other.source_id) != second


def test_huge_numbers_are_rejected_or_flagged_without_batch_failure(parent):
    with pytest.raises(RejectedRecord, match="invalid_coordinates"):
        live_record(live_line(parent, Latitude=10**400), parent, 0)
    result = live_record(live_line(parent, Sog=10**400), parent, 0).position
    assert result.speed_knots is None and "speed_invalid" in result.quality_flags
    with pytest.raises(RejectedRecord, match="invalid_mmsi"):
        noaa_position(noaa_row(parent, mmsi=10**5000), parent)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_nonstandard_static_json_numbers_quarantined(parent, number):
    with pytest.raises(RejectedRecord, match="invalid_payload_json"):
        live_record(live_line(parent, message_type="ShipStaticData", Sog=number), parent, 0)


def test_deep_payload_json_quarantined(parent):
    envelope = LiveEnvelope.model_validate_json(live_line(parent))
    envelope = envelope.model_copy(
        update={"payload_base64": base64.b64encode(b"[" * 1500 + b"0" + b"]" * 1500).decode()}
    )
    with pytest.raises(RejectedRecord, match="invalid_payload_json"):
        live_record(envelope.model_dump_json().encode(), parent, 0)


def exponent_line(parent):
    envelope = LiveEnvelope.model_validate_json(live_line(parent, message_type="ShipStaticData"))
    payload = base64.b64decode(envelope.payload_base64).replace(b'"Sog": 102.3', b'"Sog": 1e309')
    return (
        envelope.model_copy(update={"payload_base64": base64.b64encode(payload).decode()})
        .model_dump_json()
        .encode()
    )


def test_valid_json_exponent_overflow_quarantined(parent):
    with pytest.raises(RejectedRecord, match="invalid_payload_json"):
        live_record(exponent_line(parent), parent, 0)
