"""Tests for the URL pattern matching engine."""

import pytest

from app.core.url_matcher import (
    get_supported_platforms,
    is_short_link,
    match_url,
    normalize_url,
)
from app.models.enums import Platform


# ── YouTube ──────────────────────────────────────────────────────────
class TestYouTubeURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz",
        ],
    )
    def test_standard_watch_urls(self, url):
        result = match_url(url)
        assert result is not None
        assert result.platform == Platform.YOUTUBE
        assert result.media_id == "dQw4w9WgXcQ"

    def test_short_url(self):
        result = match_url("https://youtu.be/dQw4w9WgXcQ")
        assert result is not None
        assert result.platform == Platform.YOUTUBE
        assert result.media_id == "dQw4w9WgXcQ"

    def test_embed_url(self):
        result = match_url("https://www.youtube.com/embed/dQw4w9WgXcQ")
        assert result is not None
        assert result.platform == Platform.YOUTUBE
        assert result.media_id == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        result = match_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        assert result is not None
        assert result.platform == Platform.YOUTUBE
        assert result.media_id == "dQw4w9WgXcQ"

    def test_live_url(self):
        result = match_url("https://www.youtube.com/live/dQw4w9WgXcQ")
        assert result is not None
        assert result.platform == Platform.YOUTUBE

    def test_music_url(self):
        result = match_url("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
        assert result is not None
        assert result.platform == Platform.YOUTUBE
        assert result.media_id == "dQw4w9WgXcQ"


# ── Instagram ────────────────────────────────────────────────────────
class TestInstagramURLs:
    def test_post(self):
        result = match_url("https://www.instagram.com/p/CxYz123AbC/")
        assert result is not None
        assert result.platform == Platform.INSTAGRAM
        assert result.media_id == "CxYz123AbC"

    def test_reel(self):
        result = match_url("https://www.instagram.com/reel/CxYz123AbC/")
        assert result is not None
        assert result.platform == Platform.INSTAGRAM

    def test_tv(self):
        result = match_url("https://www.instagram.com/tv/CxYz123AbC/")
        assert result is not None
        assert result.platform == Platform.INSTAGRAM


# ── Twitter / X ──────────────────────────────────────────────────────
class TestTwitterURLs:
    def test_twitter_dot_com(self):
        result = match_url("https://twitter.com/elonmusk/status/123456789")
        assert result is not None
        assert result.platform == Platform.TWITTER
        assert result.media_id == "123456789"

    def test_x_dot_com(self):
        result = match_url("https://x.com/elonmusk/status/123456789")
        assert result is not None
        assert result.platform == Platform.TWITTER
        assert result.media_id == "123456789"

    def test_vxtwitter(self):
        result = match_url("https://vxtwitter.com/user/status/999")
        assert result is not None
        assert result.platform == Platform.TWITTER

    def test_fxtwitter(self):
        result = match_url("https://fxtwitter.com/user/status/999")
        assert result is not None
        assert result.platform == Platform.TWITTER

    def test_mobile(self):
        result = match_url("https://mobile.twitter.com/user/status/999")
        assert result is not None
        assert result.platform == Platform.TWITTER


# ── TikTok ───────────────────────────────────────────────────────────
class TestTikTokURLs:
    def test_standard_video(self):
        result = match_url("https://www.tiktok.com/@user/video/7123456789012345678")
        assert result is not None
        assert result.platform == Platform.TIKTOK
        assert result.media_id == "7123456789012345678"

    def test_photo(self):
        result = match_url("https://www.tiktok.com/@user/photo/7123456789012345678")
        assert result is not None
        assert result.platform == Platform.TIKTOK

    def test_short_link_vm_detected(self):
        # Short links are resolved via redirect at extraction time
        assert is_short_link("https://vm.tiktok.com/ZMxxxxxx/")

    def test_short_link_vt_detected(self):
        assert is_short_link("https://vt.tiktok.com/ZMxxxxxx/")


# ── Facebook ─────────────────────────────────────────────────────────
class TestFacebookURLs:
    def test_video_url(self):
        result = match_url("https://www.facebook.com/user/videos/123456789")
        assert result is not None
        assert result.platform == Platform.FACEBOOK

    def test_watch_url(self):
        result = match_url("https://www.facebook.com/watch/?v=123456789")
        assert result is not None
        assert result.platform == Platform.FACEBOOK

    def test_fb_watch_short_detected(self):
        # fb.watch links are resolved via redirect at extraction time
        assert is_short_link("https://fb.watch/abc123/")

    def test_reel(self):
        result = match_url("https://www.facebook.com/reel/123456789")
        assert result is not None
        assert result.platform == Platform.FACEBOOK


# ── Reddit ───────────────────────────────────────────────────────────
class TestRedditURLs:
    def test_standard_post(self):
        result = match_url("https://www.reddit.com/r/funny/comments/abc123/some_title/")
        assert result is not None
        assert result.platform == Platform.REDDIT
        assert result.media_id == "abc123"

    def test_old_reddit(self):
        result = match_url("https://old.reddit.com/r/test/comments/xyz789/t/")
        assert result is not None
        assert result.platform == Platform.REDDIT

    def test_v_redd_it(self):
        result = match_url("https://v.redd.it/abc123def")
        assert result is not None
        assert result.platform == Platform.REDDIT

    def test_redd_it_short(self):
        result = match_url("https://redd.it/abc123")
        assert result is not None
        assert result.platform == Platform.REDDIT


# ── SoundCloud ───────────────────────────────────────────────────────
class TestSoundCloudURLs:
    def test_track(self):
        result = match_url("https://soundcloud.com/artist/track-name")
        assert result is not None
        assert result.platform == Platform.SOUNDCLOUD
        assert result.media_id == "track-name"

    def test_mobile(self):
        result = match_url("https://m.soundcloud.com/artist/track-name")
        assert result is not None
        assert result.platform == Platform.SOUNDCLOUD

    def test_short_link_detected(self):
        # Short links are resolved via redirect at extraction time
        assert is_short_link("https://on.soundcloud.com/abc123")


# ── Vimeo ────────────────────────────────────────────────────────────
class TestVimeoURLs:
    def test_numeric_id(self):
        result = match_url("https://vimeo.com/123456789")
        assert result is not None
        assert result.platform == Platform.VIMEO
        assert result.media_id == "123456789"

    def test_player_url(self):
        result = match_url("https://player.vimeo.com/video/123456789")
        assert result is not None
        assert result.platform == Platform.VIMEO


# ── Twitch ───────────────────────────────────────────────────────────
class TestTwitchURLs:
    def test_clips_subdomain(self):
        result = match_url("https://clips.twitch.tv/FunnyClipSlug-abc123")
        assert result is not None
        assert result.platform == Platform.TWITCH
        assert result.media_id == "FunnyClipSlug-abc123"

    def test_channel_clip(self):
        result = match_url("https://www.twitch.tv/channel/clip/ClipSlug")
        assert result is not None
        assert result.platform == Platform.TWITCH
        assert result.media_id == "ClipSlug"

    def test_vod(self):
        result = match_url("https://www.twitch.tv/videos/123456789")
        assert result is not None
        assert result.platform == Platform.TWITCH
        assert result.media_id == "123456789"


# ── Google Drive ─────────────────────────────────────────────────────
class TestGoogleDriveURLs:
    def test_file_d(self):
        result = match_url("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view")
        assert result is not None
        assert result.platform == Platform.GOOGLE_DRIVE
        assert result.media_id == "1AbCdEfGhIjKlMnOpQrStUvWxYz"

    def test_open_id(self):
        result = match_url("https://drive.google.com/open?id=FILEID")
        assert result is not None
        assert result.platform == Platform.GOOGLE_DRIVE

    def test_uc_id(self):
        result = match_url("https://drive.google.com/uc?id=FILEID&export=download")
        assert result is not None
        assert result.platform == Platform.GOOGLE_DRIVE


# ── Pinterest ────────────────────────────────────────────────────────
class TestPinterestURLs:
    def test_pin(self):
        result = match_url("https://www.pinterest.com/pin/123456789/")
        assert result is not None
        assert result.platform == Platform.PINTEREST
        assert result.media_id == "123456789"

    def test_short_link_detected(self):
        # pin.it links are resolved via redirect at extraction time
        assert is_short_link("https://pin.it/abc123")


# ── Snapchat ─────────────────────────────────────────────────────────
class TestSnapchatURLs:
    def test_spotlight(self):
        result = match_url("https://www.snapchat.com/spotlight/abc123XYZ")
        assert result is not None
        assert result.platform == Platform.SNAPCHAT
        assert result.media_id == "abc123XYZ"

    def test_story(self):
        result = match_url("https://story.snapchat.com/s/some-story-id")
        assert result is not None
        assert result.platform == Platform.SNAPCHAT


# ── Negative cases ───────────────────────────────────────────────────
class TestUnsupportedURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.google.com",
            "https://example.com/video/123",
            "not-a-url",
            "",
            "ftp://files.example.com/video.mp4",
            "https://www.youtube.com/",
        ],
    )
    def test_unsupported(self, url):
        result = match_url(url)
        assert result is None


# ── Utilities ────────────────────────────────────────────────────────
class TestURLUtilities:
    def test_normalize_adds_scheme(self):
        assert normalize_url("youtube.com/watch?v=abc").startswith("https://")

    def test_is_short_link_positive(self):
        assert is_short_link("https://youtu.be/abc") is True
        assert is_short_link("https://vm.tiktok.com/abc") is True
        assert is_short_link("https://fb.watch/abc") is True
        assert is_short_link("https://pin.it/abc") is True

    def test_is_short_link_negative(self):
        assert is_short_link("https://www.youtube.com/watch?v=abc") is False
        assert is_short_link("https://example.com") is False

    def test_supported_platforms_complete(self):
        platforms = get_supported_platforms()
        assert len(platforms) == len(Platform)
        returned_names = {p["platform"] for p in platforms}
        for p in Platform:
            assert p.value in returned_names
