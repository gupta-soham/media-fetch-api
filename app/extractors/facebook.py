"""
Facebook extractor - extracts video from Facebook posts and watch pages.
Ported from cobalt's facebook.js and yt-dlp's facebook.py.

Supports:
- Regular video posts
- Facebook Watch (/watch/)
- Reels (/reel/)
- Short links (fb.watch)
- HD and SD quality variants
"""

import json
import logging
import re
from urllib.parse import unquote

from ..models.enums import FormatType, Platform
from ..models.request import ExtractRequest
from ..models.response import (
    ExtractResponse,
    FormatInfo,
    MediaMetadata,
)
from ..utils.helpers import (
    clean_html,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_FB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class FacebookExtractor(BaseExtractor):
    """Facebook media extractor."""

    platform = Platform.FACEBOOK

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Facebook."""
        video_id = media_id

        # Resolve short links (fb.watch)
        if "fb.watch" in url:
            try:
                resolved = await self.http.resolve_redirect(url)
                url = resolved
                # Try to re-extract video ID
                id_match = re.search(r"/(?:videos|watch|reel)/(\d+)", url)
                if id_match:
                    video_id = id_match.group(1)
            except Exception as e:
                logger.warning(f"Failed to resolve fb.watch link: {e}")

        # Resolve /share/v/ and /share/r/ links
        if "/share/" in url:
            try:
                resolved = await self.http.resolve_redirect(url)
                url = resolved
                id_match = re.search(r"/(?:videos|watch|reel)/(\d+)", url)
                if id_match:
                    video_id = id_match.group(1)
            except Exception as e:
                logger.warning(f"Failed to resolve share link: {e}")

        # Method 1: Fetch page and extract video URLs from HTML
        try:
            return await self._extract_from_html(video_id, url)
        except ExtractionError:
            raise
        except Exception as e:
            logger.warning(f"Facebook HTML extraction failed: {e}")

        raise ExtractionError(
            "Could not extract Facebook video",
            error_code="facebook.extraction_failed",
        )

    async def _extract_from_html(self, video_id: str, url: str) -> ExtractResponse:
        """Extract video URLs from Facebook page HTML."""
        # Build page URL
        if re.match(r"^\d+$", video_id):
            page_url = f"https://web.facebook.com/i/videos/{video_id}"
        else:
            page_url = url

        headers = dict(_FB_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        html = await self._download_webpage(page_url, headers=headers)

        formats = []
        title = None
        thumbnail = None

        # Extract video URLs from HTML
        # Pattern from cobalt's facebook.js: "browser_native_hd_url" and "browser_native_sd_url"
        hd_url = self._search_regex(
            r'"browser_native_hd_url"\s*:\s*"([^"]+)"',
            html,
            "HD URL",
            default=None,
        )
        sd_url = self._search_regex(
            r'"browser_native_sd_url"\s*:\s*"([^"]+)"',
            html,
            "SD URL",
            default=None,
        )

        # Also try playable_url and playable_url_quality_hd patterns
        if not hd_url:
            hd_url = self._search_regex(
                r'"playable_url_quality_hd"\s*:\s*"([^"]+)"',
                html,
                "HD URL alt",
                default=None,
            )
        if not sd_url:
            sd_url = self._search_regex(
                r'"playable_url"\s*:\s*"([^"]+)"',
                html,
                "SD URL alt",
                default=None,
            )

        # Clean up URLs (unescape)
        if hd_url:
            hd_url = hd_url.replace("\\/", "/").replace("\\u0025", "%")
            hd_url = unquote(hd_url) if "%25" in hd_url else hd_url
            formats.append(
                FormatInfo(
                    url=hd_url,
                    format_id="hd",
                    ext="mp4",
                    quality_label="HD",
                    format_type=FormatType.COMBINED,
                )
            )

        if sd_url:
            sd_url = sd_url.replace("\\/", "/").replace("\\u0025", "%")
            sd_url = unquote(sd_url) if "%25" in sd_url else sd_url
            formats.append(
                FormatInfo(
                    url=sd_url,
                    format_id="sd",
                    ext="mp4",
                    quality_label="SD",
                    format_type=FormatType.COMBINED,
                )
            )

        # Try DASH manifest
        self._search_regex(
            r'"dash_manifest"\s*:\s*"([^"]+)"',
            html,
            "DASH manifest",
            default=None,
        )
        self._search_regex(
            r'"dash_manifest_url"\s*:\s*"([^"]+)"',
            html,
            "DASH manifest URL",
            default=None,
        )

        # Try to find video data in JSON embedded in page
        if not formats:
            # Look for video data in various formats
            video_data_patterns = [
                r'"videoData"\s*:\s*(\[.+?\])\s*[,}]',
                r'"video"\s*:\s*(\{.+?"url"\s*:.+?\})',
                r"videoData\s*=\s*(\[.+?\])",
            ]

            for pattern in video_data_patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    try:
                        vdata = json.loads(match.group(1).replace("\\/", "/"))
                        if isinstance(vdata, list):
                            for item in vdata:
                                vid_url = (
                                    item.get("hd_src") or item.get("sd_src") or item.get("src")
                                )
                                if vid_url:
                                    is_hd = bool(item.get("hd_src"))
                                    formats.append(
                                        FormatInfo(
                                            url=vid_url,
                                            format_id="hd" if is_hd else "sd",
                                            ext="mp4",
                                            quality_label="HD" if is_hd else "SD",
                                            format_type=FormatType.COMBINED,
                                        )
                                    )
                        break
                    except (json.JSONDecodeError, TypeError):
                        continue

        if not formats:
            raise ExtractionError(
                "No video URLs found in Facebook page",
                error_code="facebook.no_video",
            )

        # Extract title
        title = self._search_regex(
            r"<title[^>]*>([^<]+)</title>",
            html,
            "title",
            default="Facebook Video",
        )
        title = clean_html(title).replace(" | Facebook", "").strip()

        # Extract thumbnail
        thumbnail = self._search_regex(
            r'"thumbnailImage"\s*:\s*\{"uri"\s*:\s*"([^"]+)"\}',
            html,
            "thumbnail",
            default=None,
        )
        if thumbnail:
            thumbnail = thumbnail.replace("\\/", "/")
        if not thumbnail:
            thumbnail = self._search_regex(
                r'<meta\s+property="og:image"\s+content="([^"]+)"',
                html,
                "og:image",
                default=None,
            )

        # Extract description
        description = self._search_regex(
            r'<meta\s+property="og:description"\s+content="([^"]*)"',
            html,
            "description",
            default=None,
        )

        metadata = MediaMetadata(
            description=clean_html(description) if description else None,
        )

        return ExtractResponse(
            platform=Platform.FACEBOOK,
            id=video_id,
            title=title,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )
