"""
URL pattern matching engine to identify platforms and extract media IDs.
Ported from cobalt's url.js + match.js and service-patterns.js logic.

Each platform defines a set of URL patterns with named groups for extracting
the media ID and other parameters. The matcher normalizes URLs, resolves
aliases (short links) and matches against all registered patterns.
"""

import logging
import re
from urllib.parse import urlparse, urlunparse

from ..models.enums import Platform

logger = logging.getLogger(__name__)


class URLPattern:
    """A URL pattern for matching a specific platform's URLs."""

    def __init__(
        self,
        platform: Platform,
        pattern: str,
        id_group: str = "id",
        domain_pattern: str | None = None,
    ):
        self.platform = platform
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.id_group = id_group
        self.domain_pattern = re.compile(domain_pattern, re.IGNORECASE) if domain_pattern else None


class MatchResult:
    """Result of a URL match."""

    def __init__(
        self,
        platform: Platform,
        media_id: str,
        original_url: str,
        params: dict[str, str] | None = None,
    ):
        self.platform = platform
        self.media_id = media_id
        self.original_url = original_url
        self.params = params or {}


# URL alias mappings (short links and alternate domains)
# Ported from cobalt's aliasURL() in url.js
URL_ALIASES: dict[str, tuple[str, str | None]] = {
    # (domain_from): (domain_to, path_transform)
    "youtu.be": ("youtube.com", None),
    "m.youtube.com": ("youtube.com", None),
    "music.youtube.com": ("youtube.com", None),
    "vxtwitter.com": ("twitter.com", None),
    "fixvx.com": ("twitter.com", None),
    "fxtwitter.com": ("twitter.com", None),
    "x.com": ("twitter.com", None),
    "mobile.twitter.com": ("twitter.com", None),
    "m.tiktok.com": ("tiktok.com", None),
    "vm.tiktok.com": ("tiktok.com", None),
    "vt.tiktok.com": ("tiktok.com", None),
    "m.facebook.com": ("facebook.com", None),
    "web.facebook.com": ("facebook.com", None),
    "touch.facebook.com": ("facebook.com", None),
    "fb.watch": ("facebook.com", None),
    "old.reddit.com": ("reddit.com", None),
    "new.reddit.com": ("reddit.com", None),
    "m.reddit.com": ("reddit.com", None),
    "www.reddit.com": ("reddit.com", None),
    "m.soundcloud.com": ("soundcloud.com", None),
    "on.soundcloud.com": ("soundcloud.com", None),
    "m.vimeo.com": ("vimeo.com", None),
    "player.vimeo.com": ("vimeo.com", None),
    "m.twitch.tv": ("twitch.tv", None),
    "pin.it": ("pinterest.com", None),
    "in.pinterest.com": ("pinterest.com", None),
    "m.pinterest.com": ("pinterest.com", None),
}

# Short link domains that need redirect resolution
SHORT_LINK_DOMAINS = {
    "youtu.be",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "fb.watch",
    "pin.it",
    "v.redd.it",
    "redd.it",
    "on.soundcloud.com",
}

