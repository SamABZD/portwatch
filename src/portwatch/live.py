"""Independent AISStream collector with bounded buffering and durable raw spooling."""

import asyncio
import base64
import json
import logging
import math
import os
import random
import tempfile
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psutil
from pydantic import SecretStr
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from portwatch.events import emit
from portwatch.live_spool import MAX_FRAME_BYTES, LiveEnvelope, LiveSpool, collector_lock
from portwatch.settings import Settings, load_port_config
from portwatch.storage import BatchSpec, Manifest, RawStore

STREAM_URL = "wss://stream.aisstream.io/v0/stream"
POSITION_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "LongRangeAisBroadcastMessage",
)
STATIC_TYPES = ("ShipStaticData", "StaticDataReport")
MAX_VESSELS = 50_000


class CaptureError(RuntimeError):
    """Trusted reason code only; never transport exception messages or server bodies."""

    def __init__(self, code: str, pending: LiveEnvelope | None = None):
        super().__init__(code)
        self.code, self.pending = code, pending


def live_spec(settings: Settings, port_id: str) -> tuple[BatchSpec, tuple]:
    port = load_port_config(settings.project_path(settings.port_config))
    if port_id != "los_angeles" or port_id != port.port_id or port.bounding_box is None:
        raise CaptureError("invalid_port_configuration")
    return BatchSpec(
        source_id="aisstream_v0",
        dataset_id=f"{port_id}_live_aisstream_v1",
        source_url=STREAM_URL,
        mode="live",
        file_suffix=".jsonl",
        processing_version="live-envelope-v1",
    ), port.bounding_box


def subscription(bounds: tuple, key: SecretStr) -> dict:
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise CaptureError("invalid_port_configuration")
    return {
        "APIKey": key.get_secret_value(),
        # Internal west/south/east/north becomes provider latitude/longitude corners.
        "BoundingBoxes": [[[south, west], [north, east]]],
        "FilterMessageTypes": [*POSITION_TYPES, *STATIC_TYPES],
    }


@dataclass
class LiveStatus:
    state: str = "starting"
    connected: bool = False
    subscription_confirmed: bool = False
    compression_enabled: bool | None = None
    started_at: str | None = None
    ended_at: str | None = None
    elapsed_seconds: float = 0
    last_socket_heartbeat: str | None = None
    last_socket_message_at: str | None = None
    last_ais_observation_at: str | None = None
    last_source_timestamp: str | None = None
    received_records: int = 0
    observations: int = 0
    static_messages: int = 0
    control_messages: int = 0
    unknown_messages: int = 0
    parse_errors: int = 0
    reconnect_count: int = 0
    connections: int = 0
    queue_high_water: int = 0
    pressure_events: int = 0
    raw_persistence_failures: int = 0
    written_records: int = 0
    finalized_records: int = 0
    finalized_batches: int = 0
    recovered_records: int = 0
    recovered_files: int = 0
    truncated_tail_bytes: int = 0
    last_backoff_seconds: float = 0
    failure_code: str | None = None
    stop_reason: str | None = None


def _utc() -> str:
    return datetime.now(UTC).isoformat()


