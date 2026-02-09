"""
YouTube extractor - extracts video/audio URLs from YouTube.
Ported from yt-dlp's youtube extractor and cobalt's youtube.js.

Supports:
- Regular videos, shorts, live streams, music
- Multiple quality formats (144p to 4320p)
- Audio-only extraction
- Subtitle extraction
- Cookie-based authentication for age-restricted/private content
- Cipher signature decryption
- nsig (throttle) bypass
"""

import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from ..core.js_interpreter import JSInterpreter, JSInterpreterError
from ..models.enums import FormatType, Platform
from ..models.request import ExtractRequest
from ..models.response import (
    ExtractResponse,
    FormatInfo,
    MediaMetadata,
    SubtitleTrack,
)
from ..utils.helpers import (
    float_or_none,
    format_date,
    int_or_none,
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

# InnerTube API endpoint
_INNERTUBE_API_URL = "https://www.youtube.com/youtubei/v1"

# InnerTube client configurations
# Ported from yt-dlp's _base.py INNERTUBE_CLIENTS
_INNERTUBE_CLIENTS = {
    "web": {
        "client_name": "WEB",
        "client_version": "2.20260114.08.00",
        "api_key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "context_client_name": 1,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "requires_js_player": True,
        "supports_cookies": True,
    },
    "web_safari": {
        "client_name": "WEB",
        "client_version": "2.20260114.08.00",
        "api_key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "context_client_name": 1,
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/15.5 Safari/605.1.15,gzip(gfe)"
        ),
        "requires_js_player": True,
        "supports_cookies": True,
    },
    "ios": {
        "client_name": "IOS",
        "client_version": "21.02.3",
        "device_model": "iPhone16,2",
        "os_version": "18.3.2.22D62",
        "api_key": "AIzaSyB-63vPrdThhKuerbB2N_l7Kwwcxj6yUAc",
        "context_client_name": 5,
        "user_agent": "com.google.ios.youtube/21.02.3 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
        "requires_js_player": False,
        "supports_cookies": False,
    },
    "android": {
        "client_name": "ANDROID",
        "client_version": "21.02.35",
        "android_sdk_version": 30,
        "os_version": "11",
        "api_key": "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w",
        "context_client_name": 3,
        "user_agent": "com.google.android.youtube/21.02.35 (Linux; U; Android 11) gzip",
        "requires_js_player": False,
        "supports_cookies": False,
    },
    "android_vr": {
        "client_name": "ANDROID_VR",
        "client_version": "1.71.26",
        "android_sdk_version": 30,
        "os_version": "11",
        "api_key": "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w",
        "context_client_name": 28,
        "user_agent": "com.google.android.apps.youtube.vr.oculus/1.71.26 (Linux; U; Android 11; eureka-user Build/SQ3A.220605.009.A1) gzip",
        "requires_js_player": False,
        "supports_cookies": False,
    },
    "tv_embedded": {
        "client_name": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
        "client_version": "2.0",
        "api_key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "context_client_name": 85,
        "user_agent": (
            "Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "85.0.4183.93/6.5 TV Safari/537.36"
        ),
        "requires_js_player": True,
        "supports_cookies": False,
    },
}

# Clients that return deciphered URLs (no signature decryption needed)
_CLIENTS_NO_CIPHER = {"IOS", "ANDROID", "ANDROID_VR", "YTSTUDIO_ANDROID", "YTMUSIC_ANDROID"}

# Default client order when no cookies
_DEFAULT_CLIENTS = ["android_vr", "web", "ios"]
# When cookies are present, try web first so auth is used and bot check is avoided
_CLIENTS_WITH_COOKIES = ["web", "web_safari", "android_vr", "ios"]

# Codec configurations
_CODEC_MAP = {
    "h264": {"video_codec": "avc1", "audio_codec": "mp4a", "container": "mp4"},
    "av1": {"video_codec": "av01", "audio_codec": "opus", "container": "webm"},
    "vp9": {"video_codec": "vp9", "audio_codec": "opus", "container": "webm"},
}

