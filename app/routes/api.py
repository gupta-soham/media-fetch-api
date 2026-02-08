"""
API route definitions for the Media Fetch API.
"""

import logging
import tempfile
from pathlib import Path
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..config import get_settings
from ..core.cookies import get_cookie_manager
from ..core.download import download_with_ytdlp
from ..core.ffmpeg import get_ffmpeg_version, is_ffmpeg_available
from ..core.url_matcher import get_supported_platforms, match_url
from ..extractors import ExtractionError, get_extractor
from ..models.request import ExtractRequest
from ..models.response import ErrorResponse, ExtractResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Allowed hostnames for stream proxy (avoids SSRF)
_STREAM_ALLOWED_HOSTS = (
    "googlevideo.com",
    "vimeocdn.com",
    "vod-adaptive-ak.vimeocdn.com",
    "skyfire.vimeocdn.com",
    "facebook.com",
    "fbcdn.net",
    "cdninstagram.com",
    "tiktokcdn.com",
    "tiktokv.com",
    "twimg.com",
    "redd.it",
    "redditmedia.com",
)


@router.post(
    "/extract",
    response_model=ExtractResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid URL or unsupported platform"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Extraction failed"},
    },
    summary="Extract media URLs from a supported platform",
    description=(
        "Accepts a URL from a supported platform and returns direct media URLs, "
        "available formats and optional metadata."
    ),
)
async def extract_media(request: ExtractRequest):
    """
    Main extraction endpoint.

    Accepts a URL, identifies the platform and extracts direct media URLs
    with format information and optional metadata.
    """
    url = request.url.strip()

    # Match URL to a platform
    match_result = match_url(url)

    if match_result is None:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": f"Unsupported URL or platform: {url}",
                "error_code": "url.unsupported",
            },
        )

    logger.info(f"Matched URL to {match_result.platform.value} (id={match_result.media_id})")

    # Get the appropriate extractor
    try:
        extractor = get_extractor(match_result.platform)
    except ExtractionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": str(e),
                "error_code": "extractor.unavailable",
                "platform": match_result.platform.value,
            },
        )

    # Perform extraction
    try:
        response = await extractor.extract(
            media_id=match_result.media_id,
            url=url,
            request=request,
            params=match_result.params,
        )
        return response
    except ExtractionError as e:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": str(e),
                "error_code": e.error_code or "extraction.failed",
                "platform": match_result.platform.value,
            },
        )
    except Exception as e:
        logger.exception("Unexpected error during extraction: %s", e)
        # Do not leak exception details in production
        message = (
            str(e)
            if get_settings().debug
            else "An internal error occurred. Please try again later."
        )
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": message,
                "error_code": "internal.error",
                "platform": match_result.platform.value,
            },
        )


