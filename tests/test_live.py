"""Synthetic Step 04 frames and local WebSocket servers; no external subscription."""

import asyncio
import base64
import json
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from portwatch.cli import app
from portwatch.live import CaptureError, LiveCollector, subscription
from portwatch.live_spool import LiveEnvelope, LiveSpool, collector_lock
from portwatch.settings import Settings
from portwatch.storage import BatchSpec, RawStore

BOUNDS = (-118.34, 33.66, -118.225, 33.80)
CONFIRMATION = json.dumps(
    {
        "MessageType": "SubscriptionConfirmation",
        "Message": {"CompressionEnabled": True},
    }
).encode()
SYNTHETIC_POSITION = {
    "MessageType": "PositionReport",
    "MetaData": {"MMSI": 999000001, "time_utc": "2026-10-11 12:00:00.123456 +0000 UTC"},
    "Message": {"PositionReport": {"Latitude": 33.72, "Longitude": -118.26, "Valid": True}},
}
POSITION_FRAME = json.dumps(SYNTHETIC_POSITION, indent=2).encode()


def synthetic_spec() -> BatchSpec:
    return BatchSpec(
        source_id="synthetic_step04",
        dataset_id="synthetic_live_fixture",
        source_url="wss://example.invalid/synthetic",
        mode="live",
        file_suffix=".jsonl",
        processing_version="live-envelope-v1",
    )


def make_collector(tmp_path: Path, **kwargs) -> LiveCollector:
    return LiveCollector(
        SecretStr("synthetic_step04_secret"),
        BOUNDS,
        synthetic_spec(),
        RawStore(tmp_path / "raw"),
        tmp_path / "active",
        tmp_path / "status.json",
        **{
            "flush_seconds": 0.02,
            "ping_interval": 0.1,
            "ping_timeout": 1,
            "backoff_base": 0.01,
            "backoff_max": 0.04,
            **kwargs,
        },
    )


def saved_records(collector: LiveCollector) -> list[LiveEnvelope]:
    records = []
    for item in collector.spool.store.inspect():
        assert item.state == "complete"
        path = collector.spool.store._path(item.manifest.local_path)
        records.extend(
            LiveEnvelope.model_validate_json(line) for line in path.read_bytes().splitlines()
        )
    return records


async def wait_received(collector, count):
    """Wait for the condition under test, independent of Windows I/O scheduling."""
    async with asyncio.timeout(3):
        while collector.status.received_records < count:
            await asyncio.sleep(0.01)


def run_server(tmp_path, handler, duration=1, **kwargs):
    async def scenario():
        async with serve(
            handler, "127.0.0.1", 0, compression="deflate", ping_interval=None
        ) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            collector = make_collector(tmp_path, uri=uri, **kwargs)
            try:
                await asyncio.wait_for(collector.run(duration=duration), timeout=3)
            except CaptureError as error:
                return collector, error.code
            return collector, None

    return asyncio.run(scenario())


