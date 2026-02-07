"""
Media Fetch API - FastAPI application entry point.

A comprehensive media extraction API that supports 12 platforms including
YouTube, Instagram, Twitter/X, TikTok, Facebook, Reddit, SoundCloud,
Vimeo, Twitch, Google Drive, Pinterest and Snapchat.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes.api import router

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if get_settings().debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    logger.info("Media Fetch API starting up...")

    # Log configuration
    settings = get_settings()
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"Cookie directory: {settings.cookie_dir}")

    # Check FFmpeg availability
    from .core.ffmpeg import get_ffmpeg_version, is_ffmpeg_available

    if is_ffmpeg_available():
        logger.info(f"FFmpeg available: {get_ffmpeg_version()}")
    else:
        logger.warning("FFmpeg not found - audio conversion will be unavailable")

    # Initialize cookie manager
    from .core.cookies import get_cookie_manager

    get_cookie_manager()
    logger.info("Cookie manager initialized")

    yield

    logger.info("Media Fetch API shutting down...")


app = FastAPI(
    title="Media Fetch API",
    description=(
        "A comprehensive media extraction API supporting YouTube, Instagram, "
        "Twitter/X, TikTok, Facebook, Reddit, SoundCloud, Vimeo, Twitch, "
        "Google Drive, Pinterest and Snapchat. Extracts direct media URLs "
        "with format selection and metadata."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: use CORS_ORIGINS env (comma-separated) for explicit origins; empty = "*" without credentials (safe default)
_origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins if _origins else ["*"],
    allow_credentials=bool(_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, prefix="/api")


@app.get("/", tags=["root"])
async def root():
    return {
        "name": "Media Fetch API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "extract": "/api/extract",
            "supported": "/api/supported",
            "health": "/api/health",
        },
    }


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
