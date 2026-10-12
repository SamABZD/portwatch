"""Reported properties are optional evidence, never invented voyages."""

import base64
import json
from datetime import UTC, datetime
from uuid import uuid4

from portwatch.live_spool import LiveEnvelope
from portwatch.profiles import number, reported_profile, text


def envelope(kind, body):
    payload = dict(MessageType=kind, Message={kind: dict(UserID=123456789, Valid=True, **body)})
    return (
        LiveEnvelope(
            source_id="synthetic",
            dataset_id="synthetic_live",
            kind="ais_static",
            frame_type="binary",
            received_at=datetime(2024, 1, 1, tzinfo=UTC),
            connection_session=uuid4(),
            record_sequence=1,
            payload_base64=base64.b64encode(json.dumps(payload).encode()).decode(),
        )
        .model_dump_json()
        .encode()
    )


def test_unknown_and_invalid_properties_stay_missing():
    assert text("@@ UNKNOWN @") is None
    assert text("SYNTHETIC @ VESSEL@@") == "SYNTHETIC VESSEL"
    for value in (0, -1, float("nan"), float("inf"), True, "invalid"):
        assert number(value) is None


def test_static_voyage_eta_has_no_invented_year():
    _, at, values = reported_profile(
        envelope(
            "ShipStaticData",
            dict(
                Name="Synthetic",
                Destination="Synthetic port",
                Eta=dict(Month=2, Day=3, Hour=4, Minute=5),
                Dimension=dict(A=100, B=20, C=5, D=5),
            ),
        )
    )
    assert at == datetime(2024, 1, 1, tzinfo=UTC)
    assert values["length_m"] == 120
    assert values["reported_eta"] == "02-03 04:05 UTC (year unspecified)"


def test_class_b_invalid_part_does_not_overwrite_valid_properties():
    _, _, values = reported_profile(
        envelope(
            "StaticDataReport",
            dict(
                ReportA=dict(Valid=True, Name="Synthetic"),
                ReportB=dict(Valid=False, ShipType=70, CallSign="BAD"),
            ),
        )
    )
    assert values["name"] == "Synthetic"
    assert values["reported_type"] is None and values["call_sign"] is None
