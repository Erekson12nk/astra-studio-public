"""Уточнение границ реза.

Модель выбирает *место*, а не миллисекунду. Реплики Whisper начинаются и
заканчиваются приблизительно, и рез ровно по ним звучит рвано: то полслова
в начале, то обрубленный хвост фразы.

Человек замечает промах в 200 мс, поэтому границы сдвигаются к ближайшей
настоящей паузе в речи — по пословным тайм-кодам.
"""

from __future__ import annotations

from .config import Config
from .models import Candidate, Transcript, Word


def _flat_words(transcript: Transcript) -> list[Word]:
    words: list[Word] = []
    for s in transcript.sentences:
        words.extend(s.words)
    words.sort(key=lambda w: w.start)
    return words


def _best_start(words: list[Word], target: float, cfg: Config) -> float | None:
    """Ищет начало слова, перед которым была самая заметная пауза."""
    best: tuple[float, float] | None = None  # (вес, время)
    for i, w in enumerate(words):
        if abs(w.start - target) > cfg.pause_search_s:
            continue
        gap = w.start - words[i - 1].end if i > 0 else cfg.min_pause_s * 2
        if gap < cfg.min_pause_s:
            continue
        # Чем длиннее пауза и чем ближе к цели — тем лучше.
        weight = gap - abs(w.start - target) * 0.5
        if best is None or weight > best[0]:
            best = (weight, w.start)
    return best[1] if best else None


def _best_end(words: list[Word], target: float, cfg: Config) -> float | None:
    """Ищет конец слова, после которого начинается самая заметная пауза."""
    best: tuple[float, float] | None = None
    for i, w in enumerate(words):
        if abs(w.end - target) > cfg.pause_search_s:
            continue
        gap = words[i + 1].start - w.end if i + 1 < len(words) else cfg.min_pause_s * 2
        if gap < cfg.min_pause_s:
            continue
        weight = gap - abs(w.end - target) * 0.5
        if best is None or weight > best[0]:
            best = (weight, w.end)
    return best[1] if best else None


def refine(cand: Candidate, transcript: Transcript, cfg: Config,
           *, media_duration: float = 0.0) -> Candidate:
    words = _flat_words(transcript)

    start = cand.start
    end = cand.end

    if words:
        snapped_start = _best_start(words, cand.start, cfg)
        snapped_end = _best_end(words, cand.end, cfg)
        if snapped_start is not None:
            start = snapped_start
        if snapped_end is not None:
            end = snapped_end

    start = max(0.0, start - cfg.pad_start_s)
    end = end + cfg.pad_end_s
    if media_duration:
        end = min(end, media_duration)

    if end - start < 1.0:  # защита от вырожденного случая
        return cand

    cand.features["refined_shift_start"] = round(start - cand.start, 3)
    cand.features["refined_shift_end"] = round(end - cand.end, 3)
    cand.start = start
    cand.end = end
    return cand


def refine_all(cands: list[Candidate], transcript: Transcript, cfg: Config,
               *, media_duration: float = 0.0) -> list[Candidate]:
    return [refine(c, transcript, cfg, media_duration=media_duration) for c in cands]
