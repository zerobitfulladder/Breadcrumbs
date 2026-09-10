"""Shared HTTP client: per-host rate limiting and retry on 429/5xx.

Semantic Scholar returns 429 readily on the unauthenticated shared pool, so
every outbound call goes through a token bucket keyed by host.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

# requests per second permitted per host
RATE_LIMITS: dict[str, float] = {
    "api.semanticscholar.org": 1.0,
    "api.openalex.org": 8.0,
    "api.crossref.org": 8.0,
    "api.unpaywall.org": 8.0,
    # arXiv asks for no more than one request every three seconds.
    "export.arxiv.org": 0.33,
}
DEFAULT_RATE = 4.0
MAX_RETRIES = 4

# Per-source retry budgets. Semantic Scholar is optional enrichment: without an
# API key it throttles constantly, and burning 18 seconds on backoff blocks the
# whole lookup for data OpenAlex can mostly supply anyway. So it fails fast and
# the caller degrades, rather than making the user wait.
RETRY_BUDGET: dict[str, int] = {"semantic_scholar": 2}
# Cap on any single backoff sleep, per source.
MAX_BACKOFF: dict[str, float] = {"semantic_scholar": 3.0}


class _Bucket:
    """Minimal token bucket. One instance per host, shared across tasks."""

    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / rate
        self.next_at = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                await asyncio.sleep(self.next_at - now)
                now = time.monotonic()
            self.next_at = now + self.interval


_buckets: dict[str, _Bucket] = {}


def _bucket(host: str) -> _Bucket:
    if host not in _buckets:
        _buckets[host] = _Bucket(RATE_LIMITS.get(host, DEFAULT_RATE))
    return _buckets[host]


# Keys for hosts that authenticate with a bearer token, by hostname.
#
# Kept here rather than threaded through every source function: OpenAlex alone
# has ten entry points across fifteen client call sites, none of which otherwise
# need to know about settings. Scoped by host so a key is never sent anywhere
# but the service it belongs to.
_API_KEYS: dict[str, str] = {}


#: When set, no request leaves the machine.
#:
#: A build should be reproducible and should not depend on a network, an API
#: budget, or what a third party happens to say today — nor quietly send a
#: contact email to ORCID and OpenAlex hundreds of times while it runs. The
#: static export turns this on, and every source then behaves as it does when
#: a service is unreachable: SourceError, which each caller already handles by
#: falling back to what the library holds.
_offline = False
#: What was refused, so a caller can report which data is missing and why.
_refused: list[str] = []


def set_offline(value: bool) -> list[str]:
    """Turn the network off (or back on). Returns what was refused meanwhile."""
    global _offline
    _offline = value
    refused = list(_refused)
    _refused.clear()
    return refused


def offline() -> bool:
    return _offline


def set_api_key(host: str, key: str) -> None:
    """Register (or clear, with a blank key) the bearer token for one host."""
    if key:
        _API_KEYS[host] = key
    else:
        _API_KEYS.pop(host, None)


class SourceError(RuntimeError):
    """A source could not answer. Callers degrade rather than fail the ingest."""

    def __init__(self, source: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.status = status


async def get_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    allow_404: bool = True,
    max_retries: int | None = None,
) -> str | None:
    """GET returning the body as text, rate-limited and retried like the rest.

    For sources that do not answer in JSON — arXiv replies in Atom XML — so
    they still queue behind the same per-host bucket instead of going straight
    at the service.
    """
    resp = await _get(
        client, url, source=source, params=params, headers=headers,
        allow_404=allow_404, max_retries=max_retries,
    )
    return None if resp is None else resp.text


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    allow_404: bool = True,
    max_retries: int | None = None,
) -> Any | None:
    """GET returning parsed JSON, or None when the record does not exist.

    Raises SourceError for anything the caller cannot treat as "absent".
    """
    resp = await _get(
        client, url, source=source, params=params, headers=headers,
        allow_404=allow_404, max_retries=max_retries,
    )
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(source, f"invalid JSON: {exc}") from exc


async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    allow_404: bool = True,
    max_retries: int | None = None,
) -> httpx.Response | None:
    """The shared request: per-host rate limit, backoff, and error mapping."""
    host = httpx.URL(url).host
    key = _API_KEYS.get(host or "")
    if key:
        headers = {**(headers or {}), "Authorization": f"Bearer {key}"}
    # Before the retry loop, not inside it. An offline build is a decision,
    # not a flaky connection: refusing further in would raise httpx.ConnectError
    # from the transport, which the loop below cannot tell from a real network
    # fault, so it would sleep its way through an exponential backoff for every
    # call that was never going to be made.
    if _offline:
        _refused.append(f"{source}: {url}")
        raise SourceError(source, "offline: this build makes no network requests")

    attempts = max_retries or RETRY_BUDGET.get(source, MAX_RETRIES)
    cap = MAX_BACKOFF.get(source, 16.0)
    last: Exception | None = None

    for attempt in range(attempts):
        await _bucket(host).wait()
        try:
            resp = await client.get(url, params=params, headers=headers, timeout=45.0)
        except httpx.HTTPError as exc:
            last = exc
            if attempt == attempts - 1:
                break
            await asyncio.sleep(min(2**attempt, cap) + random.random())
            continue

        if resp.status_code == 404:
            if allow_404:
                return None
            raise SourceError(source, "not found", 404)

        if resp.status_code == 429 or resp.status_code >= 500:
            last = SourceError(source, f"HTTP {resp.status_code}", resp.status_code)
            if attempt == attempts - 1:
                break  # no point sleeping before giving up
            retry_after = resp.headers.get("Retry-After")
            delay = min(
                float(retry_after) if (retry_after or "").isdigit() else 2**attempt, cap
            )
            log.warning("%s %s -> %s, retrying in %.1fs", source, host, resp.status_code, delay)
            await asyncio.sleep(delay + random.random())
            continue

        if resp.status_code >= 400:
            raise SourceError(source, f"HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)

        return resp

    if isinstance(last, SourceError) and last.status == 429:
        raise SourceError(source, "rate limited (no API key configured)", 429)
    raise SourceError(source, f"gave up after {attempts} attempts ({last})")


class _RefuseTransport(httpx.AsyncBaseTransport):
    """Backstop for code that reaches for httpx without going through get_json.

    get_json covers the source modules, but a portrait download or a PDF probe
    calls the client directly. Refusing at the transport means "no requests"
    is a property of the client rather than a promise each call site has to
    keep.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        _refused.append(f"direct: {request.url}")
        raise httpx.ConnectError("offline: this build makes no network requests")


#: Named once, because more than one client speaks for this app and at least
#: one host — Wikimedia — answers 403 to anyone who does not introduce
#: themselves.
USER_AGENT = "Breadcrumbs/0.1 (literature study tool)"


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        transport=_RefuseTransport() if _offline else None,
    )
