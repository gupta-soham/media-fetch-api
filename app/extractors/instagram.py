"""
Instagram extractor - extracts video/audio from Instagram posts, reels, stories.
Ported from cobalt's instagram.js and yt-dlp's instagram.py.

Supports:
- Posts (/p/), Reels (/reel/), IGTV (/tv/)
- Carousel/multi-media posts
- Stories (with cookies)
- Cookie-based authentication for private content

Uses multiple fallback methods:
1. GraphQL API (i.instagram.com)
2. HTML embed page parsing
3. Mobile API with bearer token
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
    clean_html,
    float_or_none,
    format_date,
    int_or_none,
    str_or_none,
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

# Instagram App ID (from yt-dlp and cobalt)
_IG_APP_ID = "936619743392459"

# Instagram GraphQL API
_IG_API_BASE = "https://i.instagram.com/api/v1"
_IG_WEB_API = "https://www.instagram.com/api/v1"

# Common headers for Instagram API requests
_IG_HEADERS = {
    "X-IG-App-ID": _IG_APP_ID,
    "X-IG-WWW-Claim": "0",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; SM-S908B) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/112.0.0.0 Mobile Safari/537.36"
    ),
}

# Bearer token for mobile API fallback
_IG_BEARER_TOKEN = (
    "IGT:2:eyJkc191c2VyX2lkIjoiMCIsImRzX3VzZXJfaWRfb3ZlcnJpZGUiOiIwIiwiYXV0aF90eXBlIjowfQ=="
)


def _shortcode_to_media_id(shortcode: str) -> str:
    """Convert Instagram shortcode to numeric media ID."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + alphabet.index(char)
    return str(media_id)


