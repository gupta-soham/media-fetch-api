"""Tests for Pydantic models and enum definitions."""

import pytest

from app.models.enums import AudioFormat, FormatType, MediaType, Platform, Quality, VideoFormat
from app.models.request import ExtractRequest
from app.models.response import ExtractResponse, FormatInfo, MediaMetadata


# ── Enum completeness ────────────────────────────────────────────────
class TestEnums:
    def test_platform_count(self):
        assert len(Platform) == 12

    def test_audio_formats(self):
        expected = {"best", "mp3", "flac", "wav", "ogg"}
        assert {f.value for f in AudioFormat} == expected

    def test_video_formats(self):
        expected = {"best", "mp4", "mkv", "webm", "mov"}
        assert {f.value for f in VideoFormat} == expected

    def test_media_types(self):
        expected = {"video", "audio", "both"}
        assert {m.value for m in MediaType} == expected

    def test_format_types(self):
        expected = {"video_only", "audio_only", "combined"}
        assert {f.value for f in FormatType} == expected

    def test_quality_values_are_numeric_or_keyword(self):
        for q in Quality:
            assert q.value.isdigit() or q.value in ("best", "worst")


# ── ExtractRequest ───────────────────────────────────────────────────
class TestExtractRequest:
    def test_minimal(self):
        req = ExtractRequest(url="https://youtube.com/watch?v=abc")
        assert req.media_type == MediaType.BOTH
        assert req.quality == Quality.BEST
        assert req.audio_format == AudioFormat.BEST
        assert req.video_format == VideoFormat.BEST
        assert req.include_metadata is True
        assert req.include_subtitles is False
        assert req.subtitle_lang is None
        assert req.cookie_file is None
        assert req.password is None

    def test_full(self):
        req = ExtractRequest(
            url="https://vimeo.com/123",
            media_type="audio",
            quality="720",
            audio_format="ogg",
            video_format="mkv",
            include_metadata=False,
            include_subtitles=True,
            subtitle_lang="es",
            cookie_file="vimeo",
            password="secret",
        )
        assert req.audio_format == AudioFormat.OGG
        assert req.video_format == VideoFormat.MKV
        assert req.quality == Quality.Q720
        assert req.password == "secret"

    def test_invalid_audio_format_rejected(self):
        with pytest.raises(ValueError, match="Input should be"):
            ExtractRequest(url="https://x.com/a/status/1", audio_format="aac")

    def test_invalid_video_format_rejected(self):
        with pytest.raises(ValueError, match="Input should be"):
            ExtractRequest(url="https://x.com/a/status/1", video_format="avi")


# ── FormatInfo ───────────────────────────────────────────────────────
class TestFormatInfo:
    def test_minimal(self):
        f = FormatInfo(url="https://example.com/video.mp4")
        assert f.format_type == FormatType.COMBINED
        assert f.ext is None

    def test_video_only(self):
        f = FormatInfo(
            url="https://example.com/v.mp4",
            vcodec="avc1",
            acodec="none",
            format_type=FormatType.VIDEO_ONLY,
            height=1080,
        )
        assert f.format_type == FormatType.VIDEO_ONLY
        assert f.height == 1080

    def test_audio_only(self):
        f = FormatInfo(
            url="https://example.com/a.mp3",
            vcodec="none",
            acodec="mp3",
            format_type=FormatType.AUDIO_ONLY,
            abr=320.0,
        )
        assert f.format_type == FormatType.AUDIO_ONLY
        assert f.abr == 320.0


# ── ExtractResponse ──────────────────────────────────────────────────
class TestExtractResponse:
    def test_minimal(self):
        r = ExtractResponse(platform=Platform.YOUTUBE, id="abc")
        assert r.success is True
        assert r.formats == []
        assert r.metadata is None
        assert r.subtitles is None

    def test_with_formats(self):
        r = ExtractResponse(
            platform=Platform.TIKTOK,
            id="123",
            title="Test",
            formats=[FormatInfo(url="https://example.com/v.mp4")],
        )
        assert len(r.formats) == 1


# ── MediaMetadata ────────────────────────────────────────────────────
class TestMediaMetadata:
    def test_all_optional(self):
        m = MediaMetadata()
        assert m.uploader is None
        assert m.view_count is None
