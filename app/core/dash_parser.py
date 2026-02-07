"""
DASH (MPD) manifest parser.
Ported from yt-dlp's dash downloader patterns.

Parses DASH MPD (Media Presentation Description) manifests to extract
adaptation sets and representations with format information.
"""

import logging
import re
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from ..models.enums import FormatType
from ..models.response import FormatInfo
from ..utils.helpers import (
    float_or_none,
    int_or_none,
)

logger = logging.getLogger(__name__)

# MPD XML namespace
_MPD_NS = {
    "mpd": "urn:mpeg:dash:schema:mpd:2011",
    "": "urn:mpeg:dash:schema:mpd:2011",
}


class DASHRepresentation:
    """Represents a DASH representation (stream variant)."""

    def __init__(
        self,
        url: str | None = None,
        base_url: str | None = None,
        bandwidth: int | None = None,
        width: int | None = None,
        height: int | None = None,
        codecs: str | None = None,
        mime_type: str | None = None,
        frame_rate: float | None = None,
        content_type: str | None = None,
        segment_template: dict | None = None,
        segment_list: list[str] | None = None,
        initialization: str | None = None,
        rep_id: str | None = None,
    ):
        self.url = url
        self.base_url = base_url
        self.bandwidth = bandwidth
        self.width = width
        self.height = height
        self.codecs = codecs
        self.mime_type = mime_type
        self.frame_rate = frame_rate
        self.content_type = content_type
        self.segment_template = segment_template
        self.segment_list = segment_list
        self.initialization = initialization
        self.rep_id = rep_id


def parse_mpd(content: str, base_url: str = "") -> list[DASHRepresentation]:
    """
    Parse a DASH MPD manifest.

    Args:
        content: The MPD XML content
        base_url: Base URL for resolving relative URLs

    Returns:
        List of DASHRepresentation objects
    """
    representations = []

    try:
        # Remove default namespace for easier parsing
        content = re.sub(r'\sxmlns="[^"]*"', "", content, count=1)
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.warning(f"Failed to parse MPD manifest: {e}")
        return representations

    # Get MPD-level base URL
    mpd_base_url = base_url
    base_url_elem = root.find("BaseURL")
    if base_url_elem is not None and base_url_elem.text:
        mpd_base_url = urljoin(base_url, base_url_elem.text)

    # Parse each Period
    for period in root.findall("Period"):
        period_base_url = mpd_base_url
        period_base = period.find("BaseURL")
        if period_base is not None and period_base.text:
            period_base_url = urljoin(mpd_base_url, period_base.text)

        # Parse each AdaptationSet
        for adaptation_set in period.findall("AdaptationSet"):
            as_mime = adaptation_set.get("mimeType", "")
            as_codecs = adaptation_set.get("codecs", "")
            as_content_type = adaptation_set.get("contentType", "")
            as_width = int_or_none(adaptation_set.get("width"))
            as_height = int_or_none(adaptation_set.get("height"))
            as_frame_rate = _parse_frame_rate(adaptation_set.get("frameRate"))

            as_base_url = period_base_url
            as_base = adaptation_set.find("BaseURL")
            if as_base is not None and as_base.text:
                as_base_url = urljoin(period_base_url, as_base.text)

            # Segment template at AdaptationSet level
            as_seg_template = _parse_segment_template(
                adaptation_set.find("SegmentTemplate"), as_base_url
            )

            # Parse each Representation
            for rep in adaptation_set.findall("Representation"):
                rep_id = rep.get("id", "")
                rep_bandwidth = int_or_none(rep.get("bandwidth"))
                rep_width = int_or_none(rep.get("width")) or as_width
                rep_height = int_or_none(rep.get("height")) or as_height
                rep_codecs = rep.get("codecs") or as_codecs
                rep_mime = rep.get("mimeType") or as_mime
                rep_frame_rate = _parse_frame_rate(rep.get("frameRate")) or as_frame_rate

                rep_base_url = as_base_url
                rep_base = rep.find("BaseURL")
                if rep_base is not None and rep_base.text:
                    rep_base_url = urljoin(as_base_url, rep_base.text)

                # Segment template at Representation level
                rep_seg_template = (
                    _parse_segment_template(rep.find("SegmentTemplate"), rep_base_url)
                    or as_seg_template
                )

                # Determine content type
                content_type = as_content_type
                if not content_type:
                    if "video" in rep_mime:
                        content_type = "video"
                    elif "audio" in rep_mime:
                        content_type = "audio"

                representation = DASHRepresentation(
                    url=rep_base_url if not rep_seg_template else None,
                    base_url=rep_base_url,
                    bandwidth=rep_bandwidth,
                    width=rep_width,
                    height=rep_height,
                    codecs=rep_codecs,
                    mime_type=rep_mime,
                    frame_rate=rep_frame_rate,
                    content_type=content_type,
                    segment_template=rep_seg_template,
                    rep_id=rep_id,
                )

                representations.append(representation)

    return representations