class InstagramExtractor(BaseExtractor):
    """Instagram media extractor."""

    platform = Platform.INSTAGRAM

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Instagram."""
        shortcode = media_id

        # Detect if this is a story URL
        is_story = "/stories/" in url
        if is_story:
            return await self._extract_story(media_id, url, request, params)

        # Try multiple extraction methods
        media_data = None
        errors = []

        # Method 1: GraphQL API
        try:
            media_data = await self._extract_graphql(shortcode)
        except Exception as e:
            errors.append(f"GraphQL: {e}")
            logger.debug(f"Instagram GraphQL failed: {e}")

        # Method 2: Web API (media info endpoint)
        if not media_data:
            try:
                numeric_id = _shortcode_to_media_id(shortcode)
                media_data = await self._extract_web_api(numeric_id)
            except Exception as e:
                errors.append(f"WebAPI: {e}")
                logger.debug(f"Instagram Web API failed: {e}")

        # Method 3: Embed page parsing
        if not media_data:
            try:
                media_data = await self._extract_embed(shortcode)
            except Exception as e:
                errors.append(f"Embed: {e}")
                logger.debug(f"Instagram embed failed: {e}")

        # Method 4: Mobile API with bearer token
        if not media_data and self._has_cookies():
            try:
                numeric_id = _shortcode_to_media_id(shortcode)
                media_data = await self._extract_mobile_api(numeric_id)
            except Exception as e:
                errors.append(f"MobileAPI: {e}")
                logger.debug(f"Instagram Mobile API failed: {e}")

        if not media_data:
            raise ExtractionError(
                f"Could not extract Instagram media. Tried: {'; '.join(errors)}",
                error_code="instagram.extraction_failed",
            )

        return media_data

    async def _extract_graphql(self, shortcode: str) -> ExtractResponse | None:
        """Extract using Instagram GraphQL API."""
        url = f"{_IG_WEB_API}/media/{shortcode}/info/"

        headers = dict(_IG_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()
            csrf = self._get_cookie("csrftoken")
            if csrf:
                headers["X-CSRFToken"] = csrf

        response = await self.http.get(url, headers=headers)
        if response.status_code != 200:
            raise ExtractionError(f"GraphQL API returned {response.status_code}")

        data = response.json()
        items = data.get("items", [])
        if not items:
            raise ExtractionError("No items in GraphQL response")

        return self._parse_media_item(items[0], shortcode)

    async def _extract_web_api(self, numeric_id: str) -> ExtractResponse | None:
        """Extract using Instagram web API."""
        url = f"{_IG_API_BASE}/media/{numeric_id}/info/"

        headers = dict(_IG_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()
            csrf = self._get_cookie("csrftoken")
            if csrf:
                headers["X-CSRFToken"] = csrf

        response = await self.http.get(url, headers=headers)
        if response.status_code != 200:
            raise ExtractionError(f"Web API returned {response.status_code}")

        data = response.json()
        items = data.get("items", [])
        if not items:
            raise ExtractionError("No items in Web API response")

        return self._parse_media_item(items[0], numeric_id)

    async def _extract_embed(self, shortcode: str) -> ExtractResponse | None:
        """Extract from the embed page."""
        embed_url = f"https://www.instagram.com/p/{shortcode}/embed/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        html = await self._download_webpage(embed_url, headers=headers)

        # Try to find video URL in embed page
        video_url = self._search_regex(
            r'"video_url"\s*:\s*"([^"]+)"',
            html,
            "video_url",
            default=None,
        )

        if not video_url:
            # Try alternate patterns
            video_url = self._search_regex(
                r'<video[^>]+src="([^"]+)"',
                html,
                "video_src",
                default=None,
            )

        if not video_url:
            raise ExtractionError("Could not find video in embed page")

        video_url = video_url.replace("\\u0026", "&").replace("\\/", "/")

        # Extract metadata from embed
        title = self._search_regex(
            r"<title>([^<]+)</title>", html, "title", default="Instagram Video"
        )

        thumbnail = self._search_regex(
            r'"thumbnail_src"\s*:\s*"([^"]+)"',
            html,
            "thumbnail",
            default=None,
        )
        if thumbnail:
            thumbnail = thumbnail.replace("\\u0026", "&").replace("\\/", "/")

        formats = [
            FormatInfo(
                url=video_url,
                format_id="embed",
                ext="mp4",
                format_type=FormatType.COMBINED,
            )
        ]

        return ExtractResponse(
            platform=Platform.INSTAGRAM,
            id=shortcode,
            title=clean_html(title),
            formats=formats,
            thumbnail=thumbnail,
        )

    async def _extract_mobile_api(self, numeric_id: str) -> ExtractResponse | None:
        """Extract using Instagram mobile API with bearer token."""
        url = f"{_IG_API_BASE}/media/{numeric_id}/info/"

        headers = {
            "Authorization": f"Bearer {_IG_BEARER_TOKEN}",
            "User-Agent": (
                "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; "
                "samsung; SM-S908B; b0q; exynos2200; en_US)"
            ),
        }

        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        response = await self.http.get(url, headers=headers)
        if response.status_code != 200:
            raise ExtractionError(f"Mobile API returned {response.status_code}")

        data = response.json()
        items = data.get("items", [])
        if not items:
            raise ExtractionError("No items in Mobile API response")

        return self._parse_media_item(items[0], numeric_id)

    async def _extract_story(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract Instagram story."""
        user = params.get("user", "")
        story_pk = media_id

        if not self._has_cookies():
            raise ExtractionError(
                "Instagram cookies are required to access stories. "
                "Add cookies to cookies/instagram.txt",
                error_code="instagram.cookies_required",
            )

        headers = dict(_IG_HEADERS)
        headers["Cookie"] = self._get_cookie_header()
        csrf = self._get_cookie("csrftoken")
        if csrf:
            headers["X-CSRFToken"] = csrf

        # Get story reel
        reel_url = f"{_IG_API_BASE}/feed/reels_media/"
        params_dict = {"reel_ids": story_pk}

        response = await self.http.get(
            reel_url,
            headers=headers,
            params=params_dict,
        )

        if response.status_code != 200:
            raise ExtractionError(f"Stories API returned {response.status_code}")

        data = response.json()
        reels = data.get("reels_media", []) or data.get("reels", {})

        if not reels:
            raise ExtractionError("No stories found")

        # Find the specific story item
        formats = []
        title = f"Instagram Story by @{user}"

        if isinstance(reels, list):
            for reel in reels:
                for item in reel.get("items", []):
                    if str(item.get("pk")) == story_pk or not story_pk:
                        video_versions = item.get("video_versions", [])
                        for v in video_versions:
                            formats.append(
                                FormatInfo(
                                    url=v.get("url"),
                                    width=v.get("width"),
                                    height=v.get("height"),
                                    ext="mp4",
                                    format_type=FormatType.COMBINED,
                                )
                            )
                        if not video_versions:
                            # Image story
                            candidates = item.get("image_versions2", {}).get("candidates", [])
                            for c in candidates:
                                formats.append(
                                    FormatInfo(
                                        url=c.get("url"),
                                        width=c.get("width"),
                                        height=c.get("height"),
                                        ext="jpg",
                                        format_type=FormatType.COMBINED,
                                    )
                                )

        return ExtractResponse(
            platform=Platform.INSTAGRAM,
            id=story_pk,
            title=title,
            formats=formats,
        )

    def _parse_media_item(self, item: dict, media_id: str) -> ExtractResponse:
        """Parse a media item from any Instagram API response."""
        media_type = item.get("media_type")
        # 1 = photo, 2 = video, 8 = carousel

        formats = []
        title = None
        duration = None
        thumbnail = None

        # Get caption/title
        caption = traverse_obj(item, ("caption", "text"))
        user = traverse_obj(item, ("user", "username"))
        title = (
            f"@{user}: {caption[:100]}"
            if caption and user
            else f"Instagram Post by @{user}"
            if user
            else "Instagram Post"
        )

        # Get thumbnail
        thumbnail = traverse_obj(item, ("image_versions2", "candidates", 0, "url"))

        if media_type == 8:
            # Carousel - extract all items
            carousel_media = item.get("carousel_media", [])
            for idx, cm in enumerate(carousel_media):
                cm_type = cm.get("media_type")
                if cm_type == 2:  # Video in carousel
                    video_versions = cm.get("video_versions", [])
                    for v in video_versions:
                        fmt_url = v.get("url")
                        if fmt_url:
                            formats.append(
                                FormatInfo(
                                    url=fmt_url,
                                    format_id=f"carousel_{idx}",
                                    width=v.get("width"),
                                    height=v.get("height"),
                                    ext="mp4",
                                    format_type=FormatType.COMBINED,
                                )
                            )
                elif cm_type == 1:  # Photo in carousel
                    candidates = traverse_obj(cm, ("image_versions2", "candidates")) or []
                    for c in candidates[:1]:  # Just the best quality
                        fmt_url = c.get("url")
                        if fmt_url:
                            formats.append(
                                FormatInfo(
                                    url=fmt_url,
                                    format_id=f"carousel_{idx}_img",
                                    width=c.get("width"),
                                    height=c.get("height"),
                                    ext="jpg",
                                    format_type=FormatType.COMBINED,
                                )
                            )
        elif media_type == 2:
            # Single video
            duration = float_or_none(item.get("video_duration"))
            video_versions = item.get("video_versions", [])

            for v in video_versions:
                fmt_url = v.get("url")
                if fmt_url:
                    formats.append(
                        FormatInfo(
                            url=fmt_url,
                            width=v.get("width"),
                            height=v.get("height"),
                            ext="mp4",
                            format_type=FormatType.COMBINED,
                        )
                    )
        elif media_type == 1:
            # Single photo
            candidates = traverse_obj(item, ("image_versions2", "candidates")) or []
            for c in candidates:
                fmt_url = c.get("url")
                if fmt_url:
                    formats.append(
                        FormatInfo(
                            url=fmt_url,
                            width=c.get("width"),
                            height=c.get("height"),
                            ext="jpg",
                            format_type=FormatType.COMBINED,
                        )
                    )

        # Build metadata
        metadata = MediaMetadata(
            uploader=user,
            uploader_id=str_or_none(traverse_obj(item, ("user", "pk"))),
            description=caption,
            like_count=int_or_none(item.get("like_count")),
            comment_count=int_or_none(item.get("comment_count")),
            upload_date=format_date(item.get("taken_at")),
        )

        return ExtractResponse(
            platform=Platform.INSTAGRAM,
            id=media_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )
