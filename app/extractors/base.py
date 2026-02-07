"""
Base extractor class that all platform-specific extractors inherit from.
Ported from yt-dlp's InfoExtractor base class patterns.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from ..core.cookies import CookieManager, get_cookie_manager
from ..core.http_client import HTTPClient
from ..models.enums import FormatType, Platform, Quality
from ..models.request import ExtractRequest
from ..models.response import (
    ExtractResponse,
    FormatInfo,
)

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when media extraction fails."""

    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


class BaseExtractor(ABC):
    """
    Abstract base class for all platform extractors.

    Subclasses must implement:
    - platform: The Platform enum value
    - _extract(): The main extraction logic

    Provides common utilities for HTTP requests, HTML parsing,
    JSON extraction, cookie management and format handling.
    """

    platform: Platform

    def __init__(self):
        self._http: HTTPClient | None = None
        self._cookies: CookieManager = get_cookie_manager()

    @property
    def http(self) -> HTTPClient:
        """Lazy-initialized HTTP client."""
        if self._http is None:
            cookies = self._cookies.get_cookies(self.platform.value)
            self._http = HTTPClient(cookies=cookies if cookies else None)
        return self._http

    async def close(self):
        """Clean up HTTP client."""
        if self._http:
            await self._http.close()
            self._http = None

    async def extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str] | None = None,
    ) -> ExtractResponse:
        """
        Main extraction entry point. Calls _extract() and processes results.

        Args:
            media_id: The extracted media ID from URL matching
            url: The original URL
            request: The full extraction request with preferences
            params: Additional URL-matched parameters (user, subreddit, etc.)
        """
        try:
            response = await self._extract(media_id, url, request, params or {})

            # Post-process formats
            if response.formats:
                self._classify_formats(response)
                self._select_best_formats(response, request)

            return response
        except ExtractionError:
            raise
        except Exception as e:
            logger.exception(f"Extraction failed for {self.platform.value}: {e}")
            raise ExtractionError(
                f"Failed to extract from {self.platform.value}: {e!s}",
                error_code="extraction.failed",
            )
        finally:
            await self.close()

    @abstractmethod
    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """
        Perform platform-specific extraction.

        Must be implemented by each platform extractor.
        Should return an ExtractResponse with formats and metadata populated.
        """
        ...

    def _classify_formats(self, response: ExtractResponse):
        """Classify formats as video_only, audio_only, or combined."""
        for fmt in response.formats:
            if fmt.format_type != FormatType.COMBINED:
                continue  # Already classified

            has_video = fmt.vcodec and fmt.vcodec != "none"
            has_audio = fmt.acodec and fmt.acodec != "none"

            if has_video and has_audio:
                fmt.format_type = FormatType.COMBINED
            elif has_video:
                fmt.format_type = FormatType.VIDEO_ONLY
            elif has_audio:
                fmt.format_type = FormatType.AUDIO_ONLY

    def _select_best_formats(self, response: ExtractResponse, request: ExtractRequest):
        """Select best video, audio and combined formats based on request preferences."""
        video_formats = [f for f in response.formats if f.format_type == FormatType.VIDEO_ONLY]
        audio_formats = [f for f in response.formats if f.format_type == FormatType.AUDIO_ONLY]
        combined_formats = [f for f in response.formats if f.format_type == FormatType.COMBINED]

        # Select best video
        if video_formats:
            target_height = self._quality_to_height(request.quality)
            if target_height and request.quality != Quality.BEST:
                # Find closest match to target quality
                video_formats.sort(key=lambda f: abs((f.height or 0) - target_height))
            else:
                # Sort by resolution descending
                video_formats.sort(
                    key=lambda f: (f.height or 0, f.width or 0, f.tbr or 0),
                    reverse=True,
                )
            response.best_video = video_formats[0]

        # Select best audio
        if audio_formats:
            audio_formats.sort(
                key=lambda f: f.abr or f.tbr or 0,
                reverse=True,
            )
            response.best_audio = audio_formats[0]

        # Select best combined
        if combined_formats:
            target_height = self._quality_to_height(request.quality)
            if target_height and request.quality != Quality.BEST:
                combined_formats.sort(key=lambda f: abs((f.height or 0) - target_height))
            else:
                combined_formats.sort(
                    key=lambda f: (f.height or 0, f.width or 0, f.tbr or 0),
                    reverse=True,
                )
            response.best_combined = combined_formats[0]

        # If no best_audio from audio-only formats, use audio from combined
        if not response.best_audio and combined_formats:
            best = max(combined_formats, key=lambda f: f.abr or f.tbr or 0)
            response.best_audio = best

    @staticmethod
    def _quality_to_height(quality: Quality) -> int | None:
        """Convert Quality enum to pixel height."""
        quality_map = {
            Quality.Q144: 144,
            Quality.Q240: 240,
            Quality.Q360: 360,
            Quality.Q480: 480,
            Quality.Q720: 720,
            Quality.Q1080: 1080,
            Quality.Q1440: 1440,
            Quality.Q2160: 2160,
            Quality.Q4320: 4320,
        }
        return quality_map.get(quality)

    # === Common utility methods ===

    def _make_format(
        self,
        url: str,
        format_id: str | None = None,
        ext: str | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        vcodec: str | None = None,
        acodec: str | None = None,
        abr: float | None = None,
        vbr: float | None = None,
        tbr: float | None = None,
        filesize: int | None = None,
        filesize_approx: int | None = None,
        format_type: FormatType = FormatType.COMBINED,
        quality_label: str | None = None,
        protocol: str | None = None,
        http_headers: dict[str, str] | None = None,
    ) -> FormatInfo:
        """Create a FormatInfo object with the given parameters."""
        return FormatInfo(
            url=url,
            format_id=format_id,
            ext=ext,
            width=width,
            height=height,
            fps=fps,
            vcodec=vcodec,
            acodec=acodec,
            abr=abr,
            vbr=vbr,
            tbr=tbr,
            filesize=filesize,
            filesize_approx=filesize_approx,
            format_type=format_type,
            quality_label=quality_label,
            protocol=protocol,
            http_headers=http_headers,
        )

    async def _download_webpage(self, url: str, **kwargs) -> str:
        """Download a webpage and return the HTML text."""
        return await self.http.get_text(url, **kwargs)

    async def _download_json(self, url: str, **kwargs) -> Any:
        """Download and parse JSON from a URL."""
        return await self.http.get_json(url, **kwargs)

    async def _post_json(self, url: str, **kwargs) -> Any:
        """POST request and parse JSON response."""
        return await self.http.post_json(url, **kwargs)

    def _search_regex(
        self,
        pattern: str,
        text: str,
        name: str = "value",
        default: Any = None,
        group: int | str = 1,
        flags: int = 0,
    ) -> Any:
        """Search for a regex pattern in text. Returns default if not found."""
        match = re.search(pattern, text, flags)
        if match:
            try:
                return match.group(group)
            except (IndexError, re.error):
                return default
        if default is not None:
            return default
        raise ExtractionError(f"Unable to extract {name}")

    def _search_json(
        self,
        start_pattern: str,
        text: str,
        name: str = "JSON",
        default: Any = None,
    ) -> Any:
        """Search for JSON data following a pattern in text."""
        match = re.search(rf"{start_pattern}\s*[=:]\s*", text)
        if not match:
            if default is not None:
                return default
            raise ExtractionError(f"Unable to find {name}")

        # Try to find the JSON object/array
        start = match.end()
        if start >= len(text):
            return default

        bracket = text[start]
        if bracket not in ("{", "["):
            return default

        end_bracket = "}" if bracket == "{" else "]"
        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == bracket:
                depth += 1
            elif c == end_bracket:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return default

        return default

    def _get_cookie(self, name: str) -> str | None:
        """Get a cookie value for this platform."""
        return self._cookies.get_cookie(self.platform.value, name)

    def _get_cookies(self) -> dict[str, str]:
        """Get all cookies for this platform."""
        return self._cookies.get_cookies(self.platform.value)

    def _has_cookies(self) -> bool:
        """Check if cookies are available for this platform."""
        return self._cookies.has_cookies(self.platform.value)

    def _get_cookie_header(self) -> str:
        """Get cookie header string for this platform."""
        return self._cookies.get_cookie_header(self.platform.value)
