"""Shared HTTP client for every source adapter.

Provides the three things each adapter would otherwise reimplement badly: a browser-like
identity (several houses 403 a default user agent), polite rate limiting, and retry with
exponential backoff on transient failures only.

A disk cache sits in front of the network so a re-run of the pipeline costs nothing and
so the raw bytes behind every published figure remain auditable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS: Final[Mapping[str, str]] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

#: Retried. Anything else (404, 403, 401) is a real answer and is surfaced immediately.
_TRANSIENT_STATUSES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})


class TransientHTTPError(RuntimeError):
    """A request failed in a way that is worth retrying."""


class _RateLimiter:
    """Minimum wall-clock spacing between requests to a single host."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            earliest = self._last.get(host, 0.0) + self._min_interval
            delay = max(0.0, earliest - now)
            self._last[host] = now + delay
        if delay:
            time.sleep(delay)


@dataclass(slots=True)
class FetchStats:
    requests: int = 0
    cache_hits: int = 0
    bytes_downloaded: int = 0


@dataclass
class HttpClient:
    """A caching, rate-limited, retrying HTTP client.

    Args:
        cache_dir: Where response bodies are memoised. ``None`` disables caching.
        min_interval: Seconds to leave between consecutive requests to the same host.
        timeout: Per-request timeout in seconds.
    """

    cache_dir: Path | None = None
    min_interval: float = 0.7
    timeout: float = 45.0
    headers: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADERS))

    _client: httpx.Client = field(init=False, repr=False)
    _limiter: _RateLimiter = field(init=False, repr=False)
    stats: FetchStats = field(init=False, default_factory=FetchStats)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            headers=dict(self.headers),
            timeout=self.timeout,
            follow_redirects=True,
            http2=True,
        )
        self._limiter = _RateLimiter(self.min_interval)
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- caching -----------------------------------------------------------------

    def _cache_path(
        self,
        method: str,
        url: str,
        body: bytes | None,
        ignore_params: Sequence[str] = (),
    ) -> Path | None:
        """Where a response is memoised.

        ``ignore_params`` exists for APIs that carry a rotating credential in the query
        string - Sotheby's Algolia key is in the URL and changes every few hours, so
        including it would invalidate the whole cache on every run.
        """
        if self.cache_dir is None:
            return None
        if ignore_params:
            parsed = urlsplit(url)
            kept = [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key not in set(ignore_params)
            ]
            url = urlunsplit(parsed._replace(query=urlencode(kept)))
        digest = hashlib.sha256(f"{method} {url} ".encode() + (body or b"")).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.bin"

    # -- requests ----------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((TransientHTTPError, httpx.TransportError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=1.0, max=30.0),
        reraise=True,
    )
    def _send(self, request: httpx.Request) -> httpx.Response:
        self._limiter.wait(request.url.host)
        response = self._client.send(request)
        if response.status_code in _TRANSIENT_STATUSES:
            msg = f"{response.status_code} from {request.url}"
            raise TransientHTTPError(msg)
        return response

    def get_bytes(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        use_cache: bool = True,
        cache_ignore_params: Sequence[str] = (),
    ) -> bytes:
        """GET a URL, returning the raw body. Raises for any non-transient error status."""
        request = self._client.build_request("GET", url, params=params, headers=headers)
        cache_path = (
            self._cache_path("GET", str(request.url), None, cache_ignore_params)
            if use_cache
            else None
        )
        if cache_path is not None and cache_path.exists():
            self.stats.cache_hits += 1
            return cache_path.read_bytes()

        response = self._send(request)
        response.raise_for_status()
        payload = response.content
        self.stats.requests += 1
        self.stats.bytes_downloaded += len(payload)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(payload)
        return payload

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.get_bytes(url, **kwargs).decode("utf-8", errors="replace")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return json.loads(self.get_bytes(url, **kwargs))

    def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        use_cache: bool = True,
    ) -> Any:
        """POST JSON and decode a JSON response."""
        merged = {"Content-Type": "application/json", "Accept": "application/json"}
        merged.update(headers or {})
        body = json.dumps(payload, sort_keys=True).encode()
        request = self._client.build_request("POST", url, content=body, headers=merged)
        cache_path = self._cache_path("POST", str(request.url), body) if use_cache else None
        if cache_path is not None and cache_path.exists():
            self.stats.cache_hits += 1
            return json.loads(cache_path.read_bytes())

        response = self._send(request)
        response.raise_for_status()
        content = response.content
        self.stats.requests += 1
        self.stats.bytes_downloaded += len(content)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(content)
        return json.loads(content)
