"""Источники: превью ролика и кадры стрима (yt-dlp + ffmpeg)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yt_dlp

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import ffmpeg as _ff

from .config import ScanConfig

FFMPEG = _ff.find("ffmpeg")


class SourceError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    title: str
    duration: float | None
    is_live: bool
    stream_url: str
    headers: dict
    width: int
    height: int


def _first_entry(info: dict) -> dict:
    while info.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise SourceError("По ссылке не нашлось ни одного видео.")
        info = entries[0]
    return info


def fetch_preview(url: str, timeout: int = 30):
    """Возвращает (BGR-картинка превью, заголовок ролика)."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _first_entry(ydl.sanitize_info(ydl.extract_info(url, download=False)))

    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if info.get("thumbnail"):
        thumbs.append({"url": info["thumbnail"], "width": 0, "height": 0})
    if not thumbs:
        raise SourceError("У этого ролика yt-dlp не отдал ни одного превью.")

    thumbs.sort(key=lambda t: ((t.get("width") or 0) * (t.get("height") or 0),
                               t.get("preference") or 0))
    last_err = None
    for t in reversed(thumbs):
        try:
            req = urllib.request.Request(t["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                buf = np.frombuffer(r.read(), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is not None and img.size:
                return img, (info.get("title") or "")
        except Exception as exc:
            last_err = exc
    raise SourceError(f"Не удалось скачать превью: {last_err}")


def probe_stream(url: str, cfg: ScanConfig) -> MediaInfo:
    """Достаёт прямую ссылку на видеопоток нужного качества."""
    fmt = (f"bv*[height<={cfg.max_height}][vcodec!*=av01]/"
           f"b[height<={cfg.max_height}]/bv*/b")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "noplaylist": True, "format": fmt}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _first_entry(ydl.sanitize_info(ydl.extract_info(url, download=False)))

    fmts = info.get("requested_formats") or ([info] if info.get("url") else [])
    video = next((f for f in fmts if f.get("vcodec", "none") != "none"), None)
    if video is None and fmts:
        video = fmts[0]
    if not video or not video.get("url"):
        raise SourceError("yt-dlp не отдал прямую ссылку на видеопоток.")

    is_live = bool(info.get("is_live"))
    duration = info.get("duration")
    return MediaInfo(
        title=info.get("title") or "",
        duration=None if is_live else (float(duration) if duration else None),
        is_live=is_live,
        stream_url=video["url"],
        headers=video.get("http_headers") or {},
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
    )


def _header_args(headers: dict) -> list:
    args = []
    ua = headers.get("User-Agent")
    if ua:
        args += ["-user_agent", ua]
    rest = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k != "User-Agent")
    if rest:
        args += ["-headers", rest]
    return args


def grab_frame(media: MediaInfo, ts: float, timeout: int = 90):
    """Один кадр на позиции ts секунд (быстрый seek по http)."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "f.jpg"
        cmd = [FFMPEG, "-nostdin", "-loglevel", "error",
               "-rw_timeout", "20000000",
               *_header_args(media.headers),
               "-ss", f"{max(ts, 0):.3f}", "-i", media.stream_url,
               "-frames:v", "1", "-q:v", "2", "-y", str(out)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return None
        if not out.exists() or out.stat().st_size == 0:
            return None
        return cv2.imread(str(out))


def grab_live_frames(media: MediaInfo, cfg: ScanConfig, timeout: int = 300):
    """Для прямого эфира: пишем кусок с живого края и режем его на кадры."""
    with tempfile.TemporaryDirectory() as td:
        pattern = Path(td) / "f_%04d.jpg"
        cmd = [FFMPEG, "-nostdin", "-loglevel", "error",
               *_header_args(media.headers),
               "-i", media.stream_url, "-t", f"{cfg.live_window:.1f}",
               "-vf", f"fps={cfg.live_fps}", "-q:v", "2", "-y", str(pattern)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            pass
        frames = []
        for i, p in enumerate(sorted(Path(td).glob("f_*.jpg"))):
            img = cv2.imread(str(p))
            if img is not None:
                frames.append((i / max(cfg.live_fps, 1e-6), img))
        return frames


def sample_timeline(media: MediaInfo, cfg: ScanConfig) -> list:
    """Равномерная сетка меток времени по стриму."""
    dur = media.duration
    if not dur or dur <= 0:
        return [float(i * 30) for i in range(cfg.coarse_samples)]
    start, end = dur * cfg.skip_head, dur * (1 - cfg.skip_tail)
    n = max(cfg.coarse_samples, 2)
    step = (end - start) / (n - 1)
    return [start + i * step for i in range(n)]


def grab_many(media: MediaInfo, timestamps: list, cfg: ScanConfig, progress=None) -> list:
    """Параллельно тянет кадры по списку меток времени."""
    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        for ts, frame in zip(timestamps, pool.map(lambda t: grab_frame(media, t), timestamps)):
            done += 1
            if progress:
                progress(done, len(timestamps))
            if frame is not None:
                results.append((ts, frame))
    results.sort(key=lambda x: x[0])
    return results
