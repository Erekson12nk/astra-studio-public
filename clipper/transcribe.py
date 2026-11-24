"""Расшифровка через faster-whisper.

Модель получает только звуковую дорожку — видеоряд Whisper не смотрит.
Результат кешируется: расшифровка это самая долгая стадия, и повторно
гонять её при каждом эксперименте с промптом бессмысленно.

Сама работа с моделью переехала в общий `core.asr`; здесь остались выбор
параметров под этот конвейер и приведение к своим Sentence/Transcript.
Кеш теперь общий и лежит рядом с исходником — благодаря этому уже
расшифрованный audioclean файл повторно не расшифровывается.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import asr as _asr

from .config import Config
from .ffmpeg_util import extract_audio
from .models import Sentence, Transcript, Word

WhisperMissing = _asr.WhisperMissing

_register_cuda_dlls = _asr.register_cuda_dlls


def _load_model(cfg: Config):
    return _asr.load_model(cfg.whisper_model, cfg.whisper_device,
                           cfg.whisper_compute, log=lambda m: print("  " + m))


def _key(cfg: Config, source: Path) -> dict:
    return _asr.cache_key(source, cfg.whisper_model, cfg.language or "",
                          beam_size=5, vad=True)


def transcribe(source: Path, cfg: Config, *, force: bool = False) -> Transcript:
    cfg.ensure_dirs()
    key = _key(cfg, source)

    if not force:
        cached = _asr.load_cache(source, key)
        if cached is not None:
            return _convert(cached)

    wav = extract_audio(source, cfg.audio_dir / f"{source.stem}.wav")

    result = _asr.transcribe(
        wav, _load_model(cfg),
        language=cfg.language,
        word_timestamps=True,       # нужны для привязки границ реза к паузам
        vad=True,                   # отсекает тишину, ускоряет и чистит вывод
        vad_min_silence_ms=400,
    )
    _asr.save_cache(source, key, result)
    return _convert(result)


def _convert(result) -> Transcript:
    sentences = [
        Sentence(idx=s.idx, start=s.start, end=s.end, text=s.text,
                 words=[Word(start=w.start, end=w.end, text=w.text)
                        for w in s.words])
        for s in result.segments
    ]
    return Transcript(sentences=sentences, language=result.language,
                      duration=result.duration)
