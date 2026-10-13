"""Regression gates: skipped suites and accidental provider networking fail CI."""

import socket
from pathlib import Path

import pytest
import yaml

from scripts.validate import assert_tests_ran


def test_compose_and_ci_match_required_postgres_postgis_versions():
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "compose.yaml").read_text("utf-8"))
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text("utf-8"))
    image = "postgis/postgis:17-3.6-alpine"
    assert compose["services"]["db"]["image"] == image
    assert workflow["jobs"]["integration"]["services"]["postgis"]["image"] == image


@pytest.mark.parametrize(
    "attributes",
    [
        'tests="0" skipped="0" failures="0" errors="0"',
        'tests="1" skipped="1" failures="0" errors="0"',
        'tests="1" skipped="0" failures="1" errors="0"',
        'tests="1" skipped="0" failures="0" errors="1"',
    ],
)
def test_unexecuted_or_failed_suite_rejected(tmp_path, attributes):
    report = tmp_path / "report.xml"
    report.write_text(f"<testsuites><testsuite {attributes}/></testsuites>", encoding="utf-8")
    with pytest.raises(ValueError, match="requires executed tests"):
        assert_tests_ran(report)


def test_executed_suite_accepted(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text('<testsuites><testsuite tests="2"/></testsuites>', encoding="utf-8")
    assert_tests_ran(report)


def test_external_network_blocked_before_lookup_or_connection():
    with pytest.raises(AssertionError, match="only to loopback"):
        socket.getaddrinfo("example.invalid", 443)
    with socket.socket() as connection:
        with pytest.raises(AssertionError, match="only to loopback"):
            connection.connect(("192.0.2.1", 443))
        with pytest.raises(AssertionError, match="only to loopback"):
            connection.connect_ex(("192.0.2.1", 443))
