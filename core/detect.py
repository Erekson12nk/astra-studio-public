from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

ENGINES = {
    "loudness": "по громкости звука — мгновенно, без расшифровки и без модели",
    "heuristic": "по тексту расшифровки, правилами — мгновенно, бесплатно",
    "ollama": "локальная модель через Ollama — бесплатно",
    "openai-compat": "локальный сервер LM Studio / llama.cpp — бесплатно",
    "claude": "Claude API — платно, самое точное качество",
}

TEXT_ENGINES = ("heuristic", "ollama", "openai-compat", "claude")
AUDIO_ENGINES = ("loudness",)


@dataclass
class Moment:
    start: float
    end: float
    score: float = 0.0
    title: str = ""
    hook: str = ""
    reason: str = ""
    engine: str = ""
    peak: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_THINK_OPEN = re.compile(r"<think>.*", re.S | re.I)


def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None

    text = _THINK.sub(" ", text)
    if "<think>" in text.lower():
        text = _THINK_OPEN.sub(" ", text)

    candidates: list = []

    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))

    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break

    candidates.append(text)

    for raw in candidates:
        raw = raw.strip()
        for attempt in (raw, _TRAILING_COMMA.sub(r"\1", raw)):
            try:
                data = json.loads(attempt)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return {"clips": data}
    return None


def coerce_clips(data: Optional[dict]) -> list:
    if not data:
        return []
    items = None
    for key in ("clips", "fragments", "segments", "результат", "items"):
        if isinstance(data.get(key), list):
            items = data[key]
            break
    if items is None:
        items = [data] if "start_sentence" in data else []

    out: list = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "start_sentence" not in item or "end_sentence" not in item:
            continue
        try:
            out.append({
                "start_sentence": int(item["start_sentence"]),
                "end_sentence": int(item["end_sentence"]),
                "title": str(item.get("title", "")).strip(),
                "hook": str(item.get("hook", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
                "score": float(item.get("score", 5.0)),
            })
        except (TypeError, ValueError):
            continue
    return out
