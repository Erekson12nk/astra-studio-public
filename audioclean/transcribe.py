"""Распознавание речи с таймкодами слов (faster-whisper).

Работа с моделью переехала в общий `core.asr`; здесь остались прежние
имена и своя нормализация слов, чтобы `pipeline`, `gui` и `cli` не трогать.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, List, Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import asr as _asr

from .config import Settings


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)


cuda_usable = _asr.cuda_usable


def pick_device(cfg: Settings) -> tuple[str, str]:
    """Выбор устройства и точности: cuda, если реально доступна, иначе cpu."""
    device = cfg.device
    if device == "auto":
        device = "cuda" if cuda_usable() else "cpu"
    return device, ("float16" if device == "cuda" else "int8")


def load_model(cfg: Settings, log: Optional[Callable[[str], None]] = None):
    device, ctype = pick_device(cfg)
    return _asr.load_model(cfg.model, device, ctype, log=log)


def transcribe(wav: str | Path, cfg: Settings, duration: float = 0.0,
               progress: Optional[Callable[[float, str], None]] = None,
               log: Optional[Callable[[str], None]] = None,
               cancel: Optional[Callable[[], bool]] = None) -> List[Segment]:
    """Возвращает сегменты с пословными таймкодами."""
    model = load_model(cfg, log)
    try:
        return _run(model, wav, cfg, duration, progress, log, cancel)
    except Exception as e:
        msg = str(e)
        cuda_broken = any(k in msg.lower() for k in
                          ("cublas", "cudnn", "cuda", "gpu", "device"))
        if not cuda_broken or pick_device(cfg)[0] != "cuda":
            raise
        if log:
            log("Ошибка CUDA (%s). Повторяю на процессоре." % msg[:120])
        cpu_cfg = replace(cfg, device="cpu")
        return _run(load_model(cpu_cfg, log), wav, cpu_cfg, duration, progress, log, cancel)


def _run(model, wav, cfg: Settings, duration, progress, log, cancel) -> List[Segment]:
    result = _asr.transcribe(
        wav, model,
        language=cfg.language,
        beam_size=cfg.beam_size,
        vad=cfg.vad,
        vad_min_silence_ms=400,
        condition_on_previous_text=False,
        duration=duration,
        progress=progress, log=log, cancel=cancel,
    )
    out: List[Segment] = []
    for s in result.segments:
        words = [Word(w.start, w.end, w.text.strip(), w.prob)
                 for w in s.words if w.text and w.text.strip()]
        out.append(Segment(s.start, s.end, (s.text or "").strip(), words))
    return out


def split_segment(s: Segment) -> List[Word]:
    """Сегмент без пословных таймкодов: раскладываем текст по длине слов.

    Время получается приблизительное, поэтому prob=0 — по нему в таблице видно,
    что границы прикинуты, а не измерены.
    """
    core_words = _asr.split_segment(
        _asr.Segment(idx=0, start=s.start, end=s.end, text=s.text))
    return [Word(w.start, w.end, w.text, w.prob) for w in core_words]


def all_words(segments: List[Segment]) -> List[Word]:
    out: List[Word] = []
    for s in segments:
        # изредка выравнивание не отдаёт слова — тогда лучше грубые границы,
        # чем совсем пропустить кусок речи
        out.extend(s.words or split_segment(s))
    return out


def segments_to_dict(segments: List[Segment]) -> list:
    return [{"start": s.start, "end": s.end, "text": s.text,
             "words": [{"start": w.start, "end": w.end, "text": w.text, "prob": w.prob}
                       for w in s.words]} for s in segments]


def segments_from_dict(data: list) -> List[Segment]:
    return [Segment(d["start"], d["end"], d.get("text", ""),
                    [Word(w["start"], w["end"], w["text"], w.get("prob", 1.0))
                     for w in d.get("words", [])]) for d in data]


def to_srt(segments: List[Segment]) -> str:
    return _asr.to_srt([_asr.Segment(idx=i, start=s.start, end=s.end, text=s.text)
                        for i, s in enumerate(segments)])
