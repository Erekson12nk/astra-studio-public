"""Настройки: одна плоская структура на весь инструмент."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import List

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import asr as _asr

ROOT = Path(__file__).resolve().parent
WORDLISTS = ROOT / "wordlists"


@dataclass
class Settings:
    # --- файлы ---
    video: str = ""
    output: str = ""

    # --- распознавание речи ---
    model: str = _asr.DEFAULT_MODEL
    language: str = "ru"            # "" = определить самой
    device: str = "auto"            # auto|cpu|cuda
    beam_size: int = 5
    vad: bool = True

    # --- что глушить ---
    use_ru: bool = True             # русский мат
    use_en: bool = True             # английская брань
    use_platform: bool = True       # слова, за которые режут показы
    extra_words: List[str] = field(default_factory=list)

    # --- как глушить ---
    mode: str = "silence"           # silence | beep
    beep_hz: int = 1000
    beep_gain: float = 0.30
    pad_before: float = 0.12        # запас до слова, сек (когда глушим целиком)
    pad_after: float = 0.15         # запас после слова, сек (когда глушим целиком)
    merge_gap: float = 0.25         # склеивать заглушки ближе чем...

    # --- слышны ли края слова ---
    keep_edges: bool = True         # глушить только середину: начало и конец слышно
    keep_head: float = 0.07         # сколько слышно в начале слова, сек
    keep_tail: float = 0.07         # сколько слышно в конце слова, сек
    min_mute: float = 0.10          # но заглушить не меньше этого куска, сек

    # --- вывод ---
    abitrate: str = "192k"          # видео не трогаем вообще, только звук

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                              encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "Settings":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in data.items() if k in known})


CONFIG_FILE = ROOT / "settings.json"


def load_saved() -> Settings:
    """Настройки прошлого запуска (если были)."""
    try:
        s = Settings.load(CONFIG_FILE)
        s.video = ""
        s.output = ""
        return s
    except Exception:
        return Settings()


def save_current(s: Settings) -> None:
    """Помним только настройки, пути к файлам каждый раз свои."""
    from dataclasses import replace
    try:
        replace(s, video="", output="").save(CONFIG_FILE)
    except OSError:
        pass
