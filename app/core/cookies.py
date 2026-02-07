"""
Cookie management for per-platform authentication.

Supports loading cookies from Netscape/Mozilla format files stored in the
cookies/ directory. Each platform has its own cookie file
(e.g. cookies/youtube.txt).

Robustness features:
- Graceful handling of corrupt / malformed cookie files (never crashes,
  always logs the problem and falls back to manual line-by-line parsing).
- Expired cookies are loaded but logged as warnings so the operator knows
  when a refresh is needed.
- Thread-safe singleton via module-level lock.
- reload() can be called at any time to re-read files from disk (useful
  after a 401/403 signals stale cookies).
- set_cookies() for runtime cookie injection (e.g. after OAuth refresh).
"""

import logging
import threading
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

from ..config import get_cookie_dir

logger = logging.getLogger(__name__)

# Services that support cookie-based authentication
COOKIE_SERVICES = frozenset(
    {
        "youtube",
        "instagram",
        "instagram_bearer",
        "twitter",
        "reddit",
        "vimeo",
        "facebook",
        "tiktok",
        "pinterest",
        "google_drive",
    }
)

_singleton_lock = threading.Lock()


class CookieManager:
    """
    Manages cookies for different platforms.

    Loads cookies from Netscape/Mozilla format text files in the configured
    cookie directory. Each platform can have its own cookie file.

    Cookie file format (Netscape):
        # domain  include_subdomains  path  secure  expiry  name  value
    """

    def __init__(self, cookie_dir: Path | None = None):
        self._cookie_dir = cookie_dir or get_cookie_dir()
        self._jars: dict[str, MozillaCookieJar] = {}
        self._raw_cookies: dict[str, dict[str, str]] = {}
        self._load_errors: dict[str, str] = {}
        self._load_all()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_all(self):
        """Load all cookie files from the cookie directory."""
        if not self._cookie_dir.exists():
            logger.info(
                "Cookie directory %s does not exist, creating it",
                self._cookie_dir,
            )
            self._cookie_dir.mkdir(parents=True, exist_ok=True)
            return

        for cookie_file in self._cookie_dir.glob("*.txt"):
            service = cookie_file.stem
            if service in COOKIE_SERVICES:
                self._load_cookie_file(service, cookie_file)

    def _load_cookie_file(self, service: str, path: Path):
        """Load a single cookie file in Netscape/Mozilla format."""
        # Reset any previous error for this service
        self._load_errors.pop(service, None)

        try:
            jar = MozillaCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
            self._jars[service] = jar

            cookies: dict[str, str] = {}
            now = time.time()
            expired_count = 0

            for cookie in jar:
                cookies[cookie.name] = cookie.value or ""
                # Warn about expired cookies
                if cookie.expires and cookie.expires < now:
                    expired_count += 1

            self._raw_cookies[service] = cookies

            msg = f"Loaded {len(cookies)} cookies for {service} from {path}"
            if expired_count:
                msg += f" ({expired_count} expired)"
                logger.warning(msg)
            else:
                logger.info(msg)

        except Exception as e:
            logger.warning(
                "MozillaCookieJar failed for %s (%s), trying manual parse: %s",
                service,
                path,
                e,
            )
            self._load_cookie_file_manual(service, path)

    def _load_cookie_file_manual(self, service: str, path: Path):
        """
        Manually parse a cookie file line-by-line.

        This is the fallback when Python's MozillaCookieJar rejects the file
        (e.g. missing magic header, odd whitespace, BOM, etc.).
        Supports both tab-separated Netscape format and simple key=value format.
        """
        cookies: dict[str, str] = {}
        line_errors = 0

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            err = f"Cannot read cookie file {path}: {e}"
            logger.error(err)
            self._load_errors[service] = err
            return

        for lineno, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) >= 7:
                name = parts[5].strip()
                value = parts[6].strip()
                if name:
                    cookies[name] = value
            elif "=" in line:
                # Simple key=value fallback
                name, _, value = line.partition("=")
                name = name.strip()
                value = value.strip()
                if name:
                    cookies[name] = value
            else:
                line_errors += 1
                if line_errors <= 3:
                    logger.debug(
                        "Skipping unparseable line %d in %s: %s",
                        lineno,
                        path,
                        line[:80],
                    )

        if cookies:
            self._raw_cookies[service] = cookies
            msg = f"Manually parsed {len(cookies)} cookies for {service} from {path}"
            if line_errors:
                msg += f" ({line_errors} lines skipped)"
            logger.info(msg)
        else:
            err = f"No cookies could be parsed from {path} ({line_errors} bad lines)"
            logger.error(err)
            self._load_errors[service] = err

    # ------------------------------------------------------------------
    # Getters
    # ------------------------------------------------------------------

    def get_cookies(self, service: str) -> dict[str, str]:
        """Get cookies for a service as a dictionary."""
        return dict(self._raw_cookies.get(service, {}))

    def get_cookie_jar(self, service: str) -> MozillaCookieJar | None:
        """Get the MozillaCookieJar for a service."""
        return self._jars.get(service)

    def get_cookie(self, service: str, name: str) -> str | None:
        """Get a specific cookie value for a service."""
        return self._raw_cookies.get(service, {}).get(name)

    def has_cookies(self, service: str) -> bool:
        """Check if cookies are available for a service."""
        return bool(self._raw_cookies.get(service))

    def get_cookie_header(self, service: str) -> str:
        """Get cookies formatted as a Cookie header string."""
        cookies = self._raw_cookies.get(service, {})
        if not cookies:
            return ""
        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    def get_load_errors(self) -> dict[str, str]:
        """Return a map of services that had load errors."""
        return dict(self._load_errors)

    # ------------------------------------------------------------------
    # Setters
    # ------------------------------------------------------------------

    def set_cookies(self, service: str, cookies: dict[str, str]):
        """
        Merge cookies for a service (in-memory only, not persisted to disk).
        Useful for runtime token refreshes (e.g. Reddit OAuth).
        """
        if service not in self._raw_cookies:
            self._raw_cookies[service] = {}
        self._raw_cookies[service].update(cookies)

    def clear_cookies(self, service: str):
        """Remove all in-memory cookies for a service."""
        self._raw_cookies.pop(service, None)
        self._jars.pop(service, None)

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def reload(self, service: str | None = None):
        """
        Reload cookies from disk.

        Call with a service name to reload just that platform, or without
        arguments to reload everything. Useful after receiving a 401/403
        that suggests cookies have gone stale and the operator has refreshed
        the file on disk.
        """
        if service:
            cookie_file = self._cookie_dir / f"{service}.txt"
            if cookie_file.exists():
                logger.info("Reloading cookies for %s", service)
                self._load_cookie_file(service, cookie_file)
            else:
                logger.warning(
                    "Cookie file %s does not exist, clearing in-memory cookies for %s",
                    cookie_file,
                    service,
                )
                self.clear_cookies(service)
        else:
            logger.info("Reloading all cookies")
            self._jars.clear()
            self._raw_cookies.clear()
            self._load_errors.clear()
            self._load_all()


# ------------------------------------------------------------------
# Thread-safe singleton
# ------------------------------------------------------------------

_cookie_manager: CookieManager | None = None


def get_cookie_manager() -> CookieManager:
    global _cookie_manager
    if _cookie_manager is None:
        with _singleton_lock:
            # Double-checked locking
            if _cookie_manager is None:
                _cookie_manager = CookieManager()
    return _cookie_manager
