"""Core utilities: HTTP, cookies, URL matching, parsing, and FFmpeg."""

from .cookies import CookieManager, get_cookie_manager
from .ffmpeg import FFmpegError, get_ffmpeg_version, is_ffmpeg_available
from .url_matcher import MatchResult, get_supported_platforms, match_url

__all__ = [
    "CookieManager",
    "FFmpegError",
    "MatchResult",
    "get_cookie_manager",
    "get_ffmpeg_version",
    "get_supported_platforms",
    "is_ffmpeg_available",
    "match_url",
]
