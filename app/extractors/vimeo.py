"""
Vimeo extractor - extracts video/audio from Vimeo.
Ported from cobalt's vimeo.js and yt-dlp's vimeo.py.

Supports:
- Regular videos
- Unlisted videos (with hash)
- Password-protected videos
- Multiple quality formats (progressive + HLS)
- OAuth bearer token authentication
"""

import logging
from urllib.parse import unquote

from ..config import get_settings
from ..core.dash_parser import parse_mpd
from ..models.enums import FormatType, Platform
from ..models.request import ExtractRequest
from ..models.response import (
    ExtractResponse,
    FormatInfo,
    MediaMetadata,
)
from ..utils.helpers import (
    float_or_none,
    format_date,
    int_or_none,
    parse_m3u8_attributes,
    str_or_none,
    traverse_obj,
)
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

# Vimeo API
_VIMEO_API = "https://api.vimeo.com"

# Fallback OAuth client (may 401; set VIMEO_CLIENT_ID + VIMEO_CLIENT_SECRET in .env for your app)
_VIMEO_CLIENT_ID_DEFAULT = "b0fa1cf52f268e1f03d3f5bab7e62651b27e0e38"
_VIMEO_CLIENT_SECRET_DEFAULT = (
    "RmduFDMYmbecHnaWO17Brd+iGHPaLJGQVzGKu5cpLUxEygHDDPFSJBMsz7oTJ7HuQh9+"
    "wFBPUKcKJrRZNKK/2MK3YIHQ3BnCpDWEYPTNbf7RlC56SmCdBbUKJUhOjGk"
)

# Resolution mapping (from cobalt)
_RESOLUTION_MAP = {
    3840: 2160,
    2560: 1440,
    1920: 1080,
    1280: 720,
    960: 540,
    854: 480,
    640: 360,
    426: 240,
}


