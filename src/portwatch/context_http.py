"""Small GET-only public APIs: strict byte, timeout, retry and deadline budgets."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from portwatch.context import ContextError


@dataclass(frozen=True)
class HttpResponse:
    payload: bytes
    retrieved_at: datetime
    status: int
    etag: str | None = None
    last_modified: str | None = None


def retry_after(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    try:
        seconds = int(value)
        return now + timedelta(seconds=max(0, seconds))
    except (ValueError, OverflowError):
        try:
            stamp = parsedate_to_datetime(value)
            return stamp.astimezone(UTC) if stamp.utcoffset() is not None else None
        except (TypeError, ValueError, OverflowError):
            return None


def fetch_context(
    url: str,
    *,
    attempts: int = 2,
    timeout: float = 15,
    deadline: float = 35,
    max_bytes: int = 2 * 1024 * 1024,
) -> HttpResponse:
    if (
        not 1 <= attempts <= 3
        or not 0 < timeout <= 30
        or not 0 < deadline <= 60
        or not 1 <= max_bytes <= 2 * 1024 * 1024
    ):
        raise ContextError("invalid_http_budget")
    stop = time.monotonic() + deadline
    for attempt in range(attempts):
        remaining = stop - time.monotonic()
        if remaining <= 0:
            raise ContextError("source_deadline")
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "PortWatch/0.1 educational local demo",
                    "Accept": "application/json, application/rss+xml, text/html;q=0.8",
                },
            )
            with urlopen(request, timeout=min(timeout, remaining)) as response:
                length = response.headers.get("Content-Length")
                try:
                    expected = int(length) if length is not None else None
                except ValueError:
                    raise ContextError(
                        "source_invalid_content_length", http_status=response.status
                    ) from None
                if expected is not None and (expected < 0 or expected > max_bytes):
                    raise ContextError("source_response_too_large", http_status=response.status)
                chunks, size = [], 0
                while True:
                    if time.monotonic() >= stop:
                        raise ContextError("source_deadline")
                    chunk = response.read1(min(65536, max_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ContextError("source_response_too_large")
                    chunks.append(chunk)
                if expected is not None and size != expected:
                    raise ContextError("source_incomplete_response", http_status=response.status)
                return HttpResponse(
                    b"".join(chunks),
                    datetime.now(UTC),
                    response.status,
                    response.headers.get("ETag"),
                    response.headers.get("Last-Modified"),
                )
        except HTTPError as error:
            stamp = retry_after(error.headers.get("Retry-After"), datetime.now(UTC))
            status = error.code
            error.close()
            # 429 is persisted for a later invocation; never sleep/retry against a throttle.
            if status == 429:
                raise ContextError(
                    "source_rate_limited", http_status=status, next_allowed_at=stamp
                ) from None
            delay = max(0, (stamp - datetime.now(UTC)).total_seconds()) if stamp else 1
            if status not in {408, 425, 500, 502, 503, 504} or delay > 5:
                raise ContextError(
                    "source_http_failure", http_status=status, next_allowed_at=stamp
                ) from None
            failure = ContextError("source_http_failure", http_status=status, next_allowed_at=stamp)
        except (TimeoutError, URLError, OSError) as error:
            failure = ContextError(
                "source_timeout"
                if isinstance(error, TimeoutError)
                or isinstance(getattr(error, "reason", None), TimeoutError)
                else "source_connection_failure"
            )
            delay = 1
        if attempt + 1 == attempts or time.monotonic() + delay >= stop:
            raise failure
        time.sleep(delay)
    raise ContextError("source_attempts_exhausted")
