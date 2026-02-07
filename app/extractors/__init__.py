"""
Platform-specific media extractors.

Each extractor handles URL matching, API interaction, format extraction,
and metadata collection for a specific platform.
"""

from ..models.enums import Platform
from .base import BaseExtractor, ExtractionError

# Lazy imports to avoid circular dependencies and speed up startup
_EXTRACTOR_MAP: dict[Platform, type[BaseExtractor]] | None = None


def _load_extractors() -> dict[Platform, type[BaseExtractor]]:
    from .facebook import FacebookExtractor
    from .google_drive import GoogleDriveExtractor
    from .instagram import InstagramExtractor
    from .pinterest import PinterestExtractor
    from .reddit import RedditExtractor
    from .snapchat import SnapchatExtractor
    from .soundcloud import SoundCloudExtractor
    from .tiktok import TikTokExtractor
    from .twitch import TwitchExtractor
    from .twitter import TwitterExtractor
    from .vimeo import VimeoExtractor
    from .youtube import YouTubeExtractor

    return {
        Platform.YOUTUBE: YouTubeExtractor,
        Platform.INSTAGRAM: InstagramExtractor,
        Platform.TWITTER: TwitterExtractor,
        Platform.TIKTOK: TikTokExtractor,
        Platform.FACEBOOK: FacebookExtractor,
        Platform.REDDIT: RedditExtractor,
        Platform.SOUNDCLOUD: SoundCloudExtractor,
        Platform.VIMEO: VimeoExtractor,
        Platform.TWITCH: TwitchExtractor,
        Platform.GOOGLE_DRIVE: GoogleDriveExtractor,
        Platform.PINTEREST: PinterestExtractor,
        Platform.SNAPCHAT: SnapchatExtractor,
    }


def get_extractor(platform: Platform) -> BaseExtractor:
    """Get an extractor instance for the given platform."""
    global _EXTRACTOR_MAP
    if _EXTRACTOR_MAP is None:
        _EXTRACTOR_MAP = _load_extractors()

    extractor_class = _EXTRACTOR_MAP.get(platform)
    if extractor_class is None:
        raise ExtractionError(f"No extractor available for platform: {platform}")

    return extractor_class()


__all__ = ["BaseExtractor", "ExtractionError", "get_extractor"]
