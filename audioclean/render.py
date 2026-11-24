"""Сборка итогового файла: видео копируется как есть, меняется только звук."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from . import ffmpeg as FF
from .config import Settings

Span = Tuple[float, float]

ANORM = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
# ffmpeg не переваривает выражения глубже ~100 сложений, поэтому режем на куски
EXPR_CHUNK = 32
_SLASH: Optional[bool] = None


def supports_slash_filter() -> bool:
    """ffmpeg >= 7: файл графа передаётся как -/filter_complex file."""
    global _SLASH
    if _SLASH is None:
        try:
            out = subprocess.run([FF.FFMPEG, "-hide_banner", "-h", "full"],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", **FF._NOWIN)
            _SLASH = "filter_complex_script" not in (out.stdout or "")
        except Exception:
            _SLASH = True
    return _SLASH


def _expr(spans: Sequence[Span]) -> str:
    return "+".join("between(t,%.3f,%.3f)" % (a, b) for a, b in spans)


def _chunks(spans: Sequence[Span], size: int = EXPR_CHUNK) -> List[List[Span]]:
    return [list(spans[i:i + size]) for i in range(0, len(spans), size)]


def build_graph(s: Settings, spans: Sequence[Span], duration: float) -> Tuple[str, bool]:
    """Фильтрограф для звука. Возвращает (граф, нужен ли генератор писка)."""
    spans = sorted(spans)
    src = "[0:a]"
    lines: List[str] = []
    # цепочка: каждый следующий volume=0 добавляет свои участки
    for i, part in enumerate(_chunks(spans)):
        dst = "[m%d]" % i
        lines.append("%svolume=0:enable='%s'%s" % (src, _expr(part), dst))
        src = dst
    if not lines:
        lines.append("[0:a]anull[m0]")
        src = "[m0]"

    if s.mode == "beep" and spans:
        parts = _chunks(spans)
        lines.append("%s%s[muted]" % (src, ANORM))
        lines.append("[1:a]%s,asplit=%d%s" % (ANORM, len(parts),
                                              "".join("[t%d]" % k for k in range(len(parts)))))
        for k, part in enumerate(parts):
            lines.append("[t%d]volume=0:enable='not(%s)',volume=%.3f[tone%d]"
                         % (k, _expr(part), s.beep_gain, k))
        lines.append("[muted]%samix=inputs=%d:duration=first:normalize=0[aout]"
                     % ("".join("[tone%d]" % k for k in range(len(parts))), len(parts) + 1))
        return ";\n".join(lines), True

    lines.append("%s%s[aout]" % (src, ANORM))
    return ";\n".join(lines), False


def audio_args(out_path: str, abitrate: str) -> List[str]:
    """Кодек звука под контейнер: aac не лезет ни в mp3, ни в webm, а .wav ждёт PCM."""
    ext = Path(out_path).suffix.lower()
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", abitrate]
    if ext == ".wav":
        return ["-c:a", "pcm_s16le"]
    if ext == ".flac":
        return ["-c:a", "flac"]
    if ext in (".webm", ".ogg", ".opus"):
        return ["-c:a", "libopus", "-b:a", abitrate]
    return ["-c:a", "aac", "-b:a", abitrate]


def clean_audio(s: Settings, info: FF.MediaInfo, spans: Sequence[Span], out_path: str,
                progress: Optional[Callable[[float, str], None]] = None,
                log: Optional[Callable[[str], None]] = None,
                cancel: Optional[Callable[[], bool]] = None) -> str:
    """Пишет копию файла с заглушёнными словами. Видео не перекодируется."""
    if not info.has_audio:
        raise ValueError("В файле нет звуковой дорожки — глушить нечего")
    graph, need_tone = build_graph(s, spans, info.duration)

    inputs = ["-i", s.video]
    if need_tone:
        inputs += ["-f", "lavfi", "-t", "%.3f" % info.duration,
                   "-i", "sine=frequency=%d:sample_rate=48000" % s.beep_hz]

    maps = ["-map", "[aout]"]
    if info.has_video:
        maps = ["-map", "0:v", "-c:v", "copy"] + maps
    cmd = ["-y"] + inputs + maps + audio_args(out_path, s.abitrate) + [
        "-ar", "48000", "-ac", "2", out_path]

    fd, gpath = tempfile.mkstemp(suffix=".ffgraph", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(graph)
    flag = "-/filter_complex" if supports_slash_filter() else "-filter_complex_script"
    full = cmd[:1] + [flag, gpath] + cmd[1:]
    if log:
        log("Глушу участков: %d (%s). Видео копируется без пережатия."
            % (len(spans), "писк" if need_tone else "тишина"))
    try:
        FF.run(full, total=info.duration, progress=progress, log=log, cancel=cancel)
    finally:
        try:
            os.remove(gpath)
        except OSError:
            pass
    return out_path
