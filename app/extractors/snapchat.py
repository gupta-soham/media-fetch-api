"""
Snapchat extractor - extracts video from Snapchat Spotlight and Stories.
Ported from cobalt's snapchat.js and yt-dlp's snapchat.py.

Supports:
- Spotlight posts
- Public stories
- Video extraction from Next.js data
- Preload link fallback
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
    extract_json_from_html,
    float_or_none,
    format_date,
    int_or_none,
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_SNAPCHAT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class SnapchatExtractor(BaseExtractor):
    """Snapchat media extractor."""

    platform = Platform.SNAPCHAT

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Snapchat."""
        # Determine content type from URL
        if "/spotlight/" in url:
            return await self._extract_spotlight(media_id, url)
        elif "/stories/" in url or "story.snapchat.com" in url:
            return await self._extract_story(media_id, url, params)
        elif "/p/" in url:
            return await self._extract_spotlight(media_id, url)
        else:
            return await self._extract_spotlight(media_id, url)

    async def _extract_spotlight(self, media_id: str, url: str) -> ExtractResponse:
        """Extract from Snapchat Spotlight."""
        page_url = url
        if not page_url.startswith("http"):
            page_url = f"https://www.snapchat.com/spotlight/{media_id}"

        html = await self._download_webpage(page_url, headers=_SNAPCHAT_HEADERS)

        # Method 1: Extract __NEXT_DATA__ (yt-dlp approach)
        next_data = extract_json_from_html(html, "__NEXT_DATA__")

        if next_data:
            # Navigate to spotlight data
            stories = traverse_obj(
                next_data,
                ("props", "pageProps", "spotlightFeed", "spotlightStories"),
            )

            if stories:
                # Find matching story by storyId
                for story in stories:
                    story_id = story.get("storyId", "")
                    if media_id in story_id or not media_id:
                        return self._parse_spotlight_story(story, media_id)

            # Try direct pageProps path
            story = traverse_obj(next_data, ("props", "pageProps", "story"))
            if story:
                return self._parse_story_data(story, media_id)

        # Method 2: Extract video preload link (cobalt approach)
        video_url = self._search_regex(
            r'<link\s+rel="preload"\s+href="([^"]+)"\s+as="video"',
            html,
            "preload video",
            default=None,
        )

        if not video_url:
            # Try alternate patterns
            video_url = self._search_regex(
                r'"contentUrl"\s*:\s*"([^"]+)"',
                html,
                "content URL",
                default=None,
            )

        if not video_url:
            video_url = self._search_regex(
                r'<video[^>]+src="([^"]+)"',
                html,
                "video src",
                default=None,
            )

        if not video_url:
            raise ExtractionError(
                "Could not find video in Snapchat page",
                error_code="snapchat.no_video",
            )

        # Clean URL
        video_url = video_url.replace("&amp;", "&")

        # Get title
        title = self._search_regex(
            r"<title>([^<]+)</title>",
            html,
            "title",
            default="Snapchat Spotlight",
        )
        title = clean_html(title)

        # Get thumbnail
        thumbnail = self._search_regex(
            r'"thumbnailUrl"\s*:\s*"([^"]+)"',
            html,
            "thumbnail",
            default=None,
        )
        if not thumbnail:
            thumbnail = self._search_regex(
                r'<meta\s+property="og:image"\s+content="([^"]+)"',
                html,
                "og:image",
                default=None,
            )

        formats = [
            FormatInfo(
                url=video_url,
                format_id="main",
                ext="mp4",
                format_type=FormatType.COMBINED,
            )
        ]

        # Try to get description
        description = self._search_regex(
            r'"description"\s*:\s*"([^"]*)"',
            html,
            "description",
            default=None,
        )

        metadata = None
        if description:
            metadata = MediaMetadata(description=description)

        return ExtractResponse(
            platform=Platform.SNAPCHAT,
            id=media_id,
            title=title,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    async def _extract_story(
        self, media_id: str, url: str, params: dict[str, str]
    ) -> ExtractResponse:
        """Extract from Snapchat Stories."""
        page_url = url
        if not page_url.startswith("http"):
            user = params.get("user", "")
            if user:
                page_url = f"https://story.snapchat.com/s/{user}"
            else:
                page_url = f"https://www.snapchat.com/stories/{media_id}"

        html = await self._download_webpage(page_url, headers=_SNAPCHAT_HEADERS)

        next_data = extract_json_from_html(html, "__NEXT_DATA__")
        if not next_data:
            raise ExtractionError("Could not parse Snapchat story page")

        # Navigate to story data (cobalt pattern)
        story = traverse_obj(next_data, ("props", "pageProps", "story"))

        if not story:
            # Try curatedHighlights
            story = traverse_obj(next_data, ("props", "pageProps", "curatedHighlights", 0))

        if not story:
            raise ExtractionError("No story data found")

        return self._parse_story_data(story, media_id)

    def _parse_spotlight_story(self, story: dict, media_id: str) -> ExtractResponse:
        """Parse a Spotlight story object from __NEXT_DATA__."""
        video_metadata = story.get("videoMetadata", {})
        content_url = video_metadata.get("contentUrl")

        if not content_url:
            # Try snapInfo
            snap_info = story.get("snapInfo", {})
            content_url = snap_info.get("streamingMediaUrl") or snap_info.get("mediaUrl")

        if not content_url:
            raise ExtractionError("No content URL in spotlight story")

        title = video_metadata.get("name") or story.get("storyTitle", "Snapchat Spotlight")
        description = video_metadata.get("description", "")
        duration = float_or_none(video_metadata.get("durationMs"), scale=1000)
        thumbnail = video_metadata.get("thumbnailUrl")

        # Creator info
        creator = story.get("creator", {})
        username = creator.get("username", "")

        formats = [
            FormatInfo(
                url=content_url,
                format_id="main",
                ext="mp4",
                format_type=FormatType.COMBINED,
            )
        ]

        metadata = MediaMetadata(
            uploader=creator.get("displayName") or username,
            uploader_id=username,
            description=description,
            view_count=int_or_none(video_metadata.get("viewCount")),
            repost_count=int_or_none(video_metadata.get("shareCount")),
            upload_date=format_date(video_metadata.get("uploadDateMs")),
        )

        return ExtractResponse(
            platform=Platform.SNAPCHAT,
            id=media_id or story.get("storyId", ""),
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    def _parse_story_data(self, story: dict, media_id: str) -> ExtractResponse:
        """Parse story data from __NEXT_DATA__."""
        formats = []
        title = story.get("title", "Snapchat Story")

        # Get snaps from story
        snap_list = story.get("snapList", []) or story.get("snaps", [])

        for snap in snap_list:
            media_url = snap.get("snapUrls", {}).get("mediaUrl")
            if not media_url:
                media_url = snap.get("media", {}).get("mediaUrl")
            if not media_url:
                continue

            snap_type = snap.get("snapMediaType", 0)
            # 0 = image, 1 = video
            if snap_type == 1 or "video" in media_url.lower():
                formats.append(
                    FormatInfo(
                        url=media_url,
                        format_id=f"snap_{snap.get('snapId', '')}",
                        ext="mp4",
                        format_type=FormatType.COMBINED,
                    )
                )
            else:
                formats.append(
                    FormatInfo(
                        url=media_url,
                        format_id=f"snap_{snap.get('snapId', '')}",
                        ext="jpg",
                        format_type=FormatType.COMBINED,
                    )
                )

        if not formats:
            raise ExtractionError(
                "No media found in Snapchat story",
                error_code="snapchat.no_media",
            )

        return ExtractResponse(
            platform=Platform.SNAPCHAT,
            id=media_id,
            title=title,
            formats=formats,
        )
