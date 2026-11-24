"""Тонкая обёртка над ffmpeg / ffprobe / ffplay.

Всё содержимое переехало в общий `core.ffmpeg`; здесь остались прежние
имена, чтобы `pipeline`, `render`, `cli` и `gui` не пришлось трогать.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import ffmpeg as _ff

FFMPEG = _ff.find("ffmpeg")
FFPROBE = _ff.find("ffprobe")
FFPLAY = _ff.find("ffplay")

_NOWIN = _ff._NOWIN

FFmpegError = _ff.FFmpegError
MediaInfo = _ff.MediaInfo

have_ffmpeg = _ff.have_ffmpeg
probe = _ff.probe
play_range = _ff.play_range

_parse_fps = _ff.parse_fps
_q = _ff.quote
_hms = _ff.format_hms


def run(cmd: list[str], total: float = 0.0,
        progress: Optional[Callable[[float, str], None]] = None,
        log: Optional[Callable[[str], None]] = None,
        cancel: Optional[Callable[[], bool]] = None) -> None:
    """Запускает ffmpeg с -progress и отдаёт долю выполнения 0..1."""
    _ff.run_progress(cmd, total=total, progress=progress, log=log, cancel=cancel)


def extract_audio(src, dst, sr: int = 16000,
                  progress: Optional[Callable[[float, str], None]] = None,
                  log: Optional[Callable[[str], None]] = None,
                  total: float = 0.0) -> str:
    """Достаёт моно-WAV нужной частоты (для распознавания и анализа)."""
    return _ff.extract_audio(src, dst, sr, progress=progress, log=log, total=total)
