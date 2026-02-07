from enum import Enum


class Platform(str, Enum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"
    REDDIT = "reddit"
    SOUNDCLOUD = "soundcloud"
    VIMEO = "vimeo"
    TWITCH = "twitch"
    GOOGLE_DRIVE = "google_drive"
    PINTEREST = "pinterest"
    SNAPCHAT = "snapchat"


class MediaType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    BOTH = "both"


class Quality(str, Enum):
    BEST = "best"
    WORST = "worst"
    Q4320 = "4320"
    Q2160 = "2160"
    Q1440 = "1440"
    Q1080 = "1080"
    Q720 = "720"
    Q480 = "480"
    Q360 = "360"
    Q240 = "240"
    Q144 = "144"


class AudioFormat(str, Enum):
    BEST = "best"
    MP3 = "mp3"
    FLAC = "flac"
    WAV = "wav"
    OGG = "ogg"


class VideoFormat(str, Enum):
    BEST = "best"
    MP4 = "mp4"
    MKV = "mkv"
    WEBM = "webm"
    MOV = "mov"


class FormatType(str, Enum):
    VIDEO_ONLY = "video_only"
    AUDIO_ONLY = "audio_only"
    COMBINED = "combined"
