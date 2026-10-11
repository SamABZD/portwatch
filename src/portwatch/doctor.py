"""Checks for the reproducible local development environment."""

import sys
import tempfile
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

import psycopg
import yaml
from pydantic import ValidationError

from portwatch.db import connect_db
from portwatch.settings import PROJECT_ROOT, Settings, load_port_config


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str
    required: bool = True


def diagnose() -> list[Check]:
    checks: list[Check] = []

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_ok = (3, 11) <= sys.version_info[:2] < (3, 15)
    checks.append(Check("PASS" if python_ok else "FAIL", "Python", python_version))

    try:
        package_version = version("portwatch")
        checks.append(Check("PASS", "Package", f"portwatch {package_version} installed"))
    except PackageNotFoundError:
        checks.append(Check("FAIL", "Package", "portwatch distribution is not installed"))

    settings: Settings | None
    try:
        settings = Settings()
        checks.append(Check("PASS", "Settings", "environment configuration is valid"))
    except ValidationError:
        settings = None
        checks.append(Check("FAIL", "Settings", "invalid environment configuration"))

    config_path = (
        settings.project_path(settings.port_config)
        if settings is not None
        else PROJECT_ROOT / "configs/ports/los_angeles.yaml"
    )
    try:
        config = load_port_config(config_path)
        geography = (
            "collection bounds pending"
            if config.bounding_box is None
            else "collection bounds configured; analytical geofence "
            + ("pending" if config.geofence_path is None else "configured")
        )
        checks.append(Check("PASS", "Port config", f"{config.port_id}; {geography}"))
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        checks.append(Check("FAIL", "Port config", f"invalid or unreadable ({type(exc).__name__})"))

    raw_dir = settings.project_path(settings.raw_dir) if settings else PROJECT_ROOT / "data/raw"
    try:
        raw_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=raw_dir, prefix=".doctor-", delete=True):
            pass
        checks.append(Check("PASS", "Raw directory", f"writable: {raw_dir}"))
    except OSError as exc:
        checks.append(Check("FAIL", "Raw directory", f"not writable ({type(exc).__name__})"))

    if settings is None:
        checks.append(Check("SKIP", "Database", "settings invalid"))
        checks.append(Check("SKIP", "PostGIS", "database not checked"))
        checks.append(Check("SKIP", "Live AIS key", "settings invalid", required=False))
        return checks

    try:
        with connect_db(settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise RuntimeError("database query returned an unexpected result")
                checks.append(Check("PASS", "Database", "SELECT 1 succeeded"))
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
                postgis_version = cursor.fetchone()
                if postgis_version and postgis_version[0]:
                    checks.append(
                        Check("PASS", "PostGIS", f"extension {postgis_version[0]} available")
                    )
                else:
                    checks.append(Check("FAIL", "PostGIS", "extension is not installed"))
    except (psycopg.Error, OSError, RuntimeError) as exc:
        checks.append(Check("FAIL", "Database", f"connection/query failed ({type(exc).__name__})"))
        checks.append(Check("SKIP", "PostGIS", "database unavailable"))

    key_present = settings.ais_api_key is not None and bool(
        settings.ais_api_key.get_secret_value().strip()
    )
    checks.append(
        Check(
            "PASS" if key_present else "OPTIONAL",
            "Live AIS key",
            "configured" if key_present else "missing; historical demo remains available",
            required=False,
        )
    )
    return checks


def required_checks_pass(checks: list[Check]) -> bool:
    return all(check.status == "PASS" for check in checks if check.required)
