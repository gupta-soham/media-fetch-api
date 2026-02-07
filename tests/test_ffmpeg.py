"""Tests for FFmpeg codec maps and availability check."""

from app.core.ffmpeg import (
    AUDIO_CODEC_MAP,
    SUPPORTED_AUDIO_FORMATS,
    SUPPORTED_VIDEO_FORMATS,
    VIDEO_CONTAINER_MAP,
    is_ffmpeg_available,
)


class TestCodecMaps:
    def test_audio_formats_match_enum(self):
        from app.models.enums import AudioFormat

        enum_values = {f.value for f in AudioFormat if f.value != "best"}
        assert enum_values == SUPPORTED_AUDIO_FORMATS

    def test_video_formats_match_enum(self):
        from app.models.enums import VideoFormat

        enum_values = {f.value for f in VideoFormat if f.value != "best"}
        assert enum_values == SUPPORTED_VIDEO_FORMATS

    def test_every_audio_codec_has_required_keys(self):
        for fmt, info in AUDIO_CODEC_MAP.items():
            assert "codec" in info, f"{fmt} missing 'codec'"
            assert "ext" in info, f"{fmt} missing 'ext'"
            assert "params" in info, f"{fmt} missing 'params'"
            assert isinstance(info["params"], list)

    def test_every_video_container_has_required_keys(self):
        for fmt, info in VIDEO_CONTAINER_MAP.items():
            assert "ext" in info, f"{fmt} missing 'ext'"
            assert "params" in info, f"{fmt} missing 'params'"
            assert "vcodec_default" in info, f"{fmt} missing 'vcodec_default'"
            assert "acodec_default" in info, f"{fmt} missing 'acodec_default'"

    def test_mp3_uses_libmp3lame(self):
        assert AUDIO_CODEC_MAP["mp3"]["codec"] == "libmp3lame"

    def test_ogg_uses_libvorbis(self):
        assert AUDIO_CODEC_MAP["ogg"]["codec"] == "libvorbis"

    def test_flac_is_lossless(self):
        assert AUDIO_CODEC_MAP["flac"]["codec"] == "flac"

    def test_wav_is_pcm(self):
        assert AUDIO_CODEC_MAP["wav"]["codec"] == "pcm_s16le"

    def test_mp4_has_faststart(self):
        assert "-movflags" in VIDEO_CONTAINER_MAP["mp4"]["params"]
        assert "+faststart" in VIDEO_CONTAINER_MAP["mp4"]["params"]

    def test_mov_has_faststart(self):
        assert "-movflags" in VIDEO_CONTAINER_MAP["mov"]["params"]

    def test_webm_no_extra_params(self):
        assert VIDEO_CONTAINER_MAP["webm"]["params"] == []

    def test_mkv_no_extra_params(self):
        assert VIDEO_CONTAINER_MAP["mkv"]["params"] == []

    def test_no_aac_or_opus_in_audio_map(self):
        assert "aac" not in AUDIO_CODEC_MAP
        assert "opus" not in AUDIO_CODEC_MAP


class TestFFmpegAvailability:
    def test_is_ffmpeg_available_returns_bool(self):
        result = is_ffmpeg_available()
        assert isinstance(result, bool)
