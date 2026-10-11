"""Shared, explicitly synthetic acquisition metadata."""

import socket
from datetime import UTC, date, datetime
from ipaddress import ip_address

import pytest

from portwatch.storage import BatchSpec


@pytest.fixture(autouse=True)
def loopback_network_only(monkeypatch):
    """Fixtures may use local HTTP/WebSockets, never real provider endpoints."""
    original_lookup = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check_host(host):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host is None or host == "localhost":
            return
        try:
            allowed = ip_address(host).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise AssertionError("deterministic tests may connect only to loopback addresses")

    def lookup(host, *args, **kwargs):
        check_host(host)
        return original_lookup(host, *args, **kwargs)

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            check_host(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            check_host(address[0])
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture
def spec() -> BatchSpec:
    return BatchSpec(
        source_id="synthetic_fixture",
        dataset_id="synthetic_step02",
        source_url="https://example.invalid/synthetic.csv",
        mode="historical",
        source_date=date(2024, 1, 1),
        received_at=datetime(2026, 10, 11, tzinfo=UTC),
        record_count=2,
        file_suffix=".csv",
    )
