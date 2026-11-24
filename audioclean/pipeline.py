"""Связка: достать звук -> распознать речь -> найти слова -> заглушить."""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import asr as _asr
from core import handoff as _hand

from . import ffmpeg as FF
from . import render as R
from . import transcribe as TR
from .config import Settings
from .profanity import Hit, Matcher, hits_to_spans

ASR_SR = 16000
Span = Tuple[float, float]


@dataclass
class Found:
    """Одно найденное слово: что, когда, по какому правилу."""
    start: float
    end: float
    text: str
    rule: str
    enabled: bool = True
    context: str = ""
    prob: float = 1.0   # уверенность распознавалки; 0 = время прикинуто на глаз


@dataclass
class Result:
    info: FF.MediaInfo
    segments: List[TR.Segment] = field(default_factory=list)
    words: List[TR.Word] = field(default_factory=list)
    found: List[Found] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.info.duration


# ---------------------------------------------------------------- кэш текста
# Кэш общий с остальными программами репозитория: файл рядом с видео,
# формат и сверка ключа — в core.asr. Раньше сверялись только модель и
# размер файла; язык не проверялся, и на общем файле это стало опасно —
# можно было получить чужую расшифровку на другом языке.
cache_path = _asr.cache_path


def _key(s: Settings) -> dict:
    return _asr.cache_key(s.video, s.model, s.language, s.beam_size, s.vad)


def _save_cache(s: Settings, segments: List[TR.Segment]) -> None:
    _asr.save_cache(s.video, _key(s), _asr.Transcript(
        segments=[_asr.Segment(idx=i, start=g.start, end=g.end, text=g.text,
                               words=[_asr.Word(w.start, w.end, w.text, w.prob)
                                      for w in g.words])
                  for i, g in enumerate(segments)]))


def _to_segments(result) -> List[TR.Segment]:
    return [TR.Segment(g.start, g.end, g.text,
                       [TR.Word(w.start, w.end, w.text, w.prob) for w in g.words])
            for g in result.segments]


def _load_cache(s: Settings) -> Optional[List[TR.Segment]]:
    result = _asr.load_cache(s.video, _key(s))
    if result is None:
        return None
    return _to_segments(result)


def _load_from_origin(s: Settings) -> Optional[List[TR.Segment]]:
    origin = _hand.read_origin(s.video)
    if origin is None:
        return None
    key = _asr.cache_key(origin["source"], s.model, s.language, s.beam_size, s.vad)
    whole = _asr.load_cache(origin["source"], key)
    if whole is None:
        return None
    piece = _asr.slice_transcript(whole, origin["start"], origin["end"])
    if not piece.segments:
        return None
    return _to_segments(piece)


class Progress:
    """Разбивает общий прогресс 0..1 на этапы."""

    def __init__(self, cb: Optional[Callable[[float, str], None]]):
        self.cb, self.base, self.span, self.stage = cb, 0.0, 1.0, ""

    def stage_start(self, base: float, span: float, name: str) -> None:
        self.base, self.span, self.stage = base, span, name
        self(0.0, "")

    def __call__(self, p: float, note: str = "") -> None:
        if self.cb:
            self.cb(min(1.0, self.base + self.span * max(0.0, min(1.0, p))),
                    self.stage + ((" " + note) if note else ""))


