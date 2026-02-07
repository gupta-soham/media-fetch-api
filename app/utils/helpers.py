"""
General utility functions used across extractors.
Ported from yt-dlp's utils.py and cobalt's misc/ helpers.
"""

import html
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import unquote


def clean_html(raw_html: str) -> str:
    """Remove HTML tags and decode entities."""
    clean = re.sub(r"<[^>]+>", "", raw_html)
    return html.unescape(clean).strip()


def extract_json_from_html(html_text: str, variable_name: str) -> dict | None:
    """
    Extract a JSON object assigned to a JavaScript variable in HTML.
    e.g., window.__NEXT_DATA__ = {...};
    """
    patterns = [
        rf"{re.escape(variable_name)}\s*=\s*(\{{.+?\}});\s*</",
        rf"{re.escape(variable_name)}\s*=\s*(\{{.+?\}});",
        rf"{re.escape(variable_name)}\s*=\s*(\[.+?\]);",
    ]

    for pattern in patterns:
        match = re.search(pattern, html_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    return None


def extract_json_from_script(html_text: str, script_id: str) -> dict | None:
    """Extract JSON content from a <script> tag with a specific id."""
    pattern = rf'<script[^>]*id=["\']?{re.escape(script_id)}["\']?[^>]*>(.*?)</script>'
    match = re.search(pattern, html_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def traverse_obj(obj: Any, *paths: Any, default: Any = None) -> Any:
    """
    Traverse nested dicts/lists safely.
    Ported from yt-dlp's traverse_obj utility.

    Usage:
        traverse_obj(data, 'key1', 'key2', 'key3')
        traverse_obj(data, ('key1', 'key2'), ('alt_key1', 'alt_key2'))
    """
    for path in paths:
        if isinstance(path, (list, tuple)):
            result = obj
            for key in path:
                if result is None:
                    break
                if isinstance(result, dict):
                    result = result.get(key)
                elif isinstance(result, (list, tuple)):
                    try:
                        result = result[key]
                    except (IndexError, TypeError):
                        result = None
                else:
                    result = None
            if result is not None:
                return result
        else:
            if isinstance(obj, dict) and path in obj:
                return obj[path]
    return default


def int_or_none(v: Any, scale: int = 1) -> int | None:
    """Convert value to int or return None."""
    if v is None:
        return None
    try:
        return int(v) // scale
    except (ValueError, TypeError):
        return None


def float_or_none(v: Any, scale: float = 1.0) -> float | None:
    """Convert value to float or return None."""
    if v is None:
        return None
    try:
        return float(v) / scale
    except (ValueError, TypeError):
        return None


def str_or_none(v: Any) -> str | None:
    """Convert value to string or return None."""
    if v is None:
        return None
    result = str(v).strip()
    return result if result else None


def url_or_none(v: Any) -> str | None:
    """Validate and return URL or None."""
    if not v or not isinstance(v, str):
        return None
    v = v.strip()
    if v.startswith(("http://", "https://")):
        return v
    if v.startswith("//"):
        return f"https:{v}"
    return None


def unified_timestamp(date_str: str) -> int | None:
    """Parse various date formats into Unix timestamp."""
    if not date_str:
        return None

    date_str = date_str.strip()

    # ISO 8601
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y%m%d",
    ):
        try:
            dt = datetime.strptime(date_str, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue

    # Unix timestamp
    try:
        ts = float(date_str)
        if ts > 1e12:
            ts /= 1000  # milliseconds
        return int(ts)
    except (ValueError, TypeError):
        pass

    return None


def format_date(timestamp: int | float | str | None) -> str | None:
    """Convert timestamp to YYYY-MM-DD format."""
    if timestamp is None:
        return None

    if isinstance(timestamp, str):
        ts = unified_timestamp(timestamp)
        if ts is None:
            return None
        timestamp = ts

    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return None


def parse_resolution(quality_label: str) -> tuple[int | None, int | None]:
    """Parse resolution from quality label like '1080p', '720p60', '4K'."""
    if not quality_label:
        return None, None

    quality_label = quality_label.lower().strip()

    # Common labels
    label_map = {
        "4k": (3840, 2160),
        "2k": (2560, 1440),
        "8k": (7680, 4320),
    }

    if quality_label in label_map:
        return label_map[quality_label]

    match = re.match(r"(\d+)p", quality_label)
    if match:
        height = int(match.group(1))
        # Standard aspect ratio 16:9
        width_map = {
            4320: 7680,
            2160: 3840,
            1440: 2560,
            1080: 1920,
            720: 1280,
            480: 854,
            360: 640,
            240: 426,
            144: 256,
        }
        width = width_map.get(height, int(height * 16 / 9))
        return width, height

    return None, None


def sort_formats(formats: list[dict]) -> list[dict]:
    """
    Sort formats by quality (best last).
    Ported from yt-dlp's format sorting logic.
    """

    def sort_key(f: dict):
        # Prefer combined > video-only > audio-only
        type_order = {"combined": 2, "video_only": 1, "audio_only": 0}
        fmt_type = f.get("format_type", "combined")

        height = f.get("height") or 0
        width = f.get("width") or 0
        tbr = f.get("tbr") or f.get("vbr", 0) or 0
        abr = f.get("abr") or 0
        fps = f.get("fps") or 0
        filesize = f.get("filesize") or f.get("filesize_approx") or 0

        # Prefer h264 > vp9 > av01 for compatibility
        vcodec = f.get("vcodec") or ""
        codec_order = 0
        if "avc1" in vcodec or "h264" in vcodec:
            codec_order = 3
        elif "vp9" in vcodec or "vp09" in vcodec:
            codec_order = 2
        elif "av01" in vcodec or "av1" in vcodec:
            codec_order = 1

        return (
            type_order.get(fmt_type, 0),
            height,
            width,
            tbr + abr,
            codec_order,
            fps,
            filesize,
        )

    return sorted(formats, key=sort_key)


def parse_content_disposition(header: str) -> str | None:
    """Extract filename from Content-Disposition header."""
    if not header:
        return None

    # filename*=UTF-8''encoded_name
    match = re.search(r"filename\*=(?:UTF-8''|utf-8'')(.+?)(?:;|$)", header, re.I)
    if match:
        return unquote(match.group(1).strip())

    # filename="name"
    match = re.search(r'filename="(.+?)"', header)
    if match:
        return match.group(1).strip()

    # filename=name
    match = re.search(r"filename=([^\s;]+)", header)
    if match:
        return match.group(1).strip()

    return None


def sanitize_filename(filename: str) -> str:
    """Remove or replace characters that are invalid in filenames."""
    # Replace problematic characters
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    # Remove control characters
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)
    # Trim whitespace and dots
    filename = filename.strip(". ")
    return filename or "download"


def decode_base64_url(s: str) -> str:
    """Decode URL-safe base64 string."""
    import base64

    # Add padding if needed
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    s = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s).decode("utf-8", errors="replace")


def parse_m3u8_attributes(line: str) -> dict[str, str]:
    """Parse M3U8 attribute list (key=value pairs)."""
    attrs = {}
    # Match KEY=VALUE or KEY="VALUE"
    for match in re.finditer(r'(?:^|,)([A-Z0-9-]+)=(?:"([^"]*?)"|([^,]*))', line):
        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        attrs[key] = value
    return attrs
