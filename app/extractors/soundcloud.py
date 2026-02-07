"""
SoundCloud extractor - extracts audio from SoundCloud tracks.
Ported from cobalt's soundcloud.js and yt-dlp's soundcloud.py.

Supports:
- Individual tracks
- Multiple transcoding formats (opus, mp3, aac)
- Track metadata (title, artist, artwork, duration)
- Client ID extraction from SoundCloud JS
"""

import logging
import re

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
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_SC_API_BASE = "https://api-v2.soundcloud.com"
_SC_HOMEPAGE = "https://soundcloud.com"

_SC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://soundcloud.com",
    "Referer": "https://soundcloud.com/",
}


class SoundCloudExtractor(BaseExtractor):
    """SoundCloud media extractor."""

    platform = Platform.SOUNDCLOUD
    _client_id: str | None = None

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract audio from SoundCloud."""
        user = params.get("user", "")
        track_slug = media_id

        # Resolve short links
        if "on.soundcloud.com" in url:
            try:
                resolved = await self.http.resolve_redirect(url)
                url = resolved
            except Exception as e:
                logger.warning(f"Failed to resolve SoundCloud short link: {e}")

        # Get client ID
        client_id = await self._get_client_id()
        if not client_id:
            raise ExtractionError(
                "Could not obtain SoundCloud client ID",
                error_code="soundcloud.no_client_id",
            )

        # Resolve the track URL to get track data
        track_url = url
        if not track_url.startswith("http"):
            track_url = f"https://soundcloud.com/{user}/{track_slug}"

        resolve_url = f"{_SC_API_BASE}/resolve"
        params_dict = {
            "url": track_url,
            "client_id": client_id,
        }

        response = await self.http.get(
            resolve_url,
            headers=_SC_HEADERS,
            params=params_dict,
        )

        if response.status_code != 200:
            raise ExtractionError(
                f"SoundCloud resolve API returned {response.status_code}",
                error_code="soundcloud.resolve_failed",
            )

        track_data = response.json()

        if track_data.get("kind") != "track":
            raise ExtractionError(
                "URL does not point to a SoundCloud track",
                error_code="soundcloud.not_a_track",
            )

        return await self._parse_track(track_data, client_id)

    async def _get_client_id(self) -> str | None:
        """
        Extract SoundCloud client_id from their JavaScript files.
        Ported from cobalt's soundcloud.js and yt-dlp's soundcloud.py.
        """
        if self._client_id:
            return self._client_id

        try:
            # Fetch homepage to get JS file URLs
            homepage = await self._download_webpage(
                _SC_HOMEPAGE,
                headers={"User-Agent": _SC_HEADERS["User-Agent"]},
            )

            # Find script URLs (a-v2.sndcdn.com)
            script_urls = re.findall(
                r'src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"',
                homepage,
            )

            # Also try to get version for cache
            re.search(r'__sc_version\s*=\s*"(\d+)"', homepage)

            # Search JS files for client_id
            for script_url in reversed(script_urls):  # Usually in last files
                try:
                    js_content = await self._download_webpage(
                        script_url,
                        headers={"User-Agent": _SC_HEADERS["User-Agent"]},
                    )
                    # Look for client_id pattern
                    match = re.search(
                        r'client_id\s*:\s*"([a-zA-Z0-9]{32})"',
                        js_content,
                    )
                    if not match:
                        match = re.search(
                            r"client_id=([a-zA-Z0-9]{32})",
                            js_content,
                        )
                    if match:
                        self._client_id = match.group(1)
                        logger.info(f"Found SoundCloud client_id: {self._client_id[:8]}...")
                        return self._client_id
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"Failed to extract SoundCloud client_id: {e}")

        return None

    async def _parse_track(self, track: dict, client_id: str) -> ExtractResponse:
        """Parse SoundCloud track data into ExtractResponse."""
        track_id = str(track.get("id", ""))
        title = track.get("title", "")
        duration = float_or_none(track.get("duration"), scale=1000)

        # Get artwork
        artwork_url = track.get("artwork_url") or ""
        if artwork_url:
            # Replace size for higher quality
            thumbnail = artwork_url.replace("-large", "-t500x500")
        else:
            thumbnail = traverse_obj(track, ("user", "avatar_url"))

        # Extract transcoding formats
        formats = []
        transcodings = traverse_obj(track, ("media", "transcodings")) or []
        track_auth = track.get("track_authorization", "")

        for tc in transcodings:
            tc_url = tc.get("url")
            if not tc_url:
                continue

            # Skip DRM-protected or snipped content
            if tc.get("snipped") or tc.get("is_snipped"):
                continue

            # Get format info
            fmt = tc.get("format", {})
            protocol = fmt.get("protocol", "")
            mime_type = fmt.get("mime_type", "")
            preset = tc.get("preset", "")
            quality = tc.get("quality", "")

            # Determine codec and extension
            ext = "mp3"
            acodec = "mp3"
            if "opus" in preset.lower() or "opus" in mime_type:
                ext = "opus"
                acodec = "opus"
            elif "aac" in mime_type:
                ext = "m4a"
                acodec = "aac"

            # Get the actual stream URL
            stream_url = await self._get_stream_url(tc_url, client_id, track_auth)
            if not stream_url:
                continue

            formats.append(
                FormatInfo(
                    url=stream_url,
                    format_id=f"{preset}_{quality}" if preset else quality,
                    ext=ext,
                    acodec=acodec,
                    vcodec="none",
                    format_type=FormatType.AUDIO_ONLY,
                    quality_label=preset or quality,
                    protocol="hls" if protocol == "hls" else "https",
                )
            )

        if not formats:
            raise ExtractionError(
                "No playable formats found for SoundCloud track",
                error_code="soundcloud.no_formats",
            )

        # Sort formats: prefer opus > mp3 > aac
        codec_priority = {"opus": 3, "mp3": 2, "aac": 1}
        formats.sort(
            key=lambda f: codec_priority.get(f.acodec or "", 0),
            reverse=True,
        )

        # Build metadata
        user = track.get("user", {})
        metadata = MediaMetadata(
            uploader=user.get("username"),
            uploader_id=str(user.get("id", "")),
            uploader_url=user.get("permalink_url"),
            description=track.get("description"),
            view_count=int_or_none(track.get("playback_count")),
            like_count=int_or_none(track.get("likes_count") or track.get("favoritings_count")),
            comment_count=int_or_none(track.get("comment_count")),
            repost_count=int_or_none(track.get("reposts_count")),
            upload_date=format_date(track.get("created_at")),
            tags=track.get("tag_list", "").split() if track.get("tag_list") else None,
            categories=[track.get("genre")] if track.get("genre") else None,
        )

        return ExtractResponse(
            platform=Platform.SOUNDCLOUD,
            id=track_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    async def _get_stream_url(
        self, transcoding_url: str, client_id: str, track_auth: str
    ) -> str | None:
        """Get the actual streaming URL from a transcoding URL."""
        params = {
            "client_id": client_id,
            "track_authorization": track_auth,
        }

        try:
            response = await self.http.get(
                transcoding_url,
                headers=_SC_HEADERS,
                params=params,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("url")
        except Exception as e:
            logger.debug(f"Failed to get stream URL: {e}")

        return None
