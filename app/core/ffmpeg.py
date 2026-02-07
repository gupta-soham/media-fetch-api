"""
FFmpeg integration for audio format conversion and stream merging.
Supports audio output in mp3, flac, wav, ogg and video containers
mp4, mkv, webm, mov.
"""

import asyncio
import logging
import os
import subprocess

from ..config import get_ffmpeg_path

logger = logging.getLogger(__name__)

# Supported audio formats and their FFmpeg codec configuration
AUDIO_CODEC_MAP = {
    "mp3": {"codec": "libmp3lame", "ext": "mp3", "params": ["-q:a", "2"]},
    "flac": {"codec": "flac", "ext": "flac", "params": []},
    "wav": {"codec": "pcm_s16le", "ext": "wav", "params": []},
    "ogg": {"codec": "libvorbis", "ext": "ogg", "params": ["-q:a", "6"]},
}

# Supported video container formats and their FFmpeg muxer configuration
VIDEO_CONTAINER_MAP = {
    "mp4": {
        "ext": "mp4",
        "params": ["-movflags", "+faststart"],
        "vcodec_default": "copy",
        "acodec_default": "copy",
    },
    "mkv": {
        "ext": "mkv",
        "params": [],
        "vcodec_default": "copy",
        "acodec_default": "copy",
    },
    "webm": {
        "ext": "webm",
        "params": [],
        "vcodec_default": "copy",
        "acodec_default": "copy",
    },
    "mov": {
        "ext": "mov",
        "params": ["-movflags", "+faststart"],
        "vcodec_default": "copy",
        "acodec_default": "copy",
    },
}

SUPPORTED_AUDIO_FORMATS = set(AUDIO_CODEC_MAP.keys())
SUPPORTED_VIDEO_FORMATS = set(VIDEO_CONTAINER_MAP.keys())


class FFmpegError(Exception):
    """Raised when FFmpeg operations fail."""

    pass


def is_ffmpeg_available() -> bool:
    """Check if FFmpeg is available on the system."""
    ffmpeg = get_ffmpeg_path()
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_ffmpeg_version() -> str | None:
    """Get the FFmpeg version string."""
    ffmpeg = get_ffmpeg_path()
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            first_line = result.stdout.split("\n")[0]
            return first_line
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


async def convert_audio(
    input_url: str,
    output_format: str,
    output_path: str | None = None,
    http_headers: dict[str, str] | None = None,
) -> str:
    """
    Convert audio from a URL to the specified format using FFmpeg.

    Supported formats: mp3, flac, wav, ogg.
    Returns the path to the converted file.
    """
    if output_format not in AUDIO_CODEC_MAP:
        raise FFmpegError(
            f"Unsupported audio format: {output_format}. "
            f"Supported: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}"
        )

    codec_info = AUDIO_CODEC_MAP[output_format]
    ffmpeg = get_ffmpeg_path()

    if output_path is None:
        output_path = f"/tmp/media_fetch_{os.getpid()}.{codec_info['ext']}"

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    # Add HTTP headers if provided
    if http_headers:
        headers_str = "\r\n".join(f"{k}: {v}" for k, v in http_headers.items())
        cmd.extend(["-headers", headers_str])

    cmd.extend(
        [
            "-i",
            input_url,
            "-vn",  # No video
            "-c:a",
            codec_info["codec"],
            *codec_info["params"],
            output_path,
        ]
    )

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        raise FFmpegError(f"FFmpeg audio conversion failed: {error_msg}")

    return output_path


async def remux_video(
    input_url: str,
    output_format: str,
    output_path: str | None = None,
    http_headers: dict[str, str] | None = None,
) -> str:
    """
    Remux video into the specified container format using FFmpeg.

    Supported formats: mp4, mkv, webm, mov.
    Codecs are copied (no re-encoding) unless the container requires it.
    Returns the path to the output file.
    """
    if output_format not in VIDEO_CONTAINER_MAP:
        raise FFmpegError(
            f"Unsupported video format: {output_format}. "
            f"Supported: {', '.join(sorted(SUPPORTED_VIDEO_FORMATS))}"
        )

    container = VIDEO_CONTAINER_MAP[output_format]
    ffmpeg = get_ffmpeg_path()

    if output_path is None:
        output_path = f"/tmp/media_fetch_{os.getpid()}.{container['ext']}"

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    if http_headers:
        headers_str = "\r\n".join(f"{k}: {v}" for k, v in http_headers.items())
        cmd.extend(["-headers", headers_str])

    cmd.extend(
        [
            "-i",
            input_url,
            "-c:v",
            container["vcodec_default"],
            "-c:a",
            container["acodec_default"],
            *container["params"],
            output_path,
        ]
    )

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        raise FFmpegError(f"FFmpeg remux failed: {error_msg}")

    return output_path


async def merge_video_audio(
    video_url: str,
    audio_url: str,
    output_path: str,
    output_format: str = "mp4",
    http_headers: dict[str, str] | None = None,
) -> str:
    """
    Merge separate video and audio streams into a single file.
    Used for platforms like YouTube and Reddit that serve them separately.

    The output container is chosen from: mp4, mkv, webm, mov.
    """
    container = VIDEO_CONTAINER_MAP.get(output_format, VIDEO_CONTAINER_MAP["mp4"])
    ffmpeg = get_ffmpeg_path()

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    if http_headers:
        headers_str = "\r\n".join(f"{k}: {v}" for k, v in http_headers.items())
        cmd.extend(["-headers", headers_str])

    cmd.extend(
        [
            "-i",
            video_url,
            "-i",
            audio_url,
            "-c:v",
            container["vcodec_default"],
            "-c:a",
            container["acodec_default"],
            *container["params"],
            output_path,
        ]
    )

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        raise FFmpegError(f"FFmpeg merge failed: {error_msg}")

    return output_path


async def probe_format(url: str, http_headers: dict[str, str] | None = None) -> dict:
    """
    Use ffprobe to get format information about a media URL.
    """
    ffmpeg = get_ffmpeg_path()
    ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")

    cmd = [ffprobe]

    if http_headers:
        headers_str = "\r\n".join(f"{k}: {v}" for k, v in http_headers.items())
        cmd.extend(["-headers", headers_str])

    cmd.extend(
        [
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            url,
        ]
    )

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _stderr = await process.communicate()

    if process.returncode != 0:
        return {}

    import json

    try:
        return json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