# Quality mapping
_QUALITY_MAP = {
    "tiny": 144,
    "small": 240,
    "medium": 360,
    "large": 480,
    "hd720": 720,
    "hd1080": 1080,
    "hd1440": 1440,
    "hd2160": 2160,
    "hd2880": 2880,
    "highres": 4320,
}

# itag to format mapping (common itags)
_ITAG_MAP = {
    # Video + Audio (progressive)
    18: {"ext": "mp4", "width": 640, "height": 360, "vcodec": "avc1", "acodec": "mp4a"},
    22: {"ext": "mp4", "width": 1280, "height": 720, "vcodec": "avc1", "acodec": "mp4a"},
    # Video only (adaptive)
    133: {"ext": "mp4", "width": 426, "height": 240, "vcodec": "avc1"},
    134: {"ext": "mp4", "width": 640, "height": 360, "vcodec": "avc1"},
    135: {"ext": "mp4", "width": 854, "height": 480, "vcodec": "avc1"},
    136: {"ext": "mp4", "width": 1280, "height": 720, "vcodec": "avc1"},
    137: {"ext": "mp4", "width": 1920, "height": 1080, "vcodec": "avc1"},
    298: {"ext": "mp4", "width": 1280, "height": 720, "vcodec": "avc1", "fps": 60},
    299: {"ext": "mp4", "width": 1920, "height": 1080, "vcodec": "avc1", "fps": 60},
    264: {"ext": "mp4", "width": 2560, "height": 1440, "vcodec": "avc1"},
    266: {"ext": "mp4", "width": 3840, "height": 2160, "vcodec": "avc1"},
    # VP9 video only
    242: {"ext": "webm", "width": 426, "height": 240, "vcodec": "vp9"},
    243: {"ext": "webm", "width": 640, "height": 360, "vcodec": "vp9"},
    244: {"ext": "webm", "width": 854, "height": 480, "vcodec": "vp9"},
    247: {"ext": "webm", "width": 1280, "height": 720, "vcodec": "vp9"},
    248: {"ext": "webm", "width": 1920, "height": 1080, "vcodec": "vp9"},
    271: {"ext": "webm", "width": 2560, "height": 1440, "vcodec": "vp9"},
    313: {"ext": "webm", "width": 3840, "height": 2160, "vcodec": "vp9"},
    302: {"ext": "webm", "width": 1280, "height": 720, "vcodec": "vp9", "fps": 60},
    303: {"ext": "webm", "width": 1920, "height": 1080, "vcodec": "vp9", "fps": 60},
    308: {"ext": "webm", "width": 2560, "height": 1440, "vcodec": "vp9", "fps": 60},
    315: {"ext": "webm", "width": 3840, "height": 2160, "vcodec": "vp9", "fps": 60},
    # AV1 video only
    394: {"ext": "mp4", "width": 426, "height": 240, "vcodec": "av01"},
    395: {"ext": "mp4", "width": 640, "height": 360, "vcodec": "av01"},
    396: {"ext": "mp4", "width": 854, "height": 480, "vcodec": "av01"},
    397: {"ext": "mp4", "width": 1280, "height": 720, "vcodec": "av01"},
    398: {"ext": "mp4", "width": 1920, "height": 1080, "vcodec": "av01"},
    399: {"ext": "mp4", "width": 2560, "height": 1440, "vcodec": "av01"},
    400: {"ext": "mp4", "width": 3840, "height": 2160, "vcodec": "av01"},
    401: {"ext": "mp4", "width": 7680, "height": 4320, "vcodec": "av01"},
    # Audio only
    139: {"ext": "m4a", "acodec": "mp4a", "abr": 48},
    140: {"ext": "m4a", "acodec": "mp4a", "abr": 128},
    141: {"ext": "m4a", "acodec": "mp4a", "abr": 256},
    171: {"ext": "webm", "acodec": "vorbis", "abr": 128},
    172: {"ext": "webm", "acodec": "vorbis", "abr": 256},
    249: {"ext": "webm", "acodec": "opus", "abr": 50},
    250: {"ext": "webm", "acodec": "opus", "abr": 70},
    251: {"ext": "webm", "acodec": "opus", "abr": 160},
    256: {"ext": "m4a", "acodec": "mp4a", "abr": 192},
    258: {"ext": "m4a", "acodec": "mp4a", "abr": 384},
}