# ------------------------------------------------------------------- анализ
def analyze(s: Settings,
            progress: Optional[Callable[[float, str], None]] = None,
            log: Optional[Callable[[str], None]] = None,
            cancel: Optional[Callable[[], bool]] = None,
            use_cache: bool = True) -> Result:
    """Распознаёт речь и ищет запрещённые слова."""
    log = log or (lambda m: None)
    pg = Progress(progress)

    if not s.video or not Path(s.video).exists():
        raise FileNotFoundError("Не выбран файл")
    info = FF.probe(s.video)
    if not info.has_audio:
        raise ValueError("В файле нет звуковой дорожки")
    log("Файл: %s, звук %s%s" % (hms(info.duration), info.acodec,
                                 (", видео %dx%d %s" % (info.width, info.height, info.vcodec))
                                 if info.has_video else " (без видео)"))
    res = Result(info=info)

    tmpdir = tempfile.mkdtemp(prefix="audioclean_")
    wav = str(Path(tmpdir) / "audio.wav")
    try:
        pg.stage_start(0.0, 0.10, "Достаю звук...")
        FF.extract_audio(s.video, wav, ASR_SR, progress=pg, total=info.duration)
        if cancel and cancel():
            raise RuntimeError("Отменено")

        cached = _load_cache(s) if use_cache else None
        from_origin = False
        if cached is None and use_cache:
            cached = _load_from_origin(s)
            from_origin = cached is not None
        if cached:
            if from_origin:
                log("Беру расшифровку исходного стрима и режу по границам "
                    "ролика - распознавать заново не нужно.")
            else:
                log("Беру распознанный текст из кэша (рядом с видео).")
            res.segments = cached
            pg.stage_start(0.10, 0.85, "Текст из кэша")
            pg(1.0)
        else:
            pg.stage_start(0.10, 0.85, "Распознаю речь...")
            res.segments = TR.transcribe(wav, s, info.duration,
                                         progress=pg, log=log, cancel=cancel)
            _save_cache(s, res.segments)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    res.words = TR.all_words(res.segments)
    log("Распознано слов: %d" % len(res.words))

    pg.stage_start(0.95, 0.05, "Проверяю по словарям...")
    res.found = find_words(res, s)
    pg(1.0)
    log("Найдено запрещённых слов: %d" % len(res.found))
    pg.stage_start(1.0, 0.0, "Готово")
    return res


def find_words(res: Result, s: Settings) -> List[Found]:
    """Ищет слова из словарей; контекст берём из соседних слов."""
    matcher = Matcher(s)
    hits: List[Hit] = matcher.scan(res.words)
    index = {round(w.start, 3): i for i, w in enumerate(res.words)}
    out: List[Found] = []
    for h in hits:
        i = index.get(round(h.start, 3))
        if i is None:
            ctx = h.text
        else:
            lo, hi = max(0, i - 4), min(len(res.words), i + 5)
            ctx = " ".join(w.text for w in res.words[lo:hi])
        out.append(Found(h.start, h.end, h.text, h.rule, True, ctx, h.prob))
    return out


def spans_of(found: Sequence[Found], s: Settings, duration: float) -> List[Span]:
    """Включённые находки -> интервалы для заглушки (с запасом и склейкой)."""
    hits = [Hit(f.start, f.end, f.text, f.rule, f.prob) for f in found if f.enabled]
    return [(a, b) for a, b, _ in hits_to_spans(hits, s, duration)]


# ------------------------------------------------------------------- рендер
def clean(s: Settings, res: Result, out_path: str = "",
          progress: Optional[Callable[[float, str], None]] = None,
          log: Optional[Callable[[str], None]] = None,
          cancel: Optional[Callable[[], bool]] = None) -> str:
    """Пишет очищенный файл."""
    log = log or (lambda m: None)
    out_path = out_path or s.output or default_output(s.video)
    if Path(out_path).resolve() == Path(s.video).resolve():
        raise ValueError("Итоговый файл совпадает с исходным, выберите другое имя")
    spans = spans_of(res.found, s, res.duration)
    total = sum(b - a for a, b in spans)
    log("Заглушаю %d участков, всего %.1f с из %s"
        % (len(spans), total, hms(res.duration)))
    R.clean_audio(s, res.info, spans, out_path, progress=progress, log=log, cancel=cancel)
    return out_path


def default_output(video: str) -> str:
    p = Path(video)
    return str(p.with_name(p.stem + "_clean" + (p.suffix or ".mp4")))


def report(res: Result, s: Settings) -> str:
    """Текстовый отчёт: что и когда заглушено."""
    lines = ["Файл: %s" % s.video,
             "Длительность: %s, найдено слов: %d" % (hms(res.duration), len(res.found)), ""]
    for f in res.found:
        lines.append("%s  %-16s %-12s %-5s %s"
                     % (hms(f.start), f.text[:16], "(" + f.rule + ")",
                        "~" if f.prob <= 0 else "%.2f" % f.prob,
                        "" if f.enabled else "— выключено"))
    return "\n".join(lines)


def hms(t: float) -> str:
    t = max(0.0, float(t))
    h, r = divmod(int(t), 3600)
    m, sec = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, sec)
