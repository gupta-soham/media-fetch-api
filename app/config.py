from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    host: str = "0.0.0.0"
    port: int = 7652
    debug: bool = False

    cookie_dir: str = "./cookies"
    ffmpeg_path: str = ""
    request_timeout: int = 30
    max_retries: int = 3
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    # Comma-separated origins for CORS (e.g. "https://app.example.com"). Empty = allow "*" with no credentials.
    cors_origins: str = ""

    # Optional: Vimeo OAuth (create app at https://developer.vimeo.com/apps). If set, used instead of built-in client.
    vimeo_client_id: str = ""
    vimeo_client_secret: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_cookie_dir() -> Path:
    settings = get_settings()
    cookie_path = Path(settings.cookie_dir)
    cookie_path.mkdir(parents=True, exist_ok=True)
    return cookie_path


def get_ffmpeg_path() -> str:
    settings = get_settings()
    if settings.ffmpeg_path:
        return settings.ffmpeg_path
    return "ffmpeg"
