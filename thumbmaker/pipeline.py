"""Пайплайн: ссылка на ролик + ссылка на стрим -> готовое превью."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

from .compose import compose
from .config import OUT_DIR, Settings
from .cutout import PersonCutter
from .faces import FaceHit, FaceScorer, rescore
from . import sources


@dataclass
class Candidate:
    hit: FaceHit
    thumb: np.ndarray            # BGR-превьюшка лица для галереи

    @property
    def ts(self) -> float:
        return self.hit.ts

    def timecode(self) -> str:
        s = int(self.hit.ts)
        return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


@dataclass
class Result:
    preview: np.ndarray
    title: str
    candidates: list = field(default_factory=list)
    thumbnail: np.ndarray | None = None


def _face_thumb(hit: FaceHit, size: int = 320) -> np.ndarray:
    x, y, w, h = hit.box
    frame = hit.frame
    fh, fw = frame.shape[:2]
    cx, cy = x + w / 2, y + h / 2
    half = max(w, h) * 0.95
    x0, x1 = int(max(cx - half, 0)), int(min(cx + half, fw))
    y0, y1 = int(max(cy - half * 1.05, 0)), int(min(cy + half * 1.05, fh))
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        crop = frame
    return cv2.resize(crop, (size, int(size * crop.shape[0] / crop.shape[1])))


def load_image(src) -> np.ndarray:
    """Принимает путь, байты или numpy-массив и отдаёт BGR-картинку."""
    if isinstance(src, np.ndarray):
        if src.ndim == 3 and src.shape[2] == 4:
            return cv2.cvtColor(src, cv2.COLOR_RGBA2BGR)
        return src
    if isinstance(src, (bytes, bytearray)):
        img = cv2.imdecode(np.frombuffer(src, np.uint8), cv2.IMREAD_COLOR)
    else:
        path = Path(str(src))
        if not path.exists():
            raise sources.SourceError(f"Файл не найден: {path}")
        img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if img is None or not img.size:
        raise sources.SourceError("Не удалось прочитать изображение — битый или неподдерживаемый файл.")
    return img


def analyze_photo(photo, settings: Settings | None = None) -> Candidate:
    """Находит лицо на фотографии стримера и оценивает его эмоцию."""
    settings = settings or Settings()
    frame = load_image(photo)

    with FaceScorer(settings.face) as scorer:
        hit = scorer.analyze(frame, ts=0.0, keep_frame=True)
        if hit is None:
            # на фото лицо всего одно и выбирать не из чего — не привередничаем
            relaxed = replace(settings.face, min_face_ratio=0.01, max_yaw=0.95,
                              blur_floor=0.0)
            with FaceScorer(relaxed) as soft:
                hit = soft.analyze(frame, ts=0.0, keep_frame=True)
    if hit is None:
        raise sources.SourceError(
            "На фото не нашлось лица. Нужен снимок, где стример смотрит примерно "
            "в камеру и лицо не перекрыто.")

    rescore([hit])
    return Candidate(hit=hit, thumb=_face_thumb(hit))


def scan_stream(stream_url: str, settings: Settings | None = None, top_k: int = 8,
                progress=None) -> list:
    """Ищет в стриме кадры с самой яркой эмоцией. Возвращает список Candidate."""
    settings = settings or Settings()
    cfg = settings.scan

    def say(stage, done=0, total=0):
        if progress:
            progress(stage, done, total)

    say("Запрашиваю поток…")
    media = sources.probe_stream(stream_url, cfg)

    if media.is_live:
        say(f"Прямой эфир — снимаю {int(cfg.live_window)} с с живого края…")
        frames = sources.grab_live_frames(media, cfg)
    else:
        stamps = sources.sample_timeline(media, cfg)
        say(f"Скан {len(stamps)} кадров по хронометражу…", 0, len(stamps))
        frames = sources.grab_many(media, stamps, cfg,
                                   progress=lambda d, t: say("Скачиваю кадры…", d, t))
    if not frames:
        raise sources.SourceError("Не удалось вытащить ни одного кадра из стрима.")

    def score_frames(frames_, face_cfg, stage):
        out = []
        say(stage, 0, len(frames_))
        with FaceScorer(face_cfg) as scorer:
            for i, (ts, frame) in enumerate(frames_, 1):
                hit = scorer.analyze(frame, ts=ts, keep_frame=True)
                if hit:
                    out.append(hit)
                say(stage, i, len(frames_))
        return out

    face_cfg = settings.face
    hits = score_frames(frames, face_cfg, "Ищу лицо и эмоции…")

    if not hits:
        # кадры уже скачаны — прежде чем сдаваться, пробуем мягкие пороги
        relaxed = replace(settings.face,
                          min_face_ratio=settings.face.min_face_ratio * 0.5,
                          max_yaw=min(settings.face.max_yaw * 1.6, 0.95),
                          blur_floor=0.0)
        face_cfg = relaxed
        hits = score_frames(frames, relaxed, "Лица не видно — смягчаю пороги…")

    rescore(hits)

    # плотный до-скан вокруг лучших мест — пик эмоции длится доли секунды
    if hits and not media.is_live and cfg.refine_top > 0:
        peaks = sorted(hits, key=lambda h: h.score, reverse=True)[:cfg.refine_top]
        extra = []
        for pk in peaks:
            step = (2 * cfg.refine_radius) / max(cfg.refine_samples, 1)
            extra += [pk.ts - cfg.refine_radius + step * i
                      for i in range(cfg.refine_samples)]
        extra = [t for t in extra if t > 0]
        say("Уточняю пики эмоций…", 0, len(extra))
        refined = sources.grab_many(media, extra, cfg,
                                    progress=lambda d, t: say("Уточняю пики эмоций…", d, t))
        hits += score_frames(refined, face_cfg, "Уточняю пики эмоций…")
        rescore(hits)

    if not hits:
        raise sources.SourceError(
            "Лицо в стриме не найдено. Попробуйте увеличить число кадров "
            "или ослабить требования (min_face_ratio / max_yaw).")

    hits.sort(key=lambda h: h.score, reverse=True)

    # не берём несколько кадров из одного и того же момента
    picked: list[FaceHit] = []
    for h in hits:
        if all(abs(h.ts - p.ts) > 2.0 for p in picked):
            picked.append(h)
        if len(picked) >= top_k:
            break

    return [Candidate(hit=h, thumb=_face_thumb(h)) for h in picked]


def render(preview: np.ndarray, hit: FaceHit, settings: Settings | None = None) -> np.ndarray:
    """Собирает финальную картинку из превью и выбранного кадра с эмоцией."""
    settings = settings or Settings()
    with PersonCutter() as cutter:
        rgba, face = cutter.cut(hit.frame, hit.box, with_face=True)
    return compose(preview, rgba, settings.compose, face_box=face, turn=hit.turn)


def build(video_url: str, stream_url: str | None = None,
          settings: Settings | None = None, top_k: int = 8,
          photo=None, progress=None) -> Result:
    """Полный цикл: превью ролика + лицо (из фото или из стрима) -> превью.

    Лицо берётся из `photo`, если он передан, иначе сканируется `stream_url`.
    """
    settings = settings or Settings()
    if photo is None and not stream_url:
        raise sources.SourceError("Нужен источник лица: фото стримера или ссылка на стрим.")

    if progress:
        progress("Тяну превью ролика…", 0, 0)
    preview, title = sources.fetch_preview(video_url)

    if photo is not None:
        if progress:
            progress("Ищу лицо на фото…", 0, 0)
        candidates = [analyze_photo(photo, settings)]
    else:
        candidates = scan_stream(stream_url, settings, top_k=top_k, progress=progress)

    if progress:
        progress("Вырезаю стримера и собираю превью…", 0, 0)
    thumb = render(preview, candidates[0].hit, settings)
    return Result(preview=preview, title=title, candidates=candidates, thumbnail=thumb)


def save(image: np.ndarray, name: str | None = None, out_dir: Path | None = None) -> Path:
    """Сохраняет картинку; .png — без потерь, .jpg — почти без них."""
    out_dir = Path(out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = name or f"thumb_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    path = out_dir / name
    if path.suffix.lower() == ".png":
        params = [cv2.IMWRITE_PNG_COMPRESSION, 6]
    else:
        params = [cv2.IMWRITE_JPEG_QUALITY, 97, cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                  cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
    cv2.imwrite(str(path), image, params)
    return path