@pytest.mark.parametrize("text_frames", [False, True])
def test_protocol_original_frames_metrics_and_graceful_duration(tmp_path, text_frames):
    received_subscriptions = []
    static = json.dumps(
        {
            "MessageType": "StaticDataReport",
            "MetaData": {"MMSI": 999000002},
            "Message": {"StaticDataReport": {"ReportA": {"Name": "SYNTHETIC"}}},
        }
    ).encode()
    frames = [CONFIRMATION, POSITION_FRAME, static, b'{"broken"', b"\xff\xfe"]

    async def handler(socket):
        received_subscriptions.append(json.loads(await socket.recv()))
        for frame in frames:
            await socket.send(frame.decode() if text_frames and frame != b"\xff\xfe" else frame)
        await socket.wait_closed()

    # Real Windows flush/status I/O can exceed a subsecond test capture deadline.
    collector, error = run_server(tmp_path, handler, duration=1)
    assert error is None
    assert received_subscriptions[0]["BoundingBoxes"] == [[[33.66, -118.34], [33.80, -118.225]]]
    assert set(received_subscriptions[0]["FilterMessageTypes"]) == {
        "PositionReport",
        "StandardClassBPositionReport",
        "ExtendedClassBPositionReport",
        "LongRangeAisBroadcastMessage",
        "ShipStaticData",
        "StaticDataReport",
    }
    records = sorted(saved_records(collector), key=lambda record: record.record_sequence)
    assert [base64.b64decode(record.payload_base64) for record in records] == frames
    assert len({record.connection_session for record in records}) == 1
    assert [record.record_sequence for record in records] == [1, 2, 3, 4, 5]
    assert records[1].source_timestamp == SYNTHETIC_POSITION["MetaData"]["time_utc"]
    report = collector.snapshot()
    assert report["observations"] == 1 and report["static_messages"] == 1
    assert report["parse_errors"] == 2 and report["control_messages"] == 1
    assert report["distinct_vessels"] == 2 and report["received_records"] == 5
    assert report["finalized_records"] == 5 and report["unfinalized_received_records"] == 0
    assert report["last_socket_heartbeat"] and report["last_ais_observation_at"]
    assert report["state"] == "stopped" and report["stop_reason"] == "duration"
    assert not list((tmp_path / "active").glob("*.active"))
    assert "synthetic_step04_secret" not in (tmp_path / "status.json").read_text()
    for path in (tmp_path / "raw").rglob("*"):
        if path.is_file():
            assert b"synthetic_step04_secret" not in path.read_bytes()


def test_reconnects_keep_distinct_sessions_and_reset_stable_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr("portwatch.live.random.uniform", lambda *_: 1)
    connections = 0

    async def handler(socket):
        nonlocal connections
        connections += 1
        await socket.recv()
        await socket.send(CONFIRMATION)
        await socket.send(POSITION_FRAME)
        if connections == 2:
            await asyncio.sleep(0.3)  # Exceeds test stable-connection threshold.
        if connections <= 2:
            await socket.close(code=1011)
        else:
            await socket.wait_closed()

    # This test targets reconnect/backoff, not sub-30ms heartbeat timing on a busy laptop.
    collector, error = run_server(
        tmp_path, handler, duration=2, stable_seconds=0.15, ping_interval=0.1, ping_timeout=0.5
    )
    assert error is None and connections == 3
    assert collector.status.reconnect_count == 2
    assert collector.status.last_backoff_seconds == pytest.approx(0.01)
    records = saved_records(collector)
    assert len({record.connection_session for record in records}) == 3
    assert collector.status.observations == 3


def test_heartbeat_cancellation_survives_simultaneous_completed_pong(tmp_path):
    async def scenario():
        collector = make_collector(tmp_path, ping_interval=0.001, ping_timeout=1)
        task = None

        class Socket:
            pings = 0

            async def ping(self):
                self.pings += 1
                pong = asyncio.get_running_loop().create_future()
                if self.pings == 1:
                    asyncio.get_running_loop().call_soon(task.cancel)

                    def complete():
                        if not pong.done():
                            pong.set_result(None)

                    asyncio.get_running_loop().call_soon(complete)
                return pong

            async def close(self):
                pass

        socket = Socket()
        task = asyncio.create_task(collector._heartbeat(socket))
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)
        assert socket.pings == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "frame,expected",
    [
        (b'{"error":"synthetic authentication rejection"}', "subscription_rejected"),
        (
            b'{"MessageType":"SubscriptionConfirmation","Message":{"CompressionEnabled":false}}',
            "compression_not_confirmed",
        ),
        (
            b'{"MessageType":"SubscriptionConfirmation","Message":{}}',
            "invalid_subscription_confirmation",
        ),
    ],
)
def test_protocol_configuration_failures_stop_without_retry(tmp_path, frame, expected):
    async def handler(socket):
        await socket.recv()
        await socket.send(frame)
        await socket.wait_closed()

    collector, error = run_server(tmp_path, handler)
    assert error == expected and collector.status.reconnect_count == 0
    assert len(saved_records(collector)) == 1
    assert collector.status.state == "failed"


