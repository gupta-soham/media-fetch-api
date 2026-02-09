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


def _ffprobe_path() -> str:
    ffmpeg = get_ffmpeg_path()
    return ffmpeg.replace("ffmpeg", "ffprobe") if "ffmpeg" in ffmpeg else "ffprobe"


async def _file_has_audio(path: Path) -> bool:
    """Return True if the file has at least one audio stream (via ffprobe)."""
    ffprobe = _ffprobe_path()
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        return proc.returncode == 0 and b"audio" in (stdout or b"")
    except Exception:
        return False


async def _audio_codec_name(path: Path) -> str | None:
    """Return the first audio stream codec name (e.g. opus, aac) or None."""
    ffprobe = _ffprobe_path()
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        if proc.returncode != 0:
            return None
        name = (stdout or b"").decode(errors="replace").strip().lower()
        return name or None
    except Exception:
        return None


async def _reencode_audio_to_aac(input_path: Path, out_path: Path) -> tuple[bool, str | None]:
    """Re-encode file to MP4 with video copy and audio as AAC (for Opus etc.)."""
    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
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
            return False, err or "ffmpeg failed"
        if not out_path.exists() or out_path.stat().st_size == 0:
            return False, "ffmpeg produced empty file"
        return True, None
    except Exception as e:
        return False, str(e)


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


async def _merge_with_ffmpeg(
    video_path: Path, audio_path: Path, out_path: Path
) -> tuple[bool, str | None]:
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
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
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
    # Use "best" first so HLS-only videos (and any extractor that lacks bv/ba) never hit
    # "Requested format is not available". Retry with bestvideo+bestaudio for better quality
    # when "best" fails (e.g. 403) so we don't give up.
    base_args = [
        ytdlp,
        "--merge-output-format",
        "mp4",
        "--no-part",
        "--no-warnings",
    ]
    if cookie_file.exists():
        base_args.extend(["--cookies", str(cookie_file)])
        logger.info("yt-dlp using cookies: %s", cookie_file)
    else:
        logger.warning(
            "yt-dlp running without cookies (file not found: %s); quality may be limited",
            cookie_file,
        )

    # 5-level format waterfall so we download in any case.
    # Run 1: one -f string (merge → best → b → worst) so yt-dlp tries 4 options in a single run.
    # Run 2 (if run 1 fails with format not available or Sign in): no -f (yt-dlp default).
    format_fallbacks: list[str | None] = [
        "bestvideo+bestaudio/best/best/b/worst",  # 4 steps in one run
        None,  # no -f: 5th fallback
    ]
    env = _subprocess_env_with_ffmpeg_in_path()
    with tempfile.TemporaryDirectory(prefix="ytdlp_") as tmpdir:
        work_dir = Path(tmpdir)
        output_template = str(work_dir / "out.%(ext)s")
        media_url_arg = media_url
        err_text = ""

        for idx, format_spec in enumerate(format_fallbacks):
            if idx > 0:
                for f in work_dir.iterdir():
                    if f.is_file():
                        f.unlink()
            args = list(base_args)
            if format_spec is not None:
                args.extend(["-f", format_spec])
            args.extend(["-o", output_template, media_url_arg])
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
                if proc.returncode == 0:
                    break
                retry_reasons = (
                    "Requested format is not available",
                    "Sign in to confirm you're not a bot",
                )
                if any(r in err_text for r in retry_reasons):
                    logger.info(
                        "yt-dlp failed (tried %s): %s; trying next fallback",
                        format_spec or "default",
                        err_text[:200] if err_text else "unknown",
                    )
                    continue
                snippet = (err_text[:400] + "…") if len(err_text) > 400 else err_text
                logger.warning("yt-dlp exited %s: %s", proc.returncode, snippet)
                return False, snippet or f"yt-dlp exited with code {proc.returncode}"
            except Exception as e:
                logger.warning("yt-dlp failed: %s", e)
                return False, str(e)

        try:
            candidates = sorted(
                [
                    f
                    for f in work_dir.iterdir()
                    if f.is_file() and not f.name.endswith(".part") and f.stat().st_size > 0
                ],
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
                if not await _file_has_audio(single):
                    return (
                        False,
                        "Downloaded file has no audio track; ensure ffmpeg is available so video+audio can be merged.",
                    )
                # If audio is Opus (or other non-AAC), re-encode to AAC so it plays everywhere
                codec = await _audio_codec_name(single)
                if codec and codec != "aac":
                    reencoded = work_dir / f"reencoded.{_MERGED_EXT}"
                    ok, err = await _reencode_audio_to_aac(single, reencoded)
                    if ok:
                        shutil.copy2(reencoded, out_path)
                        return True, None
                    logger.warning("Re-encode to AAC failed: %s; returning original", err)
                shutil.copy2(single, out_path)
                return True, None

            if (
                "Requested format is not available" in err_text
                or "Only images are available" in err_text
            ):
                return False, (
                    (err_text[:350] + "…")
                    + " Try refreshing cookies/youtube.txt or wait if YouTube is rate-limiting."
                )
            return False, err_text or "yt-dlp produced no output file."
        except Exception as e:
            logger.warning("yt-dlp failed: %s", e)
            return False, str(e)