@router.get(
    "/stream",
    summary="Stream a media URL (server uses cookies for cookie-bound CDNs like YouTube)",
    description=(
        "Proxies a GET to the given URL using the API's cookies for the platform. "
        "Use when direct curl of best_combined.url returns 403 (e.g. YouTube). "
        "Only URLs from allowed CDN hostnames are permitted."
    ),
)
async def stream_media(url: str, platform: str = ""):
    from urllib.parse import urlparse

    try:
        decoded = unquote(url)
        parsed = urlparse(decoded)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Invalid url parameter")
        host = (parsed.netloc or "").lower().split(":")[0]
        if not any(host == h or host.endswith("." + h) for h in _STREAM_ALLOWED_HOSTS):
            raise HTTPException(
                status_code=400,
                detail="URL host not allowed for streaming (SSRF protection)",
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail="Invalid url parameter")

    cookies_dict = {}
    if platform:
        cm = get_cookie_manager()
        cookies_dict = cm.get_cookies(platform) or {}
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies_dict.items()) if cookies_dict else None

    headers = {
        "User-Agent": get_settings().user_agent
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    # googlevideo.com: don't send cookies (URL often from iOS/android_vr). Match client in URL if present.
    if "googlevideo.com" in host:
        headers["Referer"] = "https://www.youtube.com/"
        headers["Origin"] = "https://www.youtube.com"
        # Match User-Agent to the client that produced the URL (c= in query string)
        if "&c=ANDROID_VR" in decoded or "&c=ANDROID" in decoded:
            headers["User-Agent"] = "com.google.android.apps.youtube.vr.oculus/1.71.26 (Linux; U; Android 11; eureka-user Build/SQ3A.220605.009.A1) gzip"
        else:
            headers["User-Agent"] = "com.google.ios.youtube/21.02.3 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)"
    elif cookie_header:
        headers["Cookie"] = cookie_header

    # Fetch full response first so we can raise 502 before sending any body (avoids "response already started")
    async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
        resp = await client.get(decoded, headers=headers)
        if resp.status_code not in (200, 206):
            raise HTTPException(
                status_code=502,
                detail=f"Upstream returned {resp.status_code}",
            )
        content = await resp.aread()
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/download",
    summary="Download media file (server-side)",
    description=(
        "Extract and return the media file in one call. For YouTube, uses the API's cookies and "
        "runs yt-dlp on the server when the stream proxy fails (403), so the client does not need "
        "yt-dlp or cookies. Pass the media URL as the 'url' query parameter (URL-encoded)."
    ),
)
async def download_media(url: str):
    """
    Single-call download: extract, then stream or run yt-dlp on the server.
    Returns the media file; client just saves the response body.
    """
    from urllib.parse import urlparse

    try:
        decoded = unquote(url.strip())
        if not decoded.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Invalid url parameter")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail="Invalid url parameter")

    match_result = match_url(decoded)
    if match_result is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "Unsupported URL", "error_code": "url.unsupported"},
        )

    try:
        extractor = get_extractor(match_result.platform)
    except ExtractionError as e:
        raise HTTPException(status_code=400, detail={"error": str(e), "error_code": "extractor.unavailable"})

    request = ExtractRequest(url=decoded)
    try:
        response = await extractor.extract(
            media_id=match_result.media_id,
            url=decoded,
            request=request,
            params=match_result.params,
        )
    except Exception as e:
        logger.warning("Extract failed in /download: %s", e)
        # For YouTube, try yt-dlp with the watch URL before giving up (handles strict playability / bot blocks)
        if match_result.platform.value == "youtube":
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                ok, ytdlp_msg = await download_with_ytdlp(decoded, tmp_path)
                if ok:
                    content = tmp_path.read_bytes()
                    return Response(
                        content=content,
                        media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"},
                    )
                # Surface yt-dlp failure so user sees real cause (e.g. not installed, bad cookies)
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "Extraction failed; yt-dlp fallback also failed.",
                        "error_code": "ytdlp.failed",
                        "server_error": ytdlp_msg,
                        "extract_error": str(e),
                    },
                )
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
        raise HTTPException(status_code=502, detail={"error": str(e), "error_code": "extract.failed"})

    platform = response.platform.value if hasattr(response, "platform") else match_result.platform.value
    best_url = (
        (response.best_combined.url if response.best_combined else None)
        or (response.best_video.url if response.best_video else None)
        or (response.formats[0].url if response.formats else None)
    )
    if not best_url:
        raise HTTPException(status_code=502, detail={"error": "No stream URL in response", "error_code": "no_url"})

    # YouTube: try stream proxy first; on failure run yt-dlp on server (uses server cookies)
    if platform == "youtube" and "googlevideo.com" in best_url:
        headers = {
            "Referer": "https://www.youtube.com/",
            "Origin": "https://www.youtube.com",
            "User-Agent": "com.google.ios.youtube/21.02.3 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
        }
        if "&c=ANDROID_VR" in best_url or "&c=ANDROID" in best_url:
            headers["User-Agent"] = "com.google.android.apps.youtube.vr.oculus/1.71.26 (Linux; U; Android 11; eureka-user Build/SQ3A.220605.009.A1) gzip"
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
                resp = await client.get(best_url, headers=headers)
                if resp.status_code in (200, 206):
                    content = await resp.aread()
                    if len(content) >= 1000 and not content.lstrip().startswith((b"<", b"{")):
                        return Response(
                            content=content,
                            media_type="application/octet-stream",
                            headers={"Cache-Control": "no-store"},
                        )
        except Exception as e:
            logger.warning("Stream proxy failed in /download: %s", e)

        # Fallback: yt-dlp on server with server's cookies
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            ok, ytdlp_msg = await download_with_ytdlp(decoded, tmp_path)
            if not ok:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "yt-dlp failed on the server.",
                        "error_code": "ytdlp.failed",
                        "hint": "Ensure yt-dlp is installed (pip install yt-dlp) and cookies/youtube.txt exists if needed.",
                        "server_error": ytdlp_msg,
                    },
                )
            content = tmp_path.read_bytes()
            return Response(
                content=content,
                media_type="application/octet-stream",
                headers={"Cache-Control": "no-store"},
            )
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    # Non-YouTube or non-googlevideo: stream the URL with cookies if needed
    parsed = urlparse(best_url)
    host = (parsed.netloc or "").lower().split(":")[0]
    cookies_dict = get_cookie_manager().get_cookies(platform) or {}
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies_dict.items()) if cookies_dict else None
    headers = {"User-Agent": get_settings().user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            resp = await client.get(best_url, headers=headers)
            if resp.status_code not in (200, 206):
                raise HTTPException(status_code=502, detail=f"Upstream returned {resp.status_code}")
            content = await resp.aread()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Stream failed in /download: %s", e)
        raise HTTPException(status_code=502, detail={"error": str(e), "error_code": "stream.failed"})
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/supported",
    summary="List supported platforms",
    description="Returns a list of all supported platforms with example URLs.",
)
async def list_supported():
    """Return information about all supported platforms."""
    return {
        "platforms": get_supported_platforms(),
        "total": len(get_supported_platforms()),
    }


@router.get(
    "/health",
    summary="Health check",
    description="Check the health of the API including FFmpeg and cookie status.",
)
async def health_check():
    """Health check endpoint."""
    cookie_manager = get_cookie_manager()

    cookie_status = {}
    for service in [
        "youtube",
        "instagram",
        "twitter",
        "reddit",
        "vimeo",
        "facebook",
        "tiktok",
        "pinterest",
        "google_drive",
    ]:
        cookie_status[service] = cookie_manager.has_cookies(service)

    ffmpeg_available = is_ffmpeg_available()
    ffmpeg_version = get_ffmpeg_version() if ffmpeg_available else None

    return {
        "status": "healthy",
        "ffmpeg": {
            "available": ffmpeg_available,
            "version": ffmpeg_version,
        },
        "cookies": cookie_status,
    }
