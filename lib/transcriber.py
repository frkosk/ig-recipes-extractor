"""Extract audio via ffmpeg, transcribe via mlx-whisper."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


@dataclass
class Transcript:
    text: str
    language: str | None


class TranscribeError(RuntimeError):
    pass


def _extract_audio(video: Path, out: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-q:a", "5",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscribeError(f"ffmpeg failed: {proc.stderr.strip()[:500]}")


def transcribe(video_path: Path, model: str = DEFAULT_MODEL) -> Transcript:
    audio_path = video_path.with_suffix(".mp3")
    if not audio_path.exists():
        _extract_audio(video_path, audio_path)

    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model,
        verbose=False,
    )
    text = (result.get("text") or "").strip()
    lang = result.get("language")
    return Transcript(text=text, language=lang)