def test_confirmation_timeout_is_bounded_and_empty_is_not_zero_vessels(tmp_path):
    async def handler(socket):
        await socket.recv()
        await socket.wait_closed()

    collector, error = run_server(tmp_path, handler, confirmation_timeout=0.03)
    assert error == "subscription_confirmation_timeout"
    assert collector.status.received_records == 0
    assert collector.status.connections == 1 and collector.status.reconnect_count == 0
    assert saved_records(collector) == []


def test_buffer_pressure_reports_and_persists_held_record(tmp_path):
    async def handler(socket):
        await socket.recv()
        with suppress(ConnectionClosed):
            await socket.send(CONFIRMATION)
            for _ in range(20):
                await socket.send(POSITION_FRAME)
        await socket.wait_closed()

    original_append = LiveSpool.append

    def slow_append(self, record):
        time.sleep(0.04)
        return original_append(self, record)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(LiveSpool, "append", slow_append)
        collector, error = run_server(tmp_path, handler, queue_records=1)
    assert error == "buffer_pressure" and collector.status.pressure_events == 1
    assert collector.status.queue_high_water <= 1
    assert len(saved_records(collector)) == collector.status.received_records
    assert collector.snapshot()["unfinalized_received_records"] == 0


def test_storage_failure_stops_reader_and_retains_active_recovery(tmp_path, monkeypatch):
    def fail_register(_batch):
        raise OSError("synthetic_step04_secret")

    async def handler(socket):
        await socket.recv()
        await socket.send(CONFIRMATION)
        await socket.send(POSITION_FRAME)
        await socket.wait_closed()

    collector, error = run_server(tmp_path, handler, register=fail_register, rotate_seconds=0.03)
    assert error == "raw_persistence_failure"
    assert collector.status.raw_persistence_failures == 1
    assert list((tmp_path / "active").glob("*.active"))
    report = json.loads((tmp_path / "status.json").read_text())
    assert report["unfinalized_received_records"] > 0
    assert "synthetic_step04_secret" not in json.dumps(report)
    collector.spool.register = None
    recovered = collector.spool.recover()
    assert sum(item.complete_records for item in recovered) == collector.status.received_records
    assert len(saved_records(collector)) == collector.status.received_records


def test_stop_event_drains_queue_and_finalizes(tmp_path):
    async def scenario():
        stop = asyncio.Event()

        async def handler(socket):
            await socket.recv()
            await socket.send(CONFIRMATION)
            await socket.send(POSITION_FRAME)
            await wait_received(collector, 2)
            stop.set()
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            collector = make_collector(
                tmp_path, uri=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            )
            await asyncio.wait_for(collector.run(stop_event=stop), timeout=2)
            assert collector.status.stop_reason == "requested"
            assert len(saved_records(collector)) == 2

    asyncio.run(scenario())


def test_task_cancellation_behaves_like_graceful_ctrl_c(tmp_path):
    async def scenario():
        async def handler(socket):
            await socket.recv()
            await socket.send(CONFIRMATION)
            await socket.send(POSITION_FRAME)
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            collector = make_collector(
                tmp_path, uri=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            )
            task = asyncio.create_task(collector.run())
            await wait_received(collector, 2)
            task.cancel()
            await asyncio.wait_for(task, timeout=2)
            assert collector.status.stop_reason == "interrupted"
            assert len(saved_records(collector)) == 2

    asyncio.run(scenario())


def envelope(spec, sequence=1, received_at=None):
    return LiveEnvelope(
        source_id=spec.source_id,
        dataset_id=spec.dataset_id,
        connection_session=uuid4(),
        received_at=received_at or datetime.now(UTC),
        record_sequence=sequence,
        kind="ais_observation",
        frame_type="binary",
        payload_base64=base64.b64encode(POSITION_FRAME).decode(),
    )


