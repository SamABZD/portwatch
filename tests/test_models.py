"""Public dbt outcome projection and validation before any external process/database work."""

import json

import pytest
from pydantic import SecretStr

from portwatch.models import dbt_environment, result_summary, run_models
from portwatch.pipeline import PipelineError
from portwatch.settings import Settings


def test_dbt_environment_uses_redacted_secret_and_excludes_live_key(monkeypatch):
    monkeypatch.setenv("PORTWATCH_AIS_API_KEY", "synthetic_live_key")
    monkeypatch.setenv("PORTWATCH_DB_PASSWORD", "synthetic_db_secret")
    settings = Settings(db_password=SecretStr("synthetic_db_secret"))
    env = dbt_environment(settings, "pw_dbt_synthetic")
    assert "PORTWATCH_AIS_API_KEY" not in env
    assert "PORTWATCH_DB_PASSWORD" not in env
    assert env["DBT_ENV_SECRET_PORTWATCH_PASSWORD"] == "synthetic_db_secret"
    assert env["PORTWATCH_DBT_SCHEMA"] == "pw_dbt_synthetic"
    assert "timezone=UTC" in env["PGOPTIONS"]
    assert env["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"


def test_dbt_summary_omits_error_bodies_and_compiled_sql(tmp_path):
    path = tmp_path / "run_results.json"
    path.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "unique_id": "test.synthetic",
                        "status": "fail",
                        "execution_time": 0.25,
                        "message": "synthetic_secret",
                        "compiled_code": "private_connection_details",
                    }
                ]
            }
        )
    )
    assert result_summary(path) == [{"node": "test.synthetic", "status": "fail", "seconds": 0.25}]
    path.write_text('{"results":[]}')
    with pytest.raises(PipelineError, match="dbt_no_results"):
        result_summary(path)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2024-01-01", "2024-01-09"),
        ("2024-01-02", "2024-01-01"),
        ("2026-01-01", "2026-01-02"),
        ("2024-1-1", "2024-01-02"),
    ],
)
def test_model_range_is_bounded_before_connecting(start, end):
    with pytest.raises(ValueError):
        run_models(Settings(), "los_angeles", start, end)


def test_models_missing_environment_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTWATCH_DBT_PYTHON", str(tmp_path / "missing.exe"))
    with pytest.raises(PipelineError, match="dbt_environment_missing"):
        run_models(Settings(), "los_angeles", "2024-01-01", "2024-01-02")
