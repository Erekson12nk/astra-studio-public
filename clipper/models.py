"""Структуры данных конвейера.

`Task` — единица работы. Она намеренно самодостаточна: содержит всё, что нужно
для обработки одного исходника, и не ссылается на глобальное состояние. Это та
самая граница, по которой позже втыкается распределение между машинами —
работнику достаточно передать сериализованный Task.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Word:
    """Слово с тайм-кодом. Нужно для уточнения границ реза по паузам."""
    start: float
    end: float
    text: str


@dataclass
class Sentence:
    """Реплика с тайм-кодами — базовая единица, которой оперирует модель."""
    idx: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Candidate:
    """Предложенный фрагмент до уточнения границ и рендера."""
    start: float
    end: float
    title: str
    reason: str
    score: float
    hook: str = ""
    start_sentence: int = -1
    end_sentence: int = -1

    # Механические признаки. В фазе 1 они только пишутся в базу —
    # это будущий обучающий датасет для реранкера.
    features: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlap_ratio(self, other: "Candidate") -> float:
        """IoU по времени — для отсева дублей на стыках окон."""
        inter = max(0.0, min(self.end, other.end) - max(self.start, other.start))
        if inter <= 0:
            return 0.0
        union = max(self.end, other.end) - min(self.start, other.start)
        return inter / union if union > 0 else 0.0


@dataclass
class Task:
    """Самодостаточная единица работы — граница для будущего распределения."""
    source: Path
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_json(self) -> str:
        d = asdict(self)
        d["source"] = str(self.source)
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Task":
        d = json.loads(raw)
        d["source"] = Path(d["source"])
        return cls(**d)


@dataclass
class Transcript:
    sentences: list[Sentence]
    language: str = ""
    duration: float = 0.0

    def text_window(self, lo: int, hi: int) -> str:
        """Пронумерованные реплики для подачи в модель.

        Номера — это якоря: модель возвращает диапазоны номеров, а тайм-коды
        мы подставляем сами. Так модель не может выдумать несуществующее время.
        """
        return "\n".join(
            f"[{s.idx}] {s.text.strip()}"
            for s in self.sentences[lo:hi]
            if s.text.strip()
        )