class YouTubeExtractor(BaseExtractor):
    """YouTube media extractor."""

    platform = Platform.YOUTUBE

    # Cache for player JS and cipher functions
    _player_cache: dict[str, Any] = {}

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract video/audio information from YouTube."""
        video_id = media_id

        # Step 1: Fetch the watch page to get player URL and initial data
        watch_page = None
        player_url = None
        ytcfg = None

        try:
            watch_url = (
                f"https://www.youtube.com/watch?v={video_id}&bpctr=9999999999&has_verified=1"
            )
            headers = {
                "User-Agent": _INNERTUBE_CLIENTS["web"]["user_agent"],
                "Accept-Language": "en-US,en;q=0.9",
            }
            cookie_header = self._get_cookie_header()
            if cookie_header:
                headers["Cookie"] = cookie_header

            watch_page = await self._download_webpage(watch_url, headers=headers)

            # Extract player URL
            player_url = self._extract_player_url(watch_page)

            # Extract initial player response from page
            self._extract_initial_data(watch_page)

            # Extract ytcfg
            ytcfg = self._search_json("ytcfg.set", watch_page, "ytcfg", default={})

        except Exception as e:
            logger.warning(f"Failed to fetch watch page: {e}")

        # Step 2: Fetch player response via InnerTube API
        player_response = None
        used_client = None
        clients_to_try = _CLIENTS_WITH_COOKIES if self._has_cookies() else _DEFAULT_CLIENTS

        for client_name in clients_to_try:
            try:
                pr = await self._fetch_innertube_player(video_id, client_name, ytcfg)
                if pr and pr.get("playabilityStatus", {}).get("status") == "OK":
                    player_response = pr
                    used_client = client_name
                    break
                elif pr:
                    status = pr.get("playabilityStatus", {})
                    reason = status.get("reason", status.get("status", "UNKNOWN"))
                    logger.warning(f"Client {client_name} returned: {reason}")
            except Exception as e:
                logger.warning(f"InnerTube API failed for client {client_name}: {e}")

        if not player_response:
            raise ExtractionError(
                "Could not retrieve video info from YouTube. "
                "The video may be private, age-restricted, or unavailable.",
                error_code="youtube.playability_error",
            )

        # Step 3: Extract video details
        video_details = player_response.get("videoDetails", {})
        microformat = (
            traverse_obj(player_response, ("microformat", "playerMicroformatRenderer")) or {}
        )

        title = video_details.get("title") or microformat.get("title", {}).get("simpleText")
        duration = float_or_none(video_details.get("lengthSeconds"))

        # Step 4: Extract formats from streamingData
        streaming_data = player_response.get("streamingData", {})
        formats = []

        # Process progressive (combined) formats
        for fmt in streaming_data.get("formats", []):
            format_info = await self._process_format(fmt, player_url, used_client, video_id)
            if format_info:
                formats.append(format_info)

        # Process adaptive (video-only and audio-only) formats
        for fmt in streaming_data.get("adaptiveFormats", []):
            format_info = await self._process_format(fmt, player_url, used_client, video_id)
            if format_info:
                formats.append(format_info)

        # Step 5: Extract subtitles
        subtitles = None
        if request.include_subtitles:
            subtitles = self._extract_subtitles(player_response, request.subtitle_lang)

        # Step 6: Build metadata
        metadata = None
        if request.include_metadata:
            metadata = MediaMetadata(
                uploader=video_details.get("author"),
                uploader_id=video_details.get("channelId"),
                uploader_url=f"https://www.youtube.com/channel/{video_details.get('channelId', '')}",
                upload_date=format_date(
                    microformat.get("uploadDate") or microformat.get("publishDate")
                ),
                description=video_details.get("shortDescription"),
                view_count=int_or_none(video_details.get("viewCount")),
                tags=video_details.get("keywords"),
                is_live=video_details.get("isLiveContent", False),
                age_restricted=microformat.get("isFamilySafe") is False,
            )

        # Get thumbnail
        thumbnails = video_details.get("thumbnail", {}).get("thumbnails", [])
        thumbnail = thumbnails[-1]["url"] if thumbnails else None
        if not thumbnail:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

        return ExtractResponse(
            platform=Platform.YOUTUBE,
            id=video_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            subtitles=subtitles,
            metadata=metadata,
        )

    def _extract_player_url(self, watch_page: str) -> str | None:
        """Extract the player JS URL from the watch page."""
        patterns = [
            r'"(?:PLAYER_JS_URL|jsUrl)"\s*:\s*"([^"]+)"',
            r'"js"\s*:\s*"([^"]+)"',
            r'/s/player/([a-zA-Z0-9]+)/player_ias\.vflset/[^"]+/base\.js',
        ]
        for pattern in patterns:
            match = re.search(pattern, watch_page)
            if match:
                js_url = match.group(1) if "/" in match.group(1) else match.group(0)
                js_url = js_url.replace("\\/", "/")
                if not js_url.startswith("http"):
                    js_url = f"https://www.youtube.com{js_url}"
                return js_url
        return None

    def _extract_initial_data(self, watch_page: str) -> dict | None:
        """Extract ytInitialPlayerResponse from the watch page."""
        for var_name in ["ytInitialPlayerResponse", "ytInitialData"]:
            data = self._search_json(var_name, watch_page, var_name, default=None)
            if data:
                return data
        return None

    async def _fetch_innertube_player(
        self,
        video_id: str,
        client_name: str,
        ytcfg: dict | None = None,
    ) -> dict | None:
        """Fetch player response from YouTube InnerTube API."""
        client_config = _INNERTUBE_CLIENTS.get(client_name)
        if not client_config:
            return None

        # Build the InnerTube context
        context = {
            "client": {
                "clientName": client_config["client_name"],
                "clientVersion": client_config["client_version"],
                "hl": "en",
                "gl": "US",
            }
        }

        # Add platform-specific fields
        if "device_model" in client_config:
            context["client"]["deviceModel"] = client_config["device_model"]
        if "os_version" in client_config:
            context["client"]["osVersion"] = client_config["os_version"]
        if "android_sdk_version" in client_config:
            context["client"]["androidSdkVersion"] = client_config["android_sdk_version"]

        payload = {
            "context": context,
            "videoId": video_id,
            "playbackContext": {
                "contentPlaybackContext": {
                    "html5Preference": "HTML5_PREF_WANTS",
                    "signatureTimestamp": 0,
                }
            },
            "contentCheckOk": True,
            "racyCheckOk": True,
        }

        # If we have signature timestamp from player JS, use it
        if ytcfg and "STS" in ytcfg:
            payload["playbackContext"]["contentPlaybackContext"]["signatureTimestamp"] = ytcfg[
                "STS"
            ]

        api_key = client_config.get("api_key", "")
        api_url = f"{_INNERTUBE_API_URL}/player?key={api_key}&prettyPrint=false"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": client_config["user_agent"],
            "X-YouTube-Client-Name": str(client_config["context_client_name"]),
            "X-YouTube-Client-Version": client_config["client_version"],
            "Origin": "https://www.youtube.com",
            "Referer": f"https://www.youtube.com/watch?v={video_id}",
        }

        # Add cookies if available and client supports them
        if client_config.get("supports_cookies") and self._has_cookies():
            headers["Cookie"] = self._get_cookie_header()

        response = await self.http.post(
            api_url,
            json=payload,
            headers=headers,
        )

        if response.status_code != 200:
            logger.warning(f"InnerTube API returned status {response.status_code}")
            return None

        # Decode body with UTF-8; use errors=replace so invalid bytes don't break parsing
        try:
            text = response.content.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"InnerTube response decode failed: {e}")
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"InnerTube response JSON parse failed: {e}")
            return None

    async def _process_format(
        self,
        fmt: dict,
        player_url: str | None,
        client_name: str | None,
        video_id: str,
    ) -> FormatInfo | None:
        """Process a single format entry from streamingData."""
        # Check for DRM
        if fmt.get("drmFamilies") or (fmt.get("signatureCipher") and not player_url):
            if fmt.get("drmFamilies"):
                return None

        # Get the URL
        fmt_url = fmt.get("url")

        # Handle signatureCipher (encrypted URLs)
        if not fmt_url and fmt.get("signatureCipher"):
            if (
                client_name
                and _INNERTUBE_CLIENTS.get(client_name, {}).get("client_name") in _CLIENTS_NO_CIPHER
            ):
                # This shouldn't happen for these clients, but handle it
                return None

            sc = parse_qs(fmt["signatureCipher"])
            fmt_url = sc.get("url", [None])[0]
            encrypted_sig = sc.get("s", [None])[0]
            sp = sc.get("sp", ["signature"])[0]

            if fmt_url and encrypted_sig and player_url:
                try:
                    decrypted_sig = await self._decrypt_signature(encrypted_sig, player_url)
                    fmt_url = f"{fmt_url}&{sp}={quote(decrypted_sig)}"
                except Exception as e:
                    logger.warning(f"Failed to decrypt signature: {e}")
                    return None

        if not fmt_url:
            return None

        # Handle nsig (throttle bypass)
        try:
            fmt_url = await self._handle_nsig(fmt_url, player_url)
        except Exception as e:
            logger.debug(f"nsig bypass failed: {e}")

        # Parse mime type
        mime_type = fmt.get("mimeType", "")
        vcodec = None
        acodec = None
        ext = None

        mime_match = re.match(r'(video|audio)/(\w+);\s*codecs="([^"]+)"', mime_type)
        if mime_match:
            media_type_str = mime_match.group(1)
            container = mime_match.group(2)
            codecs = mime_match.group(3)

            ext = container
            codec_list = [c.strip() for c in codecs.split(",")]

            if media_type_str == "video":
                vcodec = codec_list[0] if codec_list else None
                acodec = codec_list[1] if len(codec_list) > 1 else "none"
            elif media_type_str == "audio":
                vcodec = "none"
                acodec = codec_list[0] if codec_list else None

        # Get dimensions from format or itag map
        itag = int_or_none(fmt.get("itag"))
        itag_info = _ITAG_MAP.get(itag, {}) if itag else {}

        width = int_or_none(fmt.get("width")) or itag_info.get("width")
        height = int_or_none(fmt.get("height")) or itag_info.get("height")
        fps = float_or_none(fmt.get("fps")) or itag_info.get("fps")

        if not ext:
            ext = itag_info.get("ext", "mp4")
        if not vcodec:
            vcodec = itag_info.get("vcodec")
        if not acodec:
            acodec = itag_info.get("acodec")

        # Determine format type
        has_video = vcodec and vcodec != "none"
        has_audio = acodec and acodec != "none"

        if has_video and has_audio:
            format_type = FormatType.COMBINED
        elif has_video:
            format_type = FormatType.VIDEO_ONLY
        elif has_audio:
            format_type = FormatType.AUDIO_ONLY
        else:
            format_type = FormatType.COMBINED

        # Quality label
        quality_label = fmt.get("qualityLabel")
        quality = fmt.get("quality", "")

        # Bitrates
        tbr = float_or_none(fmt.get("bitrate"), scale=1000)
        abr = float_or_none(fmt.get("averageBitrate"), scale=1000) or itag_info.get("abr")
        if format_type == FormatType.AUDIO_ONLY:
            abr = tbr or abr

        # File size
        filesize = int_or_none(fmt.get("contentLength"))
        float_or_none(fmt.get("approxDurationMs"), scale=1000)

        return FormatInfo(
            url=fmt_url,
            format_id=str(itag) if itag else None,
            ext=ext,
            width=width,
            height=height,
            fps=fps,
            vcodec=vcodec,
            acodec=acodec,
            abr=abr,
            tbr=tbr,
            filesize=filesize,
            format_type=format_type,
            quality_label=quality_label or self._quality_to_label(quality),
            protocol="https",
        )

    @staticmethod
    def _quality_to_label(quality: str) -> str | None:
        """Convert YouTube quality string to human-readable label."""
        height = _QUALITY_MAP.get(quality)
        if height:
            return f"{height}p"
        return quality if quality else None

    async def _decrypt_signature(self, encrypted_sig: str, player_url: str) -> str:
        """
        Decrypt a YouTube signature using the player JS cipher functions.
        Ported from yt-dlp's signature decryption logic.
        """
        # Check cache
        cache_key = f"sig_{player_url}"
        if cache_key in self._player_cache:
            sig_func = self._player_cache[cache_key]
            return sig_func(encrypted_sig)

        # Download player JS
        player_js = await self._get_player_js(player_url)

        # Find the signature function name
        # These patterns are used by yt-dlp to find the initial cipher function
        sig_func_patterns = [
            r"\b[cs]\s*&&\s*[adf]\.set\([^,]+\s*,\s*encodeURIComponent\(([a-zA-Z0-9$]+)\(",
            r"\b[a-zA-Z0-9]+\s*&&\s*[a-zA-Z0-9]+\.set\([^,]+\s*,\s*encodeURIComponent\(([a-zA-Z0-9$]+)\(",
            r"\bm=([a-zA-Z0-9$]{2,})\(decodeURIComponent\(h\.s\)\)",
            r"\bc\s*&&\s*d\.set\([^,]+\s*,\s*(?:encodeURIComponent\s*\()([a-zA-Z0-9$]+)\(",
            r"\bc\s*&&\s*[a-z]\.set\([^,]+\s*,\s*([a-zA-Z0-9$]+)\(",
            r"\bc\s*&&\s*[a-z]\.set\([^,]+\s*,\s*encodeURIComponent\(([a-zA-Z0-9$]+)\(",
            r'(?:\b|[^a-zA-Z0-9$])([a-zA-Z0-9$]{2,})\s*=\s*function\(\s*a\s*\)\s*\{\s*a\s*=\s*a\.split\(\s*""\s*\)',
            r'([a-zA-Z0-9$]+)\s*=\s*function\(\s*a\s*\)\s*\{\s*a\s*=\s*a\.split\(\s*""\s*\)',
        ]

        func_name = None
        for pattern in sig_func_patterns:
            match = re.search(pattern, player_js)
            if match:
                func_name = match.group(1)
                break

        if not func_name:
            raise ExtractionError("Could not find signature function in player JS")

        # Create JS interpreter and extract the function
        interpreter = JSInterpreter(player_js)
        sig_func = interpreter.extract_function(func_name)

        # Cache the function
        self._player_cache[cache_key] = sig_func

        return sig_func(encrypted_sig)

    async def _handle_nsig(self, fmt_url: str, player_url: str | None) -> str:
        """
        Handle nsig (n parameter) transformation for throttle bypass.
        """
        parsed = urlparse(fmt_url)
        qs = parse_qs(parsed.query)
        n_param = qs.get("n", [None])[0]

        if not n_param or not player_url:
            return fmt_url

        # Check cache
        cache_key = f"nsig_{player_url}_{n_param}"
        if cache_key in self._player_cache:
            n_result = self._player_cache[cache_key]
        else:
            player_js = await self._get_player_js(player_url)
            n_result = self._transform_nsig(n_param, player_js)

            # Validate: result should not end with input
            if n_result and not n_result.endswith(n_param):
                self._player_cache[cache_key] = n_result
            else:
                logger.warning(f"nsig transformation may have failed: {n_result}")
                return fmt_url

        # Replace n parameter in URL
        new_qs = parse_qs(parsed.query, keep_blank_values=True)
        new_qs["n"] = [n_result]
        new_query = urlencode(new_qs, doseq=True)
        fmt_url = parsed._replace(query=new_query).geturl()

        return fmt_url

    def _transform_nsig(self, n_param: str, player_js: str) -> str:
        """
        Transform the n parameter using the function from player JS.
        """
        # Find the nsig function
        # Pattern: function(a){...enhanced_except...}
        nsig_patterns = [
            r'\.get\("n"\)\)&&\(b=([a-zA-Z0-9$]+)(?:\[(\d+)\])?\([a-zA-Z0-9]\)',
            r"([a-zA-Z0-9$]+)\s*=\s*function\(\s*a\s*\)\s*\{.*?(?:enhanced_except|b=a\.split)",
            r"var\s+([a-zA-Z0-9$]+)\s*=\s*\[([a-zA-Z0-9$]+)\];",
        ]

        func_name = None
        for pattern in nsig_patterns:
            match = re.search(pattern, player_js)
            if match:
                func_name = match.group(1)
                if match.lastindex and match.lastindex >= 2 and match.group(2):
                    # It's an array reference like var b = [func]; b[0](a)
                    match.group(2)
                    # Find the actual function name
                    arr_match = re.search(
                        rf"var\s+{re.escape(func_name)}\s*=\s*\[([a-zA-Z0-9$]+)\]",
                        player_js,
                    )
                    if arr_match:
                        func_name = arr_match.group(1)
                break

        if not func_name:
            raise ExtractionError("Could not find nsig function in player JS")

        interpreter = JSInterpreter(player_js)
        try:
            nsig_func = interpreter.extract_function(func_name)
            result = nsig_func(n_param)
            return str(result) if result else n_param
        except JSInterpreterError as e:
            logger.warning(f"nsig interpretation failed: {e}")
            return n_param

    async def _get_player_js(self, player_url: str) -> str:
        """Download and cache the YouTube player JavaScript."""
        cache_key = f"player_js_{player_url}"
        if cache_key in self._player_cache:
            return self._player_cache[cache_key]

        player_js = await self._download_webpage(
            player_url,
            headers={"User-Agent": _INNERTUBE_CLIENTS["web"]["user_agent"]},
        )

        self._player_cache[cache_key] = player_js
        return player_js

    def _extract_subtitles(
        self, player_response: dict, preferred_lang: str | None = None
    ) -> dict[str, list[SubtitleTrack]] | None:
        """Extract subtitle tracks from the player response."""
        captions = traverse_obj(
            player_response,
            ("captions", "playerCaptionsTracklistRenderer"),
        )

        if not captions:
            return None

        caption_tracks = captions.get("captionTracks", [])
        if not caption_tracks:
            return None

        subtitles: dict[str, list[SubtitleTrack]] = {}

        for track in caption_tracks:
            base_url = track.get("baseUrl")
            if not base_url:
                continue

            lang_code = track.get("languageCode", "und")
            lang_name = traverse_obj(track, ("name", "simpleText")) or traverse_obj(
                track, ("name", "runs", 0, "text")
            )

            is_asr = track.get("kind") == "asr"

            # Create subtitle entries for different formats
            for fmt in ("vtt", "srv3", "json3"):
                sub_url = f"{base_url}&fmt={fmt}"

                sub_track = SubtitleTrack(
                    url=sub_url,
                    lang=lang_code,
                    lang_name=lang_name,
                    ext=fmt,
                    is_auto_generated=is_asr,
                )

                if lang_code not in subtitles:
                    subtitles[lang_code] = []
                subtitles[lang_code].append(sub_track)

        return subtitles if subtitles else None
