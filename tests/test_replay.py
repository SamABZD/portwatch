"""Explicit synthetic replay evidence: time cutoffs, gaps, qualification and API isolation."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from portwatch.demo.api import ReplayService, create_app
from portwatch.demo.replay import Observation, ReplayDataset, ReplayError, load_prepared, stamp
from portwatch.settings import Settings
from portwatch.visits import VisitRules

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def dataset(timeline, *, coverage=True):
    zones = dict(
        type="FeatureCollection",
        features=[
            dict(
                type="Feature",
                properties=dict(zone_id=zone, zone_type=kind, name=f"Synthetic {zone}"),
                geometry=dict(type="Polygon", coordinates=[[[0, 0], [1, 0], [1, 1], [0, 0]]]),
            )
            for zone, kind in (
                ("harbor", "harbor"),
                ("terminal", "terminal"),
                ("wait", "waiting_candidate"),
            )
        ],
    )
    points = []
    for i, item in enumerate(timeline):
        minute, zone = item[:2]
        members = tuple(item[2]) if len(item) > 2 else () if zone == "outside" else (zone,)
        points.append(
            Observation(
                i,
                f"synthetic-{i}",
                "123456789",
                BASE + timedelta(minutes=minute),
                0.1 + i * 0.01,
                0.2 + i * 0.01,
                0.5,
                0,
                50,
                (),
                "provider_documented_utc",
                zone,
                members,
            )
        )
    metadata = dict(
        mode="historical",
        start_utc=BASE.isoformat(),
        end_utc=(BASE + timedelta(days=1)).isoformat(),
        source_id="synthetic",
        dataset_id="synthetic_replay",
        revision="synthetic-revision",
        bounds=[0, 0, 1, 1],
        port_name="Synthetic port",
    )
    weather = [
        dict(
            valid_time_utc=BASE + timedelta(hours=h),
            wind_speed_mps=2,
            gusts_mps=3,
            preceding_hour_precipitation_mm=0,
        )
        for h in (0, 1, 2)
    ]
    return ReplayDataset(
        metadata,
        points,
        zones,
        VisitRules(),
        weather,
        [(BASE, BASE + timedelta(days=1))] if coverage else [],
    )


def test_latest_actual_report_no_future_points_weather_or_partial_hour():
    saved = dataset([(0, "outside"), (10, "harbor"), (12, "harbor"), (90, "terminal")])
    frame = saved.frame(BASE + timedelta(minutes=75))
    vessel = frame["vessels"][0]
    assert vessel["longitude"] == pytest.approx(0.12)
    assert vessel["stale"] and vessel["observation_age_seconds"] == 63 * 60
    assert frame["prefix_observations"] == 3
    assert (
        len(frame["chart"]) == 1
        and frame["chart"][0]["hour_end_utc"] == "2024-01-01T01:00:00+00:00"
    )
    assert frame["weather"]["valid_time_utc"] == "2024-01-01T01:00:00+00:00"
    assert frame["news"] == [] and frame["news_status"] == "excluded_recent_source"
    assert all(
        stamp(v["last_observation_utc"]) <= BASE + timedelta(minutes=75) for v in frame["vessels"]
    )


def test_waiting_qualification_and_later_visit_association_are_not_retroactive():
    saved = dataset(
        [
            (0, "outside"),
            (5, "wait"),
            (15, "wait"),
            (25, "wait"),
            (35, "wait"),
            (40, "harbor", ("harbor", "wait")),
            (42, "harbor", ("harbor", "wait")),
            (50, "outside"),
            (52, "outside"),
        ]
    )
    early = saved.frame(BASE + timedelta(minutes=34))
    qualified = saved.frame(BASE + timedelta(minutes=35))
    associated = saved.frame(BASE + timedelta(minutes=42))
    assert early["metrics"]["candidate_waiting"] == 0
    assert qualified["metrics"]["candidate_waiting"] == 1
    assert qualified["vessels"][0]["waiting"] == "regional_candidate"
    assert associated["vessels"][0]["waiting"] == "port_associated_candidate"
    assert associated["metrics"]["completed_dwell_sample_count"] == 0
    assert associated["metrics"]["completed_dwell_median_seconds"] is None
    assert saved.frame(BASE + timedelta(minutes=52))["metrics"]["completed_dwell_sample_count"] == 1
    assert saved.frame(BASE + timedelta(minutes=34)) == early


def test_old_qualified_episode_does_not_qualify_a_new_short_streak():
    saved = dataset(
        [
            (0, "wait"),
            (10, "wait"),
            (20, "wait"),
            (30, "wait"),
            (35, "outside"),
            (40, "wait"),
            (45, "wait"),
        ]
    )
    assert saved.frame(BASE + timedelta(minutes=30))["metrics"]["candidate_waiting"] == 1
    assert saved.frame(BASE + timedelta(minutes=45))["metrics"]["candidate_waiting"] == 0


def test_trails_break_gaps_before_display_downsampling_and_use_actual_points():
    saved = dataset([(0, "harbor"), (1, "harbor"), (20, "harbor"), (21, "harbor")])
    frame = saved.frame(BASE + timedelta(minutes=21), trail_minutes=30)
    assert len(frame["trails"]) == 2
    assert [len(t["points"]) for t in frame["trails"]] == [2, 2]
    points = {p.at.isoformat(): (p.longitude, p.latitude) for p in saved.groups["123456789"]}
    for trail in frame["trails"]:
        assert all(tuple(p[:2]) == points[p[2]] for p in trail["points"])
        assert all(
            (stamp(b[2]) - stamp(a[2])).total_seconds() <= 900
            for a, b in zip(trail["points"], trail["points"][1:], strict=False)
        )


def test_conflicting_latest_coordinates_never_average_or_invent_a_location():
    saved = dataset([(0, "outside"), (10, "harbor"), (10, "outside")])
    frame = saved.frame(BASE + timedelta(minutes=10))
    vessel = frame["vessels"][0]
    assert vessel["ambiguous_position"] and vessel["longitude"] is None
    assert vessel["zone"] == "unknown" and frame["metrics"]["zone_counts"] == {"unknown": 1}
    assert frame["trails"] == []


def test_empty_unknown_period_is_not_zero_occupancy_or_zero_waiting():
    saved = dataset([], coverage=False)
    frame = saved.frame(BASE + timedelta(hours=2))
    assert frame["vessels"] == [] and frame["metrics"]["seen_vessels"] == 0
    assert frame["metrics"]["fresh_port_occupancy"] is None
    assert frame["metrics"]["candidate_waiting"] is None
    assert all(row["observed_vessels"] is None for row in frame["chart"])


@pytest.mark.parametrize(
    "at", ["2024-01-01T00:00:00", "2023-12-31T23:59:00Z", "2024-01-02T00:01:00Z"]
)
def test_replay_rejects_naive_and_outside_period(at):
    with pytest.raises(ReplayError):
        dataset([]).frame(at)


def test_prepared_api_has_no_database_calls_and_masks_bad_inputs(monkeypatch):
    service = ReplayService(Settings(ais_api_key=None))
    service.prepared = dataset([(0, "harbor"), (10, "harbor")])
    monkeypatch.setattr(
        "portwatch.demo.api.connect_db", lambda *_: pytest.fail("prepared mode must not use DB")
    )
    with TestClient(create_app(service=service), raise_server_exceptions=False) as client:
        catalog = client.get("/api/catalog").json()
        assert catalog["prepared_mode"] and not catalog["datasets"][1]["available"]
        url = "/api/frame?at=2024-01-01T00:10:00Z"
        first, second = client.get(url).json(), client.get(url).json()
        assert not first["cache_hit"] and second["cache_hit"]
        assert client.get("/api/health").json()["database"]["status"] == "not_used"
        assert client.get("/api/frame?at=private_synthetic_secret").status_code == 422
        assert (
            "private_synthetic_secret"
            not in client.get("/api/frame?at=private_synthetic_secret").text
        )
        assert client.get("/api/frame?mode=live&at=2024-01-01T00:10:00Z").status_code == 422
        assert client.get(url + "&stale=0").status_code == 422
        assert client.post("/api/frame").status_code == 405


def test_prepared_checksum_damage_is_rejected(tmp_path):
    path = tmp_path / "synthetic-prepared.json"
    path.write_text('{"synthetic":"truncated"}')
    path.with_name(path.name + ".sha256").write_text("0" * 64)
    with pytest.raises(ReplayError, match="checksum"):
        load_prepared(path)


def test_cache_changes_with_revision_and_display_staleness():
    service = ReplayService(Settings(ais_api_key=None))
    service.prepared = dataset([(0, "harbor")])
    at = (BASE + timedelta(minutes=10)).isoformat()
    assert service.frame("historical", at, 15, 30)["metrics"]["fresh_vessels"] == 1
    assert service.frame("historical", at, 5, 30)["metrics"]["fresh_vessels"] == 0
    service.prepared = dataset([(0, "harbor"), (10, "outside")])
    service.prepared.metadata["revision"] = "synthetic-late-revision"
    result = service.frame("historical", at, 15, 30)
    assert not result["cache_hit"] and result["vessels"][0]["zone"] == "outside"


def test_api_masks_unavailable_database_exception_details(capsys):
    from portwatch.events import enable_event_logs

    service = ReplayService(Settings(ais_api_key=None))

    def fail(*_):
        raise RuntimeError("private_synthetic_password")

    service.dataset = fail
    cleanup = enable_event_logs()
    try:
        # Default raise_server_exceptions=True proves no original error escapes to Uvicorn.
        with TestClient(create_app(service=service)) as client:
            response = client.get("/api/catalog")
            assert response.status_code == 503
            assert response.json()["detail"]["error_class"] == "RuntimeError"
            assert "private_synthetic_password" not in response.text
    finally:
        cleanup()
    captured = capsys.readouterr()
    assert "private_synthetic_password" not in captured.err + captured.out
    assert "replay_query_unavailable" in captured.err
