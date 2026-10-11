"""Regression gates: skipped suites and accidental provider networking fail CI."""

import socket

import pytest

from scripts.validate import assert_tests_ran


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
