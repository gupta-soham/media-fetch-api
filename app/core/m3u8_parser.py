"""
HLS (M3U8) playlist parser.
Ported from yt-dlp's hls downloader and cobalt's internal-hls.js.

Parses both master playlists (variant streams) and media playlists
(segment lists) to extract format information and stream URLs.
"""

import logging
from urllib.parse import urljoin

from ..models.enums import FormatType
from ..models.response import FormatInfo
from ..utils.helpers import (
    float_or_none,
    int_or_none,
    parse_m3u8_attributes,
)

logger = logging.getLogger(__name__)


class HLSVariant:
    """Represents a variant stream in an HLS master playlist."""

    def __init__(
        self,
        url: str,
        bandwidth: int | None = None,
        width: int | None = None,
        height: int | None = None,
        codecs: str | None = None,
        frame_rate: float | None = None,
        audio_group: str | None = None,
        subtitle_group: str | None = None,
    ):
        self.url = url
        self.bandwidth = bandwidth
        self.width = width
        self.height = height
        self.codecs = codecs
        self.frame_rate = frame_rate
        self.audio_group = audio_group
        self.subtitle_group = subtitle_group


class HLSSegment:
    """Represents a segment in an HLS media playlist."""

    def __init__(
        self,
        url: str,
        duration: float,
        title: str | None = None,
        byte_range: tuple[int, int] | None = None,
    ):
        self.url = url
        self.duration = duration
        self.title = title
        self.byte_range = byte_range


class HLSPlaylist:
    """Parsed HLS playlist."""

    def __init__(self):
        self.is_master: bool = False
        self.variants: list[HLSVariant] = []
        self.segments: list[HLSSegment] = []
        self.media_groups: dict[str, list[dict]] = {}
        self.target_duration: float | None = None
        self.total_duration: float = 0.0
        self.is_live: bool = True  # Default to live; set to False if #EXT-X-ENDLIST found


def parse_m3u8(content: str, base_url: str = "") -> HLSPlaylist:
    """
    Parse an M3U8 playlist (master or media).

    Args:
        content: The M3U8 playlist content
        base_url: Base URL for resolving relative URLs

    Returns:
        HLSPlaylist with parsed data
    """
    playlist = HLSPlaylist()
    lines = content.strip().split("\n")

    if not lines or not lines[0].strip().startswith("#EXTM3U"):
        logger.warning("Content does not start with #EXTM3U")

    # Check if this is a master playlist
    has_stream_inf = any(line.strip().startswith("#EXT-X-STREAM-INF:") for line in lines)
    playlist.is_master = has_stream_inf

    if has_stream_inf:
        _parse_master_playlist(lines, base_url, playlist)
    else:
        _parse_media_playlist(lines, base_url, playlist)

    return playlist


def _parse_master_playlist(lines: list[str], base_url: str, playlist: HLSPlaylist):
    """Parse a master (variant) playlist."""
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = parse_m3u8_attributes(line.split(":", 1)[1])

            # Next non-comment line is the URL
            url = ""
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith("#"):
                    url = next_line
                    break
                j += 1

            if url and not url.startswith("http"):
                url = urljoin(base_url, url)

            # Parse resolution
            width = None
            height = None
            resolution = attrs.get("RESOLUTION", "")
            if "x" in resolution:
                parts = resolution.split("x")
                width = int_or_none(parts[0])
                height = int_or_none(parts[1])

            variant = HLSVariant(
                url=url,
                bandwidth=int_or_none(attrs.get("BANDWIDTH")),
                width=width,
                height=height,
                codecs=attrs.get("CODECS"),
                frame_rate=float_or_none(attrs.get("FRAME-RATE")),
                audio_group=attrs.get("AUDIO"),
                subtitle_group=attrs.get("SUBTITLES"),
            )
            playlist.variants.append(variant)
            i = j

        elif line.startswith("#EXT-X-MEDIA:"):
            attrs = parse_m3u8_attributes(line.split(":", 1)[1])
            media_type = attrs.get("TYPE", "")
            group_id = attrs.get("GROUP-ID", "")

            media_url = attrs.get("URI", "")
            if media_url and not media_url.startswith("http"):
                media_url = urljoin(base_url, media_url)

            media_info = {
                "type": media_type,
                "group_id": group_id,
                "name": attrs.get("NAME", ""),
                "language": attrs.get("LANGUAGE", ""),
                "url": media_url,
                "default": attrs.get("DEFAULT", "NO") == "YES",
                "autoselect": attrs.get("AUTOSELECT", "NO") == "YES",
            }

            if group_id not in playlist.media_groups:
                playlist.media_groups[group_id] = []
            playlist.media_groups[group_id].append(media_info)

        i += 1


def _parse_media_playlist(lines: list[str], base_url: str, playlist: HLSPlaylist):
    """Parse a media (segment) playlist."""
    current_duration = 0.0
    current_title = None

    for line in lines:
        line = line.strip()

        if line.startswith("#EXT-X-TARGETDURATION:"):
            playlist.target_duration = float_or_none(line.split(":")[1])

        elif line.startswith("#EXTINF:"):
            parts = line.split(":", 1)[1]
            comma_idx = parts.find(",")
            if comma_idx >= 0:
                current_duration = float(parts[:comma_idx])
                current_title = parts[comma_idx + 1 :].strip() or None
            else:
                current_duration = float(parts.rstrip(","))
                current_title = None

        elif line.startswith("#EXT-X-ENDLIST"):
            playlist.is_live = False

        elif line and not line.startswith("#"):
            url = line
            if not url.startswith("http"):
                url = urljoin(base_url, url)

            segment = HLSSegment(
                url=url,
                duration=current_duration,
                title=current_title,
            )
            playlist.segments.append(segment)
            playlist.total_duration += current_duration
            current_duration = 0.0
            current_title = None


def hls_variants_to_formats(
    playlist: HLSPlaylist,
    format_id_prefix: str = "hls",
) -> list[FormatInfo]:
    """Convert HLS variants to FormatInfo objects."""
    formats = []

    for idx, variant in enumerate(playlist.variants):
        # Parse codecs
        vcodec = None
        acodec = None
        if variant.codecs:
            codec_parts = [c.strip() for c in variant.codecs.split(",")]
            for c in codec_parts:
                if c.startswith(("avc", "hvc", "vp", "av0")):
                    vcodec = c
                elif c.startswith(("mp4a", "opus", "flac", "vorb")):
                    acodec = c

        # Determine format type
        if vcodec and acodec:
            format_type = FormatType.COMBINED
        elif vcodec:
            format_type = FormatType.VIDEO_ONLY
        elif acodec:
            format_type = FormatType.AUDIO_ONLY
        else:
            format_type = FormatType.COMBINED

        height = variant.height
        quality_label = f"{height}p" if height else f"stream_{idx}"

        formats.append(
            FormatInfo(
                url=variant.url,
                format_id=f"{format_id_prefix}_{quality_label}",
                ext="mp4",
                width=variant.width,
                height=variant.height,
                fps=variant.frame_rate,
                vcodec=vcodec,
                acodec=acodec,
                tbr=float_or_none(variant.bandwidth, scale=1000),
                format_type=format_type,
                quality_label=quality_label,
                protocol="hls",
            )
        )

    return formats