def _parse_segment_template(element: ET.Element | None, base_url: str) -> dict | None:
    """Parse a SegmentTemplate element."""
    if element is None:
        return None

    template = {
        "initialization": element.get("initialization"),
        "media": element.get("media"),
        "start_number": int_or_none(element.get("startNumber")) or 1,
        "timescale": int_or_none(element.get("timescale")) or 1,
        "duration": int_or_none(element.get("duration")),
    }

    # Resolve URLs
    if template["initialization"] and not template["initialization"].startswith("http"):
        template["initialization"] = urljoin(base_url, template["initialization"])
    if template["media"] and not template["media"].startswith("http"):
        template["media"] = urljoin(base_url, template["media"])

    return template


def _parse_frame_rate(value: str | None) -> float | None:
    """Parse frame rate, handling fractional notation like '30000/1001'."""
    if not value:
        return None
    if "/" in value:
        parts = value.split("/")
        try:
            return float(parts[0]) / float(parts[1])
        except (ValueError, ZeroDivisionError):
            return None
    return float_or_none(value)


def dash_representations_to_formats(
    representations: list[DASHRepresentation],
    format_id_prefix: str = "dash",
) -> list[FormatInfo]:
    """Convert DASH representations to FormatInfo objects."""
    formats = []

    for rep in representations:
        # Parse codecs
        vcodec = None
        acodec = None
        ext = "mp4"

        if rep.codecs:
            if rep.content_type == "video" or rep.codecs.startswith(("avc", "hvc", "vp", "av0")):
                vcodec = rep.codecs
                acodec = "none"
            elif rep.content_type == "audio" or rep.codecs.startswith(("mp4a", "opus", "flac")):
                vcodec = "none"
                acodec = rep.codecs

        if rep.mime_type:
            if "webm" in rep.mime_type:
                ext = "webm"
            elif "mp4" in rep.mime_type:
                ext = "mp4"

        # Determine format type
        if rep.content_type == "video":
            format_type = FormatType.VIDEO_ONLY
        elif rep.content_type == "audio":
            format_type = FormatType.AUDIO_ONLY
            ext = "m4a"
        else:
            format_type = FormatType.COMBINED

        # Use base URL or segment template
        url = rep.url or rep.base_url or ""

        if not url:
            continue

        height = rep.height
        quality_label = f"{height}p" if height else rep.rep_id

        formats.append(
            FormatInfo(
                url=url,
                format_id=f"{format_id_prefix}_{rep.rep_id}" if rep.rep_id else None,
                ext=ext,
                width=rep.width,
                height=rep.height,
                fps=rep.frame_rate,
                vcodec=vcodec,
                acodec=acodec,
                tbr=float_or_none(rep.bandwidth, scale=1000),
                format_type=format_type,
                quality_label=quality_label,
                protocol="dash",
            )
        )

    return formats