# All URL patterns organized by platform
# Ported from cobalt's service-patterns.js and yt-dlp extractor _VALID_URL patterns
_PATTERNS: list[URLPattern] = [
    # === YouTube ===
    URLPattern(
        Platform.YOUTUBE,
        r"(?:https?://)?(?:www\.)?youtube\.com/watch\?.*?v=(?P<id>[a-zA-Z0-9_-]{11})",
    ),
    URLPattern(
        Platform.YOUTUBE,
        r"(?:https?://)?(?:www\.)?youtube\.com/embed/(?P<id>[a-zA-Z0-9_-]{11})",
    ),
    URLPattern(
        Platform.YOUTUBE,
        r"(?:https?://)?(?:www\.)?youtube\.com/v/(?P<id>[a-zA-Z0-9_-]{11})",
    ),
    URLPattern(
        Platform.YOUTUBE,
        r"(?:https?://)?(?:www\.)?youtube\.com/shorts/(?P<id>[a-zA-Z0-9_-]{11})",
    ),
    URLPattern(
        Platform.YOUTUBE,
        r"(?:https?://)?(?:www\.)?youtube\.com/live/(?P<id>[a-zA-Z0-9_-]{11})",
    ),
    URLPattern(
        Platform.YOUTUBE,
        r"(?:https?://)?(?:www\.)?youtube\.com/clip/(?P<id>[a-zA-Z0-9_-]+)",
    ),
    URLPattern(
        Platform.YOUTUBE,
        r"(?:https?://)?youtu\.be/(?P<id>[a-zA-Z0-9_-]{11})",
    ),
    URLPattern(
        Platform.YOUTUBE,
        r"(?:https?://)?music\.youtube\.com/watch\?.*?v=(?P<id>[a-zA-Z0-9_-]{11})",
    ),
    # === Instagram ===
    URLPattern(
        Platform.INSTAGRAM,
        r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv)/(?P<id>[a-zA-Z0-9_-]+)",
    ),
    URLPattern(
        Platform.INSTAGRAM,
        r"(?:https?://)?(?:www\.)?instagram\.com/stories/(?P<user>[^/]+)/(?P<id>\d+)",
        id_group="id",
    ),
    # === Twitter/X ===
    URLPattern(
        Platform.TWITTER,
        r"(?:https?://)?(?:(?:www|mobile)\.)?(?:twitter\.com|x\.com)/(?P<user>[^/]+)/status/(?P<id>\d+)",
        id_group="id",
    ),
    URLPattern(
        Platform.TWITTER,
        r"(?:https?://)?(?:(?:vx|fixvx|fx)twitter\.com)/(?P<user>[^/]+)/status/(?P<id>\d+)",
        id_group="id",
    ),
    # === TikTok ===
    URLPattern(
        Platform.TIKTOK,
        r"(?:https?://)?(?:www\.)?tiktok\.com/@(?P<user>[^/]+)/video/(?P<id>\d+)",
        id_group="id",
    ),
    URLPattern(
        Platform.TIKTOK,
        r"(?:https?://)?(?:www\.)?tiktok\.com/@(?P<user>[^/]+)/photo/(?P<id>\d+)",
        id_group="id",
    ),
    URLPattern(
        Platform.TIKTOK,
        r"(?:https?://)?(?:vm|vt)\.tiktok\.com/(?P<id>[a-zA-Z0-9]+)",
    ),
    URLPattern(
        Platform.TIKTOK,
        r"(?:https?://)?(?:m\.)?tiktok\.com/v/(?P<id>\d+)",
    ),
    # === Facebook ===
    URLPattern(
        Platform.FACEBOOK,
        r"(?:https?://)?(?:www\.|web\.|m\.)?facebook\.com/(?:[^/]+/videos/|video\.php\?v=|watch/?\?v=|reel/)(?P<id>\d+)",
        id_group="id",
    ),
    URLPattern(
        Platform.FACEBOOK,
        r"(?:https?://)?(?:www\.|web\.|m\.)?facebook\.com/(?P<user>[^/]+)/(?:posts|videos)/(?P<id>[^/?&]+)",
        id_group="id",
    ),
    URLPattern(
        Platform.FACEBOOK,
        r"(?:https?://)?fb\.watch/(?P<id>[a-zA-Z0-9_-]+)",
    ),
    URLPattern(
        Platform.FACEBOOK,
        r"(?:https?://)?(?:www\.|web\.|m\.)?facebook\.com/share/(?:v|r)/(?P<id>[a-zA-Z0-9_-]+)",
    ),
    # === Reddit ===
    URLPattern(
        Platform.REDDIT,
        r"(?:https?://)?(?:(?:www|old|new|m)\.)?reddit\.com/r/(?P<subreddit>[^/]+)/comments/(?P<id>[a-zA-Z0-9]+)",
        id_group="id",
    ),
    URLPattern(
        Platform.REDDIT,
        r"(?:https?://)?v\.redd\.it/(?P<id>[a-zA-Z0-9]+)",
    ),
    URLPattern(
        Platform.REDDIT,
        r"(?:https?://)?redd\.it/(?P<id>[a-zA-Z0-9]+)",
    ),
    # === SoundCloud ===
    URLPattern(
        Platform.SOUNDCLOUD,
        r"(?:https?://)?(?:www\.|m\.)?soundcloud\.com/(?P<user>[^/]+)/(?P<id>[^/?&#]+)(?:\?.*)?$",
        id_group="id",
    ),
    URLPattern(
        Platform.SOUNDCLOUD,
        r"(?:https?://)?on\.soundcloud\.com/(?P<id>[a-zA-Z0-9]+)",
    ),
    # === Vimeo ===
    URLPattern(
        Platform.VIMEO,
        r"(?:https?://)?(?:www\.|player\.)?vimeo\.com/(?:video/)?(?P<id>\d+)",
    ),
    URLPattern(
        Platform.VIMEO,
        r"(?:https?://)?(?:www\.)?vimeo\.com/(?P<user>[^/]+)/(?P<id>[^/?&#]+)",
        id_group="id",
    ),
    # === Twitch ===
    URLPattern(
        Platform.TWITCH,
        r"(?:https?://)?(?:www\.|m\.)?twitch\.tv/(?P<channel>[^/]+)/clip/(?P<id>[a-zA-Z0-9_-]+)",
        id_group="id",
    ),
    URLPattern(
        Platform.TWITCH,
        r"(?:https?://)?clips\.twitch\.tv/(?P<id>[a-zA-Z0-9_-]+(?:-[a-zA-Z0-9_-]+)*)",
    ),
    URLPattern(
        Platform.TWITCH,
        r"(?:https?://)?(?:www\.|m\.)?twitch\.tv/videos/(?P<id>\d+)",
    ),
    # === Google Drive ===
    URLPattern(
        Platform.GOOGLE_DRIVE,
        r"(?:https?://)?drive\.google\.com/file/d/(?P<id>[a-zA-Z0-9_-]+)",
    ),
    URLPattern(
        Platform.GOOGLE_DRIVE,
        r"(?:https?://)?drive\.google\.com/open\?id=(?P<id>[a-zA-Z0-9_-]+)",
    ),
    URLPattern(
        Platform.GOOGLE_DRIVE,
        r"(?:https?://)?drive\.google\.com/uc\?.*?id=(?P<id>[a-zA-Z0-9_-]+)",
    ),
    URLPattern(
        Platform.GOOGLE_DRIVE,
        r"(?:https?://)?docs\.google\.com/(?:document|presentation|spreadsheets)/d/(?P<id>[a-zA-Z0-9_-]+)",
    ),
    # === Pinterest ===
    URLPattern(
        Platform.PINTEREST,
        r"(?:https?://)?(?:[a-z]{2}\.)?pinterest\.com/pin/(?P<id>\d+)",
    ),
    URLPattern(
        Platform.PINTEREST,
        r"(?:https?://)?pin\.it/(?P<id>[a-zA-Z0-9]+)",
    ),
    # === Snapchat ===
    URLPattern(
        Platform.SNAPCHAT,
        r"(?:https?://)?(?:www\.)?snapchat\.com/spotlight/(?P<id>[a-zA-Z0-9_-]+)",
    ),
    URLPattern(
        Platform.SNAPCHAT,
        r"(?:https?://)?(?:www\.)?snapchat\.com/(?:t|add)/(?P<user>[^/]+)(?:/(?P<id>[^/?]+))?",
        id_group="id",
    ),
    URLPattern(
        Platform.SNAPCHAT,
        r"(?:https?://)?story\.snapchat\.com/s/(?P<id>[^/?]+)",
    ),
    URLPattern(
        Platform.SNAPCHAT,
        r"(?:https?://)?(?:www\.)?snapchat\.com/p/(?P<id>[a-zA-Z0-9_-]+)",
    ),
]


