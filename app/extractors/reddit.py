"""
Reddit extractor - extracts video/audio from Reddit posts.
Ported from cobalt's reddit.js and yt-dlp's reddit.py.

Supports:
- Reddit-hosted videos (v.redd.it)
- GIF posts
- Short links (v.redd.it, redd.it)
- Separate video and audio streams
- OAuth2 authentication
"""

import base64
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

_REDDIT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Reddit OAuth2 credentials (anonymous access)
_REDDIT_CLIENT_ID = "1t0Fk8PCQO9INg"
_REDDIT_CLIENT_SECRET = ""


class RedditExtractor(BaseExtractor):
    """Reddit media extractor."""

    platform = Platform.REDDIT
    _oauth_token: str | None = None

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Reddit."""
        post_id = media_id

        # Resolve short links
        if "v.redd.it" in url or "redd.it" in url:
            try:
                resolved = await self.http.resolve_redirect(url)
                url = resolved
                # Re-extract post ID
                id_match = re.search(r"/comments/([a-zA-Z0-9]+)", url)
                if id_match:
                    post_id = id_match.group(1)
            except Exception as e:
                logger.warning(f"Failed to resolve Reddit short link: {e}")

        # Fetch post data via JSON API
        post_data = await self._fetch_post_data(post_id, url)

        if not post_data:
            raise ExtractionError(
                "Could not fetch Reddit post data",
                error_code="reddit.fetch_failed",
            )

        return self._parse_post(post_data, post_id)

    async def _get_oauth_token(self) -> str:
        """Get OAuth2 token for Reddit API access."""
        if self._oauth_token:
            return self._oauth_token

        # Use cookie-based auth if available
        if self._has_cookies():
            bearer = self._get_cookie("token_v2")
            if bearer:
                self._oauth_token = bearer
                return bearer

        # Otherwise, get anonymous token
        auth = base64.b64encode(f"{_REDDIT_CLIENT_ID}:{_REDDIT_CLIENT_SECRET}".encode()).decode()

        response = await self.http.post(
            "https://www.reddit.com/api/v1/access_token",
            headers={
                "Authorization": f"Basic {auth}",
                "User-Agent": "MediaFetchAPI/1.0",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data="grant_type=client_credentials&device_id=DO_NOT_TRACK_THIS_DEVICE",
        )

        if response.status_code == 200:
            data = response.json()
            self._oauth_token = data.get("access_token")
            return self._oauth_token or ""

        return ""

    async def _fetch_post_data(self, post_id: str, url: str) -> dict | None:
        """Fetch Reddit post data using the JSON API."""
        # Build clean JSON API URL using old.reddit.com which is more reliable
        json_url = f"https://old.reddit.com/comments/{post_id}.json"

        headers = dict(_REDDIT_HEADERS)
        headers["Accept"] = "application/json"

        try:
            response = await self.http.get(json_url, headers=headers)
            logger.debug(f"Reddit JSON API status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
        except Exception as e:
            logger.debug(f"Reddit JSON API failed: {e}")

        # Try with www.reddit.com and raw_json param
        json_url2 = f"https://www.reddit.com/comments/{post_id}/.json?raw_json=1"
        try:
            response = await self.http.get(json_url2, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
        except Exception as e:
            logger.debug(f"Reddit www JSON API failed: {e}")

        # Try OAuth API
        try:
            token = await self._get_oauth_token()
            if token:
                oauth_headers = {
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "MediaFetchAPI/1.0",
                }
                api_url = f"https://oauth.reddit.com/comments/{post_id}.json"
                response = await self.http.get(api_url, headers=oauth_headers)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list) and len(data) > 0:
                        return data[0]
        except Exception as e:
            logger.debug(f"Reddit OAuth API failed: {e}")

        return None

    def _parse_post(self, data: dict, post_id: str) -> ExtractResponse:
        """Parse Reddit post data."""
        children = traverse_obj(data, ("data", "children", 0, "data")) or {}

        if not children:
            raise ExtractionError("No post data found in Reddit response")

        title = children.get("title", "Reddit Post")
        children.get("subreddit", "")
        author = children.get("author", "")
        url = children.get("url", "")

        formats = []
        thumbnail = None
        duration = None

        # Check for Reddit-hosted video
        children.get("is_video", False)
        media = children.get("media") or {}
        reddit_video = media.get("reddit_video") or {}

        # Also check secure_media
        if not reddit_video:
            secure_media = children.get("secure_media") or {}
            reddit_video = secure_media.get("reddit_video") or {}

        # Check crosspost
        if not reddit_video:
            crosspost = traverse_obj(children, ("crosspost_parent_list", 0))
            if crosspost:
                cp_media = crosspost.get("media") or crosspost.get("secure_media") or {}
                reddit_video = cp_media.get("reddit_video") or {}

        if reddit_video:
            # Reddit-hosted video
            fallback_url = reddit_video.get("fallback_url")
            hls_url = reddit_video.get("hls_url")
            reddit_video.get("dash_url")
            duration = float_or_none(reddit_video.get("duration"))
            height = int_or_none(reddit_video.get("height"))
            width = int_or_none(reddit_video.get("width"))

            if fallback_url:
                # The fallback URL is video-only. Audio is at a separate URL.
                # Video URL format: https://v.redd.it/{id}/DASH_{quality}.mp4
                video_url = fallback_url.split("?")[0]
                base_url = re.sub(r"/DASH_\d+\.mp4$", "", video_url)
                base_url = re.sub(r"/DASH_\d+$", "", base_url)

                # Add main video format
                formats.append(
                    FormatInfo(
                        url=video_url,
                        format_id=f"video_{height}p" if height else "video",
                        ext="mp4",
                        width=width,
                        height=height,
                        vcodec="avc1",
                        acodec="none",
                        format_type=FormatType.VIDEO_ONLY,
                        quality_label=f"{height}p" if height else None,
                    )
                )

                # Try to find additional quality variants
                for quality in [1080, 720, 480, 360, 240]:
                    if quality == height:
                        continue
                    variant_url = f"{base_url}/DASH_{quality}.mp4"
                    formats.append(
                        FormatInfo(
                            url=variant_url,
                            format_id=f"video_{quality}p",
                            ext="mp4",
                            height=quality,
                            vcodec="avc1",
                            acodec="none",
                            format_type=FormatType.VIDEO_ONLY,
                            quality_label=f"{quality}p",
                        )
                    )

                # Find audio stream
                # Reddit audio is at DASH_AUDIO_128.mp4 or DASH_audio.mp4
                audio_variants = [
                    f"{base_url}/DASH_AUDIO_128.mp4",
                    f"{base_url}/DASH_audio.mp4",
                    f"{base_url}/DASH_AUDIO_64.mp4",
                    f"{base_url}/audio",
                ]

                for audio_url in audio_variants:
                    formats.append(
                        FormatInfo(
                            url=audio_url,
                            format_id="audio",
                            ext="mp4",
                            acodec="mp4a",
                            vcodec="none",
                            format_type=FormatType.AUDIO_ONLY,
                        )
                    )
                    break  # Just add the first variant; client will verify

            # Add HLS if available
            if hls_url:
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

        # Check for GIF
        elif children.get("post_hint") == "image" and url.endswith(".gif"):
            formats.append(
                FormatInfo(
                    url=url,
                    format_id="gif",
                    ext="gif",
                    format_type=FormatType.COMBINED,
                )
            )

        # Check for Reddit gallery
        elif children.get("is_gallery"):
            gallery_data = children.get("gallery_data", {})
            media_metadata = children.get("media_metadata", {})
            for item in gallery_data.get("items", []):
                mid = item.get("media_id")
                if mid and mid in media_metadata:
                    mm = media_metadata[mid]
                    if mm.get("e") == "AnimatedImage":
                        gif_url = traverse_obj(mm, ("s", "gif"))
                        mp4_url = traverse_obj(mm, ("s", "mp4"))
                        if mp4_url:
                            formats.append(
                                FormatInfo(url=mp4_url, ext="mp4", format_type=FormatType.COMBINED)
                            )
                        elif gif_url:
                            formats.append(
                                FormatInfo(url=gif_url, ext="gif", format_type=FormatType.COMBINED)
                            )

        # Thumbnail
        thumbnail = children.get("thumbnail")
        if thumbnail in ("default", "self", "nsfw", "spoiler", ""):
            thumbnail = None
        if not thumbnail:
            preview = traverse_obj(children, ("preview", "images", 0, "source", "url"))
            if preview:
                thumbnail = preview.replace("&amp;", "&")

        if not formats:
            raise ExtractionError(
                "No downloadable media found in Reddit post. "
                "The post may be a text post, link, or image.",
                error_code="reddit.no_media",
            )

        metadata = MediaMetadata(
            uploader=author,
            uploader_id=author,
            uploader_url=f"https://www.reddit.com/user/{author}",
            description=children.get("selftext", ""),
            view_count=int_or_none(children.get("view_count")),
            like_count=int_or_none(children.get("ups")),
            comment_count=int_or_none(children.get("num_comments")),
            upload_date=format_date(children.get("created_utc")),
        )

        return ExtractResponse(
            platform=Platform.REDDIT,
            id=post_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )
