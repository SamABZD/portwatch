"""Immutable raw bytes, recovery sidecars, and original-record quarantine."""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$"
SHA256 = r"^[0-9a-f]{64}$"


def public_source_url(value: str) -> str:
    """Only public URL provenance; never persist userinfo, query, or fragment."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "wss"} or not parsed.hostname:
        raise ValueError("source URL must be an HTTP(S) or WSS URL")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is not None:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


class BatchSpec(BaseModel):
    """Acquisition metadata is deliberately closed to arbitrary secret fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(pattern=SLUG)
    dataset_id: str = Field(pattern=SLUG)
    source_url: str
    mode: Literal["historical", "live"]
    source_date: date | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_start_utc: datetime | None = None
    source_end_utc: datetime | None = None
    processing_version: str = Field(default="raw-v1", pattern=SLUG)
    record_count: int | None = Field(default=None, ge=0)
    file_suffix: str = Field(default=".bin", pattern=r"^\.[a-z0-9]+(?:\.[a-z0-9]+)?$")
    remote_etag: str | None = Field(default=None, max_length=200)
    remote_last_modified: str | None = Field(default=None, max_length=100)
    expected_byte_count: int | None = Field(default=None, ge=0)

    @field_validator("source_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        return public_source_url(value)

    @field_validator("received_at", "source_start_utc", "source_end_utc")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.utcoffset() is None:
                raise ValueError("timestamps must include a timezone")
            return value.astimezone(UTC)
        return None

    @model_validator(mode="after")
    def valid_times_and_layout(self) -> "BatchSpec":
        if self.mode == "historical" and self.source_date is None:
            raise ValueError("historical batches require a source date")
        if self.mode == "live" and self.file_suffix not in {".jsonl", ".json", ".xml"}:
            raise ValueError("live batches must be JSONL or an original JSON/XML response")
        if (self.source_start_utc is None) != (self.source_end_utc is None):
            raise ValueError("source time range must contain both endpoints or neither")
        if self.source_start_utc is not None and self.source_end_utc < self.source_start_utc:
            raise ValueError("source time range is reversed")
        return self


def batch_identity(spec: BatchSpec, checksum: str) -> str:
    parts = [spec.source_id, spec.dataset_id, spec.source_url, checksum]
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def relative_path(spec: BatchSpec, batch_id: str) -> Path:
    if spec.mode == "historical":
        period = spec.source_date.strftime("%Y/%m/%d")
    else:
        period = spec.received_at.strftime("%Y/%m/%d/%H")
    return (
        Path(spec.source_id) / spec.mode / period / spec.dataset_id / (batch_id + spec.file_suffix)
    )


class Manifest(BatchSpec):
    schema_version: Literal[1] = 1
    state: Literal["complete"] = "complete"
    batch_id: str = Field(pattern=SHA256)
    content_sha256: str = Field(pattern=SHA256)
    byte_count: int = Field(ge=0)
    local_path: str

    @model_validator(mode="after")
    def valid_identity_and_path(self) -> "Manifest":
        if self.batch_id != batch_identity(self, self.content_sha256):
            raise ValueError("batch identity mismatch")
        if self.local_path != relative_path(self, self.batch_id).as_posix():
            raise ValueError("batch path mismatch")
        return self


@dataclass(frozen=True)
class Inspection:
    manifest_path: Path
    state: Literal["complete", "pending", "invalid", "unmanifested"]
    manifest: Manifest | None
    detail: str


def checksum_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _sync_directory(path: Path) -> None:
    # Windows does not expose directory fsync through this standard-library API.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@contextmanager
def _publication_lock(root: Path) -> Iterator[None]:
    """OS releases this lock on process death; no stale-lock deletion needed."""
    with (root / ".publish.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _publish_json(path: Path, body: str) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(body.encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.rename(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class RawStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, relative: str) -> Path:
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("path escapes raw root")
        return path

    def write(self, chunks: Iterable[bytes], spec: BatchSpec) -> Manifest:
        """Publish recovery intent before the data rename; register separately."""
        # Stage on the same filesystem as the final raw file, never in system TEMP.
        with tempfile.NamedTemporaryFile(dir=self.root, suffix=".part", delete=False) as stream:
            temporary = Path(stream.name)
            digest = hashlib.sha256()
            size = 0
            # Cleanup failed streaming writes after closing the handle on Windows too.
        try:
            with temporary.open("wb") as stream:
                for chunk in chunks:
                    stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        try:
            if spec.expected_byte_count is not None and size != spec.expected_byte_count:
                raise ValueError("stored byte count differs from expected transfer length")
            checksum = digest.hexdigest()
            identity = batch_identity(spec, checksum)
            manifest = Manifest(
                **spec.model_dump(),
                batch_id=identity,
                content_sha256=checksum,
                byte_count=size,
                local_path=relative_path(spec, identity).as_posix(),
            )
            with _publication_lock(self.root):
                # Reuse the first receipt path, including across live hour boundaries.
                matches = list(self.root.rglob(f"{identity}*.manifest.json"))
                if len(matches) > 1:
                    raise ValueError("duplicate batch manifests")
                if matches:
                    existing = Manifest.model_validate_json(matches[0].read_text("utf-8"))
                    if existing.content_sha256 != checksum or existing.byte_count != size:
                        raise ValueError("existing manifest conflicts with content")
                    manifest = existing
                destination = self._path(manifest.local_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                sidecar = Path(str(destination) + ".manifest.json")
                if not sidecar.exists():
                    _publish_json(sidecar, manifest.model_dump_json(indent=2))
                if destination.exists():
                    if checksum_file(destination) != (checksum, size):
                        raise ValueError("existing raw content is corrupt; refusing overwrite")
                    return manifest
                os.rename(temporary, destination)
                _sync_directory(destination.parent)
                _sync_directory(self.root)
            return manifest
        finally:
            temporary.unlink(missing_ok=True)

    def reuse_historical(self, parent: Manifest, spec: BatchSpec) -> Manifest:
        """New regional receipt/identity, same immutable national bytes via a hard link."""
        if (
            spec.mode != "historical"
            or parent.mode != "historical"
            or (spec.source_id, spec.source_url, spec.source_date)
            != (parent.source_id, parent.source_url, parent.source_date)
        ):
            raise ValueError("only identical historical source artifacts may be shared")
        source = self._path(parent.local_path)
        if checksum_file(source) != (parent.content_sha256, parent.byte_count):
            raise ValueError("shared source integrity failure")
        identity = batch_identity(spec, parent.content_sha256)
        manifest = Manifest(
            **spec.model_dump(),
            batch_id=identity,
            content_sha256=parent.content_sha256,
            byte_count=parent.byte_count,
            local_path=relative_path(spec, identity).as_posix(),
        )
        with _publication_lock(self.root):
            destination = self._path(manifest.local_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            sidecar = Path(str(destination) + ".manifest.json")
            if sidecar.exists():
                manifest = Manifest.model_validate_json(sidecar.read_text("utf-8"))
            else:
                _publish_json(sidecar, manifest.model_dump_json(indent=2))
            if not destination.exists():
                os.link(source, destination)
                _sync_directory(destination.parent)
            if checksum_file(destination) != (parent.content_sha256, parent.byte_count):
                raise ValueError("shared source target integrity failure")
        return manifest

    def inspect(self) -> list[Inspection]:
        inspections = []
        referenced = set()
        inspected = set()

        def inspect_manifest(sidecar):
            inspected.add(sidecar)
            try:
                manifest = Manifest.model_validate_json(sidecar.read_text("utf-8"))
                path = self._path(manifest.local_path)
                if sidecar.resolve() != Path(str(path) + ".manifest.json").resolve():
                    raise ValueError("sidecar path mismatch")
                referenced.add(path)
                if not path.is_file():
                    inspections.append(Inspection(sidecar, "pending", manifest, "missing_raw"))
                elif checksum_file(path) != (manifest.content_sha256, manifest.byte_count):
                    inspections.append(
                        Inspection(sidecar, "invalid", manifest, "checksum_mismatch")
                    )
                else:
                    inspections.append(Inspection(sidecar, "complete", manifest, "verified"))
            except (OSError, ValueError, TypeError):
                inspections.append(Inspection(sidecar, "invalid", None, "manifest_invalid"))

        for sidecar in sorted(self.root.rglob("*.manifest.json")):
            inspect_manifest(sidecar)
        for path in sorted(self.root.rglob("*")):
            if (
                path.is_file()
                and path not in referenced
                and not path.name.startswith(".")
                and not path.name.endswith((".part", ".manifest.json"))
            ):
                # A collector can publish after the first directory scan. Its manifest
                # precedes the payload; verify that new sidecar before declaring an orphan.
                sidecar = Path(str(path) + ".manifest.json")
                if sidecar not in inspected and sidecar.is_file():
                    inspect_manifest(sidecar)
                if path not in referenced:
                    inspections.append(Inspection(path, "unmanifested", None, "missing_manifest"))
        return inspections


def quarantine_record(
    root: Path, payload: bytes, *, batch_id: str, record_offset: int, reason: str
) -> Path:
    """Preserve rejected bytes unchanged; reason is a code, never exception text."""
    if not re.fullmatch(SHA256, batch_id) or record_offset < 0:
        raise ValueError("invalid quarantine batch or offset")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", reason):
        raise ValueError("quarantine reason must be a non-secret reason code")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256(payload).hexdigest()
    directory = root / batch_id
    directory.mkdir(exist_ok=True)
    destination = directory / f"{record_offset:012d}-{checksum}.bin"
    with _publication_lock(root):
        sidecar = Path(str(destination) + ".manifest.json")
        if not sidecar.exists():
            metadata = dict(
                schema_version=1,
                batch_id=batch_id,
                record_offset=record_offset,
                reason=reason,
                content_sha256=checksum,
                byte_count=len(payload),
            )
            _publish_json(sidecar, json.dumps(metadata, indent=2))
        if not destination.exists():
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.rename(temporary, destination)
                _sync_directory(directory)
            finally:
                temporary.unlink(missing_ok=True)
        elif checksum_file(destination) != (checksum, len(payload)):
            raise ValueError("quarantine content mismatch")
    return destination
