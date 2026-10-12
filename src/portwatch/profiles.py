"""Bounded reported vessel properties with explicit event/receipt availability times."""

import base64
import json
import math

from psycopg.types.json import Jsonb

from portwatch.live_spool import LiveEnvelope
from portwatch.positions import PROCESSING_VERSION, source_record_identity


def text(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.replace("@", " ").split())[:120]
    return value if value and value.casefold() not in {"unknown", "n/a", "none"} else None


def number(value, maximum=500):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 < value <= maximum else None


def reported_profile(raw):
    """Do not infer destination ports or a year for the AIS month/day ETA."""
    if isinstance(raw, dict):
        values = dict(
            name=text(raw.get("vessel_name")),
            imo=text(raw.get("imo")),
            call_sign=text(raw.get("call_sign")),
            length_m=number(raw.get("length")),
            beam_m=number(raw.get("width")),
            draught_m=number(raw.get("draft"), 30),
        )
        return str(raw["mmsi"]).zfill(9), raw["observed_at_utc"], values
    envelope = LiveEnvelope.model_validate_json(raw)
    payload = json.loads(base64.b64decode(envelope.payload_base64))
    kind = payload.get("MessageType")
    body = payload.get("Message", {}).get(kind, {})
    if body.get("Valid") is not True or kind not in {"ShipStaticData", "StaticDataReport"}:
        return None
    a, b = body.get("ReportA", {}), body.get("ReportB", {})
    a = a if a.get("Valid") is True else {}
    b = b if b.get("Valid") is True else {}
    dimensions = body.get("Dimension", b.get("Dimension", {}))

    def dimension(first, second):
        x, y = number(dimensions.get(first), 511), number(dimensions.get(second), 511)
        return x + y if x is not None and y is not None else None

    values = dict(
        name=text(body.get("Name", a.get("Name"))),
        imo=text(str(body["ImoNumber"])) if body.get("ImoNumber") else None,
        call_sign=text(body.get("CallSign", b.get("CallSign"))),
        reported_type=number(body.get("Type", b.get("ShipType")), 99),
        length_m=dimension("A", "B"),
        beam_m=dimension("C", "D"),
        draught_m=number(body.get("MaximumStaticDraught"), 30),
        destination=text(body.get("Destination")),
    )
    eta = body.get("Eta")
    if isinstance(eta, dict) and all(
        isinstance(eta.get(k), int) for k in ("Month", "Day", "Hour", "Minute")
    ):
        month, day, hour, minute = (eta[k] for k in ("Month", "Day", "Hour", "Minute"))
        if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59:
            values["reported_eta"] = (
                f"{month:02d}-{day:02d} {hour:02d}:{minute:02d} UTC (year unspecified)"
            )
    return str(body["UserID"]).zfill(9), envelope.received_at, values


def enrich_input(connection, source):
    """Only accepted clean records become profiles; safe to resume after clean commit."""
    from portwatch.cleaning import input_records

    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(734920026)")
        if connection.execute(
            "SELECT 1 FROM vessel_profile_inputs WHERE input_id=%s AND processing_version=%s",
            (source.input_id, PROCESSING_VERSION),
        ).fetchone():
            return
        connection.execute(
            "CREATE TEMP TABLE profile_candidates (source_record_id text, mmsi text, "
            "available_at timestamptz, fields jsonb) ON COMMIT DROP"
        )
        with connection.cursor().copy("COPY profile_candidates FROM STDIN") as copy:
            for offset, raw in input_records(source):
                try:
                    report = reported_profile(raw)
                except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                    continue
                if report:
                    mmsi, at, fields = report
                    fields = {k: v for k, v in fields.items() if v is not None}
                    if fields:
                        copy.write_row(
                            (
                                source_record_identity(source.parent, offset, source.offset_unit),
                                mmsi,
                                at,
                                Jsonb(fields),
                            )
                        )
        count = connection.execute(
            """INSERT INTO vessel_profile_reports
            SELECT p.source_record_id,%s,%s,%s,p.mmsi,p.available_at,p.fields
            FROM profile_candidates p JOIN clean_records c ON c.source_record_id=p.source_record_id
            AND c.processing_version=%s WHERE c.outcome IN ('valid','duplicate')
            OR (c.outcome='skipped' AND c.reason_code='static_message_preserved')
            ON CONFLICT DO NOTHING""",
            (
                PROCESSING_VERSION,
                source.parent.source_id,
                source.parent.dataset_id,
                PROCESSING_VERSION,
            ),
        ).rowcount
        connection.execute(
            "INSERT INTO vessel_profile_inputs(input_id,processing_version,report_count) "
            "VALUES (%s,%s,%s)",
            (source.input_id, PROCESSING_VERSION, count),
        )