@pytest.mark.parametrize(
    "tail,expected_records,tail_bytes",
    [
        (b'{"partial":', 1, 11),
        (b"", 1, 0),
        (None, 2, 0),
    ],
)
def test_interrupted_spool_recovery_preserves_complete_records_and_reports_tail(
    tmp_path,
    tail,
    expected_records,
    tail_bytes,
):
    spec = synthetic_spec()
    spool = LiveSpool(tmp_path / "active", RawStore(tmp_path / "raw"), spec)
    record = envelope(spec)
    spool.append(record)
    spool.stream.flush()
    spool.stream.close()
    active = spool.path
    prefix = active.read_bytes()
    if tail is None:
        tail = envelope(spec, sequence=2).model_dump_json().encode()  # Valid final JSON without LF.
    with active.open("ab") as stream:
        stream.write(tail)
    spool.stream = None  # Simulates abrupt process termination after active writes.
    recovered = spool.recover()
    assert len(recovered) == 1
    assert recovered[0].complete_records == expected_records
    assert recovered[0].truncated_tail_bytes == tail_bytes
    raw = spool.store._path(recovered[0].batch.local_path).read_bytes()
    assert raw == prefix if tail_bytes else raw == prefix + tail
    assert spool.recover() == []
    if tail_bytes:
        assert active.with_suffix(".tail").read_bytes() == tail
        assert active.with_suffix(".tail.meta").exists()


def test_complete_corrupt_active_record_is_not_silently_skipped(tmp_path):
    spool = LiveSpool(tmp_path / "active", RawStore(tmp_path / "raw"), synthetic_spec())
    spool.append(envelope(spool.spec))
    spool.stream.close()
    spool.stream = None
    with spool.path.open("ab") as stream:
        stream.write(b'{"not":"an envelope"}\n')
    with pytest.raises(ValueError):
        spool.recover()
    assert spool.path.exists() and spool.store.inspect() == []


def test_rotation_by_size_time_hour_and_frequent_durable_flush(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("portwatch.live_spool.time.monotonic", lambda: clock[0])
    spec = synthetic_spec()
    spool = LiveSpool(
        tmp_path / "active",
        RawStore(tmp_path / "raw"),
        spec,
        rotate_seconds=0.03,
        rotate_bytes=900,
        flush_seconds=0.01,
    )
    flushes = []
    monkeypatch.setattr("portwatch.live_spool.os.fsync", lambda *_: flushes.append(True))
    first = envelope(spec, received_at=datetime(2026, 10, 11, 12, 59, 59, tzinfo=UTC))
    spool.append(first)
    assert spool.append(envelope(spec, sequence=2, received_at=first.received_at))  # Size.
    assert spool.append(
        envelope(spec, sequence=3, received_at=datetime(2026, 10, 11, 13, tzinfo=UTC))
    )  # Hour.
    clock[0] += 0.015
    before_flush = len(flushes)
    assert spool.tick() == [] and len(flushes) > before_flush
    clock[0] += 0.02
    assert spool.tick()  # Short interval rotation.
    assert len(spool.store.inspect()) == 3


def test_single_collector_lock_and_secret_safe_missing_key_cli(tmp_path, monkeypatch):
    with collector_lock(tmp_path):
        with pytest.raises(RuntimeError, match="collector_already_running"):
            with collector_lock(tmp_path):
                pass
    monkeypatch.setattr("portwatch.cli.Settings", lambda: Settings(_env_file=None))
    monkeypatch.delenv("PORTWATCH_AIS_API_KEY", raising=False)
    result = CliRunner().invoke(app, ["ingest", "live", "--port", "los_angeles", "--duration", "1"])
    assert result.exit_code == 2
    assert (
        "PORTWATCH_AIS_API_KEY" in result.output
        and "Historical mode remains available" in result.output
    )
    assert "https://aisstream.io/account" in result.output


def test_subscription_key_never_in_repr_and_invalid_limits_rejected(tmp_path):
    key = SecretStr("synthetic_step04_secret")
    assert "synthetic_step04_secret" not in repr(key)
    assert subscription(BOUNDS, key)["BoundingBoxes"] == [[[33.66, -118.34], [33.80, -118.225]]]
    with pytest.raises(CaptureError, match="invalid_collector_limits"):
        make_collector(tmp_path, queue_records=0)


def test_credential_echo_is_refused_without_logging_or_persisting_it(tmp_path):
    async def handler(socket):
        await socket.recv()
        await socket.send(b'{"error":"synthetic_step04_secret"}')
        await socket.wait_closed()

    collector, error = run_server(tmp_path, handler)
    assert error == "credential_echo_refused"
    assert saved_records(collector) == []
    assert collector.snapshot()["unfinalized_received_records"] == 1
    assert "synthetic_step04_secret" not in (tmp_path / "status.json").read_text()


def test_deep_json_parse_error_is_preserved_and_capture_continues(tmp_path):
    deep = b'{"synthetic_nested":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"

    async def handler(socket):
        await socket.recv()
        await socket.send(CONFIRMATION)
        await socket.send(deep)
        await socket.send(POSITION_FRAME)
        await socket.wait_closed()

    collector, error = run_server(tmp_path, handler)
    assert error is None and collector.status.parse_errors == 1
    assert collector.status.observations == 1
    assert any(
        base64.b64decode(record.payload_base64) == deep for record in saved_records(collector)
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        (1008, "socket_policy_or_protocol_failure"),
        (1000, "subscription_not_confirmed"),
    ],
)
def test_early_policy_or_auth_close_is_actionable_without_retries(tmp_path, code, expected):
    async def handler(socket):
        await socket.recv()
        await socket.close(code=code)

    collector, error = run_server(tmp_path, handler)
    assert error == expected and collector.status.reconnect_count == 0


