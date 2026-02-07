"""Tests for HTTP client retry and cookie logic."""

from app.core.http_client import (
    _MAX_BACKOFF,
    _NETWORK_ERRORS,
    _RETRYABLE_STATUS_CODES,
    HTTPClient,
    get_random_user_agent,
)


class TestRetryConfig:
    def test_retryable_status_codes(self):
        assert 429 in _RETRYABLE_STATUS_CODES
        assert 500 in _RETRYABLE_STATUS_CODES
        assert 502 in _RETRYABLE_STATUS_CODES
        assert 503 in _RETRYABLE_STATUS_CODES
        assert 504 in _RETRYABLE_STATUS_CODES
        # Client errors (except 429) are NOT retried
        assert 400 not in _RETRYABLE_STATUS_CODES
        assert 401 not in _RETRYABLE_STATUS_CODES
        assert 403 not in _RETRYABLE_STATUS_CODES
        assert 404 not in _RETRYABLE_STATUS_CODES

    def test_max_backoff_is_capped(self):
        assert _MAX_BACKOFF == 30.0

    def test_network_error_types(self):
        # Must catch at least timeout, connect, read, write
        error_names = {cls.__name__ for cls in _NETWORK_ERRORS}
        assert "TimeoutException" in error_names
        assert "ConnectError" in error_names
        assert "ReadError" in error_names
        assert "WriteError" in error_names
        assert "PoolTimeout" in error_names


class TestBackoff:
    def test_attempt_0(self):
        wait = HTTPClient._backoff(0)
        assert 1.0 <= wait <= 2.0  # 2^0 + jitter(0,1)

    def test_attempt_1(self):
        wait = HTTPClient._backoff(1)
        assert 2.0 <= wait <= 3.0

    def test_caps_at_max(self):
        wait = HTTPClient._backoff(100)  # huge attempt number
        assert wait <= _MAX_BACKOFF


class TestClientInit:
    def test_default_headers_set(self):
        client = HTTPClient()
        assert "User-Agent" in client._default_headers

    def test_custom_headers_override(self):
        client = HTTPClient(headers={"X-Custom": "test"})
        assert client._default_headers["X-Custom"] == "test"

    def test_cookies_stored(self):
        client = HTTPClient(cookies={"SID": "abc"})
        assert client._cookies == {"SID": "abc"}

    def test_update_cookies(self):
        client = HTTPClient(cookies={"A": "1"})
        client.update_cookies({"B": "2"})
        assert client._cookies == {"A": "1", "B": "2"}


class TestUserAgent:
    def test_random_ua_returns_string(self):
        ua = get_random_user_agent()
        assert isinstance(ua, str)
        assert "Mozilla" in ua

    def test_ua_pool_not_empty(self):
        from app.core.http_client import _USER_AGENTS

        assert len(_USER_AGENTS) >= 3
