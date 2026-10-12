"""Typed local settings and port configuration validation."""

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTWATCH_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    db_host: str = Field(default="127.0.0.1", min_length=1)
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = Field(default="portwatch", min_length=1)
    db_user: str = Field(default="portwatch", min_length=1)
    db_password: SecretStr = SecretStr("portwatch_dev_only")
    db_sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = (
        "prefer"
    )
    raw_dir: Path = Path("data/raw")
    derived_dir: Path = Path("data/derived")
    demo_config: Path = Path("configs/demo.yaml")
    quarantine_dir: Path = Path("data/quarantine")
    port_config: Path = Path("configs/ports/los_angeles.yaml")
    ais_api_key: SecretStr | None = None
    live_spool_dir: Path = Path("data/live-spool")
    live_status_dir: Path = Path("artifacts/live")
    live_queue_records: int = Field(default=128, ge=1, le=1024)
    live_rotate_seconds: float = Field(default=30, ge=1, le=300)
    live_rotate_bytes: int = Field(default=1048576, ge=262144, le=16777216)
    live_flush_seconds: float = Field(default=1, gt=0, le=5)

    @field_validator("db_host", "db_name", "db_user")
    @classmethod
    def nonblank_db_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("database setting must not be blank")
        return value

    @field_validator("db_password")
    @classmethod
    def nonblank_db_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("database password must not be blank")
        return value

    def project_path(self, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path


class PortConfig(BaseModel):
    port_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=1)
    timezone: str
    bounding_box: tuple[float, float, float, float] | None
    geofence_path: str | None
    waiting_speed_knots: float = Field(ge=0)
    waiting_min_minutes: int = Field(gt=0)
    max_observation_gap_minutes: int = Field(gt=0)
    visit_persistence_minutes: float = Field(default=2, gt=0)
    visit_min_consistent_observations: int = Field(default=2, ge=2, le=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("geofence_path")
    @classmethod
    def valid_geofence_path(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("geofence_path must be null or a nonempty path")
        return value

    @model_validator(mode="after")
    def valid_bounding_box(self) -> "PortConfig":
        if self.bounding_box is not None:
            west, south, east, north = self.bounding_box
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ValueError("bounding_box must be [west, south, east, north] in degrees")
        return self


def load_port_config(path: Path) -> PortConfig:
    with path.open(encoding="utf-8") as stream:
        config = PortConfig.model_validate(yaml.safe_load(stream))
    if config.geofence_path is not None:
        geofence = Path(config.geofence_path)
        if not geofence.is_absolute():
            geofence = PROJECT_ROOT / geofence
        if not geofence.is_file():
            raise ValueError("configured geofence_path does not point to a file")
    return config