def test_periodic_status_updates_during_connected_idle_capture(tmp_path):
    async def scenario():
        stop = asyncio.Event()

        async def handler(socket):
            await socket.recv()
            await socket.send(CONFIRMATION)
            await socket.send(POSITION_FRAME)
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            collector = make_collector(
                tmp_path,
                uri=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                status_interval=0.02,
            )
            task = asyncio.create_task(collector.run(stop_event=stop))
            try:
                async with asyncio.timeout(3):
                    while True:
                        await asyncio.sleep(0.01)
                        if collector.status_path.exists():
                            report = json.loads(collector.status_path.read_text())
                            if report["observations"] == 1 and report["last_socket_heartbeat"]:
                                break
                assert report["connected"] and report["state"] == "subscribed"
            finally:
                stop.set()
                await asyncio.wait_for(task, timeout=3)

    asyncio.run(scenario())


def test_cancelled_recovery_joins_worker_before_releasing_collector_lock(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_recover(self):
        entered.set()
        assert release.wait(2)
        return []

    monkeypatch.setattr(LiveSpool, "recover", slow_recover)

    async def scenario():
        collector = make_collector(tmp_path)
        task = asyncio.create_task(collector.run())
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        with pytest.raises(RuntimeError, match="collector_already_running"):
            with collector_lock(collector.spool.root):
                pass
        release.set()
        await asyncio.wait_for(task, timeout=2)
        assert collector.status.stop_reason == "interrupted"
        assert collector.status.connections == 0
        with collector_lock(collector.spool.root):
            pass

    asyncio.run(scenario())


def test_status_publication_serializes_snapshots_and_joins_cancelled_worker(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original_replace = __import__("os").replace
    calls = 0

    def delayed_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(2)
        return original_replace(source, target)

    monkeypatch.setattr("portwatch.live.os.replace", delayed_replace)

    async def scenario():
        collector = make_collector(tmp_path)
        first = asyncio.create_task(collector.publish_status())
        assert await asyncio.to_thread(entered.wait, 1)
        collector.status.state = "subscribed"
        second = asyncio.create_task(collector.publish_status())
        first.cancel()
        await asyncio.sleep(0.02)
        assert not first.done() and calls == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert json.loads(collector.status_path.read_text())["state"] == "subscribed"

    asyncio.run(scenario())
