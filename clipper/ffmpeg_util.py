"""Обёртки над ffmpeg и ffprobe.

Водопровод переехал в общий `core.ffmpeg` — здесь остались только вещи,
специфичные для этого конвейера, и прежние имена, чтобы остальной код
не пришлось трогать.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import ffmpeg as _ff

FFmpegMissing = _ff.FFmpegMissing

is_fragmented_mp4 = _ff.is_fragmented_mp4
make_seekable = _ff.make_seekable


def _require(name: str) -> str:
    return _ff.binary(name)


def probe(path: Path) -> dict:
    """Длительность, наличие дорожек, разрешение."""
    info = _ff.probe(path)
    return {
        "duration": info.duration,
        "has_video": info.has_video,
        "has_audio": info.has_audio,
        "width": info.width,
        "height": info.height,
    }


def extract_audio(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    """Вытаскивает моно-дорожку 16 кГц — ровно то, что ест Whisper.

    Час видео превращается в 30-60 МБ вместо нескольких гигабайт. Это же
    свойство делает дешёвым будущее распределение: работнику отправляется
    звук, а не исходник.
    """
    _ff.extract_audio(src, dst, sample_rate, skip_existing=True)
    return dst


def cut_clip(
    src: Path,
    dst: Path,
    start: float,
    end: float,
    *,
    vertical: bool = False,
    subtitles: Path | None = None,
    codec: str = "libx264",
    crf: int = 20,
    preset: str = "medium",
) -> Path:
    """Вырезает фрагмент с перекодированием.

    Перекодируем намеренно: быстрая резка по ключевым кадрам (-c copy)
    промахивается мимо заданной точки на секунды, а мы как раз боремся
    за точность границ.
    """
    exe = _require("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)

    filters: list[str] = []
    if vertical:
        # Заполняем кадр 9:16 по центру. Это грубое кадрирование —
        # слежение за лицом появится позже.
        filters.append(
            "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920:flags=lanczos"
        )
    if subtitles is not None:
        escaped = str(subtitles).replace("\\", "/").replace(":", r"\:")
        filters.append(f"subtitles='{escaped}'")

    cmd = [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(src),
    ]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += [
        "-c:v", codec, "-crf", str(crf), "-preset", preset,
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(dst),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", **_ff._NOWIN)
    if res.returncode != 0:
        raise RuntimeError(f"Не удалось вырезать клип:\n{res.stderr.strip()}")
    return dst
