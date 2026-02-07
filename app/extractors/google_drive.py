"""
Google Drive extractor - extracts video/audio from Google Drive files.
Ported from gdown's parse_url.py/download.py and yt-dlp's googledrive.py.

Supports:
- Direct file links (/file/d/ID/)
- Open links (/open?id=ID)
- UC links (/uc?id=ID)
- Google Docs/Sheets/Slides exports (video/audio only)
- Playback API for streaming formats
- Cookie-based authentication for restricted files
- MIME type checking (video/audio only per requirement)
"""

import logging
import re
from urllib.parse import parse_qs, urlparse

from ..models.enums import FormatType, Platform
from ..models.request import ExtractRequest
from ..models.response import (
    ExtractResponse,
    FormatInfo,
)
from ..utils.helpers import (
    float_or_none,
    int_or_none,
    parse_content_disposition,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

# Google Drive URLs
_DRIVE_DOWNLOAD = "https://drive.usercontent.google.com/download"
_DRIVE_PLAYBACK = "https://content-workspacevideo-pa.googleapis.com/v1/drive/media"

# Allowed MIME types (video and audio only, per gdown requirement)
_ALLOWED_MIME_PREFIXES = ("video/", "audio/")

_DRIVE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


def parse_drive_url(url: str) -> str | None:
    """
    Parse a Google Drive URL and extract the file ID.
    Ported from gdown's parse_url.py.
    """
    parsed = urlparse(url)

    # /file/d/ID/ or /file/u/0/d/ID/
    m = re.search(r"/(?:file/)?(?:u/\d+/)?d/([a-zA-Z0-9_-]+)", parsed.path)
    if m:
        return m.group(1)

    # Query parameter: ?id=ID
    qs = parse_qs(parsed.query)
    file_id = qs.get("id", [None])[0]
    if file_id:
        return file_id

    # /document/d/ID/ or /presentation/d/ID/ or /spreadsheets/d/ID/
    m = re.search(r"/(?:document|presentation|spreadsheets)/d/([a-zA-Z0-9_-]+)", parsed.path)
    if m:
        return m.group(1)

    return None


class GoogleDriveExtractor(BaseExtractor):
    """Google Drive media extractor."""

    platform = Platform.GOOGLE_DRIVE

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Google Drive."""
        file_id = media_id

        # Try to parse the file ID from URL if the matched ID seems incomplete
        parsed_id = parse_drive_url(url)
        if parsed_id:
            file_id = parsed_id

        # Method 1: Try Playback API (for streaming formats)
        try:
            playback_data = await self._fetch_playback(file_id)
            if playback_data:
                result = self._parse_playback(playback_data, file_id)
                if result:
                    return result
        except Exception as e:
            logger.debug(f"Google Drive Playback API failed: {e}")

        # Method 2: Direct download URL
        try:
            return await self._extract_direct(file_id, url)
        except ExtractionError:
            raise
        except Exception as e:
            logger.warning(f"Google Drive direct download failed: {e}")

        raise ExtractionError(
            "Could not extract media from Google Drive. "
            "The file may be private, not a video/audio file, or require authentication.",
            error_code="google_drive.extraction_failed",
        )

    async def _fetch_playback(self, file_id: str) -> dict | None:
        """Fetch playback data from Google Workspace Video API."""
        playback_url = f"{_DRIVE_PLAYBACK}/{file_id}/playback"

        headers = dict(_DRIVE_HEADERS)
        headers["Accept"] = "application/json"

        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        params = {
            "key": "AIzaSyC1eQ1xj69IdTMeii5r7brs3R90SLLhN0E",
        }

        try:
            response = await self.http.get(playback_url, headers=headers, params=params)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.debug(f"Playback API error: {e}")

        return None

    def _parse_playback(self, data: dict, file_id: str) -> ExtractResponse | None:
        """Parse Playback API response."""
        streaming_data = data.get("mediaStreamingData", {})
        format_data = streaming_data.get("formatStreamingData", {})

        formats = []

        # Adaptive formats (separate video/audio)
        for fmt in format_data.get("adaptiveTranscodes", []):
            fmt_url = fmt.get("url")
            if not fmt_url:
                continue

            mime_type = fmt.get("mimeType", "")
            itag = fmt.get("itag")

            # Parse mime type
            vcodec = None
            acodec = None
            ext = "mp4"

            if mime_type.startswith("video/"):
                vcodec = "avc1"
                acodec = "none"
            elif mime_type.startswith("audio/"):
                vcodec = "none"
                acodec = "mp4a"
                ext = "m4a"
            else:
                continue

            formats.append(
                FormatInfo(
                    url=fmt_url,
                    format_id=str(itag) if itag else None,
                    ext=ext,
                    width=int_or_none(fmt.get("width")),
                    height=int_or_none(fmt.get("height")),
                    vcodec=vcodec,
                    acodec=acodec,
                    tbr=float_or_none(fmt.get("bitrate"), scale=1000),
                    filesize=int_or_none(fmt.get("contentLength")),
                    format_type=FormatType.VIDEO_ONLY
                    if vcodec != "none"
                    else FormatType.AUDIO_ONLY,
                )
            )

        # Progressive formats (combined)
        for fmt in format_data.get("progressiveTranscodes", []):
            fmt_url = fmt.get("url")
            if not fmt_url:
                continue

            formats.append(
                FormatInfo(
                    url=fmt_url,
                    format_id=str(fmt.get("itag", "")),
                    ext="mp4",
                    width=int_or_none(fmt.get("width")),
                    height=int_or_none(fmt.get("height")),
                    format_type=FormatType.COMBINED,
                )
            )

        if not formats:
            return None

        # Get metadata
        media_metadata = data.get("mediaMetadata", {})
        title = media_metadata.get("title", f"Google Drive File {file_id}")
        duration = float_or_none(media_metadata.get("durationMs"), scale=1000)

        # Thumbnail
        thumbnail = None
        thumbs = media_metadata.get("thumbnails", [])
        if thumbs:
            thumbnail = thumbs[-1].get("url")

        return ExtractResponse(
            platform=Platform.GOOGLE_DRIVE,
            id=file_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
        )

    async def _extract_direct(self, file_id: str, url: str) -> ExtractResponse:
        """Extract using direct download approach (from gdown)."""
        # Step 1: Request the download URL
        download_url = f"{_DRIVE_DOWNLOAD}?id={file_id}&export=download&confirm=t"

        headers = dict(_DRIVE_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        response = await self.http.get(
            download_url,
            headers=headers,
            follow_redirects=False,
        )

        # Check if we need to handle a confirmation page
        final_url = download_url
        content_type = response.headers.get("content-type", "")

        if response.status_code in (302, 303, 307):
            # Follow redirect
            final_url = response.headers.get("location", download_url)
        elif "text/html" in content_type:
            # Confirmation page - extract the actual download URL
            html = response.text
            confirm_match = re.search(r'href="(/uc\?export=download[^"]+)"', html)
            if confirm_match:
                final_url = f"https://drive.google.com{confirm_match.group(1)}"
                final_url = final_url.replace("&amp;", "&")
            else:
                # Try the confirm=t approach
                final_url = f"{_DRIVE_DOWNLOAD}?id={file_id}&export=download&confirm=t"
        else:
            final_url = str(response.url)

        # Get file metadata via HEAD request
        try:
            head_response = await self.http.head(final_url, headers=headers)
            content_type = head_response.headers.get("content-type", "")
            content_length = int_or_none(head_response.headers.get("content-length"))
            content_disp = head_response.headers.get("content-disposition", "")
        except Exception:
            content_type = ""
            content_length = None
            content_disp = ""

        # Check MIME type - only allow video/audio
        if content_type and not any(
            content_type.startswith(prefix) for prefix in _ALLOWED_MIME_PREFIXES
        ):
            raise ExtractionError(
                f"File is not a video or audio file (MIME: {content_type}). "
                "Only video and audio files are supported for Google Drive.",
                error_code="google_drive.not_media",
            )

        # Get filename
        filename = parse_content_disposition(content_disp)
        title = filename or f"Google Drive File {file_id}"

        # Determine extension
        ext = "mp4"
        if filename:
            ext_match = re.search(r"\.(\w+)$", filename)
            if ext_match:
                ext = ext_match.group(1).lower()
        elif "audio" in content_type:
            ext = "mp3"

        # Determine format type
        if content_type.startswith("video/"):
            format_type = FormatType.COMBINED
        elif content_type.startswith("audio/"):
            format_type = FormatType.AUDIO_ONLY
        else:
            format_type = FormatType.COMBINED

        formats = [
            FormatInfo(
                url=final_url,
                format_id="source",
                ext=ext,
                filesize=content_length,
                format_type=format_type,
                quality_label="source",
            )
        ]

        return ExtractResponse(
            platform=Platform.GOOGLE_DRIVE,
            id=file_id,
            title=title,
            formats=formats,
        )
