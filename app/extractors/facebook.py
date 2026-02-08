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
from urllib.parse import unquote, urljoin

from ..core.dash_parser import parse_mpd
from ..models.enums import FormatType, Platform
from ..models.request import ExtractRequest
from ..models.response import (
    ExtractResponse,
    FormatInfo,
    MediaMetadata,
)
from ..utils.helpers import (
    clean_html,
    traverse_obj,
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

        errors = []

        # Method 1: Fetch page and extract video URLs from HTML
        try:
            return await self._extract_from_html(video_id, url)
        except ExtractionError:
            raise
        except Exception as e:
            errors.append(f"HTML: {e}")
            logger.warning(f"Facebook HTML extraction failed: {e}")

        # Method 2: Tahoe async endpoint (best-effort)
        try:
            return await self._extract_from_tahoe(video_id)
        except Exception as e:
            errors.append(f"Tahoe: {e}")
            logger.warning(f"Facebook Tahoe extraction failed: {e}")

        # Method 3: Mobile endpoint fallback
        try:
            return await self._extract_from_mobile(video_id, url)
        except Exception as e:
            errors.append(f"Mobile: {e}")
            logger.warning(f"Facebook mobile extraction failed: {e}")

        raise ExtractionError(
            f"Could not extract Facebook video. Tried: {'; '.join(errors)}",
            error_code="facebook.extraction_failed",
        )

    async def _extract_from_html(
        self, video_id: str, url: str, page_url: str | None = None
    ) -> ExtractResponse:
        """Extract video URLs from Facebook page HTML."""
        # Build page URL
        if not page_url:
            if re.match(r"^\d+$", video_id):
                page_url = f"https://web.facebook.com/i/videos/{video_id}"
            else:
                page_url = url

        headers = dict(_FB_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        html = await self._download_webpage(page_url, headers=headers)
        if self._is_login_page(html):
            raise ExtractionError(
                "Facebook login or checkpoint required",
                error_code="facebook.login_required",
            )

        formats, title, thumbnail, description = self._parse_html(page_url, html)

        dash_manifest_url = self._search_regex(
            r'"dash_manifest_url"\s*:\s*"([^"]+)"',
            html,
            "DASH manifest URL",
            default=None,
        )
        if dash_manifest_url:
            dash_manifest_url = dash_manifest_url.replace("\\/", "/").replace("\\u0026", "&")
            try:
                mpd_content = await self._download_webpage(dash_manifest_url, headers=headers)
                formats.extend(self._formats_from_mpd(mpd_content, dash_manifest_url))
            except Exception as e:
                logger.debug(f"Failed to parse DASH manifest: {e}")

        if not formats:
            raise ExtractionError(
                "No video URLs found in Facebook page",
                error_code="facebook.no_video",
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

    async def _extract_from_tahoe(self, video_id: str) -> ExtractResponse:
        """Best-effort extraction from Tahoe async endpoint."""
        if not re.match(r"^\d+$", video_id):
            raise ExtractionError("Tahoe requires numeric video ID")

        tahoe_url = f"https://www.facebook.com/video/tahoe/async/{video_id}/"
        headers = dict(_FB_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        response = await self.http.get(tahoe_url, headers=headers, params={"__a": "1"})
        text = response.text
        if text.startswith("for (;;);"):
            text = text[len("for (;;);") :]

        formats, title, thumbnail, description = self._parse_html(tahoe_url, text)
        if not formats:
            raise ExtractionError("Tahoe did not return playable formats")

        metadata = MediaMetadata(
            description=clean_html(description) if description else None,
        )
        return ExtractResponse(
            platform=Platform.FACEBOOK,
            id=video_id,
            title=title or "Facebook Video",
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    async def _extract_from_mobile(self, video_id: str, url: str) -> ExtractResponse:
        """Fallback to m.facebook.com endpoints."""
        if re.match(r"^\d+$", video_id):
            page_url = f"https://m.facebook.com/watch/?v={video_id}"
        else:
            page_url = url.replace("www.facebook.com", "m.facebook.com").replace(
                "web.facebook.com", "m.facebook.com"
            )
        return await self._extract_from_html(video_id, url, page_url=page_url)

    def _is_login_page(self, html: str) -> bool:
        """Detect login/checkpoint pages."""
        markers = [
            "checkpoint",  # checkpoint pages
            'id="login_form"',
            'action="/login',
            "name=\"login\"",
            "Please log in to continue",
            "You must log in to continue",
        ]
        lowered = html.lower()
        return any(marker.lower() in lowered for marker in markers)

    def _parse_html(
        self, page_url: str, html: str
    ) -> tuple[list[FormatInfo], str | None, str | None, str | None]:
        """Parse video formats and metadata from HTML or JSON blobs."""
        formats: list[FormatInfo] = []

        def clean_url(raw: str | None) -> str | None:
            if not raw:
                return None
            raw = raw.replace("\\/", "/").replace("\\u0025", "%").replace("\\u0026", "&")
            return unquote(raw) if "%25" in raw else raw

        # Primary URL patterns
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

        # Legacy src patterns
        if not hd_url:
            hd_url = self._search_regex(r'"hd_src"\s*:\s*"([^"]+)"', html, default=None)
        if not sd_url:
            sd_url = self._search_regex(r'"sd_src"\s*:\s*"([^"]+)"', html, default=None)
        if not sd_url:
            sd_url = self._search_regex(r'"src"\s*:\s*"([^"]+)"', html, default=None)

        hd_url = clean_url(hd_url)
        sd_url = clean_url(sd_url)

        if hd_url:
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
            formats.append(
                FormatInfo(
                    url=sd_url,
                    format_id="sd",
                    ext="mp4",
                    quality_label="SD",
                    format_type=FormatType.COMBINED,
                )
            )

        # Relay/GraphQL fragments
        fragment = self._search_json(r'"videoDeliveryResponseFragment"', html, default=None)
        if isinstance(fragment, dict):
            progressive = traverse_obj(
                fragment,
                ("videoDeliveryResponse", "result", "progressive_urls"),
                ("progressive_urls",),
            )
            if isinstance(progressive, list):
                for idx, item in enumerate(progressive):
                    url = clean_url(item.get("progressive_url") or item.get("url"))
                    if url:
                        formats.append(
                            FormatInfo(
                                url=url,
                                format_id=f"progressive_{idx}",
                                ext="mp4",
                                quality_label=item.get("quality"),
                                format_type=FormatType.COMBINED,
                            )
                        )

            dash_url = traverse_obj(
                fragment,
                ("videoDeliveryResponse", "result", "dash_manifest_url"),
                ("dash_manifest_url",),
            )
            if dash_url:
                formats.extend(self._formats_from_mpd_url(clean_url(dash_url), page_url))

        legacy = self._search_json(r'"videoDeliveryLegacyFields"', html, default=None)
        if isinstance(legacy, dict):
            legacy_hd = clean_url(legacy.get("playable_url_quality_hd"))
            legacy_sd = clean_url(legacy.get("playable_url"))
            if legacy_hd:
                formats.append(
                    FormatInfo(
                        url=legacy_hd,
                        format_id="legacy_hd",
                        ext="mp4",
                        quality_label="HD",
                        format_type=FormatType.COMBINED,
                    )
                )
            if legacy_sd:
                formats.append(
                    FormatInfo(
                        url=legacy_sd,
                        format_id="legacy_sd",
                        ext="mp4",
                        quality_label="SD",
                        format_type=FormatType.COMBINED,
                    )
                )

        # DASH manifest from HTML
        dash_manifest = self._search_regex(
            r'"dash_manifest"\s*:\s*"([^"]+)"',
            html,
            "DASH manifest",
            default=None,
        )
        if dash_manifest:
            dash_manifest = dash_manifest.replace("\\u003C", "<").replace("\\u003E", ">")
            dash_manifest = dash_manifest.replace("\\u0026", "&").replace("\\/", "/")
            formats.extend(self._formats_from_mpd(dash_manifest, page_url))

        dash_manifest_url = self._search_regex(
            r'"dash_manifest_url"\s*:\s*"([^"]+)"',
            html,
            "DASH manifest URL",
            default=None,
        )
        if dash_manifest_url:
            formats.extend(self._formats_from_mpd_url(clean_url(dash_manifest_url), page_url))

        # Video data blocks
        if not formats:
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

        title = self._search_regex(
            r"<title[^>]*>([^<]+)</title>",
            html,
            "title",
            default="Facebook Video",
        )
        title = clean_html(title).replace(" | Facebook", "").strip()

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

        description = self._search_regex(
            r'<meta\s+property="og:description"\s+content="([^"]*)"',
            html,
            "description",
            default=None,
        )

        return formats, title, thumbnail, description

    def _formats_from_mpd_url(self, mpd_url: str | None, page_url: str) -> list[FormatInfo]:
        if not mpd_url:
            return []
        # Provide the manifest URL as a DASH format (client can fetch/parse)
        return [
            FormatInfo(
                url=mpd_url,
                format_id="dash_manifest",
                ext="mpd",
                format_type=FormatType.COMBINED,
                protocol="dash",
            )
        ]

    def _formats_from_mpd(self, mpd_xml: str, base_url: str) -> list[FormatInfo]:
        formats: list[FormatInfo] = []
        representations = parse_mpd(mpd_xml, base_url=base_url)
        for idx, rep in enumerate(representations):
            url = rep.url or rep.base_url
            if not url:
                continue
            formats.append(
                FormatInfo(
                    url=url,
                    format_id=f"dash_{rep.rep_id or idx}",
                    ext="mp4",
                    width=rep.width,
                    height=rep.height,
                    tbr=rep.bandwidth / 1000 if rep.bandwidth else None,
                    vcodec=rep.codecs,
                    format_type=FormatType.COMBINED,
                    protocol="dash",
                )
            )
        return formats