async def _thread_join(function, *args):
    """Cancellation must not leave a filesystem worker running after lock release."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await worker
        finally:
            raise


class LiveCollector:
    def __init__(
        self,
        key: SecretStr,
        bounds: tuple,
        spec: BatchSpec,
        store: RawStore,
        spool_root: Path,
        status_path: Path,
        *,
        register: Callable[[Manifest], None] | None = None,
        queue_records: int = 128,
        rotate_seconds: float = 30,
        rotate_bytes: int = 1048576,
        flush_seconds: float = 1,
        status_callback: Callable[[dict], None] | None = None,
        uri: str = STREAM_URL,
        ping_interval: float = 20,
        ping_timeout: float = 20,
        confirmation_timeout: float = 10,
        stable_seconds: float = 60,
        backoff_base: float = 1,
        backoff_max: float = 60,
        status_interval: float = 10,
    ):
        if not key.get_secret_value().strip():
            raise CaptureError("missing_api_key")
        if not 1 <= queue_records <= 1024 or not 0 < flush_seconds <= 5:
            raise CaptureError("invalid_collector_limits")
        self.key, self.bounds, self.spec = key, bounds, spec
        subscription(bounds, key)  # Validate before opening files or a socket.
        self.spool = LiveSpool(
            spool_root,
            store,
            spec,
            rotate_seconds=rotate_seconds,
            rotate_bytes=rotate_bytes,
            flush_seconds=flush_seconds,
            register=register,
        )
        self.status_path = status_path
        self.status_callback = status_callback
        self.queue = asyncio.Queue(maxsize=queue_records)
        self.status = LiveStatus()
        self.process_created_at_utc = datetime.fromtimestamp(psutil.Process().create_time(), UTC)
        self.status_lock = asyncio.Lock()
        self.terminal_error: CaptureError | None = None
        self.vessels: set[int] = set()
        self.batch_paths = deque(maxlen=20)
        self.uri = uri  # Test-only injection; the public CLI uses the fixed WSS endpoint.
        self.ping_interval, self.ping_timeout = ping_interval, ping_timeout
        self.confirmation_timeout, self.stable_seconds = confirmation_timeout, stable_seconds
        self.backoff_base, self.backoff_max = backoff_base, backoff_max
        self.status_interval = status_interval
        # WebSocket DEBUG logging can expose subscription payloads and credentials.
        self.socket_logger = logging.Logger("portwatch.private_websocket", level=logging.CRITICAL)
        self.socket_logger.disabled = True

    def snapshot(self) -> dict:
        return {
            **asdict(self.status),
            "status_schema_version": 2,
            "process_id": os.getpid(),
            "process_created_at_utc": self.process_created_at_utc.isoformat(),
            "status_updated_at_utc": _utc(),
            "source_id": self.spec.source_id,
            "dataset_id": self.spec.dataset_id,
            "distinct_vessels": len(self.vessels),
            "queue_occupancy": self.queue.qsize(),
            "queue_capacity": self.queue.maxsize,
            "recent_batch_paths": list(self.batch_paths),
            "unfinalized_received_records": max(
                0,
                self.status.received_records
                - (self.status.finalized_records - self.status.recovered_records),
            ),
        }

    async def publish_status(self) -> None:
        async with self.status_lock:
            await self._publish_status_locked()

    async def _publish_status_locked(self) -> None:
        snapshot = self.snapshot()

        def write():
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=self.status_path.parent, suffix=".part", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(snapshot, indent=2).encode())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.replace(temporary, self.status_path)
            finally:
                temporary.unlink(missing_ok=True)

        await _thread_join(write)
        emit(
            "collector_status",
            state=self.status.state,
            connected=self.status.connected,
            observations=self.status.observations,
            reconnect_count=self.status.reconnect_count,
            raw_persistence_failures=self.status.raw_persistence_failures,
        )
        if self.status_callback:
            self.status_callback(snapshot)

    def _batches(self, batches) -> None:
        for batch in batches:
            if batch:
                self.status.finalized_batches += 1
                self.status.finalized_records += batch.record_count
                self.batch_paths.append(str(self.spool.store._path(batch.local_path)))

    async def _writer(self) -> None:
        last_status = time.monotonic()
        try:
            while True:
                if time.monotonic() - last_status >= self.status_interval:
                    await self.publish_status()
                    last_status = time.monotonic()
                try:
                    record = await asyncio.wait_for(
                        self.queue.get(), timeout=self.spool.flush_seconds
                    )
                except TimeoutError:
                    self._batches(await _thread_join(self.spool.tick))
                    continue
                try:
                    if record is None:
                        self._batches([await _thread_join(self.spool.close)])
                        return
                    self._batches(await _thread_join(self.spool.append, record))
                    self.status.written_records += 1
                finally:
                    self.queue.task_done()
        except Exception:
            self.status.raw_persistence_failures += 1
            # Keep active bytes + metadata for bounded startup recovery; never delete a tail.
            if self.spool.stream is not None:
                with suppress(OSError):
                    self.spool.stream.close()
                self.spool.stream = None
            raise CaptureError("raw_persistence_failure") from None

    def _envelope(self, frame: str | bytes, session, sequence: int) -> tuple[LiveEnvelope, dict]:
        payload = frame.encode("utf-8") if isinstance(frame, str) else frame
        if self.key.get_secret_value().encode() in payload:
            raise CaptureError("credential_echo_refused")
        if len(payload) > MAX_FRAME_BYTES:
            raise CaptureError("frame_size_limit")
        kind, source_time, parsed = "parse_error", None, {}
        try:
            parsed = json.loads(payload.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError
            message_type = parsed.get("MessageType")
            metadata = parsed.get("MetaData", {})
            body = parsed.get("Message", {})
            if "error" in parsed or "Error" in parsed or message_type == "SubscriptionConfirmation":
                kind = "protocol_control"
            elif message_type in (*POSITION_TYPES, *STATIC_TYPES):
                if not isinstance(body, dict) or not isinstance(body.get(message_type), dict):
                    raise ValueError
                if not isinstance(metadata, dict):
                    raise ValueError
                kind = "ais_observation" if message_type in POSITION_TYPES else "ais_static"
                candidate = metadata.get("time_utc")
                if isinstance(candidate, str) and len(candidate) <= 200:
                    source_time = candidate
            else:
                kind = "unknown"
        except (UnicodeDecodeError, ValueError, RecursionError):
            parsed = {}
        return LiveEnvelope(
            source_id=self.spec.source_id,
            dataset_id=self.spec.dataset_id,
            connection_session=session,
            received_at=datetime.now(UTC),
            record_sequence=sequence,
            source_timestamp=source_time,
            kind=kind,
            frame_type="text" if isinstance(frame, str) else "binary",
            payload_base64=base64.b64encode(payload).decode("ascii"),
        ), parsed

    def _record_metrics(self, record: LiveEnvelope, parsed: dict) -> None:
        if record.kind == "ais_observation":
            self.status.observations += 1
            self.status.last_ais_observation_at = record.received_at.isoformat()
            self.status.last_source_timestamp = record.source_timestamp
        elif record.kind == "ais_static":
            self.status.static_messages += 1
        elif record.kind == "protocol_control":
            self.status.control_messages += 1
        elif record.kind == "parse_error":
            self.status.parse_errors += 1
        else:
            self.status.unknown_messages += 1
        if record.kind in {"ais_observation", "ais_static"}:
            value = parsed.get("MetaData", {}).get("MMSI")
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 100000000 <= value <= 999999999
            ):
                if value not in self.vessels and len(self.vessels) >= MAX_VESSELS:
                    raise CaptureError("vessel_counter_limit", record)
                self.vessels.add(value)

    async def _heartbeat(self, socket) -> None:
        try:
            while True:
                await asyncio.sleep(self.ping_interval)
                pong = await socket.ping()
                # Await the Pong directly so a simultaneous completed Pong cannot swallow
                # external cancellation in Python3.11 wait_for's cancellation race.
                async with asyncio.timeout(self.ping_timeout):
                    await pong
                self.status.last_socket_heartbeat = _utc()
        except (TimeoutError, ConnectionClosed):
            await socket.close()

    async def _close_socket(self, socket) -> None:
        close = asyncio.create_task(socket.close())
        try:
            await asyncio.wait_for(asyncio.shield(close), timeout=3)
        except (TimeoutError, asyncio.CancelledError):
            # A paused receive buffer or interrupted close must not leave TCP open.
            socket.transport.abort()
            with suppress(ConnectionClosed, asyncio.CancelledError):
                await close
            raise
        finally:
            if not socket.transport.is_closing():
                socket.transport.abort()

    async def _network(self) -> None:
        failures = 0
        unconfirmed_disconnects = 0
        while True:
            self.status.state = "connecting"
            self.status.subscription_confirmed = False
            self.status.compression_enabled = None
            await self.publish_status()
            connected_at = time.monotonic()
            try:
                async with connect(
                    self.uri,
                    compression="deflate",
                    max_size=MAX_FRAME_BYTES,
                    max_queue=8,
                    ping_interval=None,
                    close_timeout=2,
                    open_timeout=10,
                    logger=self.socket_logger,
                ) as socket:
                    connected_at = time.monotonic()
                    self.status.connections += 1
                    self.status.connected = True
                    self.status.state = "awaiting_confirmation"
                    await asyncio.wait_for(
                        socket.send(json.dumps(subscription(self.bounds, self.key))), timeout=2
                    )
                    session, sequence = uuid4(), 0
                    heartbeat = asyncio.create_task(self._heartbeat(socket))
                    try:
                        while True:
                            remaining = self.confirmation_timeout - (
                                time.monotonic() - connected_at
                            )
                            if not self.status.subscription_confirmed and remaining <= 0:
                                raise CaptureError("subscription_confirmation_timeout")
                            try:
                                frame = await asyncio.wait_for(
                                    socket.recv(),
                                    timeout=(
                                        None if self.status.subscription_confirmed else remaining
                                    ),
                                )
                            except TimeoutError:
                                raise CaptureError("subscription_confirmation_timeout") from None
                            sequence += 1
                            self.status.received_records += 1
                            self.status.last_socket_message_at = _utc()
                            record, parsed = self._envelope(frame, session, sequence)
                            self._record_metrics(record, parsed)
                            try:
                                self.queue.put_nowait(record)
                            except asyncio.QueueFull:
                                self.status.pressure_events += 1
                                raise CaptureError("buffer_pressure", record) from None
                            self.status.queue_high_water = max(
                                self.status.queue_high_water, self.queue.qsize()
                            )
                            if record.kind == "protocol_control":
                                if "error" in parsed or "Error" in parsed:
                                    raise CaptureError("subscription_rejected")
                                body = parsed.get("Message", {})
                                if not isinstance(body, dict) or not isinstance(
                                    body.get("CompressionEnabled"), bool
                                ):
                                    raise CaptureError("invalid_subscription_confirmation")
                                self.status.compression_enabled = body["CompressionEnabled"]
                                if not self.status.compression_enabled:
                                    raise CaptureError("compression_not_confirmed")
                                self.status.subscription_confirmed = True
                                self.status.state = "subscribed"
                                await self.publish_status()
                    except CaptureError as failure:
                        # Record failure before the context manager awaits socket closure;
                        # a duration/stop cancellation must not hide it or its held frame.
                        self.terminal_error = failure
                        raise
                    except ConnectionClosed as failure:
                        code = failure.rcvd.code if failure.rcvd else None
                        if code in {1002, 1003, 1007, 1008, 1009}:
                            self.terminal_error = CaptureError("socket_policy_or_protocol_failure")
                            raise self.terminal_error from None
                        if not self.status.subscription_confirmed and code == 1000:
                            self.terminal_error = CaptureError("subscription_not_confirmed")
                            raise self.terminal_error from None
                        raise
                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError):
                            await heartbeat
                        await self._close_socket(socket)
            except InvalidStatus as error:
                if 400 <= error.response.status_code < 500 and error.response.status_code != 429:
                    raise CaptureError("subscription_rejected") from None
            except ConnectionClosed as error:
                code = error.rcvd.code if error.rcvd else None
                if code in {1002, 1003, 1007, 1008, 1009}:
                    raise CaptureError("socket_policy_or_protocol_failure") from None
                if not self.status.subscription_confirmed and code == 1000:
                    raise CaptureError("subscription_not_confirmed") from None
            except (OSError, TimeoutError, WebSocketException):
                pass
            finally:
                self.status.connected = False
            self.status.state = "disconnected"
            if self.status.subscription_confirmed:
                unconfirmed_disconnects = 0
            else:
                unconfirmed_disconnects += 1
                if unconfirmed_disconnects >= 3:
                    raise CaptureError("repeated_unconfirmed_disconnects")
            await self.publish_status()
            if (
                self.status.subscription_confirmed
                and time.monotonic() - connected_at >= self.stable_seconds
            ):
                failures = 0
            delay = min(self.backoff_max, self.backoff_base * 2 ** min(failures, 16))
            delay *= random.uniform(0.8, 1.2)
            self.status.last_backoff_seconds = min(self.backoff_max, delay)
            self.status.reconnect_count += 1
            self.status.state = "backoff"
            await self.publish_status()
            await asyncio.sleep(self.status.last_backoff_seconds)
            failures += 1

    async def _submit(self, item, writer) -> None:
        """Drain a held pressure record/sentinel, but never wait on a dead writer."""
        put = asyncio.create_task(self.queue.put(item))
        await asyncio.wait([put, writer], return_when=asyncio.FIRST_COMPLETED)
        if writer.done():
            put.cancel()
            with suppress(asyncio.CancelledError):
                await put
            await writer
            raise CaptureError("raw_writer_stopped")
        await put

    async def run(
        self, duration: float | None = None, stop_event: asyncio.Event | None = None
    ) -> dict:
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            raise CaptureError("invalid_duration")
        started = time.monotonic()
        error = None
        tasks = []
        self.status.started_at = _utc()
        with collector_lock(self.spool.root):
            try:
                recovery = asyncio.create_task(asyncio.to_thread(self.spool.recover))
                startup_interrupted = False
                try:
                    recovered = await asyncio.shield(recovery)
                except asyncio.CancelledError:
                    recovered = await recovery
                    startup_interrupted = True
                for result in recovered:
                    self.status.recovered_files += 1
                    self.status.recovered_records += result.complete_records
                    self.status.truncated_tail_bytes += result.truncated_tail_bytes
                    self._batches([result.batch])
                if startup_interrupted:
                    self.status.state = "stopped"
                    self.status.stop_reason = "interrupted"
                    self.status.ended_at = _utc()
                    self.status.elapsed_seconds = time.monotonic() - started
                    await self.publish_status()
                    return self.snapshot()
            except Exception:
                self.status.raw_persistence_failures += 1
                self.status.failure_code = "active_recovery_failure"
                self.status.state = "failed"
                await self.publish_status()
                raise CaptureError("active_recovery_failure") from None
            writer = asyncio.create_task(self._writer())
            network = asyncio.create_task(self._network())
            tasks = [network]
            if duration is not None:
                tasks.append(asyncio.create_task(asyncio.sleep(duration)))
            if stop_event is not None:
                tasks.append(asyncio.create_task(stop_event.wait()))
            try:
                done, _ = await asyncio.wait([*tasks, writer], return_when=asyncio.FIRST_COMPLETED)
                if network in done:
                    await network
                elif writer in done:
                    await writer
                else:
                    self.status.stop_reason = (
                        "duration" if tasks[1] in done and duration else "requested"
                    )
            except asyncio.CancelledError:
                self.status.stop_reason = "interrupted"
            except Exception as failure:
                error = (
                    failure
                    if isinstance(failure, CaptureError)
                    else CaptureError("transport_failure")
                )
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with suppress(asyncio.CancelledError, Exception):
                        await task
                if error is None and self.terminal_error is not None:
                    error = self.terminal_error
                self.status.connected = False
                self.status.state = "draining"
                try:
                    if error and error.pending is not None:
                        await self._submit(error.pending, writer)
                    await self._submit(None, writer)
                    await writer
                except Exception as failure:
                    error = (
                        failure
                        if isinstance(failure, CaptureError)
                        else CaptureError("raw_persistence_failure")
                    )
                self.status.failure_code = error.code if error else None
                self.status.state = "failed" if error else "stopped"
                self.status.ended_at = _utc()
                self.status.elapsed_seconds = time.monotonic() - started
                await self.publish_status()
            if error:
                raise error
        return self.snapshot()
