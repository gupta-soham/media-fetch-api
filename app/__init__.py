"""Media Fetch API application package."""

from .config import get_settings
from .main import app

__all__ = ["app", "get_settings"]
