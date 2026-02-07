"""
Async HTTP client wrapper with robust retry logic, cookie support,
and browser impersonation.

Retry policy:
- Retries on network errors: TimeoutException, ConnectError, ReadError,
  WriteError, PoolTimeout, ConnectTimeout.
- Retries on server errors: HTTP 429 (rate-limit), 500, 502, 503, 504.
- Exponential back-off with jitter, capped at 30 s per wait.
- Respects Retry-After header on 429 responses.
- Does NOT retry on 4xx client errors (except 429).

Cookie handling:
- Constructor accepts an initial cookie dict that is passed to the
  underlying httpx.AsyncClient.
- Per-request cookies can be passed via the `cookies` kwarg and are
  merged on top of the client-level jar for that single request.
- update_cookies() allows runtime cookie injection (e.g. after a
  token refresh).
"""

import asyncio
import logging
import random
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# Maximum back-off wait time (seconds) between retries
_MAX_BACKOFF = 30.0

# HTTP status codes that trigger an automatic retry
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# User-Agent pool for rotation
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"),
]


def get_random_user_agent() -> str:
    return random.choice(_USER_AGENTS)


# All httpx exception types that represent transient network problems
_NETWORK_ERRORS = (
    httpx.TimeoutException,  # base for all timeout variants
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.CloseError,
)


class HTTPClient:
    """
    Async HTTP client with retry logic, cookie jar support and
    configurable headers. Wraps httpx.AsyncClient.
    """

    def __init__(
        self,
        timeout: int | None = None,
        max_retries: int | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        follow_redirects: bool = True,
        impersonate_browser: bool = True,
    ):
        settings = get_settings()
        self._timeout = timeout or settings.request_timeout
        self._max_retries = max_retries or settings.max_retries
        self._follow_redirects = follow_redirects

        default_headers: dict[str, str] = {}
        if impersonate_browser:
            default_headers = {
                "User-Agent": settings.user_agent or get_random_user_agent(),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }

        if headers:
            default_headers.update(headers)

        self._default_headers = default_headers
        self._cookies: dict[str, str] = dict(cookies) if cookies else {}
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._timeout,
                    connect=10.0,
                    read=self._timeout,
                    write=10.0,
                    pool=10.0,
                ),
                follow_redirects=self._follow_redirects,
                headers=self._default_headers,
                cookies=self._cookies,
                http2=True,
                limits=httpx.Limits(
                    max_connections=50,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Cookie helpers
    # ------------------------------------------------------------------

    def update_cookies(self, cookies: dict[str, str]):
        """Merge new cookies into the client-level jar at runtime."""
        self._cookies.update(cookies)
        # Also push into the live client if it exists
        if self._client and not self._client.is_closed:
            for k, v in cookies.items():
                self._client.cookies.set(k, v)

    # ------------------------------------------------------------------
    # Core request with retry
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        data: Any = None,
        json: Any = None,
        cookies: dict[str, str] | None = None,
        follow_redirects: bool | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """
        Make an HTTP request with automatic retries on transient failures.

        Retries on:
        - Network errors (timeout, connect, read, write, pool).
        - HTTP 429 / 5xx responses.

        Back-off: exponential (2^attempt) + random jitter, capped at 30 s.
        On 429, the Retry-After header is respected if present.
        """
        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    data=data,
                    json=json,
                    cookies=cookies,
                    follow_redirects=(
                        follow_redirects if follow_redirects is not None else self._follow_redirects
                    ),
                    timeout=timeout,
                )

                # ---- check for retryable HTTP status ----
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    if attempt < self._max_retries:
                        wait = self._backoff(attempt, response)
                        logger.warning(
                            "HTTP %d from %s %s (attempt %d/%d). Retrying in %.1fs...",
                            response.status_code,
                            method,
                            url,
                            attempt + 1,
                            self._max_retries + 1,
                            wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    else:
                        logger.error(
                            "HTTP %d from %s %s after %d attempts, giving up.",
                            response.status_code,
                            method,
                            url,
                            self._max_retries + 1,
                        )

                return response

            except _NETWORK_ERRORS as exc:
                last_error = exc
                if attempt < self._max_retries:
                    wait = self._backoff(attempt)
                    logger.warning(
                        "%s on %s %s (attempt %d/%d). Retrying in %.1fs...",
                        type(exc).__name__,
                        method,
                        url,
                        attempt + 1,
                        self._max_retries + 1,
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "%s on %s %s after %d attempts: %s",
                        type(exc).__name__,
                        method,
                        url,
                        self._max_retries + 1,
                        exc,
                    )

        # All retries exhausted
        if last_error is not None:
            raise last_error
        # Should never happen, but satisfy the type checker
        raise httpx.ReadError("All retries exhausted with no response")

    # ------------------------------------------------------------------
    # Back-off calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _backoff(
        attempt: int,
        response: httpx.Response | None = None,
    ) -> float:
        """
        Compute wait time with exponential back-off + jitter, capped.

        If *response* is a 429 with a Retry-After header, that value is
        used as a floor.
        """
        base = min((2**attempt) + random.uniform(0, 1), _MAX_BACKOFF)

        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    ra = float(retry_after)
                    base = max(base, min(ra, _MAX_BACKOFF))
                except ValueError:
                    pass

        return base

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def head(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("HEAD", url, **kwargs)

    async def get_text(self, url: str, **kwargs) -> str:
        """GET request returning response text."""
        response = await self.get(url, **kwargs)
        response.raise_for_status()
        return response.text

    async def get_json(self, url: str, **kwargs) -> Any:
        """GET request returning parsed JSON."""
        response = await self.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def post_json(self, url: str, **kwargs) -> Any:
        """POST request returning parsed JSON."""
        response = await self.post(url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def resolve_redirect(self, url: str) -> str:
        """Follow redirects and return the final URL.

        Tries HEAD first (lighter); falls back to GET if HEAD fails.
        Both attempts go through the normal retry loop.
        """
        try:
            response = await self.head(url, follow_redirects=True)
            return str(response.url)
        except (httpx.HTTPError, httpx.HTTPStatusError):
            response = await self.get(url, follow_redirects=True)
            return str(response.url)

    async def get_cookies_from_response(self, url: str, **kwargs) -> dict[str, str]:
        """Make a request and return cookies set by the response."""
        response = await self.get(url, **kwargs)
        return dict(response.cookies)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
