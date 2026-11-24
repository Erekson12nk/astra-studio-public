"""Рендер клипов и субтитры."""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .ffmpeg_util import cut_clip
from .models import Candidate, Transcript

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(text: str, fallback: str, limit: int = 60) -> str:
    name = _UNSAFE.sub("", text).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return (name[:limit].strip() or fallback)


def _ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def write_srt(cand: Candidate, transcript: Transcript, dst: Path) -> Path:
    """Субтитры с временем, отсчитанным от начала клипа."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    n = 1
    for s in transcript.sentences:
        if s.end <= cand.start or s.start >= cand.end:
            continue
        start = max(s.start, cand.start) - cand.start
        end = min(s.end, cand.end) - cand.start
        if end - start < 0.2:
            continue
        lines.append(str(n))
        lines.append(f"{_ts(start)} --> {_ts(end)}")
        lines.append(s.text.strip())
        lines.append("")
        n += 1
    dst.write_text("\n".join(lines), encoding="utf-8")
    return dst


def render(
    source: Path,
    cand: Candidate,
    transcript: Transcript,
    cfg: Config,
    *,
    index: int,
    read_from: Path | None = None,
) -> Path:
    """Вырезает клип.

    `source` задаёт ИМЯ папки с результатами, `read_from` — откуда реально
    читать. Они расходятся, когда для быстрой перемотки используется
    переложенная копия: клипы всё равно должны лечь в папку с именем
    исходного файла, а не «500.seekable».
    """
    cfg.ensure_dirs()
    media = read_from or source
    stem = f"{index:02d}_{cand.score:.1f}_{safe_name(cand.title, f'clip{index}')}"
    dst = cfg.out_dir / source.stem / f"{stem}.mp4"

    subs: Path | None = None
    if cfg.burn_subs:
        subs = write_srt(cand, transcript, cfg.cache_dir / f"{source.stem}_{index:02d}.srt")

    cut_clip(
        media, dst, cand.start, cand.end,
        vertical=cfg.vertical,
        subtitles=subs,
        codec=cfg.video_codec,
        crf=cfg.crf,
        preset=cfg.preset,
    )
    return dst
