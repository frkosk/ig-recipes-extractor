"""yt-dlp wrapper: download video + metadata + thumbnail for one IG URL."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DOWNLOAD_TIMEOUT_SECONDS = 300

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")
IMAGE_PRIORITY = (".jpg", ".jpeg", ".png", ".webp")


def _resolve_cookies_file(raw: str) -> str:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p)


@dataclass
class DownloadResult:
    video_path: Path | None
    thumbnail_path: Path | None
    caption: str
    uploader: str


class DownloadError(RuntimeError):
    pass


def download(url: str, work_root: Path, post_id: str) -> DownloadResult:
    work_dir = work_root / post_id
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-progress",
        "--no-warnings",
        "--write-info-json",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "-o", str(work_dir / "media.%(ext)s"),
    ]
    if browser := os.environ.get("IG_COOKIES_FROM_BROWSER"):
        cmd += ["--cookies-from-browser", browser]
    elif cookies_file := os.environ.get("IG_COOKIES_FILE"):
        cmd += ["--cookies", _resolve_cookies_file(cookies_file)]
    cmd.append(url)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise DownloadError(f"yt-dlp timed out after {DOWNLOAD_TIMEOUT_SECONDS}s")
    if proc.returncode != 0:
        raise DownloadError(f"yt-dlp failed ({proc.returncode}): {proc.stderr.strip()[:500]}")

    info_files = list(work_dir.glob("*.info.json"))
    if not info_files:
        raise DownloadError("yt-dlp produced no info.json")
    with info_files[0].open() as f:
        info = json.load(f)

    video_path = next(
        (p for p in sorted(work_dir.iterdir()) if p.suffix.lower() in VIDEO_EXTS),
        None,
    )
    images = [p for p in work_dir.iterdir() if p.suffix.lower() in IMAGE_PRIORITY]
    images.sort(key=lambda p: (IMAGE_PRIORITY.index(p.suffix.lower()), p.name))
    thumb_path = images[0] if images else None

    caption = info.get("description") or info.get("title") or ""
    uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id") or ""

    return DownloadResult(
        video_path=video_path,
        thumbnail_path=thumb_path,
        caption=caption,
        uploader=uploader,
    )
