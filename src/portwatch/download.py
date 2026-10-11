"""Bounded whole-file downloads; interrupted transfers restart, never append."""

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from portwatch.storage import BatchSpec, Manifest, RawStore, _publication_lock, _sync_directory

MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class DownloadError(RuntimeError):
    """Safe failure summary containing no response body or secret URL."""


@dataclass(frozen=True)
class DownloadResult:
    manifest: Manifest
    downloaded: bool


def selection_path(store: RawStore, spec: BatchSpec) -> Path:
    key = [spec.source_id, spec.dataset_id, spec.source_url, str(spec.source_date), spec.mode]
    identity = hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()
    return store.root / f".source-selection-{identity}.json"


def remember_selection(store: RawStore, spec: BatchSpec, batch: Manifest, etag: str | None) -> None:
    """Mutable current-source pointer; immutable first receipts retain their provenance."""
    path = selection_path(store, spec)
    body = json.dumps({
        "schema_version": 1, "batch_id": batch.batch_id, "remote_etag": etag,
        "byte_count": batch.byte_count, "last_verified_at": datetime.now(UTC).isoformat(),
    }, indent=2)
    with _publication_lock(store.root):
        with tempfile.NamedTemporaryFile(dir=store.root, suffix=".part", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body.encode())
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, path)
            _sync_directory(store.root)
        finally:
            temporary.unlink(missing_ok=True)


def _size(headers) -> int | None:
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        count = int(value)
    except ValueError:
        raise DownloadError("invalid_content_length") from None
    if count <= 0:
        raise DownloadError("empty_content")
    return count


def acquire(
    store: RawStore,
    spec: BatchSpec,
    *,
    refresh: bool = False,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    timeout: float = 30,
    attempts: int = 3,
    deadline_seconds: float = 600,
    progress: Callable[[int], None] | None = None,
) -> DownloadResult:
    if not 1 <= attempts <= 3 or not 0 < max_bytes <= MAX_DOWNLOAD_BYTES:
        raise ValueError("unsupported download limits")
    candidates = []
    for item in store.inspect():
        batch = item.manifest
        if batch and (
            batch.source_id, batch.dataset_id, batch.source_url, batch.source_date
        ) == (spec.source_id, spec.dataset_id, spec.source_url, spec.source_date):
            if item.state == "invalid":
                raise DownloadError("cached_raw_integrity_failure")
            if item.state == "complete":
                candidates.append(batch)
    pointer = selection_path(store, spec)
    selection = json.loads(pointer.read_text("utf-8")) if pointer.exists() else None
    if selection:
        if selection.get("schema_version") != 1:
            raise DownloadError("unsupported_source_selection_version")
        cached = next(
            (item for item in candidates if item.batch_id == selection.get("batch_id")), None
        )
        if cached is None or cached.byte_count != selection.get("byte_count"):
            raise DownloadError("source_selection_has_no_verified_raw_batch")
    else:
        cached = max(candidates, key=lambda item: item.received_at) if candidates else None
    if cached and not refresh:
        return DownloadResult(cached, False)
    last_code = "transfer_failed"
    for attempt in range(attempts):
        try:
            with urlopen(Request(spec.source_url, method="HEAD"), timeout=timeout) as response:
                if response.status != 200:
                    raise DownloadError("unexpected_head_status")
                expected = _size(response.headers)
                etag = response.headers.get("ETag")
                modified = response.headers.get("Last-Modified")
            if expected is not None and expected > max_bytes:
                raise DownloadError("download_exceeds_size_limit")
            cached_etag = selection.get("remote_etag") if selection else (
                cached.remote_etag if cached else None
            )
            if cached and etag and cached_etag == etag and cached.byte_count == expected:
                remember_selection(store, spec, cached, etag)
                return DownloadResult(cached, False)
            headers = {"Accept-Encoding": "identity"}
            if etag and not etag.startswith("W/"):
                headers["If-Match"] = etag
            with urlopen(Request(spec.source_url, headers=headers), timeout=timeout) as response:
                if response.status != 200:
                    raise DownloadError("full_download_requires_http_200")
                actual_size = _size(response.headers)
                actual_etag = response.headers.get("ETag")
                if expected is not None and actual_size is not None and expected != actual_size:
                    raise DownloadError("remote_identity_changed")
                if etag and actual_etag != etag:
                    raise DownloadError("remote_identity_changed")
                expected = actual_size if actual_size is not None else expected
                if expected is not None and expected > max_bytes:
                    raise DownloadError("download_exceeds_size_limit")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise DownloadError("unexpected_content_encoding")
                acquisition = BatchSpec(**{
                    **spec.model_dump(), "remote_etag": actual_etag or etag,
                    "remote_last_modified": response.headers.get("Last-Modified") or modified,
                    "expected_byte_count": expected,
                })
                started = time.monotonic()

                def chunks(response=response, expected=expected, started=started):
                    transferred = 0
                    while chunk := response.read(CHUNK_BYTES):
                        transferred += len(chunk)
                        if transferred > max_bytes:
                            raise DownloadError("download_exceeds_size_limit")
                        if time.monotonic() - started > deadline_seconds:
                            raise DownloadError("transfer_deadline_exceeded")
                        if progress:
                            progress(transferred)
                        yield chunk
                    if expected is not None and transferred != expected:
                        raise DownloadError("incomplete_transfer")
                    if transferred == 0:
                        raise DownloadError("empty_content")

                manifest = store.write(chunks(), acquisition)
                remember_selection(store, spec, manifest, actual_etag or etag)
                return DownloadResult(manifest, True)
        except HTTPError as error:
            last_code = f"http_{error.code}"
            if error.code not in {408, 412, 425, 429, 500, 502, 503, 504}:
                raise DownloadError(last_code) from None
        except (URLError, TimeoutError, IncompleteRead, ConnectionError):
            last_code = "network_transfer_failure"
        except DownloadError as error:
            last_code = str(error)
            if last_code not in {"incomplete_transfer", "remote_identity_changed"}:
                raise
        if attempt + 1 < attempts:
            time.sleep(2**attempt)
    raise DownloadError(f"{last_code}_after_{attempts}_attempts")
