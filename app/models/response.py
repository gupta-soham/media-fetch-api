from typing import Any

from pydantic import BaseModel, Field

from .enums import FormatType, Platform


class FormatInfo(BaseModel):
    """Information about a single media format/stream."""

    url: str = Field(..., description="Direct URL to the media stream")
    format_id: str | None = Field(None, description="Format identifier")
    ext: str | None = Field(None, description="File extension (mp4, webm, m4a, etc.)")
    width: int | None = Field(None, description="Video width in pixels")
    height: int | None = Field(None, description="Video height in pixels")
    fps: float | None = Field(None, description="Frames per second")
    vcodec: str | None = Field(None, description="Video codec (avc1, vp9, av01, none)")
    acodec: str | None = Field(None, description="Audio codec (mp4a, opus, none)")
    abr: float | None = Field(None, description="Audio bitrate in kbps")
    vbr: float | None = Field(None, description="Video bitrate in kbps")
    tbr: float | None = Field(None, description="Total bitrate in kbps")
    filesize: int | None = Field(None, description="File size in bytes")
    filesize_approx: int | None = Field(None, description="Approximate file size in bytes")
    format_type: FormatType = Field(
        FormatType.COMBINED,
        description="Type: video_only, audio_only, or combined",
    )
    quality_label: str | None = Field(None, description="Quality label (e.g., '1080p60')")
    protocol: str | None = Field(None, description="Protocol (https, hls, dash)")
    http_headers: dict[str, str] | None = Field(
        None, description="Required HTTP headers for downloading"
    )
    fragment_base_url: str | None = Field(None, description="Base URL for fragmented streams")
    fragments: list[dict[str, Any]] | None = Field(
        None, description="Fragment list for HLS/DASH streams"
    )


class SubtitleTrack(BaseModel):
    """Information about a subtitle track."""

    url: str = Field(..., description="URL to the subtitle file")
    lang: str = Field(..., description="Language code (ISO 639-1)")
    lang_name: str | None = Field(None, description="Human-readable language name")
    ext: str | None = Field(None, description="Subtitle format (vtt, srt, srv3)")
    is_auto_generated: bool = Field(False, description="Whether auto-generated")


class MediaMetadata(BaseModel):
    """Metadata about the media."""

    uploader: str | None = Field(None, description="Uploader/channel name")
    uploader_id: str | None = Field(None, description="Uploader/channel ID")
    uploader_url: str | None = Field(None, description="Uploader/channel URL")
    upload_date: str | None = Field(None, description="Upload date (YYYY-MM-DD)")
    description: str | None = Field(None, description="Media description")
    view_count: int | None = Field(None, description="View count")
    like_count: int | None = Field(None, description="Like count")
    comment_count: int | None = Field(None, description="Comment count")
    repost_count: int | None = Field(None, description="Repost/share count")
    tags: list[str] | None = Field(None, description="Tags/keywords")
    categories: list[str] | None = Field(None, description="Categories")
    is_live: bool | None = Field(None, description="Whether this is a live stream")
    was_live: bool | None = Field(None, description="Whether this was a live stream")
    age_restricted: bool | None = Field(None, description="Age-restricted content")


class ExtractResponse(BaseModel):
    """Response model for the /extract endpoint."""

    success: bool = Field(True, description="Whether extraction was successful")
    platform: Platform = Field(..., description="Detected platform")
    id: str = Field(..., description="Media ID on the platform")
    title: str | None = Field(None, description="Media title")
    duration: float | None = Field(None, description="Duration in seconds")
    thumbnail: str | None = Field(None, description="Thumbnail URL")
    formats: list[FormatInfo] = Field(default_factory=list, description="All available formats")
    best_video: FormatInfo | None = Field(None, description="Best video-only format")
    best_audio: FormatInfo | None = Field(None, description="Best audio-only format")
    best_combined: FormatInfo | None = Field(None, description="Best combined (muxed) format")
    subtitles: dict[str, list[SubtitleTrack]] | None = Field(
        None, description="Available subtitles keyed by language code"
    )
    metadata: MediaMetadata | None = Field(None, description="Media metadata")


class ErrorResponse(BaseModel):
    """Error response model."""

    success: bool = Field(False)
    error: str = Field(..., description="Error message")
    error_code: str | None = Field(None, description="Machine-readable error code")
    platform: Platform | None = Field(None, description="Detected platform, if any")
