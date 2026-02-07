"""
Twitter/X extractor - extracts video/audio from tweets.
Ported from cobalt's twitter.js and yt-dlp's twitter.py.

Supports:
- Tweet videos
- GIFs (animated_gif)
- Multi-media tweets (picker)
- Syndication API fallback
- Guest token authentication
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
    float_or_none,
    format_date,
    int_or_none,
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

# Twitter/X API endpoints
_API_BASE = "https://api.x.com"
_GRAPHQL_API = "https://x.com/i/api/graphql"
_SYNDICATION_API = "https://cdn.syndication.twimg.com"

# Bearer token for Twitter API (from yt-dlp)
_BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs="
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# GraphQL features for TweetResultByRestId
_GRAPHQL_FEATURES = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
}


class TwitterExtractor(BaseExtractor):
    """Twitter/X media extractor."""

    platform = Platform.TWITTER
    _guest_token: str | None = None

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from a tweet."""
        tweet_id = media_id

        # Try GraphQL API first
        tweet_data = None
        try:
            tweet_data = await self._fetch_graphql(tweet_id)
        except Exception as e:
            logger.warning(f"Twitter GraphQL failed: {e}")

        # Fallback to syndication API
        if not tweet_data:
            try:
                tweet_data = await self._fetch_syndication(tweet_id)
            except Exception as e:
                logger.warning(f"Twitter syndication failed: {e}")

        if not tweet_data:
            raise ExtractionError(
                "Could not extract tweet data. The tweet may be private or deleted.",
                error_code="twitter.extraction_failed",
            )

        return tweet_data

    async def _get_guest_token(self) -> str:
        """Obtain a guest token for unauthenticated API access."""
        if self._guest_token:
            return self._guest_token

        headers = {
            "Authorization": f"Bearer {_BEARER_TOKEN}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        response = await self.http.post(
            f"{_API_BASE}/1.1/guest/activate.json",
            headers=headers,
        )

        if response.status_code != 200:
            raise ExtractionError("Failed to obtain guest token")

        data = response.json()
        self._guest_token = data.get("guest_token")
        if not self._guest_token:
            raise ExtractionError("Empty guest token received")

        return self._guest_token

    async def _fetch_graphql(self, tweet_id: str) -> ExtractResponse | None:
        """Fetch tweet data using GraphQL API."""
        guest_token = await self._get_guest_token()

        # GraphQL query for TweetResultByRestId
        variables = {
            "tweetId": tweet_id,
            "withCommunity": False,
            "includePromotedContent": False,
            "withVoice": False,
        }

        params = {
            "variables": json.dumps(variables),
            "features": json.dumps(_GRAPHQL_FEATURES),
        }

        headers = {
            "Authorization": f"Bearer {_BEARER_TOKEN}",
            "X-Guest-Token": guest_token,
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "X-Twitter-Active-User": "yes",
            "X-Twitter-Client-Language": "en",
        }

        if self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()
            ct0 = self._get_cookie("ct0")
            if ct0:
                headers["X-Csrf-Token"] = ct0

        # Use TweetResultByRestId endpoint
        response = await self.http.get(
            f"{_GRAPHQL_API}/zZXycP0V6H7m-2r0mOnFcA/TweetResultByRestId",
            headers=headers,
            params=params,
        )

        if response.status_code != 200:
            raise ExtractionError(f"GraphQL API returned {response.status_code}")

        data = response.json()

        # Navigate to tweet result
        tweet_result = traverse_obj(data, ("data", "tweetResult", "result"))

        if not tweet_result:
            raise ExtractionError("No tweet result in GraphQL response")

        # Handle tombstone (deleted/restricted)
        if tweet_result.get("__typename") == "TweetTombstone":
            reason = traverse_obj(tweet_result, ("tombstone", "text", "text"))
            raise ExtractionError(f"Tweet unavailable: {reason}")

        # Handle TweetWithVisibilityResults wrapper
        if tweet_result.get("__typename") == "TweetWithVisibilityResults":
            tweet_result = tweet_result.get("tweet", tweet_result)

        return self._parse_tweet(tweet_result, tweet_id)

    async def _fetch_syndication(self, tweet_id: str) -> ExtractResponse | None:
        """Fetch tweet data using syndication API (fallback)."""
        url = f"{_SYNDICATION_API}/tweet-result"
        params = {
            "id": tweet_id,
            "lang": "en",
            "token": self._generate_syndication_token(tweet_id),
        }

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        response = await self.http.get(url, headers=headers, params=params)

        if response.status_code != 200:
            raise ExtractionError(f"Syndication API returned {response.status_code}")

        data = response.json()

        if not data:
            raise ExtractionError("Empty syndication response")

        return self._parse_syndication_tweet(data, tweet_id)

    @staticmethod
    def _generate_syndication_token(tweet_id: str) -> str:
        """Generate syndication API token."""
        # The token is derived from the tweet ID
        # From cobalt's twitter.js and yt-dlp
        id_int = int(tweet_id)
        token_value = ((id_int / 1e15) * 3.14159) % 1
        return f"{token_value:.16f}"[2:]

    def _parse_tweet(self, tweet_result: dict, tweet_id: str) -> ExtractResponse:
        """Parse tweet data from GraphQL response."""
        legacy = tweet_result.get("legacy", tweet_result)
        core = tweet_result.get("core", {})

        # Get user info
        user_result = traverse_obj(core, ("user_results", "result", "legacy")) or {}
        username = user_result.get("screen_name", "")

        # Get tweet text
        text = legacy.get("full_text", "")

        # Extract media
        extended_entities = legacy.get("extended_entities", {})
        media_list = extended_entities.get("media", [])

        if not media_list:
            # Try entities
            entities = legacy.get("entities", {})
            media_list = entities.get("media", [])

        formats = []
        thumbnail = None

        for media_item in media_list:
            media_type = media_item.get("type")

            if media_type == "video" or media_type == "animated_gif":
                # Get thumbnail
                if not thumbnail:
                    thumbnail = media_item.get("media_url_https")

                # Extract video variants
                video_info = media_item.get("video_info", {})
                variants = video_info.get("variants", [])
                float_or_none(video_info.get("duration_millis"), scale=1000)

                for variant in variants:
                    content_type = variant.get("content_type", "")
                    if content_type != "video/mp4":
                        continue

                    bitrate = int_or_none(variant.get("bitrate"))
                    variant_url = variant.get("url")
                    if not variant_url:
                        continue

                    # Parse resolution from URL
                    res_match = re.search(r"/(\d+)x(\d+)/", variant_url)
                    width = int(res_match.group(1)) if res_match else None
                    height = int(res_match.group(2)) if res_match else None

                    formats.append(
                        FormatInfo(
                            url=variant_url,
                            format_id=f"mp4_{bitrate}" if bitrate else "mp4",
                            ext="mp4",
                            width=width,
                            height=height,
                            tbr=float_or_none(bitrate, scale=1000),
                            vcodec="avc1",
                            acodec="mp4a" if media_type != "animated_gif" else "none",
                            format_type=FormatType.COMBINED
                            if media_type != "animated_gif"
                            else FormatType.VIDEO_ONLY,
                        )
                    )

            elif media_type == "photo":
                photo_url = media_item.get("media_url_https")
                if photo_url:
                    # Get original quality
                    photo_url = re.sub(r"\?.*$", "", photo_url) + "?format=jpg&name=orig"
                    formats.append(
                        FormatInfo(
                            url=photo_url,
                            ext="jpg",
                            format_type=FormatType.COMBINED,
                        )
                    )

        if not formats:
            raise ExtractionError(
                "No media found in tweet",
                error_code="twitter.no_media",
            )

        # Title
        title = f"@{username}: {text[:100]}" if text else f"Tweet by @{username}"

        # Metadata
        metadata = MediaMetadata(
            uploader=user_result.get("name"),
            uploader_id=username,
            uploader_url=f"https://twitter.com/{username}",
            description=text,
            like_count=int_or_none(legacy.get("favorite_count")),
            repost_count=int_or_none(legacy.get("retweet_count")),
            comment_count=int_or_none(legacy.get("reply_count")),
            upload_date=format_date(legacy.get("created_at")),
        )

        return ExtractResponse(
            platform=Platform.TWITTER,
            id=tweet_id,
            title=title,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    def _parse_syndication_tweet(self, data: dict, tweet_id: str) -> ExtractResponse:
        """Parse tweet data from syndication API."""
        text = data.get("text", "")
        user = data.get("user", {})
        username = user.get("screen_name", "")

        formats = []
        thumbnail = None

        # Check for media entities
        media_details = data.get("mediaDetails", [])

        for media in media_details:
            media_type = media.get("type")

            if media_type in ("video", "animated_gif"):
                if not thumbnail:
                    thumbnail = media.get("media_url_https")

                video_info = media.get("video_info", {})
                variants = video_info.get("variants", [])

                for variant in variants:
                    if variant.get("content_type") != "video/mp4":
                        continue

                    bitrate = int_or_none(variant.get("bitrate"))
                    variant_url = variant.get("url")
                    if not variant_url:
                        continue

                    res_match = re.search(r"/(\d+)x(\d+)/", variant_url)
                    width = int(res_match.group(1)) if res_match else None
                    height = int(res_match.group(2)) if res_match else None

                    formats.append(
                        FormatInfo(
                            url=variant_url,
                            format_id=f"mp4_{bitrate}" if bitrate else "mp4",
                            ext="mp4",
                            width=width,
                            height=height,
                            tbr=float_or_none(bitrate, scale=1000),
                            format_type=FormatType.COMBINED,
                        )
                    )

            elif media_type == "photo":
                photo_url = media.get("media_url_https")
                if photo_url:
                    formats.append(
                        FormatInfo(
                            url=photo_url + "?format=jpg&name=orig",
                            ext="jpg",
                            format_type=FormatType.COMBINED,
                        )
                    )

        if not formats:
            raise ExtractionError("No media in syndication response")

        title = f"@{username}: {text[:100]}" if text else f"Tweet by @{username}"

        metadata = MediaMetadata(
            uploader=user.get("name"),
            uploader_id=username,
            description=text,
            like_count=int_or_none(data.get("favorite_count")),
            repost_count=int_or_none(data.get("retweet_count")),
        )

        return ExtractResponse(
            platform=Platform.TWITTER,
            id=tweet_id,
            title=title,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )
