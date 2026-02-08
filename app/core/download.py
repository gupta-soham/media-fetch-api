"""
Server-side download: run yt-dlp with the API's cookies and return the file path.

Used when the stream proxy fails (e.g. YouTube 403). Keeps yt-dlp and cookies
on the server so clients only call the API.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from ..config import get_cookie_dir, get_ffmpeg_path

logger = logging.getLogger(__name__)

# Final container we produce (we merge with ffmpeg)
_MERGED_EXT = "mp4"


async def _file_has_audio(path: Path) -> bool:
    """Return True if the file has at least one audio stream (via ffprobe)."""
    ffmpeg = get_ffmpeg_path()
    ffprobe = ffmpeg.replace("ffmpeg", "ffprobe") if "ffmpeg" in ffmpeg else "ffprobe"
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        return proc.returncode == 0 and b"audio" in (stdout or b"")
    except Exception:
        return False


def _ytdlp_path() -> str | None:
    """Return yt-dlp executable path or None if not found."""
    return shutil.which("yt-dlp")


def _subprocess_env_with_ffmpeg_in_path() -> dict[str, str]:
    """Return env dict for subprocess so yt-dlp can find ffmpeg for merging."""
    env = os.environ.copy()
    ffmpeg = get_ffmpeg_path()
    if os.path.isabs(ffmpeg) or os.sep in ffmpeg:
        ffmpeg_dir = os.path.dirname(ffmpeg)
        if ffmpeg_dir:
            env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")
    return env


async def _merge_with_ffmpeg(video_path: Path, audio_path: Path, out_path: Path) -> tuple[bool, str | None]:
    """
    Merge video and audio into out_path. Video is stream-copied; audio is encoded to AAC
    so the MP4 is playable everywhere (YouTube often gives Opus, which many players don't support).
    """
    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(out_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=_subprocess_env_with_ffmpeg_in_path(),
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr.decode(errors="replace") or "").strip()[:500]
            return False, err or "ffmpeg merge failed"
        if not out_path.exists() or out_path.stat().st_size == 0:
            return False, "ffmpeg produced empty file"
        return True, None
    except Exception as e:
        return False, str(e)


async def download_with_ytdlp(media_url: str, out_path: Path) -> tuple[bool, str | None]:
    """
    Run yt-dlp to download media_url and write the result to out_path. Uses cookies
    from the API's cookie dir (cookies/youtube.txt for YouTube) when present.

    Ensures ffmpeg is in PATH so bestvideo+bestaudio is merged. If yt-dlp leaves
    separate video/audio files (e.g. ffmpeg not found by yt-dlp), we merge them ourselves.
    """
    ytdlp = _ytdlp_path()
    if not ytdlp:
        msg = "yt-dlp not installed on the server. Install it: pip install yt-dlp (or rebuild the Docker image)."
        logger.warning(msg)
        return False, msg

    cookie_dir = get_cookie_dir()
    cookie_file = (cookie_dir / "youtube.txt").resolve()
    # Use comma so yt-dlp downloads video and audio as separate files (no merge);
    # we merge with ffmpeg so we always get both tracks regardless of yt-dlp's ffmpeg.
    args = [
        ytdlp,
        "-f", "bestvideo,bestaudio",
        "--no-part",
        "--no-warnings",
    ]
    if cookie_file.exists():
        args.extend(["--cookies", str(cookie_file)])
        logger.info("yt-dlp using cookies: %s", cookie_file)
    else:
        logger.warning(
            "yt-dlp running without cookies (file not found: %s); quality may be limited",
            cookie_file,
        )

    env = _subprocess_env_with_ffmpeg_in_path()
    with tempfile.TemporaryDirectory(prefix="ytdlp_") as tmpdir:
        work_dir = Path(tmpdir)
        output_template = str(work_dir / "out.%(ext)s")
        args.extend(["-o", output_template])
        args.append(media_url)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
                env=env,
            )
            _, stderr = await proc.communicate()
            err_text = stderr.decode(errors="replace").strip() if stderr else ""
            if proc.returncode != 0:
                snippet = (err_text[:400] + "…") if len(err_text) > 400 else err_text
                logger.warning("yt-dlp exited %s: %s", proc.returncode, snippet)
                return False, snippet or f"yt-dlp exited with code {proc.returncode}"

            candidates = sorted(
                [f for f in work_dir.iterdir() if f.is_file() and not f.name.endswith(".part") and f.stat().st_size > 0],
                key=lambda f: f.stat().st_size,
                reverse=True,
            )

            if len(candidates) >= 2:
                # Merge video (largest) + audio (second) with ffmpeg
                video_path, audio_path = candidates[0], candidates[1]
                merged_tmp = work_dir / f"merged.{_MERGED_EXT}"
                ok, merge_err = await _merge_with_ffmpeg(video_path, audio_path, merged_tmp)
                if not ok:
                    logger.warning("ffmpeg merge failed: %s", merge_err)
                    return False, f"Audio/video merge failed: {merge_err}"
                shutil.copy2(merged_tmp, out_path)
                return True, None

            if len(candidates) == 1:
                single = candidates[0]
                if await _file_has_audio(single):
                    shutil.copy2(single, out_path)
                    return True, None
                return False, "Downloaded file has no audio track; ensure ffmpeg is available so video+audio can be merged."

            return False, err_text or "yt-dlp produced no output file."
        except Exception as e:
            logger.warning("yt-dlp failed: %s", e)
            return False, str(e)
