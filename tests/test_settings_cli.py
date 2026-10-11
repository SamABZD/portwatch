from pathlib import Path

import pytest
from typer.testing import CliRunner

from portwatch.cli import app
from portwatch.settings import PROJECT_ROOT, PortConfig, Settings, load_port_config


def test_settings_are_typed_and_paths_resolve_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTWATCH_DB_PORT", "5544")
    monkeypatch.setenv("PORTWATCH_RAW_DIR", "data/raw")
    settings = Settings()
    assert settings.db_port == 5544
    assert settings.project_path(settings.raw_dir) == PROJECT_ROOT / "data/raw"


def test_los_angeles_config_has_collection_bounds_without_geofence() -> None:
    config = load_port_config(PROJECT_ROOT / "configs/ports/los_angeles.yaml")
    assert isinstance(config, PortConfig)
    assert config.port_id == "los_angeles"
    assert config.bounding_box == (-118.34, 33.66, -118.225, 33.80)
    assert config.geofence_path is None


def test_blank_db_password_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTWATCH_DB_PASSWORD", " ")
    with pytest.raises(ValueError, match="db_password"):
        Settings()


def test_rejects_invalid_bounding_box(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """port_id: los_angeles
display_name: Port of Los Angeles
timezone: America/Los_Angeles
bounding_box: [5, 0, -5, 1]
geofence_path: null
waiting_speed_knots: 1.0
waiting_min_minutes: 30
max_observation_gap_minutes: 15
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bounding_box"):
        load_port_config(path)


def test_doctor_exits_nonzero_and_never_prints_connection_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PORTWATCH_DB_PASSWORD", "very_private_test_value")
    monkeypatch.setenv("PORTWATCH_RAW_DIR", str(tmp_path / "raw"))
    # Explicitly override ignored local .env when the developer has a real key.
    monkeypatch.setenv("PORTWATCH_AIS_API_KEY", "")

    def fail_connect(**_kwargs: object) -> None:
        raise OSError("very_private_test_value")

    monkeypatch.setattr("portwatch.db.psycopg.connect", fail_connect)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[PASS] Port config" in result.stdout
    assert "[PASS] Raw directory" in result.stdout
    assert "[FAIL] Database" in result.stdout
    assert "[SKIP] PostGIS" in result.stdout
    assert "[OPTIONAL] Live AIS key" in result.stdout
    assert "very_private_test_value" not in result.stdout
