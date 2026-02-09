"""Tests for extractor loading and base class behaviour."""

from app.extractors import ExtractionError, get_extractor
from app.extractors.base import BaseExtractor
from app.models.enums import Platform


class TestExtractorFactory:
    def test_all_platforms_have_extractors(self):
        for platform in Platform:
            ext = get_extractor(platform)
            assert isinstance(ext, BaseExtractor)
            assert ext.platform == platform

    def test_extractor_class_names_match(self):
        expected = {
            Platform.YOUTUBE: "YouTubeExtractor",
            Platform.INSTAGRAM: "InstagramExtractor",
            Platform.TWITTER: "TwitterExtractor",
            Platform.TIKTOK: "TikTokExtractor",
            Platform.FACEBOOK: "FacebookExtractor",
            Platform.REDDIT: "RedditExtractor",
            Platform.SOUNDCLOUD: "SoundCloudExtractor",
            Platform.VIMEO: "VimeoExtractor",
            Platform.TWITCH: "TwitchExtractor",
            Platform.GOOGLE_DRIVE: "GoogleDriveExtractor",
            Platform.PINTEREST: "PinterestExtractor",
            Platform.SNAPCHAT: "SnapchatExtractor",
        }
        for platform, class_name in expected.items():
            ext = get_extractor(platform)
            assert ext.__class__.__name__ == class_name


class TestExtractionError:
    def test_message(self):
        err = ExtractionError("something broke")
        assert str(err) == "something broke"
        assert err.error_code is None

    def test_with_code(self):
        err = ExtractionError("nope", error_code="youtube.cipher_fail")
        assert err.error_code == "youtube.cipher_fail"