def normalize_url(url: str) -> str:
    """
    Normalize a URL by cleaning up query parameters and resolving domain aliases.
    Ported from cobalt's normalizeURL() and cleanURL() in url.js.
    """
    url = url.strip()

    # Ensure scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Strip www. prefix for matching
    hostname_clean = hostname.removeprefix("www.")

    # Apply domain aliases
    if hostname_clean in URL_ALIASES:
        new_domain, _path_transform = URL_ALIASES[hostname_clean]
        parsed = parsed._replace(netloc=new_domain)

    # Handle youtu.be short links -> youtube.com/watch?v=ID
    if hostname_clean == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0] if parsed.path else ""
        if video_id:
            parsed = parsed._replace(
                netloc="youtube.com",
                path="/watch",
                query=f"v={video_id}",
            )

    return urlunparse(parsed)


def is_short_link(url: str) -> bool:
    """Check if a URL is a short link that needs redirect resolution."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        hostname = (parsed.hostname or "").removeprefix("www.")
        return hostname in SHORT_LINK_DOMAINS
    except Exception:
        return False


def match_url(url: str) -> MatchResult | None:
    """
    Match a URL against all registered platform patterns.

    Returns a MatchResult with the platform, media ID and any extra params,
    or None if no match is found.
    """
    normalized = normalize_url(url)

    for pattern in _PATTERNS:
        match = pattern.pattern.search(normalized)
        if match:
            try:
                media_id = match.group(pattern.id_group)
            except IndexError:
                media_id = match.group(1)

            if not media_id:
                continue

            # Extract all named groups as params
            params = {k: v for k, v in match.groupdict().items() if v and k != pattern.id_group}

            return MatchResult(
                platform=pattern.platform,
                media_id=media_id,
                original_url=url,
                params=params,
            )

    return None


def get_supported_platforms() -> list[dict]:
    """Return information about all supported platforms."""
    platform_info = {
        Platform.YOUTUBE: {
            "name": "YouTube",
            "domains": ["youtube.com", "youtu.be", "music.youtube.com"],
            "examples": [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtu.be/dQw4w9WgXcQ",
                "https://www.youtube.com/shorts/VIDEO_ID",
            ],
        },
        Platform.INSTAGRAM: {
            "name": "Instagram",
            "domains": ["instagram.com"],
            "examples": [
                "https://www.instagram.com/p/POST_ID/",
                "https://www.instagram.com/reel/REEL_ID/",
            ],
        },
        Platform.TWITTER: {
            "name": "Twitter/X",
            "domains": ["twitter.com", "x.com"],
            "examples": ["https://twitter.com/user/status/123456789"],
        },
        Platform.TIKTOK: {
            "name": "TikTok",
            "domains": ["tiktok.com", "vm.tiktok.com", "vt.tiktok.com"],
            "examples": ["https://www.tiktok.com/@user/video/123456789"],
        },
        Platform.FACEBOOK: {
            "name": "Facebook",
            "domains": ["facebook.com", "fb.watch"],
            "examples": ["https://www.facebook.com/watch/?v=123456789"],
        },
        Platform.REDDIT: {
            "name": "Reddit",
            "domains": ["reddit.com", "v.redd.it"],
            "examples": ["https://www.reddit.com/r/sub/comments/abc123/title/"],
        },
        Platform.SOUNDCLOUD: {
            "name": "SoundCloud",
            "domains": ["soundcloud.com"],
            "examples": ["https://soundcloud.com/artist/track-name"],
        },
        Platform.VIMEO: {
            "name": "Vimeo",
            "domains": ["vimeo.com"],
            "examples": ["https://vimeo.com/123456789"],
        },
        Platform.TWITCH: {
            "name": "Twitch",
            "domains": ["twitch.tv", "clips.twitch.tv"],
            "examples": ["https://clips.twitch.tv/ClipSlug"],
        },
        Platform.GOOGLE_DRIVE: {
            "name": "Google Drive",
            "domains": ["drive.google.com"],
            "examples": ["https://drive.google.com/file/d/FILE_ID/view"],
        },
        Platform.PINTEREST: {
            "name": "Pinterest",
            "domains": ["pinterest.com", "pin.it"],
            "examples": ["https://www.pinterest.com/pin/123456789/"],
        },
        Platform.SNAPCHAT: {
            "name": "Snapchat",
            "domains": ["snapchat.com"],
            "examples": ["https://www.snapchat.com/spotlight/SPOTLIGHT_ID"],
        },
    }

    return [{"platform": p.value, **info} for p, info in platform_info.items()]
