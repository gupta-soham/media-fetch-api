"""
Vimeo extractor - extracts video/audio from Vimeo.
Ported from cobalt's vimeo.js and yt-dlp's vimeo.py.

Supports:
- Regular videos
- Unlisted videos (with hash)
- Password-protected videos
- Multiple quality formats (progressive + HLS)
- OAuth bearer token authentication
"""

import logging

from ..models.enums import FormatType, Platform
from ..models.request import ExtractRequest
from ..models.response import (
    ExtractResponse,
    FormatInfo,
    MediaMetadata,
)
from ..utils.helpers import (
    float_or_none,
    format_date,
    int_or_none,
    parse_m3u8_attributes,
    str_or_none,
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

# Vimeo API
_VIMEO_API = "https://api.vimeo.com"

# Vimeo OAuth client credentials (from cobalt's vimeo.js)
_VIMEO_CLIENT_ID = "b0fa1cf52f268e1f03d3f5bab7e62651b27e0e38"
_VIMEO_CLIENT_SECRET = (
    "RmduFDMYmbecHnaWO17Brd+iGHPaLJGQVzGKu5cpLUxEygHDDPFSJBMsz7oTJ7HuQh9+"
    "wFBPUKcKJrRZNKK/2MK3YIHQ3BnCpDWEYPTNbf7RlC56SmCdBbUKJUhOjGk"
)

# Resolution mapping (from cobalt)
_RESOLUTION_MAP = {
    3840: 2160,
    2560: 1440,
    1920: 1080,
    1280: 720,
    960: 540,
    854: 480,
    640: 360,
    426: 240,
}


class VimeoExtractor(BaseExtractor):
    """Vimeo media extractor."""

    platform = Platform.VIMEO
    _bearer_token: str | None = None

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Vimeo."""
        video_id = media_id

        # Get bearer token
        bearer = await self._get_bearer_token()
        if not bearer:
            raise ExtractionError(
                "Could not obtain Vimeo bearer token",
                error_code="vimeo.auth_failed",
            )

        # Fetch video info from API
        api_url = f"{_VIMEO_API}/videos/{video_id}"
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/vnd.vimeo.*+json;version=3.4.10",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        params_dict = {
            "fields": (
                "uri,name,description,duration,width,height,created_time,"
                "modified_time,pictures,files,download,play,status,"
                "user,metadata,stats,categories,tags"
            ),
        }

        # Handle password-protected videos
        if request.password:
            params_dict["password"] = request.password

        try:
            response = await self.http.get(api_url, headers=headers, params=params_dict)

            if response.status_code == 403:
                raise ExtractionError(
                    "Video is private or password-protected",
                    error_code="vimeo.forbidden",
                )
            elif response.status_code == 404:
                raise ExtractionError(
                    "Video not found",
                    error_code="vimeo.not_found",
                )
            elif response.status_code != 200:
                raise ExtractionError(
                    f"Vimeo API returned {response.status_code}",
                    error_code="vimeo.api_error",
                )

            video_data = response.json()
        except ExtractionError:
            raise
        except Exception as e:
            raise ExtractionError(f"Failed to fetch Vimeo video info: {e}")

        return await self._parse_video(video_data, video_id, bearer)

    async def _get_bearer_token(self) -> str | None:
        """Get Vimeo OAuth bearer token using client credentials."""
        if self._bearer_token:
            return self._bearer_token

        # Check for cookie-based bearer
        if self._has_cookies():
            self._get_cookie("vuid")
            # Cookie-based auth would use session, not bearer
            # For now, use client credentials

        # Client credentials grant
        import base64

        auth = base64.b64encode(f"{_VIMEO_CLIENT_ID}:{_VIMEO_CLIENT_SECRET}".encode()).decode()

        try:
            response = await self.http.post(
                f"{_VIMEO_API}/oauth/authorize/client",
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json",
                    "Accept": "application/vnd.vimeo.*+json;version=3.4.10",
                },
                json={
                    "grant_type": "client_credentials",
                    "scope": "public",
                },
            )

            if response.status_code == 200:
                data = response.json()
                self._bearer_token = data.get("access_token")
                return self._bearer_token
        except Exception as e:
            logger.warning(f"Failed to get Vimeo bearer token: {e}")

        return None

    async def _parse_video(self, data: dict, video_id: str, bearer: str) -> ExtractResponse:
        """Parse Vimeo video API response."""
        title = data.get("name", "")
        description = data.get("description", "")
        duration = float_or_none(data.get("duration"))
        int_or_none(data.get("width"))
        int_or_none(data.get("height"))

        # Get thumbnail
        pictures = data.get("pictures", {})
        thumbnail = None
        sizes = pictures.get("sizes", [])
        if sizes:
            # Get the largest thumbnail
            thumbnail = sizes[-1].get("link")

        formats = []

        # Method 1: Progressive downloads (files array)
        files = data.get("files", [])
        for f in files:
            file_url = f.get("link") or f.get("link_secure")
            if not file_url:
                continue

            f_width = int_or_none(f.get("width"))
            f_height = int_or_none(f.get("height"))
            f_quality = f.get("quality", "")
            f_type = f.get("type", "")
            f_size = int_or_none(f.get("size"))

            # Determine extension
            ext = "mp4"
            if "webm" in f_type:
                ext = "webm"

            formats.append(
                FormatInfo(
                    url=file_url,
                    format_id=f_quality or f"file_{f_height}p",
                    ext=ext,
                    width=f_width,
                    height=f_height,
                    filesize=f_size,
                    format_type=FormatType.COMBINED,
                    quality_label=f_quality,
                )
            )

        # Method 2: Download links
        downloads = data.get("download", [])
        for dl in downloads:
            dl_url = dl.get("link")
            if not dl_url:
                continue

            dl_width = int_or_none(dl.get("width"))
            dl_height = int_or_none(dl.get("height"))
            dl_quality = dl.get("quality", "")

            formats.append(
                FormatInfo(
                    url=dl_url,
                    format_id=f"download_{dl_quality}",
                    ext="mp4",
                    width=dl_width,
                    height=dl_height,
                    filesize=int_or_none(dl.get("size")),
                    format_type=FormatType.COMBINED,
                    quality_label=dl_quality,
                )
            )

        # Method 3: Play (HLS/DASH)
        play = data.get("play", {})
        hls = play.get("hls", {})
        hls_url = hls.get("link")

        if hls_url:
            # Parse HLS master playlist for formats
            try:
                hls_formats = await self._parse_hls_master(hls_url)
                formats.extend(hls_formats)
            except Exception as e:
                logger.debug(f"Failed to parse Vimeo HLS: {e}")
                # Add raw HLS as single format
                formats.append(
                    FormatInfo(
                        url=hls_url,
                        format_id="hls",
                        ext="mp4",
                        protocol="hls",
                        format_type=FormatType.COMBINED,
                        quality_label="HLS",
                    )
                )

        # Progressive play
        progressive = play.get("progressive", [])
        for pg in progressive:
            pg_url = pg.get("url")
            if not pg_url:
                continue

            pg_width = int_or_none(pg.get("width"))
            pg_height = int_or_none(pg.get("height"))

            formats.append(
                FormatInfo(
                    url=pg_url,
                    format_id=f"progressive_{pg_height}p" if pg_height else "progressive",
                    ext="mp4",
                    width=pg_width,
                    height=pg_height,
                    fps=float_or_none(pg.get("fps")),
                    format_type=FormatType.COMBINED,
                    quality_label=pg.get("quality"),
                )
            )

        if not formats:
            raise ExtractionError(
                "No playable formats found for Vimeo video",
                error_code="vimeo.no_formats",
            )

        # Build metadata
        user = data.get("user", {})
        stats = data.get("stats", {})

        metadata = MediaMetadata(
            uploader=user.get("name"),
            uploader_id=str_or_none(traverse_obj(user, ("uri",))),
            uploader_url=user.get("link"),
            description=description,
            upload_date=format_date(data.get("created_time")),
            view_count=int_or_none(stats.get("plays")),
            like_count=int_or_none(
                traverse_obj(data, ("metadata", "connections", "likes", "total"))
            ),
            comment_count=int_or_none(
                traverse_obj(data, ("metadata", "connections", "comments", "total"))
            ),
            tags=[t.get("name") for t in (data.get("tags") or []) if t.get("name")],
            categories=[c.get("name") for c in (data.get("categories") or []) if c.get("name")],
        )

        return ExtractResponse(
            platform=Platform.VIMEO,
            id=video_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    async def _parse_hls_master(self, hls_url: str) -> list[FormatInfo]:
        """Parse an HLS master playlist into individual format entries."""
        formats = []

        try:
            content = await self._download_webpage(hls_url)

            lines = content.strip().split("\n")
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.startswith("#EXT-X-STREAM-INF:"):
                    attrs = parse_m3u8_attributes(line.split(":", 1)[1])
                    # Next line is the URL
                    if i + 1 < len(lines):
                        stream_url = lines[i + 1].strip()
                        if not stream_url.startswith("http"):
                            # Relative URL
                            base = hls_url.rsplit("/", 1)[0]
                            stream_url = f"{base}/{stream_url}"

                        bandwidth = int_or_none(attrs.get("BANDWIDTH"))
                        resolution = attrs.get("RESOLUTION", "")
                        attrs.get("CODECS", "")

                        width = None
                        height = None
                        if "x" in resolution:
                            parts = resolution.split("x")
                            width = int_or_none(parts[0])
                            height = int_or_none(parts[1])

                        formats.append(
                            FormatInfo(
                                url=stream_url,
                                format_id=f"hls_{height}p" if height else "hls",
                                ext="mp4",
                                width=width,
                                height=height,
                                tbr=float_or_none(bandwidth, scale=1000),
                                protocol="hls",
                                format_type=FormatType.COMBINED,
                                quality_label=f"{height}p" if height else None,
                            )
                        )
                        i += 1
                i += 1
        except Exception as e:
            logger.debug(f"HLS parsing failed: {e}")

        return formats
