"""Versioned clean-position contract and source-specific, timezone-explicit adapters."""

import base64
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from portwatch.live import POSITION_TYPES, STATIC_TYPES
from portwatch.live_spool import LiveEnvelope
from portwatch.storage import Manifest

PROCESSING_VERSION = "positions-v1"


class RejectedRecord(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def identity(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def mmsi_text(value) -> str:
    # Integer identifiers may lose leading zeroes; restore the AIS nine-digit width.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise RejectedRecord("invalid_mmsi")
    if isinstance(value, int) and not 0 < value <= 999999999:
        raise RejectedRecord("invalid_mmsi")
    text = str(value).strip()
    if isinstance(value, int):
        text = text.zfill(9)
    if not re.fullmatch(r"[0-9]{9}", text) or text == "000000000":
        raise RejectedRecord("invalid_mmsi")
    return text


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise RejectedRecord("invalid_timestamp")
    return value.astimezone(UTC)


def coordinate(value, latitude: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RejectedRecord("missing_position")
    if value == (91 if latitude else 181):
        raise RejectedRecord("unavailable_position")
    limit = 90 if latitude else 180
    # Compare before float conversion: arbitrarily large JSON integers can overflow.
    if not -limit <= value <= limit or not math.isfinite(value):
        raise RejectedRecord("invalid_coordinates")
    return float(value) + 0.0  # Canonicalize negative zero.


def optional_number(value, name, maximum, unavailable, flags):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        flags.append(f"{name}_missing")
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not -1 <= value <= max(maximum, unavailable) + 1
        or not math.isfinite(value)
    ):
        flags.append(f"{name}_invalid")
        return None
    # Float32 NOAA storage adds representational noise to AIS tenth-unit values.
    value = round(float(value), 4)
    if value == unavailable:
        flags.append(f"{name}_unavailable")
        return None
    if not 0 <= value <= maximum:
        flags.append(f"{name}_invalid")
        return None
    return value + 0.0


def metadata_code(value, name, unknown, maximum, flags):
    if value is None:
        flags.append(f"{name}_missing")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**63:
        flags.append(f"{name}_invalid")
        return None
    if value == unknown:
        flags.append(f"{name}_unknown")
    elif value > maximum:
        flags.append(f"{name}_unrecognized")
    elif (name == "vessel_type" and value >= 100) or (
        name == "navigation_status" and 9 <= value <= 13
    ):
        flags.append(f"{name}_reserved")
    return value


def provider_time(value: str | None) -> datetime | None:
    if not value:
        return None
    # Go-formatted provider time includes up to nanoseconds. Keep original text separately.
    match = re.fullmatch(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.(\d{1,9}))? \+0000 UTC", value)
    try:
        if match:
            return datetime.fromisoformat(
                match[1] + "." + (match[2] or "0")[:6].ljust(6, "0")
            ).replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return aware_utc(parsed)
    except (ValueError, RejectedRecord):
        return None


@dataclass(frozen=True)
class CleanPosition:
    source_id: str
    dataset_id: str
    mmsi: str
    observed_at: datetime | None
    received_at: datetime
    effective_at: datetime
    timestamp_quality: str
    provider_reported_at: datetime | None
    source_timestamp_raw: str | None
    longitude: float
    latitude: float
    speed_knots: float | None
    course_degrees: float | None
    heading_degrees: float | None
    vessel_type: int | None
    navigation_status: int | None
    message_type: str
    source_batch_id: str
    record_offset: int
    offset_unit: Literal["national_row", "jsonl_byte"]
    source_record_id: str
    observation_id: str
    processing_version: str
    connection_session: str | None
    record_sequence: int | None
    quality_flags: tuple[str, ...]


def source_record_identity(parent: Manifest, offset: int, unit: str) -> str:
    if offset < 0 or unit not in {"national_row", "jsonl_byte"}:
        raise RejectedRecord("invalid_lineage")
    return identity([parent.source_id, parent.batch_id, unit, offset])


def build_position(
    parent,
    offset,
    unit,
    *,
    mmsi,
    observed,
    received,
    lon,
    lat,
    speed,
    course,
    heading,
    vessel_type,
    navigation,
    message_type,
    source_time=None,
    session=None,
    sequence=None,
    long_range=False,
):
    flags = []
    vessel = mmsi_text(mmsi)
    receipt = aware_utc(received)
    observation_time = aware_utc(observed) if observed is not None else None
    longitude, latitude = coordinate(lon, False), coordinate(lat, True)
    sog = optional_number(
        speed, "speed", 62 if long_range else 102.2, 63 if long_range else 102.3, flags
    )
    cog = optional_number(
        course, "course", 359 if long_range else 359.9, 511 if long_range else 360, flags
    )
    hdg = optional_number(heading, "heading", 359, 511, flags)
    if hdg is not None and not hdg.is_integer():
        hdg = None
        flags.append("heading_invalid")
    kind = metadata_code(vessel_type, "vessel_type", 0, 255, flags)
    nav = metadata_code(navigation, "navigation_status", 15, 15, flags)
    if sog is not None and sog > 60:
        flags.append("high_reported_speed")
    if sog == 102.2 and not long_range:
        flags.append("speed_at_least_102_2")
    if long_range:
        flags.append("coarse_long_range_position")
    reported = provider_time(source_time)
    if source_time and reported is None:
        flags.append("provider_time_unparseable")
    effective = observation_time or receipt
    quality = "provider_documented_utc" if observation_time else "receipt_fallback"
    # No unverified provider timestamp or AIS UTC-second enters observation identity.
    time_identity = (
        ["event", observation_time.isoformat()]
        if observation_time
        else ["receipt", receipt.isoformat(), session, sequence]
    )
    fingerprint = identity(
        [
            parent.source_id,
            parent.dataset_id,
            vessel,
            time_identity,
            longitude,
            latitude,
            message_type,
            sog,
            cog,
            hdg,
            nav,
        ]
    )
    return CleanPosition(
        parent.source_id,
        parent.dataset_id,
        vessel,
        observation_time,
        receipt,
        effective,
        quality,
        reported,
        source_time,
        longitude,
        latitude,
        sog,
        cog,
        hdg,
        kind,
        nav,
        message_type,
        parent.batch_id,
        offset,
        unit,
        source_record_identity(parent, offset, unit),
        fingerprint,
        PROCESSING_VERSION,
        session,
        sequence,
        tuple(sorted(set(flags))),
    )


def noaa_position(row: dict, parent: Manifest) -> CleanPosition:
    if any(
        row.get(name) != expected
        for name, expected in (
            ("source_id", parent.source_id),
            ("dataset_id", parent.dataset_id),
            ("source_batch_id", parent.batch_id),
            ("source_time_quality", "provider_documented_utc"),
        )
    ):
        raise RejectedRecord("invalid_lineage")
    timestamp = row.get("base_date_time")
    if not isinstance(timestamp, datetime):
        raise RejectedRecord("missing_observation_time")
    # NOAA GeoParquet README explicitly specifies UTC for this naive column.
    observed = (
        timestamp.replace(tzinfo=UTC) if timestamp.utcoffset() is None else aware_utc(timestamp)
    )
    if row.get("observed_at_utc") != observed:
        raise RejectedRecord("timestamp_lineage_mismatch")
    return build_position(
        parent,
        row["source_row_offset"],
        "national_row",
        mmsi=row.get("mmsi"),
        observed=observed,
        received=parent.received_at,
        lon=row.get("longitude"),
        lat=row.get("latitude"),
        speed=row.get("sog"),
        course=row.get("cog"),
        heading=row.get("heading"),
        vessel_type=row.get("vessel_type"),
        navigation=row.get("status"),
        message_type="noaa_position",
    )


@dataclass(frozen=True)
class LiveRecord:
    position: CleanPosition | None
    static_message: dict | None
    reason: str | None
    mmsi: str | None = None


def live_record(line: bytes, parent: Manifest, offset: int) -> LiveRecord:
    try:
        envelope = LiveEnvelope.model_validate_json(line)
    except ValueError:
        raise RejectedRecord("invalid_live_envelope") from None
    if envelope.source_id != parent.source_id or envelope.dataset_id != parent.dataset_id:
        raise RejectedRecord("invalid_lineage")
    try:
        payload = json.loads(
            base64.b64decode(envelope.payload_base64),
            parse_constant=invalid_json_constant,
            parse_float=finite_json_float,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise RejectedRecord("invalid_payload_json") from None
    if not isinstance(payload, dict):
        raise RejectedRecord("invalid_payload_shape")
    message_type = payload.get("MessageType")
    if message_type not in POSITION_TYPES + STATIC_TYPES:
        return LiveRecord(None, None, "non_position_message")
    body = (
        payload.get("Message", {}).get(message_type)
        if isinstance(payload.get("Message"), dict)
        else None
    )
    if not isinstance(body, dict):
        raise RejectedRecord("missing_message_body")
    if body.get("Valid") is not True:
        raise RejectedRecord("provider_invalid_message")
    vessel = mmsi_text(body.get("UserID"))
    metadata = payload.get("MetaData")
    if (
        isinstance(metadata, dict)
        and metadata.get("MMSI") is not None
        and mmsi_text(metadata["MMSI"]) != vessel
    ):
        raise RejectedRecord("mmsi_metadata_mismatch")
    if message_type in STATIC_TYPES:
        return LiveRecord(None, payload, "static_message_preserved", vessel)
    # Metadata coordinates may be a last-known position: use the current typed report only.
    position = build_position(
        parent,
        offset,
        "jsonl_byte",
        mmsi=vessel,
        observed=None,
        received=envelope.received_at,
        lon=body.get("Longitude"),
        lat=body.get("Latitude"),
        speed=body.get("Sog"),
        course=body.get("Cog"),
        heading=body.get("TrueHeading"),
        vessel_type=body.get("Type"),
        navigation=body.get("NavigationalStatus"),
        message_type=message_type,
        source_time=envelope.source_timestamp,
        session=str(envelope.connection_session),
        sequence=envelope.record_sequence,
        long_range=message_type == "LongRangeAisBroadcastMessage",
    )
    return LiveRecord(position, None, None)


def invalid_json_constant(value: str):
    raise ValueError("nonstandard JSON number")


def finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number exceeds finite representation")
    return parsed


def json_original(value) -> bytes:
    """Lossless tagged representation of a logical Parquet row (raw Parquet stays unchanged)."""

    def encode(item):
        if isinstance(item, datetime):
            return {"$datetime": item.isoformat()}
        if isinstance(item, bytes):
            return {"$bytes_base64": base64.b64encode(item).decode()}
        if isinstance(item, float) and not math.isfinite(item):
            return {"$float": str(item)}
        if isinstance(item, dict):
            return {k: encode(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [encode(v) for v in item]
        return item

    return json.dumps(
        encode(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def position_values(position: CleanPosition) -> dict:
    return asdict(position)
