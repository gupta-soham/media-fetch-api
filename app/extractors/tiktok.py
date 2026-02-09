"""
TikTok extractor - extracts video/audio from TikTok posts.
Ported from cobalt's tiktok.js and yt-dlp's tiktok.py.

Supports:
- Regular videos
- Slideshows (image posts)
- Short link resolution (vm.tiktok.com, vt.tiktok.com)
- Audio extraction (original sound)
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
    extract_json_from_html,
    extract_json_from_script,
    float_or_none,
    format_date,
    int_or_none,
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_TIKTOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tiktok.com/",
}


class TikTokExtractor(BaseExtractor):
    """TikTok media extractor."""

    platform = Platform.TIKTOK

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from TikTok."""
        # Resolve short links
        if any(d in url for d in ("vm.tiktok.com", "vt.tiktok.com")):
            try:
                resolved_url = await self.http.resolve_redirect(url)
                url = resolved_url
                # Re-extract video ID from resolved URL
                id_match = re.search(r"/video/(\d+)", url)
                if id_match:
                    media_id = id_match.group(1)
                else:
                    id_match = re.search(r"/photo/(\d+)", url)
                    if id_match:
                        media_id = id_match.group(1)
            except Exception as e:
                logger.warning(f"Failed to resolve TikTok short link: {e}")

        video_id = media_id
        user = params.get("user", "")

        # Method 1: Extract from webpage __UNIVERSAL_DATA_FOR_REHYDRATION__
        try:
            return await self._extract_from_webpage(video_id, url, user)
        except ExtractionError:
            raise
        except Exception as e:
            logger.warning(f"TikTok webpage extraction failed: {e}")

        # Method 2: Try the embed API
        try:
            return await self._extract_from_embed(video_id)
        except Exception as e:
            logger.warning(f"TikTok embed extraction failed: {e}")

        raise ExtractionError(
            "Could not extract TikTok media",
            error_code="tiktok.extraction_failed",
        )

    async def _extract_from_webpage(self, video_id: str, url: str, user: str) -> ExtractResponse:
        """Extract data from TikTok webpage using __UNIVERSAL_DATA_FOR_REHYDRATION__.
        Tries multiple URL patterns (like Cobalt: @i/video) and does not hard-fail on /login
        redirect; falls back to embed and other methods before raising.
        """
        # Cobalt uses @i/video unconditionally for the main request; we try user URL first then @i
        urls_to_try = []
        if user:
            urls_to_try.append(f"https://www.tiktok.com/@{user}/video/{video_id}")
        urls_to_try.append(f"https://www.tiktok.com/@i/video/{video_id}")
        if url and "tiktok.com" in url and url not in urls_to_try:
            urls_to_try.append(url)

        headers = dict(_TIKTOK_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        universal_data = None
        html = None
        for page_url in urls_to_try:
            response = await self.http.get(page_url, headers=headers)
            html = response.text
            universal_data = extract_json_from_html(html, "__UNIVERSAL_DATA_FOR_REHYDRATION__")
            if not universal_data:
                universal_data = extract_json_from_html(html, "SIGI_STATE")
            if not universal_data:
                universal_data = extract_json_from_script(
                    html, "__UNIVERSAL_DATA_FOR_REHYDRATION__"
                )
            if not universal_data:
                universal_data = extract_json_from_script(html, "SIGI_STATE")
            if universal_data:
                break

        if not universal_data:
            try:
                return await self._extract_from_embed(video_id)
            except Exception:
                pass
            raise ExtractionError("Could not find video data in TikTok page")

        video_detail = traverse_obj(universal_data, ("__DEFAULT_SCOPE__", "webapp.video-detail"))
        if isinstance(video_detail, dict):
            status_msg = video_detail.get("statusMsg") or video_detail.get("status_msg")
            status_code = int_or_none(video_detail.get("statusCode"))
            if status_msg:
                raise ExtractionError(
                    f"Video unavailable: {status_msg}", error_code="tiktok.unavailable"
                )
            # 10204 = IP blocked. Try embed as alternate endpoint before failing (yt-dlp has no rotation; suggests proxy/cookies).
            if status_code == 10204:
                try:
                    return await self._extract_from_embed(video_id)
                except Exception:
                    raise ExtractionError(
                        "IP address blocked. Use cookies (cookies/tiktok.txt) or a different network/VPN.",
                        error_code="tiktok.ip_blocked",
                    )
            if status_code == 10216:
                raise ExtractionError("Private post", error_code="tiktok.private_post")
            if status_code == 10222:
                raise ExtractionError("Private account", error_code="tiktok.private_account")

        # Navigate to video data
        # New format: __DEFAULT_SCOPE__["webapp.video-detail"]
        video_data = traverse_obj(
            universal_data,
            ("__DEFAULT_SCOPE__", "webapp.video-detail", "itemInfo", "itemStruct"),
        )

        if not video_data:
            # Try alternate path
            video_data = traverse_obj(
                universal_data,
                ("__DEFAULT_SCOPE__", "webapp.video-detail", "itemStruct"),
            )

        if not video_data:
            # Try SIGI_STATE path
            video_data = traverse_obj(
                universal_data,
                ("ItemModule", video_id),
            )

        if not video_data:
            # Try to find any video structure
            for _key, value in universal_data.get("__DEFAULT_SCOPE__", {}).items():
                if isinstance(value, dict):
                    vd = traverse_obj(value, ("itemInfo", "itemStruct"))
                    if vd:
                        video_data = vd
                        break

        if not video_data:
            raise ExtractionError("Could not parse TikTok video data from page")

        if video_data.get("isContentClassified"):
            raise ExtractionError("Age-restricted content", error_code="tiktok.age_restricted")

        return self._parse_video_data(video_data, video_id)

    async def _extract_from_embed(self, video_id: str) -> ExtractResponse:
        """Extract data from TikTok embed page."""
        embed_url = f"https://www.tiktok.com/embed/v2/{video_id}"

        headers = dict(_TIKTOK_HEADERS)
        html = await self._download_webpage(embed_url, headers=headers)

        # Extract video data from embed
        video_data = self._search_json(r'"videoData"\s*:', html, "video data", default=None)

        if not video_data:
            raise ExtractionError("Could not find video data in embed page")

        return self._parse_video_data(video_data, video_id)

    def _parse_video_data(self, data: dict, video_id: str) -> ExtractResponse:
        """Parse TikTok video data into ExtractResponse."""
        formats = []

        # Check if this is a slideshow/image post
        image_post = data.get("imagePost") or data.get("photo")
        if image_post:
            return self._parse_slideshow(data, image_post, video_id)

        # Extract video info
        video = data.get("video", {})

        # Direct video URL variants
        play_addr = video.get("playAddr") or video.get("play_addr")
        play_addr_h264 = video.get("play_addr_h264") or video.get("playAddrH264")
        play_addr_bytevc1 = video.get("play_addr_bytevc1") or video.get("playAddrBytevc1")
        download_addr = video.get("downloadAddr") or video.get("download_addr")
        bitrate_info = (
            video.get("bitrateInfo") or video.get("bitrate_info") or video.get("bit_rate") or []
        )

        # Get dimensions
        width = int_or_none(video.get("width"))
        height = int_or_none(video.get("height"))
        duration = float_or_none(video.get("duration"))

        def addr_to_url(addr: object) -> str | None:
            if isinstance(addr, str):
                return addr
            if isinstance(addr, dict):
                url_list = addr.get("UrlList") or addr.get("url_list")
                if isinstance(url_list, list) and url_list:
                    return url_list[-1]
                return addr.get("url")
            return None

        # Add bitrate variants
        for br_info in bitrate_info:
            br_url = traverse_obj(br_info, ("PlayAddr", "UrlList", -1)) or traverse_obj(
                br_info, ("PlayAddr", "UrlList", 0)
            )
            if not br_url:
                br_url = addr_to_url(br_info.get("play_addr") or br_info.get("PlayAddr"))
            if br_url:
                codec = br_info.get("CodecType", "")
                gear = br_info.get("GearName", "")

                formats.append(
                    FormatInfo(
                        url=br_url,
                        format_id=f"{gear}_{codec}" if gear else None,
                        ext="mp4",
                        width=int_or_none(traverse_obj(br_info, ("PlayAddr", "Width"))),
                        height=int_or_none(traverse_obj(br_info, ("PlayAddr", "Height"))),
                        vcodec="h265"
                        if "h265" in codec.lower() or "bytevc1" in codec.lower()
                        else "h264",
                        acodec="mp4a",
                        tbr=float_or_none(br_info.get("Bitrate"), scale=1000),
                        format_type=FormatType.COMBINED,
                        quality_label=gear,
                    )
                )

        # Add main play URLs if missing bitrate info
        if not formats:
            for label, addr in (
                ("play", play_addr),
                ("play_h264", play_addr_h264),
                ("play_bytevc1", play_addr_bytevc1),
            ):
                play_url = addr_to_url(addr)
                if play_url:
                    formats.append(
                        FormatInfo(
                            url=play_url,
                            format_id=label,
                            ext="mp4",
                            width=width,
                            height=height,
                            format_type=FormatType.COMBINED,
                        )
                    )

        # Add download URL
        if download_addr:
            dl_url = addr_to_url(download_addr)

            if dl_url and not any(f.url == dl_url for f in formats):
                formats.append(
                    FormatInfo(
                        url=dl_url,
                        format_id="download",
                        ext="mp4",
                        width=width,
                        height=height,
                        format_type=FormatType.COMBINED,
                        quality_label="download",
                    )
                )

        # Audio
        music = data.get("music", {})
        music_url = music.get("playUrl") or music.get("play_url")
        if isinstance(music_url, dict):
            music_url = traverse_obj(music_url, ("UrlList", 0))
        if music_url:
            formats.append(
                FormatInfo(
                    url=music_url,
                    format_id="audio",
                    ext="mp3",
                    acodec="mp3",
                    vcodec="none",
                    format_type=FormatType.AUDIO_ONLY,
                    quality_label="original_sound",
                )
            )

        if not formats:
            raise ExtractionError("No playable formats found in TikTok video data")

        # Attach sid_tt cookie to formats when available
        if self._has_cookies():
            sid_tt = self._get_cookie("sid_tt")
            if sid_tt:
                for fmt in formats:
                    fmt.http_headers = fmt.http_headers or {}
                    fmt.http_headers["Cookie"] = f"sid_tt={sid_tt}"

        # Build metadata
        author = data.get("author", {})
        username = author.get("uniqueId") or author.get("unique_id", "")
        nickname = author.get("nickname", "")

        desc = data.get("desc", "")
        title = f"@{username}: {desc[:100]}" if desc else f"TikTok by @{username}"

        thumbnail = video.get("cover") or video.get("originCover") or video.get("dynamicCover")
        if isinstance(thumbnail, dict):
            thumbnail = traverse_obj(thumbnail, ("UrlList", 0))

        stats = data.get("stats", {})

        metadata = MediaMetadata(
            uploader=nickname or username,
            uploader_id=username,
            uploader_url=f"https://www.tiktok.com/@{username}",
            description=desc,
            view_count=int_or_none(stats.get("playCount") or stats.get("play_count")),
            like_count=int_or_none(stats.get("diggCount") or stats.get("digg_count")),
            comment_count=int_or_none(stats.get("commentCount") or stats.get("comment_count")),
            repost_count=int_or_none(stats.get("shareCount") or stats.get("share_count")),
            upload_date=format_date(data.get("createTime") or data.get("create_time")),
        )

        return ExtractResponse(
            platform=Platform.TIKTOK,
            id=video_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    def _parse_slideshow(self, data: dict, image_post: dict, video_id: str) -> ExtractResponse:
        """Parse a TikTok slideshow/image post."""
        formats = []

        images = image_post.get("images", [])
        for idx, img in enumerate(images):
            img_urls = img.get("imageURL", {}).get("urlList", [])
            if img_urls:
                formats.append(
                    FormatInfo(
                        url=img_urls[0],
                        format_id=f"image_{idx}",
                        ext="jpg",
                        format_type=FormatType.COMBINED,
                    )
                )

        # Music from slideshow
        music = data.get("music", {})
        music_url = music.get("playUrl") or music.get("play_url")
        if isinstance(music_url, dict):
            music_url = traverse_obj(music_url, ("UrlList", 0))
        if music_url:
            formats.append(
                FormatInfo(
                    url=music_url,
                    format_id="audio",
                    ext="mp3",
                    acodec="mp3",
                    vcodec="none",
                    format_type=FormatType.AUDIO_ONLY,
                )
            )

        author = data.get("author", {})
        username = author.get("uniqueId", "")
        desc = data.get("desc", "")
        title = f"@{username}: {desc[:100]}" if desc else f"TikTok slideshow by @{username}"

        return ExtractResponse(
            platform=Platform.TIKTOK,
            id=video_id,
            title=title,
            formats=formats,
            metadata=MediaMetadata(
                uploader=username,
                uploader_id=username,
                description=desc,
            ),
        )
