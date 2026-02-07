"""
Twitch extractor - extracts video from Twitch clips and VODs.
Ported from cobalt's twitch.js and yt-dlp's twitch.py.

Supports:
- Clips (clips.twitch.tv and twitch.tv/channel/clip/)
- VODs (twitch.tv/videos/)
- GraphQL API with access tokens
- Multiple quality formats
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
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_TWITCH_GQL = "https://gql.twitch.tv/gql"
_TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

# GraphQL query hashes (from cobalt's twitch.js)
_CLIP_QUERY_HASH = "36b89d2507fce29e5ca551df756d27c1cfe079e2609642b4390aa4c35796eb11"


class TwitchExtractor(BaseExtractor):
    """Twitch media extractor."""

    platform = Platform.TWITCH

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Twitch."""
        # Determine if this is a clip or VOD
        if "/videos/" in url:
            return await self._extract_vod(media_id, url)
        else:
            return await self._extract_clip(media_id, url, params)

    async def _extract_clip(
        self, clip_slug: str, url: str, params: dict[str, str]
    ) -> ExtractResponse:
        """Extract a Twitch clip."""
        # Step 1: Get clip metadata via GraphQL
        gql_payload = [
            {
                "extensions": {
                    "persistedQuery": {
                        "sha256Hash": _CLIP_QUERY_HASH,
                        "version": 1,
                    }
                },
                "operationName": "ClipMetadata",
                "variables": {"slug": clip_slug},
            },
            {
                "extensions": {
                    "persistedQuery": {
                        "sha256Hash": "9bfcc0177bffc730bd5a5a89005869d2773480cf1738c592143b5c5c18a0199f",
                        "version": 1,
                    }
                },
                "operationName": "VideoAccessToken_Clip",
                "variables": {"slug": clip_slug},
            },
        ]

        headers = {
            "Client-ID": _TWITCH_CLIENT_ID,
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        response = await self.http.post(
            _TWITCH_GQL,
            headers=headers,
            json=gql_payload,
        )

        if response.status_code != 200:
            raise ExtractionError(
                f"Twitch GraphQL returned {response.status_code}",
                error_code="twitch.gql_failed",
            )

        gql_data = response.json()

        if not isinstance(gql_data, list) or len(gql_data) < 2:
            raise ExtractionError("Invalid Twitch GraphQL response")

        # Parse clip metadata
        clip_data = traverse_obj(gql_data[0], ("data", "clip"))
        if not clip_data:
            raise ExtractionError("Clip not found", error_code="twitch.clip_not_found")

        # Parse access token
        token_data = traverse_obj(gql_data[1], ("data", "clip"))
        play_token = token_data.get("playbackAccessToken", {}) if token_data else {}
        sig = play_token.get("signature", "")
        token = play_token.get("value", "")

        # Build formats from video qualities
        video_qualities = clip_data.get("videoQualities", [])
        formats = []

        for vq in video_qualities:
            source_url = vq.get("sourceURL")
            if not source_url:
                continue

            # Append access token
            full_url = f"{source_url}?sig={sig}&token={token}"

            quality = vq.get("quality", "")
            fps = int_or_none(vq.get("frameRate"))

            # Parse quality to height
            height = int_or_none(quality)
            width = None
            if height:
                width = int(height * 16 / 9)

            formats.append(
                FormatInfo(
                    url=full_url,
                    format_id=f"{quality}p{fps}" if fps and fps > 30 else f"{quality}p",
                    ext="mp4",
                    width=width,
                    height=height,
                    fps=float(fps) if fps else None,
                    format_type=FormatType.COMBINED,
                    quality_label=f"{quality}p{fps}" if fps and fps > 30 else f"{quality}p",
                )
            )

        if not formats:
            raise ExtractionError(
                "No video qualities found for clip",
                error_code="twitch.no_formats",
            )

        # Build metadata
        broadcaster = clip_data.get("broadcaster", {})
        curator = clip_data.get("curator", {})
        game = clip_data.get("game", {})

        title = clip_data.get("title", "Twitch Clip")
        duration = float_or_none(clip_data.get("durationSeconds"))
        thumbnail = clip_data.get("thumbnailURL")

        metadata = MediaMetadata(
            uploader=broadcaster.get("displayName"),
            uploader_id=broadcaster.get("login"),
            uploader_url=f"https://twitch.tv/{broadcaster.get('login', '')}",
            description=f"Clipped by {curator.get('displayName', 'unknown')}",
            view_count=int_or_none(clip_data.get("viewCount")),
            upload_date=format_date(clip_data.get("createdAt")),
            categories=[game.get("name")] if game.get("name") else None,
        )

        return ExtractResponse(
            platform=Platform.TWITCH,
            id=clip_slug,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    async def _extract_vod(self, vod_id: str, url: str) -> ExtractResponse:
        """Extract a Twitch VOD."""
        # Get access token for VOD
        gql_payload = {
            "operationName": "PlaybackAccessToken",
            "extensions": {
                "persistedQuery": {
                    "sha256Hash": "ed230aa1e33a07eafc741f4b0145a3bfe57a9ec2",
                    "version": 1,
                }
            },
            "variables": {
                "isLive": False,
                "login": "",
                "isVod": True,
                "vodID": vod_id,
                "playerType": "embed",
            },
        }

        headers = {
            "Client-ID": _TWITCH_CLIENT_ID,
            "Content-Type": "application/json",
        }

        response = await self.http.post(_TWITCH_GQL, headers=headers, json=gql_payload)

        if response.status_code != 200:
            raise ExtractionError(f"Twitch GQL returned {response.status_code}")

        data = response.json()
        token_data = traverse_obj(data, ("data", "videoPlaybackAccessToken"))
        if not token_data:
            raise ExtractionError("Could not get VOD access token")

        sig = token_data.get("signature", "")
        token = token_data.get("value", "")

        # Get HLS playlist
        hls_url = (
            f"https://usher.ttvnw.net/vod/{vod_id}.m3u8"
            f"?sig={sig}&token={token}"
            f"&allow_source=true&allow_audio_only=true"
            f"&player_backend=mediaplayer"
        )

        formats = []
        try:
            playlist = await self._download_webpage(hls_url)
            formats = self._parse_hls_playlist(playlist, hls_url)
        except Exception as e:
            logger.warning(f"Failed to parse VOD HLS: {e}")
            formats.append(
                FormatInfo(
                    url=hls_url,
                    format_id="hls",
                    ext="mp4",
                    protocol="hls",
                    format_type=FormatType.COMBINED,
                )
            )

        # Get VOD info via GQL
        info_payload = {
            "operationName": "VideoMetadata",
            "extensions": {
                "persistedQuery": {
                    "sha256Hash": "45111672eea2e507f8ba44d101a61862f9c56b11",
                    "version": 1,
                }
            },
            "variables": {"channelLogin": "", "videoID": vod_id},
        }

        title = "Twitch VOD"
        duration = None
        thumbnail = None
        metadata = None

        try:
            resp = await self.http.post(_TWITCH_GQL, headers=headers, json=info_payload)
            if resp.status_code == 200:
                vod_data = traverse_obj(resp.json(), ("data", "video"))
                if vod_data:
                    title = vod_data.get("title", title)
                    duration = float_or_none(vod_data.get("lengthSeconds"))
                    thumbnail = vod_data.get("previewThumbnailURL")
                    owner = vod_data.get("owner", {})
                    metadata = MediaMetadata(
                        uploader=owner.get("displayName"),
                        uploader_id=owner.get("login"),
                        view_count=int_or_none(vod_data.get("viewCount")),
                        upload_date=format_date(vod_data.get("createdAt")),
                    )
        except Exception:
            pass

        return ExtractResponse(
            platform=Platform.TWITCH,
            id=vod_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    def _parse_hls_playlist(self, content: str, base_url: str) -> list[FormatInfo]:
        """Parse HLS master playlist."""
        formats = []
        lines = content.strip().split("\n")

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                attrs = parse_m3u8_attributes(line.split(":", 1)[1])
                if i + 1 < len(lines):
                    stream_url = lines[i + 1].strip()

                    bandwidth = int_or_none(attrs.get("BANDWIDTH"))
                    resolution = attrs.get("RESOLUTION", "")
                    video = attrs.get("VIDEO", "")

                    width = None
                    height = None
                    if "x" in resolution:
                        parts = resolution.split("x")
                        width = int_or_none(parts[0])
                        height = int_or_none(parts[1])

                    # Determine quality name
                    quality_name = video or (f"{height}p" if height else "unknown")

                    formats.append(
                        FormatInfo(
                            url=stream_url,
                            format_id=quality_name,
                            ext="mp4",
                            width=width,
                            height=height,
                            tbr=float_or_none(bandwidth, scale=1000),
                            protocol="hls",
                            format_type=FormatType.COMBINED,
                            quality_label=quality_name,
                        )
                    )
                    i += 1
            i += 1

        return formats
