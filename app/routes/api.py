"""
API route definitions for the Media Fetch API.
"""

import logging

from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..core.cookies import get_cookie_manager
from ..core.ffmpeg import get_ffmpeg_version, is_ffmpeg_available
from ..core.url_matcher import get_supported_platforms, match_url
from ..extractors import ExtractionError, get_extractor
from ..models.request import ExtractRequest
from ..models.response import ErrorResponse, ExtractResponse

logger = logging.getLogger(__name__)

router = APIRouter()


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
