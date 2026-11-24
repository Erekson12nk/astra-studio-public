"""Конфигурация конвейера.

Значения по умолчанию подобраны под конкретное железо:
RTX 3050 8 ГБ (свободно ~5.5-7 ГБ), 16 ГБ ОЗУ, NVMe на C:, SMR-диск на D:.
Всё переопределяется переменными окружения или флагами CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Config:
    # --- Пути -------------------------------------------------------------
    # Рабочие файлы держим на NVMe. D: — это Storage Spaces поверх SMR-диска,
    # он даёт многосекундные залипания на потоковой записи, а мы пишем много.
    work_dir: Path = field(default_factory=lambda: _env_path("CLIPPER_WORK", r"C:\clipper-work"))
    out_dir: Path = field(default_factory=lambda: _env_path("CLIPPER_OUT", r"C:\clipper-work\out"))

    db_file: Path | None = None
    rerank_file: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.db_file or (self.work_dir / "clipper.sqlite3")

    @property
    def rerank_path(self) -> Path:
        return self.rerank_file or (self.work_dir / "rerank.json")

    @property
    def audio_dir(self) -> Path:
        return self.work_dir / "audio"

    @property
    def cache_dir(self) -> Path:
        return self.work_dir / "cache"

    # --- Движок оценки ----------------------------------------------------
    # По умолчанию бесплатный и без установки чего-либо.
    #   heuristic      правила по тексту, без модели. Мгновенно, бесплатно, слабо.
    #   ollama         локальная модель. Бесплатно, нужна установка Ollama.
    #   openai-compat  LM Studio / llama.cpp. Бесплатно.
    #   claude         Claude API. Платно, но точнее всех.
    backend: str = field(default_factory=lambda: _env_str("CLIPPER_BACKEND", "heuristic"))

    # --- Локальные движки -------------------------------------------------
    local_model: str = field(
        default_factory=lambda: _env_str("CLIPPER_LOCAL_MODEL", "qwen3:8b")
    )
    ollama_host: str = field(
        default_factory=lambda: _env_str("OLLAMA_HOST", "http://localhost:11434")
    )
    openai_base_url: str = field(
        default_factory=lambda: _env_str("CLIPPER_OPENAI_URL", "http://localhost:1234")
    )
    openai_api_key: str = field(default_factory=lambda: _env_str("CLIPPER_OPENAI_KEY", ""))
    openai_max_tokens: int = field(
        default_factory=lambda: _env_int("CLIPPER_MAX_TOKENS", 8192))
    openai_think: bool = field(
        default_factory=lambda: _env_str("CLIPPER_THINK", "0")
        not in ("0", "no", "нет", "off", ""))
    # Без явного контекста Ollama молча обрезает расшифровку. 8192 токенов
    # хватает на окно в 4 минуты и влезает в 8 ГБ видеопамяти вместе с моделью 7B.
    local_num_ctx: int = field(default_factory=lambda: _env_int("CLIPPER_NUM_CTX", 8192))
    local_timeout: float = field(default_factory=lambda: _env_float("CLIPPER_TIMEOUT", 600.0))

    # --- Сравнение фрагментов между собой ---------------------------------
    # Оценка от 0 до 10 остаётся: она отсеивает мусор порогом min_score.
    # Порядок среди выживших даёт сравнение — относительное суждение у
    # небольших моделей устойчивее абсолютного.
    compare: bool = field(
        default_factory=lambda: _env_str("CLIPPER_COMPARE", "1")
        not in ("0", "no", "нет", "off"))
    compare_group: int = field(default_factory=lambda: _env_int("CLIPPER_COMPARE_GROUP", 6))
    compare_rounds: int = field(default_factory=lambda: _env_int("CLIPPER_COMPARE_ROUNDS", 2))
    compare_max: int = field(default_factory=lambda: _env_int("CLIPPER_COMPARE_MAX", 60))
    compare_chars: int = field(default_factory=lambda: _env_int("CLIPPER_COMPARE_CHARS", 450))
    compare_seed: int = field(default_factory=lambda: _env_int("CLIPPER_COMPARE_SEED", 20260909))

    # --- Claude API -------------------------------------------------------
    #   claude-opus-5    $5/$25 за 1M токенов — по умолчанию
    #   claude-sonnet-5  $2/$10               — вдвое дешевле
    #   claude-haiku-4-5 $1/$5                — самый дешёвый, заметно слабее
    model: str = field(default_factory=lambda: _env_str("CLIPPER_MODEL", "claude-opus-5"))
    effort: str = field(default_factory=lambda: _env_str("CLIPPER_EFFORT", "medium"))
    max_tokens: int = 16000

    # --- Расшифровка ------------------------------------------------------
    # large-v3-turbo: то же качество, что large-v3, но заметно быстрее.
    # В int8 занимает ~1.5-2 ГБ VRAM. Если не хватает — "medium"/"small".
    whisper_model: str = field(
        default_factory=lambda: _env_str("CLIPPER_WHISPER", "large-v3-turbo"))
    whisper_compute: str = field(default_factory=lambda: _env_str("CLIPPER_COMPUTE", "int8_float16"))
    whisper_device: str = field(default_factory=lambda: _env_str("CLIPPER_DEVICE", "auto"))
    language: str | None = field(default_factory=lambda: _env_str("CLIPPER_LANG", "ru") or None)

    # --- Кандидаты --------------------------------------------------------
    min_clip_s: float = field(default_factory=lambda: _env_float("CLIPPER_MIN", 20.0))
    max_clip_s: float = field(default_factory=lambda: _env_float("CLIPPER_MAX", 90.0))

    # 0 = выбрать автоматически под движок: облачной модели окно побольше,
    # локальной поменьше, чтобы влезало в контекст и не рассеивалось внимание.
    chunk_minutes: float = field(default_factory=lambda: _env_float("CLIPPER_CHUNK", 0.0))
    chunk_overlap_sentences: int = 3

    dedupe_iou: float = 0.5

    # Ниже этого движок сам признал фрагмент слабым — рендерить его не нужно.
    # В базу он всё равно попадёт: отрицательные примеры тоже нужны для обучения.
    min_score: float = field(default_factory=lambda: _env_float("CLIPPER_MIN_SCORE", 5.0))

    # --- Уточнение границ -------------------------------------------------
    # Человек замечает промах в 200 мс, поэтому границы двигаем к паузам в речи.
    pause_search_s: float = 1.5
    min_pause_s: float = 0.25
    pad_start_s: float = 0.15
    pad_end_s: float = 0.35

    # --- Рендер -----------------------------------------------------------
    vertical: bool = False
    burn_subs: bool = False
    # Перекладывать фрагментированный контейнер перед нарезкой. Стоит один
    # проход по файлу, экономит ~95 сек на каждом клипе. Требует места на диске
    # под копию исходника.
    remux_fragmented: bool = field(
        default_factory=lambda: _env_str("CLIPPER_REMUX", "1") not in ("0", "false", "no"))
    top_n: int = 10
    taste_rules: str = ""
    # Обученная модель вкуса. None — порядок как выдал движок.
    rerank: dict | None = None
    video_codec: str = "libx264"
    crf: int = 20
    preset: str = "medium"

    def ensure_dirs(self) -> None:
        for p in (self.work_dir, self.out_dir, self.audio_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)


def with_profile(cfg: "Config | None" = None) -> "Config":
    cfg = cfg if cfg is not None else Config()
    try:
        from core import profiles as _pro

        profile = _pro.active()
    except Exception:
        profile = None
    if not profile:
        return cfg
    if "CLIPPER_OUT" not in os.environ:
        cfg.out_dir = _pro.clips_dir(profile)
    cfg.db_file = _pro.db_path(profile)
    cfg.rerank_file = _pro.rerank_path(profile)
    try:
        from core import taste as _taste

        cfg.taste_rules = _taste.rules_text(profile)
    except Exception:
        cfg.taste_rules = ""
    return cfg


DEFAULT = Config()
