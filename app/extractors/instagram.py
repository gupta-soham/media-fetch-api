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
    extract_json_from_html,
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
_IG_GRAPHQL_URL = "https://www.instagram.com/graphql/query/"
_IG_GRAPHQL_DOC_ID = "8845758582119845"
_IG_STORIES_DOC_ID = "25317500907894419"

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

        # Method 2: Public JSON from post page
        if not media_data:
            try:
                media_data = await self._extract_public_json(shortcode)
            except Exception as e:
                errors.append(f"PublicJSON: {e}")
                logger.debug(f"Instagram public JSON failed: {e}")

        # Method 3: OEmbed -> Web API (media info endpoint)
        if not media_data:
            try:
                oembed_id = await self._get_oembed_media_id(shortcode)
                if oembed_id:
                    media_data = await self._extract_web_api(oembed_id)
            except Exception as e:
                errors.append(f"OEmbed: {e}")
                logger.debug(f"Instagram oEmbed failed: {e}")

        # Method 4: Embed page parsing
        if not media_data:
            try:
                media_data = await self._extract_embed(shortcode)
            except Exception as e:
                errors.append(f"Embed: {e}")
                logger.debug(f"Instagram embed failed: {e}")

        # Method 5: Web API (numeric ID fallback)
        if not media_data:
            try:
                numeric_id = _shortcode_to_media_id(shortcode)
                media_data = await self._extract_web_api(numeric_id)
            except Exception as e:
                errors.append(f"WebAPI: {e}")
                logger.debug(f"Instagram Web API failed: {e}")

        # Method 6: Mobile API with bearer token (cookies only)
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

    async def _extract_public_json(self, shortcode: str) -> ExtractResponse | None:
        """Extract from public JSON embedded in the post page."""
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        headers = {
            "User-Agent": _IG_HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        html = await self._download_webpage(post_url, headers=headers)
        parsed = self._parse_public_json(html, shortcode)
        if not parsed:
            raise ExtractionError("No public JSON found in post page")
        return parsed

    def _parse_public_json(self, html: str, shortcode: str) -> ExtractResponse | None:
        """Parse public JSON blocks from HTML and return an ExtractResponse."""
        shared = extract_json_from_html(html, "window._sharedData")
        if shared:
            node = traverse_obj(
                shared,
                ("entry_data", "PostPage", 0, "graphql", "shortcode_media"),
                ("entry_data", "ReelPage", 0, "graphql", "shortcode_media"),
            )
            if node:
                return self._parse_graphql_node(node, shortcode)

        additional = self._extract_additional_data(html)
        if additional:
            node = traverse_obj(additional, ("graphql", "shortcode_media"))
            if node:
                return self._parse_graphql_node(node, shortcode)
            items = traverse_obj(additional, ("items",))
            if isinstance(items, list) and items:
                return self._parse_media_item(items[0], shortcode)

        return None

    def _extract_additional_data(self, html: str) -> dict | None:
        """Extract window.__additionalDataLoaded payloads from HTML."""
        for match in re.finditer(
            r"window\.__additionalDataLoaded\([^,]+,\s*(\{.+?\})\s*\);",
            html,
            re.DOTALL,
        ):
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
        return None

    def _extract_html_tokens(self, html: str) -> dict[str, str]:
        """Extract common tokens needed for GraphQL requests from HTML."""
        def first_match(patterns: list[str]) -> str | None:
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    return match.group(1)
            return None

        tokens: dict[str, str] = {}
        tokens["lsd"] = first_match(
            [
                r'name="lsd"\s+value="([^"]+)"',
                r'"LSD"\s*:\s*\{"token"\s*:\s*"([^"]+)"',
            ]
        ) or ""
        tokens["csrf"] = first_match(
            [
                r'"csrf_token"\s*:\s*"([^"]+)"',
                r'"csrf_token"\s*:\s*\\?"([^"]+)"',
            ]
        ) or ""
        tokens["jazoest"] = first_match(
            [
                r'name="jazoest"\s+value="([^"]+)"',
                r'"jazoest"\s*:\s*"([^"]+)"',
            ]
        ) or ""
        tokens["dtsg"] = first_match(
            [
                r'name="fb_dtsg"\s+value="([^"]+)"',
                r'"DTSGInitialData"\s*:\s*\{"token"\s*:\s*"([^"]+)"',
                r'"dtsg"\s*:\s*\{"token"\s*:\s*"([^"]+)"',
            ]
        ) or ""
        tokens["mid"] = first_match([r'"mid"\s*:\s*"([^"]+)"']) or ""
        tokens["ig_did"] = first_match([r'"ig_did"\s*:\s*"([^"]+)"']) or ""
        tokens["app_id"] = first_match(
            [
                r'"X-IG-App-ID"\s*:\s*"([^"]+)"',
                r'"appId"\s*:\s*"([^"]+)"',
            ]
        ) or _IG_APP_ID
        tokens["bloks_version_id"] = first_match(
            [r'"X-Bloks-Version-Id"\s*:\s*"([^"]+)"']
        ) or ""
        tokens["__spin_r"] = first_match([r'"__spin_r"\s*:\s*(\d+)']) or ""
        tokens["__spin_b"] = first_match([r'"__spin_b"\s*:\s*"([^"]+)"']) or ""
        tokens["__spin_t"] = first_match([r'"__spin_t"\s*:\s*(\d+)']) or ""
        tokens["__hsi"] = first_match([r'"__hsi"\s*:\s*"([^"]+)"']) or ""
        tokens["__rev"] = first_match([r'"__rev"\s*:\s*(\d+)']) or ""
        tokens["__dyn"] = first_match([r'"__dyn"\s*:\s*"([^"]+)"']) or ""
        tokens["__csr"] = first_match([r'"__csr"\s*:\s*"([^"]+)"']) or ""
        tokens["__req"] = first_match([r'"__req"\s*:\s*"([^"]+)"']) or ""
        tokens["__hs"] = first_match([r'"__hs"\s*:\s*"([^"]+)"']) or ""
        tokens["__ccg"] = first_match([r'"__ccg"\s*:\s*"([^"]+)"']) or ""
        tokens["__user"] = first_match([r'"__user"\s*:\s*"([^"]+)"']) or ""
        tokens["__comet_req"] = first_match([r'"__comet_req"\s*:\s*"([^"]+)"']) or ""
        return {k: v for k, v in tokens.items() if v}

    def _build_graphql_headers(self, tokens: dict[str, str], referer: str) -> dict[str, str]:
        """Build headers for Instagram GraphQL requests."""
        headers = {
            "User-Agent": _IG_HEADERS["User-Agent"],
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.instagram.com",
            "Referer": referer,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-IG-App-ID": tokens.get("app_id", _IG_APP_ID),
            "X-Requested-With": "XMLHttpRequest",
            "x-asbd-id": "129477",
            "X-FB-Friendly-Name": "PolarisPostActionLoadPostQueryQuery",
        }
        if tokens.get("lsd"):
            headers["X-FB-LSD"] = tokens["lsd"]
        if tokens.get("bloks_version_id"):
            headers["X-Bloks-Version-Id"] = tokens["bloks_version_id"]
        if tokens.get("csrf"):
            headers["X-CSRFToken"] = tokens["csrf"]
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()
            csrf_cookie = self._get_cookie("csrftoken")
            if csrf_cookie:
                headers["X-CSRFToken"] = csrf_cookie
        return headers

    def _parse_graphql_node(self, node: dict, media_id: str) -> ExtractResponse:
        """Parse a GraphQL shortcode_media node into ExtractResponse."""
        formats: list[FormatInfo] = []
        title = None
        duration = float_or_none(node.get("video_duration"))
        thumbnail = node.get("display_url") or node.get("thumbnail_src")

        owner = node.get("owner") or {}
        user = owner.get("username")
        caption_edges = traverse_obj(node, ("edge_media_to_caption", "edges")) or []
        caption = None
        if caption_edges:
            caption = traverse_obj(caption_edges[0], ("node", "text"))
        title = (
            f"@{user}: {caption[:100]}"
            if caption and user
            else f"Instagram Post by @{user}"
            if user
            else "Instagram Post"
        )

        def add_video_fmt(video_url: str | None, width: int | None, height: int | None, fmt_id: str):
            if not video_url:
                return
            formats.append(
                FormatInfo(
                    url=video_url,
                    format_id=fmt_id,
                    ext="mp4",
                    width=width,
                    height=height,
                    format_type=FormatType.COMBINED,
                )
            )

        def add_image_fmt(image_url: str | None, width: int | None, height: int | None, fmt_id: str):
            if not image_url:
                return
            formats.append(
                FormatInfo(
                    url=image_url,
                    format_id=fmt_id,
                    ext="jpg",
                    width=width,
                    height=height,
                    format_type=FormatType.COMBINED,
                )
            )

        if node.get("__typename") == "GraphSidecar":
            edges = traverse_obj(node, ("edge_sidecar_to_children", "edges")) or []
            for idx, edge in enumerate(edges):
                child = edge.get("node") or {}
                dimensions = child.get("dimensions") or {}
                if child.get("is_video") or child.get("__typename") == "GraphVideo":
                    add_video_fmt(
                        child.get("video_url"),
                        int_or_none(dimensions.get("width")),
                        int_or_none(dimensions.get("height")),
                        f"carousel_{idx}",
                    )
                else:
                    add_image_fmt(
                        child.get("display_url"),
                        int_or_none(dimensions.get("width")),
                        int_or_none(dimensions.get("height")),
                        f"carousel_{idx}_img",
                    )
        elif node.get("is_video") or node.get("__typename") == "GraphVideo":
            dimensions = node.get("dimensions") or {}
            add_video_fmt(
                node.get("video_url"),
                int_or_none(dimensions.get("width")),
                int_or_none(dimensions.get("height")),
                "video",
            )
        else:
            dimensions = node.get("dimensions") or {}
            add_image_fmt(
                node.get("display_url"),
                int_or_none(dimensions.get("width")),
                int_or_none(dimensions.get("height")),
                "image",
            )

        metadata = MediaMetadata(
            uploader=user,
            uploader_id=str_or_none(owner.get("id")),
            uploader_url=f"https://www.instagram.com/{user}/" if user else None,
            description=caption,
            like_count=int_or_none(
                traverse_obj(node, ("edge_media_preview_like", "count"))
                or traverse_obj(node, ("edge_media_to_parent_comment", "count"))
            ),
            comment_count=int_or_none(traverse_obj(node, ("edge_media_to_comment", "count"))),
            view_count=int_or_none(node.get("video_view_count")),
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

    async def _get_oembed_media_id(self, shortcode: str) -> str | None:
        """Resolve media ID from the oEmbed endpoint."""
        oembed_url = f"{_IG_API_BASE}/oembed/"
        params_dict = {"url": f"https://www.instagram.com/p/{shortcode}/"}
        headers = dict(_IG_HEADERS)
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()
        response = await self.http.get(oembed_url, headers=headers, params=params_dict)
        if response.status_code != 200:
            raise ExtractionError(f"oEmbed API returned {response.status_code}")
        data = response.json()
        return data.get("media_id")

    async def _extract_graphql(self, shortcode: str) -> ExtractResponse | None:
        """Extract using Instagram GraphQL API."""
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        headers = {
            "User-Agent": _IG_HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        html = await self._download_webpage(post_url, headers=headers)
        tokens = self._extract_html_tokens(html)

        gql_headers = self._build_graphql_headers(tokens, post_url)
        variables = json.dumps({"shortcode": shortcode})
        payload: dict[str, str] = {
            "doc_id": _IG_GRAPHQL_DOC_ID,
            "variables": variables,
        }
        for key in (
            "lsd",
            "jazoest",
            "__spin_r",
            "__spin_b",
            "__spin_t",
            "__hsi",
            "__rev",
            "__dyn",
            "__csr",
            "__req",
            "__hs",
            "__ccg",
            "__user",
            "__comet_req",
        ):
            if tokens.get(key):
                payload[key] = tokens[key]

        response = await self.http.post(
            _IG_GRAPHQL_URL,
            headers=gql_headers,
            data=payload,
        )
        if response.status_code != 200:
            raise ExtractionError(f"GraphQL API returned {response.status_code}")

        data = response.json()
        media = traverse_obj(
            data,
            ("data", "xdt_shortcode_media"),
            ("data", "shortcode_media"),
        )
        if not media:
            raise ExtractionError("No media in GraphQL response")

        return self._parse_graphql_node(media, shortcode)

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

        # Try JSON data embedded in the page first
        additional = self._extract_additional_data(html)
        if additional:
            node = traverse_obj(additional, ("graphql", "shortcode_media"))
            if node:
                return self._parse_graphql_node(node, shortcode)
            items = traverse_obj(additional, ("items",))
            if isinstance(items, list) and items:
                return self._parse_media_item(items[0], shortcode)

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

    async def _extract_story_graphql(self, story_pk: str) -> ExtractResponse | None:
        """Best-effort GraphQL story extraction (requires cookies)."""
        if not self._has_cookies():
            return None

        home_headers = {
            "User-Agent": _IG_HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": self._get_cookie_header(),
        }
        html = await self._download_webpage("https://www.instagram.com/", headers=home_headers)
        tokens = self._extract_html_tokens(html)
        if not tokens.get("dtsg") and not tokens.get("lsd"):
            return None

        gql_headers = self._build_graphql_headers(tokens, "https://www.instagram.com/")
        gql_headers["X-FB-Friendly-Name"] = "PolarisStoriesV3ReelsQuery"

        variables = json.dumps({"reel_ids": [story_pk], "precomposed_overlay": False})
        payload: dict[str, str] = {
            "doc_id": _IG_STORIES_DOC_ID,
            "variables": variables,
        }
        if tokens.get("lsd"):
            payload["lsd"] = tokens["lsd"]
        if tokens.get("dtsg"):
            payload["fb_dtsg"] = tokens["dtsg"]

        response = await self.http.post(
            _IG_GRAPHQL_URL,
            headers=gql_headers,
            data=payload,
        )
        if response.status_code != 200:
            raise ExtractionError(f"Stories GraphQL returned {response.status_code}")

        data = response.json()
        reels = traverse_obj(data, ("data", "reels_media"), default=None) or traverse_obj(
            data, ("data", "reels"), default=None
        )
        if not reels:
            return None

        return self._build_story_response(reels, story_pk, user=None)

    def _build_story_response(self, reels: object, story_pk: str, user: str | None) -> ExtractResponse:
        """Build ExtractResponse for story reels payloads."""
        formats: list[FormatInfo] = []
        title = f"Instagram Story by @{user}" if user else "Instagram Story"

        reel_iter = []
        if isinstance(reels, list):
            reel_iter = reels
        elif isinstance(reels, dict):
            reel_iter = list(reels.values())

        for reel in reel_iter:
            for item in reel.get("items", []):
                if story_pk and str(item.get("pk")) != story_pk:
                    continue
                video_versions = item.get("video_versions", [])
                for v in video_versions:
                    if v.get("url"):
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
                    candidates = item.get("image_versions2", {}).get("candidates", [])
                    for c in candidates:
                        if c.get("url"):
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

        # Try GraphQL stories first (best-effort)
        try:
            graphql_story = await self._extract_story_graphql(story_pk)
            if graphql_story and graphql_story.formats:
                return graphql_story
        except Exception as e:
            logger.debug(f"Instagram story GraphQL failed: {e}")

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

        return self._build_story_response(reels, story_pk, user=user)

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
