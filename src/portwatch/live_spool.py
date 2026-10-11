"""Durable active JSONL, immutable raw publication, and interrupted-file recovery."""

import base64
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from portwatch.storage import SLUG, BatchSpec, Manifest, RawStore, _publish_json, _sync_directory

MAX_FRAME_BYTES = 65_536
MAX_LINE_BYTES = 2 * MAX_FRAME_BYTES + 4096


class LiveEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    source_id: str = Field(pattern=SLUG)
    dataset_id: str = Field(pattern=SLUG)
    connection_session: UUID
    received_at: datetime
    record_sequence: int = Field(ge=1)
    source_timestamp: str | None = Field(default=None, max_length=200)
    kind: Literal["ais_observation", "ais_static", "protocol_control", "parse_error", "unknown"]
    frame_type: Literal["text", "binary"]
    payload_encoding: Literal["base64"] = "base64"
    payload_base64: str = Field(max_length=4 * ((MAX_FRAME_BYTES + 2) // 3))

    @field_validator("received_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("receipt timestamp requires timezone")
        return value.astimezone(UTC)

    @field_validator("payload_base64")
    @classmethod
    def valid_payload(cls, value: str) -> str:
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) > MAX_FRAME_BYTES:
            raise ValueError("frame exceeds bounded payload limit")
        return value


@contextmanager
def collector_lock(root: Path) -> Iterator[None]:
    """One local collector/recovery owner per dataset; fail without waiting."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".collector.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("collector_already_running") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class Recovery:
    active_name: str
    complete_records: int
    truncated_tail_bytes: int
    batch: Manifest | None


class LiveSpool:
    def __init__(
        self,
        root: Path,
        store: RawStore,
        spec: BatchSpec,
        *,
        rotate_seconds: float = 30,
        rotate_bytes: int = 1048576,
        flush_seconds: float = 1,
        register: Callable[[Manifest], None] | None = None,
    ):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store, self.spec, self.register = store, spec, register
        self.rotate_seconds, self.rotate_bytes = rotate_seconds, rotate_bytes
        self.flush_seconds = flush_seconds
        self.stream = None
        self.path = None
        self.batch_spec = None
        self.records = self.size = 0
        self.opened = self.last_flush = time.monotonic()

    def _open(self, received_at: datetime) -> None:
        self.path = self.root / f"{uuid4().hex}.active"
        self.batch_spec = self.spec.model_copy(update={"received_at": received_at})
        _publish_json(self.path.with_suffix(".meta"), self.batch_spec.model_dump_json(indent=2))
        self.stream = self.path.open("xb")
        self.records = self.size = 0
        self.opened = self.last_flush = time.monotonic()

    def append(self, record: LiveEnvelope) -> list[Manifest]:
        if (record.source_id, record.dataset_id) != (self.spec.source_id, self.spec.dataset_id):
            raise ValueError("live envelope source mismatch")
        line = record.model_dump_json().encode("utf-8") + b"\n"
        if len(line) > MAX_LINE_BYTES:
            raise ValueError("live envelope exceeds line limit")
        batches = []
        hour = record.received_at.strftime("%Y%m%d%H")
        if self.stream and (
            self.size + len(line) > self.rotate_bytes
            or self.batch_spec.received_at.strftime("%Y%m%d%H") != hour
        ):
            batches.append(self.close())
        if self.stream is None:
            self._open(record.received_at)
        self.stream.write(line)
        self.records += 1
        self.size += len(line)
        batches.extend(self.tick())
        return batches

    def tick(self) -> list[Manifest]:
        if self.stream is None:
            return []
        now = time.monotonic()
        if now - self.last_flush >= self.flush_seconds:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.last_flush = now
        if now - self.opened >= self.rotate_seconds:
            return [self.close()]
        return []

    def _publish(self, path: Path, spec: BatchSpec, records: int, size: int) -> Manifest:
        def chunks():
            remaining = size
            with path.open("rb") as source:
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError("active file shortened during publication")
                    remaining -= len(chunk)
                    yield chunk

        completed = BatchSpec(
            **{
                **spec.model_dump(),
                "record_count": records,
                "expected_byte_count": size,
            }
        )
        batch = self.store.write(chunks(), completed)
        if self.register:
            self.register(batch)
        return batch

    def close(self) -> Manifest | None:
        if self.stream is None:
            return None
        try:
            self.stream.flush()
            os.fsync(self.stream.fileno())
        finally:
            self.stream.close()
            self.stream = None
        # On publication/registration failure, active bytes + metadata remain recoverable.
        batch = self._publish(self.path, self.batch_spec, self.records, self.size)
        self.path.unlink()
        self.path.with_suffix(".meta").unlink()
        _sync_directory(self.root)
        return batch

    def recover(self) -> list[Recovery]:
        results = []
        for path in sorted(self.root.glob("*.active")):
            spec = BatchSpec.model_validate_json(path.with_suffix(".meta").read_text("utf-8"))
            if (spec.source_id, spec.dataset_id, spec.source_url, spec.mode) != (
                self.spec.source_id,
                self.spec.dataset_id,
                self.spec.source_url,
                "live",
            ):
                raise ValueError("active source metadata mismatch")
            count = size = 0
            tail = b""
            with path.open("rb") as stream:
                while line := stream.readline(MAX_LINE_BYTES + 1):
                    if len(line) > MAX_LINE_BYTES:
                        raise ValueError("active record exceeds line limit")
                    try:
                        record = LiveEnvelope.model_validate_json(line)
                    except ValueError:
                        if not line.endswith(b"\n"):
                            tail = line
                            break
                        raise
                    if (record.source_id, record.dataset_id) != (spec.source_id, spec.dataset_id):
                        raise ValueError("active envelope source mismatch")
                    count += 1
                    size += len(line)
                    if not line.endswith(b"\n"):
                        break
            batch = self._publish(path, spec, count, size) if count else None
            if tail:
                tail_path = path.with_suffix(".tail")
                if tail_path.exists() and tail_path.read_bytes() != tail:
                    raise ValueError("recovered tail conflicts with preserved tail")
                with tail_path.open("wb") as stream:
                    stream.write(tail)
                    stream.flush()
                    os.fsync(stream.fileno())
                tail_meta = path.with_suffix(".tail.meta")
                if not tail_meta.exists():
                    _publish_json(
                        tail_meta,
                        json.dumps(
                            {
                                "spec": spec.model_dump(mode="json"),
                                "truncated_tail_bytes": len(tail),
                            },
                            indent=2,
                        ),
                    )
            path.unlink()
            path.with_suffix(".meta").unlink()
            _sync_directory(self.root)
            results.append(Recovery(path.name, count, len(tail), batch))
        return results
