from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, List, Sequence

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import censor as _cen

from .config import WORDLISTS, Settings

PREFIXES = _cen.PREFIXES
ENDINGS = _cen.ENDINGS
GLUE_MAX_GAP = _cen.GLUE_MAX_GAP
GLUE_MAX_PART = _cen.GLUE_MAX_PART
GLUE_MIN_LEN = _cen.GLUE_MIN_LEN

Hit = _cen.Hit
Rule = _cen.Rule

_read_list = _cen.read_wordlist
_prob = _cen._prob


class Matcher(_cen.Matcher):
    def __init__(self, cfg: Settings, wordlists: Path | None = None):
        folder = Path(wordlists or WORDLISTS)
        raw: List[str] = []
        if cfg.use_ru:
            raw += _read_list(folder / "ru_profanity.txt")
        if cfg.use_en:
            raw += _read_list(folder / "en_profanity.txt")
        if cfg.use_platform:
            raw += _read_list(folder / "platform_words.txt")
        raw += [w for w in cfg.extra_words if w.strip()]
        super().__init__(raw, _read_list(folder / "whitelist.txt"))


def core_span(h: Hit, cfg: Settings) -> tuple:
    return _cen.core_span(h, cfg.keep_head, cfg.keep_tail, cfg.min_mute)


def hits_to_spans(hits: Iterable[Hit], cfg: Settings, duration: float) -> List[tuple]:
    return _cen.hits_to_spans(hits, duration, cfg.keep_edges, cfg.keep_head,
                              cfg.keep_tail, cfg.min_mute, cfg.pad_before,
                              cfg.pad_after, cfg.merge_gap)
