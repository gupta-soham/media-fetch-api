"""
Pinterest extractor - extracts video/images from Pinterest pins.
Ported from cobalt's pinterest.js and yt-dlp's pinterest.py.

Supports:
- Pin videos
- Pin images
- Short links (pin.it)
- Multiple quality formats from video_list
"""

import json
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
    clean_html,
    float_or_none,
    int_or_none,
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_PINTEREST_API = "https://www.pinterest.com/resource/PinResource/get/"
_PINTEREST_SHORTLINK_API = "https://api.pinterest.com/url_shortener"

_PINTEREST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.pinterest.com/",
    "X-Requested-With": "XMLHttpRequest",
}


class PinterestExtractor(BaseExtractor):
    """Pinterest media extractor."""

    platform = Platform.PINTEREST

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Pinterest."""
        pin_id = media_id

        # Resolve short links (pin.it)
        if "pin.it" in url:
            try:
                resolved = await self.http.resolve_redirect(url)
                url = resolved
                id_match = re.search(r"/pin/(\d+)", url)
                if id_match:
                    pin_id = id_match.group(1)
            except Exception as e:
                logger.warning(f"Failed to resolve pin.it link: {e}")

        # Method 1: Pinterest Resource API
        try:
            return await self._extract_api(pin_id)
        except ExtractionError:
            raise
        except Exception as e:
            logger.warning(f"Pinterest API failed: {e}")

        # Method 2: HTML parsing (fallback)
        try:
            return await self._extract_html(pin_id, url)
        except Exception as e:
            logger.warning(f"Pinterest HTML extraction failed: {e}")

        raise ExtractionError(
            "Could not extract Pinterest media",
            error_code="pinterest.extraction_failed",
        )

    async def _extract_api(self, pin_id: str) -> ExtractResponse:
        """Extract using Pinterest Resource API."""
        data = {
            "options": {
                "id": pin_id,
                "field_set_key": "unauth_react_main_pin",
            },
        }

        params = {
            "source_url": f"/pin/{pin_id}/",
            "data": json.dumps(data),
        }

        headers = dict(_PINTEREST_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        response = await self.http.get(
            _PINTEREST_API,
            headers=headers,
            params=params,
        )

        if response.status_code != 200:
            raise ExtractionError(f"Pinterest API returned {response.status_code}")

        result = response.json()
        pin_data = traverse_obj(result, ("resource_response", "data"))

        if not pin_data:
            raise ExtractionError("No pin data in API response")

        return self._parse_pin(pin_data, pin_id)

    async def _extract_html(self, pin_id: str, url: str) -> ExtractResponse:
        """Extract from Pinterest HTML page."""
        page_url = f"https://www.pinterest.com/pin/{pin_id}/"
        headers = {"User-Agent": _PINTEREST_HEADERS["User-Agent"]}

        html = await self._download_webpage(page_url, headers=headers)

        # Check for PinNotFound
        if '"__typename":"PinNotFound"' in html or '"__typename": "PinNotFound"' in html:
            raise ExtractionError("Pin not found", error_code="pinterest.not_found")

        formats = []

        # Extract video URL from HTML (cobalt pattern)
        video_match = re.search(
            r'"url"\s*:\s*"(https://v1\.pinimg\.com/videos/[^"]+\.mp4[^"]*)"',
            html,
        )
        if video_match:
            video_url = video_match.group(1).replace("\\u002F", "/").replace("\\/", "/")
            formats.append(
                FormatInfo(
                    url=video_url,
                    format_id="video",
                    ext="mp4",
                    format_type=FormatType.COMBINED,
                )
            )

        # Extract image URL
        if not formats:
            img_match = re.search(
                r'src="(https://i\.pinimg\.com/[^"]+\.(jpg|gif))"',
                html,
            )
            if img_match:
                formats.append(
                    FormatInfo(
                        url=img_match.group(1),
                        format_id="image",
                        ext=img_match.group(2),
                        format_type=FormatType.COMBINED,
                    )
                )

        if not formats:
            raise ExtractionError("No media found in Pinterest page")

        title = self._search_regex(
            r"<title>([^<]+)</title>", html, "title", default="Pinterest Pin"
        )
        title = clean_html(title)

        return ExtractResponse(
            platform=Platform.PINTEREST,
            id=pin_id,
            title=title,
            formats=formats,
        )

    def _parse_pin(self, data: dict, pin_id: str) -> ExtractResponse:
        """Parse Pinterest pin data."""
        formats = []
        title = data.get("title") or data.get("grid_title") or ""
        description = data.get("description") or data.get("description_html") or ""
        thumbnail = None

        # Check for video
        videos = data.get("videos") or {}
        video_list = videos.get("video_list", {})

        if video_list:
            for quality_key, video_info in video_list.items():
                video_url = video_info.get("url")
                if not video_url:
                    continue

                width = int_or_none(video_info.get("width"))
                height = int_or_none(video_info.get("height"))
                float_or_none(video_info.get("duration"), scale=1000)

                formats.append(
                    FormatInfo(
                        url=video_url,
                        format_id=quality_key,
                        ext="mp4",
                        width=width,
                        height=height,
                        format_type=FormatType.COMBINED,
                        quality_label=quality_key,
                    )
                )

        # Check for story pin data (multi-page pins)
        story_pin = data.get("story_pin_data")
        if story_pin and not formats:
            pages = story_pin.get("pages", [])
            for idx, page in enumerate(pages):
                blocks = page.get("blocks", [])
                for block in blocks:
                    video = block.get("video", {})
                    vl = video.get("video_list", {})
                    for qk, vi in vl.items():
                        v_url = vi.get("url")
                        if v_url:
                            formats.append(
                                FormatInfo(
                                    url=v_url,
                                    format_id=f"story_{idx}_{qk}",
                                    ext="mp4",
                                    width=int_or_none(vi.get("width")),
                                    height=int_or_none(vi.get("height")),
                                    format_type=FormatType.COMBINED,
                                )
                            )

        # Get images if no video
        if not formats:
            images = data.get("images", {})
            if images:
                # Get highest quality image
                for size_key in ["orig", "1200x", "736x", "474x", "236x"]:
                    if size_key in images:
                        img_url = images[size_key].get("url")
                        if img_url:
                            formats.append(
                                FormatInfo(
                                    url=img_url,
                                    format_id=size_key,
                                    ext="jpg",
                                    width=int_or_none(images[size_key].get("width")),
                                    height=int_or_none(images[size_key].get("height")),
                                    format_type=FormatType.COMBINED,
                                )
                            )

            # Also check image_large_url
            large_url = data.get("image_large_url")
            if large_url and not formats:
                formats.append(
                    FormatInfo(
                        url=large_url,
                        format_id="large",
                        ext="jpg",
                        format_type=FormatType.COMBINED,
                    )
                )

        if not formats:
            raise ExtractionError(
                "No media found in Pinterest pin",
                error_code="pinterest.no_media",
            )

        # Thumbnail
        thumbnail = traverse_obj(data, ("images", "236x", "url"))
        if not thumbnail:
            thumbnail = data.get("image_medium_url")

        # Title
        if not title and description:
            title = description[:100]
        elif not title:
            title = f"Pinterest Pin {pin_id}"

        # Metadata
        pinner = data.get("pinner", {})
        metadata = MediaMetadata(
            uploader=pinner.get("full_name") or pinner.get("username"),
            uploader_id=pinner.get("username"),
            description=clean_html(description) if description else None,
            repost_count=int_or_none(data.get("repin_count")),
            comment_count=int_or_none(data.get("comment_count")),
        )

        return ExtractResponse(
            platform=Platform.PINTEREST,
            id=pin_id,
            title=title,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )
