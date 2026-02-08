"""Tests for extractor loading and base class behaviour."""

from pathlib import Path

from app.extractors import ExtractionError, get_extractor
from app.extractors.facebook import FacebookExtractor
from app.extractors.instagram import InstagramExtractor
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


class TestExtractorParsing:
    def test_instagram_public_json_parsing(self):
        html = Path("tests/data/instagram_shared.html").read_text(encoding="utf-8")
        extractor = InstagramExtractor()
        response = extractor._parse_public_json(html, "ABC123")
        assert response is not None
        assert response.formats
        assert response.formats[0].url == "https://example.com/video.mp4"

    def test_facebook_fragment_parsing(self):
        html = Path("tests/data/facebook_fragment.html").read_text(encoding="utf-8")
        extractor = FacebookExtractor()
        formats, title, thumbnail, description = extractor._parse_html(
            "https://www.facebook.com/example", html
        )
        assert any(fmt.url == "https://example.com/prog.mp4" for fmt in formats)
        assert title == "Example Video"
        assert thumbnail == "https://example.com/thumb.jpg"
        assert description == "Example description"
