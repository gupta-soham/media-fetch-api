from pydantic import BaseModel, Field

from .enums import AudioFormat, MediaType, Quality, VideoFormat


class ExtractRequest(BaseModel):
    """Request model for the /extract endpoint."""

    url: str = Field(
        ...,
        max_length=2048,
        description="URL of the media to extract",
        examples=["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    )
    media_type: MediaType = Field(
        default=MediaType.BOTH,
        description="Type of media to extract: video, audio, or both",
    )
    quality: Quality = Field(
        default=Quality.BEST,
        description="Preferred video quality",
    )
    audio_format: AudioFormat = Field(
        default=AudioFormat.BEST,
        description="Preferred audio format for extraction (mp3, flac, wav, ogg)",
    )
    video_format: VideoFormat = Field(
        default=VideoFormat.BEST,
        description="Preferred video container format (mp4, mkv, webm, mov)",
    )
    include_metadata: bool = Field(
        default=True,
        description="Whether to include metadata in the response",
    )
    include_subtitles: bool = Field(
        default=False,
        description="Whether to include subtitle tracks",
    )
    subtitle_lang: str | None = Field(
        default=None,
        max_length=16,
        description="Preferred subtitle language code (e.g., 'en', 'es')",
    )
    cookie_file: str | None = Field(
        default=None,
        description="Name of cookie file to use (e.g., 'youtube' loads cookies/youtube.txt)",
    )
    password: str | None = Field(
        default=None,
        max_length=512,
        description="Password for password-protected content (e.g., Vimeo)",
    )
