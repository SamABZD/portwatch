"""Port isolation and immutable source reuse, with synthetic inputs only."""

import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from portwatch.demo.api import ReplayService, create_app
from portwatch.download import acquire
from portwatch.settings import Settings, for_port, load_port_config
from portwatch.storage import RawStore
from tests.test_replay import BASE, dataset


def test_config_selection_roundtrip_and_nonexistent_port():
    settings = Settings()
    oakland = for_port(settings, "oakland")
    assert load_port_config(oakland.project_path(oakland.port_config)).port_id == "oakland"
    restored = for_port(oakland, "los_angeles")
    assert restored.demo_config == settings.demo_config
    assert settings.port_config != oakland.port_config
    for invalid in ("../oakland", "missing_port", "OAKLAND"):
        with pytest.raises(ValueError):
            for_port(settings, invalid)


def test_identical_national_source_reuses_bytes_without_network(tmp_path, spec):
    store = RawStore(tmp_path)
    parent = store.write([b"synthetic national data"], spec)
    alternate = spec.model_copy(update={"dataset_id": "synthetic_other_port"})
    result = acquire(store, alternate)
    assert not result.downloaded
    assert result.manifest.batch_id != parent.batch_id
    assert result.manifest.content_sha256 == parent.content_sha256
    assert os.path.samefile(store._path(parent.local_path), store._path(result.manifest.local_path))
    assert acquire(store, alternate).manifest == result.manifest
    assert all(item.state == "complete" for item in store.inspect())
    with pytest.raises(ValueError):
        store.reuse_historical(
            parent, alternate.model_copy(update={"source_url": "https://example.invalid/different"})
        )


def test_profile_availability_never_leaks_future_properties():
    replay = dataset([(0, "harbor"), (10, "harbor")])
    vessel = next(iter(replay.groups))
    replay.profile_groups[vessel] = [
        (BASE, {"name": "Synthetic vessel"}),
        (BASE + timedelta(minutes=10), {"destination": "Synthetic future port"}),
    ]
    early = replay.frame(BASE + timedelta(minutes=5))["vessels"][0]
    assert early["reported_profile"] == {"name": "Synthetic vessel"}
    later = replay.frame(BASE + timedelta(minutes=10))["vessels"][0]
    assert later["reported_profile"]["destination"] == "Synthetic future port"


def test_unknown_http_port_is_rejected_before_database_query(monkeypatch):
    service = ReplayService(Settings())
    monkeypatch.setattr(
        "portwatch.demo.api.connect_db", lambda *_: pytest.fail("unknown port must not query")
    )
    with TestClient(create_app(service=service)) as client:
        for endpoint in ("catalog", "health", "frame"):
            assert client.get(f"/api/{endpoint}?port=unknown_port").status_code == 404


def test_prepared_snapshot_cannot_claim_another_port(monkeypatch):
    service = ReplayService(Settings())
    service.prepared = dataset([(0, "harbor")])
    service.prepared.metadata["port_id"] = "los_angeles"
    monkeypatch.setattr("portwatch.demo.api.connect_db", lambda *_: pytest.fail("prepared DB call"))
    with TestClient(create_app(service=service)) as client:
        assert client.get("/api/health?port=oakland").status_code == 422
        assert client.get("/api/frame?port=oakland").status_code == 422
        report = client.get("/api/pipeline").json()
        assert [p["port_id"] for p in report["ports"]] == ["los_angeles"]


def test_frame_cache_remains_separate_for_each_port(monkeypatch):
    left = dataset([(0, "harbor"), (10, "harbor")])
    right = dataset([(0, "harbor")])
    left.metadata["revision"], right.metadata["revision"] = "synthetic-left", "synthetic-right"
    service = ReplayService(Settings())
    monkeypatch.setattr(
        service, "dataset", lambda mode, port=None: left if port == "los_angeles" else right
    )
    assert (
        service.frame("historical", BASE + timedelta(minutes=10), 15, 30, "los_angeles")[
            "prefix_observations"
        ]
        == 2
    )
    assert (
        service.frame("historical", BASE + timedelta(minutes=10), 15, 30, "oakland")[
            "prefix_observations"
        ]
        == 1
    )
    assert service.frame("historical", BASE + timedelta(minutes=10), 15, 30, "los_angeles")[
        "cache_hit"
    ]