class VimeoExtractor(BaseExtractor):
    """Vimeo media extractor."""

    platform = Platform.VIMEO
    _bearer_token: str | None = None

    async def _extract(
        self,
        media_id: str,
        url: str,
        request: ExtractRequest,
        params: dict[str, str],
    ) -> ExtractResponse:
        """Extract media from Vimeo."""
        video_id = media_id

        # Get bearer token (fallback to player config if unavailable)
        bearer = await self._get_bearer_token()
        if not bearer:
            return await self._extract_from_player_config(video_id)

        # Fetch video info from API
        api_url = f"{_VIMEO_API}/videos/{video_id}"
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/vnd.vimeo.*+json;version=3.4.10",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        params_dict = {
            "fields": (
                "uri,name,description,duration,width,height,created_time,"
                "modified_time,pictures,files,download,play,status,"
                "user,metadata,stats,categories,tags,config_url,embed_player_config_url"
            ),
        }

        # Handle password-protected videos
        if request.password:
            params_dict["password"] = request.password

        try:
            response = await self.http.get(api_url, headers=headers, params=params_dict)
            try:
                video_data = response.json()
            except Exception:
                video_data = {}

            error_code = video_data.get("error_code") if isinstance(video_data, dict) else None
            if error_code == 8003:
                # Refresh bearer token and retry once
                bearer = await self._get_bearer_token(force_refresh=True)
                if not bearer:
                    raise ExtractionError("Could not refresh Vimeo bearer token")
                headers["Authorization"] = f"Bearer {bearer}"
                response = await self.http.get(api_url, headers=headers, params=params_dict)
                video_data = response.json()
                error_code = video_data.get("error_code") if isinstance(video_data, dict) else None

            if response.status_code == 403:
                raise ExtractionError(
                    "Video is private or password-protected",
                    error_code="vimeo.forbidden",
                )
            if response.status_code == 404 and error_code == 5460:
                raise ExtractionError(
                    "Login required to access this video",
                    error_code="vimeo.login_required",
                )
            if response.status_code == 404:
                raise ExtractionError(
                    "Video not found",
                    error_code="vimeo.not_found",
                )
            if response.status_code == 400:
                invalid = traverse_obj(video_data, ("invalid_parameters",))
                if isinstance(invalid, list) and any(
                    "password" in (p.get("field") or "") for p in invalid if isinstance(p, dict)
                ):
                    raise ExtractionError(
                        "Password required or incorrect",
                        error_code="vimeo.password_required",
                    )
            if response.status_code != 200:
                raise ExtractionError(
                    f"Vimeo API returned {response.status_code}",
                    error_code="vimeo.api_error",
                )

        except ExtractionError:
            raise
        except Exception as e:
            raise ExtractionError(f"Failed to fetch Vimeo video info: {e}")

        return await self._parse_video(video_data, video_id, bearer)

    def _player_headers(self, video_id: str) -> dict:
        """Headers that mimic the Vimeo player page (Referer required for config)."""
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://player.vimeo.com/video/{video_id}",
            "Origin": "https://player.vimeo.com",
        }

    async def _extract_from_player_config(self, video_id: str) -> ExtractResponse:
        """Fallback: parse player config from the public player page or direct config URL."""
        player_url = f"https://player.vimeo.com/video/{video_id}"
        player_headers = self._player_headers(video_id)
        page_headers = {
            **player_headers,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        config_headers = {
            **player_headers,
            "Accept": "application/json",
        }

        config_url = None
        try:
            html = await self._download_webpage(player_url, headers=page_headers)
            config_url = self._search_regex(
                r'data-config-url="([^"]+)"',
                html,
                "config_url",
                default=None,
            )
            if not config_url:
                config_url = self._search_regex(
                    r'"config_url"\s*:\s*"([^"]+)"',
                    html,
                    "config_url",
                    default=None,
                )
        except Exception as e:
            logger.debug("Player page fetch failed, will try direct config URL: %s", e)

        if not config_url:
            config_url = f"https://player.vimeo.com/video/{video_id}/config"

        config_url = unquote(
            config_url.replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
        )

        config = None
        last_error = None
        for referer in (f"https://player.vimeo.com/video/{video_id}", "https://vimeo.com/"):
            try:
                headers = {**config_headers, "Referer": referer}
                config = await self._download_json(config_url, headers=headers)
                if config and isinstance(config, dict):
                    break
            except Exception as e:
                logger.debug("Config fetch with Referer %s failed: %s", referer, e)
                last_error = e
                config = None

        if not config:
            raise ExtractionError(
                "Unable to extract config_url",
                error_code="vimeo.config_missing",
            ) from last_error
        return self._parse_player_config(config, video_id)

    def _parse_player_config(self, config: dict, video_id: str) -> ExtractResponse:
        """Parse player config JSON into ExtractResponse."""
        video = config.get("video", {}) if isinstance(config, dict) else {}
        title = video.get("title") or video.get("name") or "Vimeo Video"
        duration = float_or_none(video.get("duration"))

        thumbs = video.get("thumbs") or {}
        thumbnail = (
            thumbs.get("base")
            or thumbs.get("640")
            or next((v for v in thumbs.values() if isinstance(v, str)), None)
        )

        formats: list[FormatInfo] = []
        files = traverse_obj(config, ("request", "files")) or {}

        # Progressive files
        progressive = files.get("progressive") or []
        for pg in progressive:
            pg_url = pg.get("url")
            if not pg_url:
                continue
            pg_height = int_or_none(pg.get("height"))
            formats.append(
                FormatInfo(
                    url=pg_url,
                    format_id=f"progressive_{pg_height}p" if pg_height else "progressive",
                    ext="mp4",
                    width=int_or_none(pg.get("width")),
                    height=pg_height,
                    fps=float_or_none(pg.get("fps")),
                    format_type=FormatType.COMBINED,
                    quality_label=pg.get("quality"),
                )
            )

        # Headers required for Vimeo segment requests (e.g. when using ffmpeg with HLS/DASH)
        segment_headers = {
            "Referer": f"https://player.vimeo.com/video/{video_id}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        # HLS CDNs
        hls_cdns = traverse_obj(files, ("hls", "cdns")) or {}
        for cdn_name, cdn_data in hls_cdns.items():
            hls_url = cdn_data.get("url")
            if not hls_url:
                continue
            formats.append(
                FormatInfo(
                    url=hls_url,
                    format_id=f"hls_{cdn_name}",
                    ext="mp4",
                    protocol="hls",
                    format_type=FormatType.COMBINED,
                    quality_label="HLS",
                    http_headers=segment_headers,
                )
            )

        # DASH CDNs
        dash_cdns = traverse_obj(files, ("dash", "cdns")) or {}
        for cdn_name, cdn_data in dash_cdns.items():
            dash_url = cdn_data.get("url")
            if not dash_url:
                continue
            formats.append(
                FormatInfo(
                    url=dash_url,
                    format_id=f"dash_{cdn_name}",
                    ext="mpd",
                    protocol="dash",
                    format_type=FormatType.COMBINED,
                    http_headers=segment_headers,
                )
            )

        if not formats:
            raise ExtractionError(
                "No playable formats found for Vimeo video",
                error_code="vimeo.no_formats",
            )

        metadata = MediaMetadata(
            uploader=traverse_obj(video, ("owner", "name")),
            uploader_id=str_or_none(traverse_obj(video, ("owner", "id"))),
            uploader_url=traverse_obj(video, ("owner", "url")),
        )

        return ExtractResponse(
            platform=Platform.VIMEO,
            id=str(video_id),
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    async def _get_bearer_token(self, force_refresh: bool = False) -> str | None:
        """Get Vimeo OAuth bearer token using client credentials."""
        if self._bearer_token and not force_refresh:
            return self._bearer_token
        if force_refresh:
            self._bearer_token = None

        # Check for cookie-based bearer
        if self._has_cookies():
            self._get_cookie("vuid")
            # Cookie-based auth would use session, not bearer
            # For now, use client credentials

        # Client credentials grant (form-encoded); use env credentials if set (built-in may 401)
        import base64

        settings = get_settings()
        client_id = (settings.vimeo_client_id or _VIMEO_CLIENT_ID_DEFAULT).strip()
        client_secret = (settings.vimeo_client_secret or _VIMEO_CLIENT_SECRET_DEFAULT).strip()
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        form_data = {
            "grant_type": "client_credentials",
            "scope": "private public create edit delete interact upload purchased stats video_files",
        }

        try:
            response = await self.http.post(
                f"{_VIMEO_API}/oauth/authorize/client",
                headers={
                    "Authorization": f"Basic {auth}",
                    "Accept": "application/vnd.vimeo.*+json;version=3.4.10",
                },
                data=form_data,
            )

            if response.status_code == 200:
                data = response.json()
                self._bearer_token = data.get("access_token")
                return self._bearer_token
        except Exception as e:
            logger.warning(f"Failed to get Vimeo bearer token: {e}")

        return None

    async def _parse_video(self, data: dict, video_id: str, bearer: str) -> ExtractResponse:
        """Parse Vimeo video API response."""
        title = data.get("name", "")
        description = data.get("description", "")
        duration = float_or_none(data.get("duration"))
        int_or_none(data.get("width"))
        int_or_none(data.get("height"))

        # Get thumbnail
        pictures = data.get("pictures", {})
        thumbnail = None
        sizes = pictures.get("sizes", [])
        if sizes:
            # Get the largest thumbnail
            thumbnail = sizes[-1].get("link")

        formats = []

        async def _resolve_if_redirect(url: str) -> str:
            if not url:
                return url
            if "download-video-" in url and "vimeocdn.com" in url:
                return url  # already direct CDN
            if "progressive_redirect" in url or ("player.vimeo.com" in url and "config" not in url):
                try:
                    return await self.http.resolve_redirect(url)
                except Exception as e:
                    logger.debug("Could not resolve Vimeo redirect: %s", e)
            return url

        # Method 1: Progressive downloads (files array) - same as Cobalt getDirectLink
        files = data.get("files", [])
        for f in files:
            file_url = f.get("link") or f.get("link_secure")
            if not file_url:
                continue
            file_url = await _resolve_if_redirect(file_url)

            f_width = int_or_none(f.get("width"))
            f_height = int_or_none(f.get("height"))
            f_quality = f.get("quality", "")
            f_type = f.get("type", "")
            f_size = int_or_none(f.get("size"))

            # Determine extension
            ext = "mp4"
            if "webm" in f_type:
                ext = "webm"

            formats.append(
                FormatInfo(
                    url=file_url,
                    format_id=f_quality or f"file_{f_height}p",
                    ext=ext,
                    width=f_width,
                    height=f_height,
                    filesize=f_size,
                    format_type=FormatType.COMBINED,
                    quality_label=f_quality,
                )
            )

        # Method 2: Download links
        downloads = data.get("download", [])
        for dl in downloads:
            dl_url = dl.get("link")
            if not dl_url:
                continue
            dl_url = await _resolve_if_redirect(dl_url)

            dl_width = int_or_none(dl.get("width"))
            dl_height = int_or_none(dl.get("height"))
            dl_quality = dl.get("quality", "")

            formats.append(
                FormatInfo(
                    url=dl_url,
                    format_id=f"download_{dl_quality}",
                    ext="mp4",
                    width=dl_width,
                    height=dl_height,
                    filesize=int_or_none(dl.get("size")),
                    format_type=FormatType.COMBINED,
                    quality_label=dl_quality,
                )
            )

        # Method 2b: Config URL (HLS/DASH CDNs)
        config_url = data.get("config_url") or data.get("embed_player_config_url")
        if config_url:
            try:
                config = await self._download_json(config_url)
                files = traverse_obj(config, ("request", "files")) or {}
                hls_cdns = traverse_obj(files, ("hls", "cdns")) or {}
                for cdn_name, cdn_data in hls_cdns.items():
                    hls_url = cdn_data.get("url")
                    if not hls_url:
                        continue
                    try:
                        hls_formats = await self._parse_hls_master(hls_url)
                        for fmt in hls_formats:
                            fmt.format_id = f"hls_{cdn_name}_{fmt.format_id or ''}".strip("_")
                        formats.extend(hls_formats)
                    except Exception:
                        formats.append(
                            FormatInfo(
                                url=hls_url,
                                format_id=f"hls_{cdn_name}",
                                ext="mp4",
                                protocol="hls",
                                format_type=FormatType.COMBINED,
                                quality_label="HLS",
                            )
                        )

                dash_cdns = traverse_obj(files, ("dash", "cdns")) or {}
                for cdn_name, cdn_data in dash_cdns.items():
                    dash_url = cdn_data.get("url")
                    if not dash_url:
                        continue
                    try:
                        mpd_content = await self._download_webpage(dash_url)
                        reps = parse_mpd(mpd_content, base_url=dash_url)
                        for rep in reps:
                            fmt_url = rep.url or rep.base_url
                            if not fmt_url:
                                continue
                            formats.append(
                                FormatInfo(
                                    url=fmt_url,
                                    format_id=f"dash_{cdn_name}_{rep.rep_id or ''}".strip("_"),
                                    ext="mp4",
                                    width=rep.width,
                                    height=rep.height,
                                    tbr=rep.bandwidth / 1000 if rep.bandwidth else None,
                                    vcodec=rep.codecs,
                                    format_type=FormatType.COMBINED,
                                    protocol="dash",
                                )
                            )
                    except Exception:
                        formats.append(
                            FormatInfo(
                                url=dash_url,
                                format_id=f"dash_{cdn_name}",
                                ext="mpd",
                                protocol="dash",
                                format_type=FormatType.COMBINED,
                            )
                        )
            except Exception as e:
                logger.debug(f"Failed to parse Vimeo config_url: {e}")

        # Method 3: Play (HLS/DASH)
        play = data.get("play", {})
        hls = play.get("hls", {})
        hls_url = hls.get("link")

        if hls_url:
            # Parse HLS master playlist for formats
            try:
                hls_formats = await self._parse_hls_master(hls_url)
                formats.extend(hls_formats)
            except Exception as e:
                logger.debug(f"Failed to parse Vimeo HLS: {e}")
                # Add raw HLS as single format
                formats.append(
                    FormatInfo(
                        url=hls_url,
                        format_id="hls",
                        ext="mp4",
                        protocol="hls",
                        format_type=FormatType.COMBINED,
                        quality_label="HLS",
                    )
                )

        # Progressive play (API returns redirect URLs; resolve to direct CDN like Cobalt)
        progressive = play.get("progressive", [])
        for pg in progressive:
            pg_url = pg.get("url") or pg.get("link")
            if not pg_url:
                continue

            pg_url = await _resolve_if_redirect(pg_url)
            pg_width = int_or_none(pg.get("width"))
            pg_height = int_or_none(pg.get("height"))

            formats.append(
                FormatInfo(
                    url=pg_url,
                    format_id=f"progressive_{pg_height}p" if pg_height else "progressive",
                    ext="mp4",
                    width=pg_width,
                    height=pg_height,
                    fps=float_or_none(pg.get("fps")),
                    format_type=FormatType.COMBINED,
                    quality_label=pg.get("quality"),
                )
            )

        if not formats:
            raise ExtractionError(
                "No playable formats found for Vimeo video",
                error_code="vimeo.no_formats",
            )

        # Build metadata
        user = data.get("user", {})
        stats = data.get("stats", {})

        metadata = MediaMetadata(
            uploader=user.get("name"),
            uploader_id=str_or_none(traverse_obj(user, ("uri",))),
            uploader_url=user.get("link"),
            description=description,
            upload_date=format_date(data.get("created_time")),
            view_count=int_or_none(stats.get("plays")),
            like_count=int_or_none(
                traverse_obj(data, ("metadata", "connections", "likes", "total"))
            ),
            comment_count=int_or_none(
                traverse_obj(data, ("metadata", "connections", "comments", "total"))
            ),
            tags=[t.get("name") for t in (data.get("tags") or []) if t.get("name")],
            categories=[c.get("name") for c in (data.get("categories") or []) if c.get("name")],
        )

        return ExtractResponse(
            platform=Platform.VIMEO,
            id=video_id,
            title=title,
            duration=duration,
            thumbnail=thumbnail,
            formats=formats,
            metadata=metadata,
        )

    async def _parse_hls_master(self, hls_url: str) -> list[FormatInfo]:
        """Parse an HLS master playlist into individual format entries."""
        formats = []

        try:
            content = await self._download_webpage(hls_url)

            lines = content.strip().split("\n")
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.startswith("#EXT-X-STREAM-INF:"):
                    attrs = parse_m3u8_attributes(line.split(":", 1)[1])
                    # Next line is the URL
                    if i + 1 < len(lines):
                        stream_url = lines[i + 1].strip()
                        if not stream_url.startswith("http"):
                            # Relative URL
                            base = hls_url.rsplit("/", 1)[0]
                            stream_url = f"{base}/{stream_url}"

                        bandwidth = int_or_none(attrs.get("BANDWIDTH"))
                        resolution = attrs.get("RESOLUTION", "")
                        attrs.get("CODECS", "")

                        width = None
                        height = None
                        if "x" in resolution:
                            parts = resolution.split("x")
                            width = int_or_none(parts[0])
                            height = int_or_none(parts[1])

                        formats.append(
                            FormatInfo(
                                url=stream_url,
                                format_id=f"hls_{height}p" if height else "hls",
                                ext="mp4",
                                width=width,
                                height=height,
                                tbr=float_or_none(bandwidth, scale=1000),
                                protocol="hls",
                                format_type=FormatType.COMBINED,
                                quality_label=f"{height}p" if height else None,
                            )
                        )
                        i += 1
                i += 1
        except Exception as e:
            logger.debug(f"HLS parsing failed: {e}")

        return formats
