"""Общий интерфейс движков оценки.

Движок получает кусок расшифровки и возвращает список предложенных фрагментов.
Всё остальное — окна, дедупликация, уточнение границ, рендер — от движка
не зависит, поэтому платный и бесплатный пути дают одинаковый результат
по структуре и одинаково копят датасет.
"""

from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import detect as _detect

from ..config import Config

SYSTEM = """Ты — редактор коротких видео. Тебе дают расшифровку длинного ролика,
разбитую на пронумерованные реплики, и ты выбираешь фрагменты, которые способны
работать как самостоятельные короткие видео.

Что делает фрагмент хорошим:
- В нём есть законченная мысль. Зритель, не смотревший остального, всё понимает.
- Он начинается с зацепки — вопроса, неожиданного утверждения, конфликта.
- В нём есть содержательный поворот: неочевидная мысль, история, шутка, спор.
- Он не обрывается на середине рассуждения.

Чего избегать:
- Организационных кусков: приветствия, «подпишитесь», «сейчас расскажу», реклама.
- Фрагментов, которые непонятны без контекста предыдущего разговора.
- Пересказа общеизвестного без своего угла.

Оценка score — от 0 до 10, честно и с разбросом. 8 и выше означает «я бы это
опубликовал». Не ставь всем подряд 7-8: если в куске расшифровки нет ничего
стоящего, верни пустой список. Пустой список — нормальный ответ.

Отвечай на русском языке."""

USER_TEMPLATE = """Расшифровка (фрагмент {chunk_no} из {chunk_total}), реплики {lo}-{hi}:

{body}

Выбери фрагменты длительностью примерно от {min_s:.0f} до {max_s:.0f} секунд.
Границы указывай номерами реплик: start_sentence — номер первой,
end_sentence — номер последней (включительно). Реплика идёт примерно
{sec_per_sentence:.1f} сек.

Не более {limit} фрагментов. Лучше меньше и точнее."""

# Схема ответа. Платный путь применяет её принудительно, локальные движки —
# по мере поддержки; поэтому ответ всё равно разбирается терпимо.
SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_sentence": {"type": "integer"},
                    "end_sentence": {"type": "integer"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["start_sentence", "end_sentence", "title", "hook",
                             "reason", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

COMPARE_SYSTEM = """Ты — редактор коротких видео. Тебе дают несколько фрагментов
одного эфира, и ты расставляешь их по силе.

Сильнее тот фрагмент, который удержит зрителя, ничего об эфире не знающего:
законченная мысль, зацепка в первых секундах, поворот, живая реакция.
Слабее — организационные куски, разговор ни о чём, шутка без развязки,
фрагмент, непонятный без предыдущего разговора.

Сравнивай фрагменты друг с другом, а не с идеалом. Даже если все слабые,
порядок между ними всё равно есть, и он нужен.

Отвечай на русском языке."""

COMPARE_TEMPLATE = """Фрагменты одного эфира:

{body}

Расставь их номера от лучшего к худшему. Каждый номер ровно один раз,
ничего не пропускай и не добавляй.

Ответ — только JSON: {{"order": [номера]}}"""

COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["order"],
    "additionalProperties": False,
}

TASTE_HEAD = ("\n\nЧто заходит на этом канале — по просмотрам прошлых клипов. "
              "Учитывай это при выборе и оценке:\n")


def with_taste(system: str, cfg) -> str:
    rules = str(getattr(cfg, "taste_rules", "") or "").strip()
    return system + TASTE_HEAD + rules if rules else system


extract_json = _detect.extract_json
coerce_clips = _detect.coerce_clips


def render_items(items) -> str:
    blocks = []
    for item in items:
        head = "[%d]" % item["no"]
        title = str(item.get("title") or "").strip()
        if title:
            head += " «%s»" % title
        hook = str(item.get("hook") or "").strip()
        if hook:
            head += " — " + hook
        blocks.append(head + "\n" + str(item.get("text") or "").strip())
    return "\n\n".join(blocks)


def coerce_order(data, count: int) -> list[int]:
    raw = data.get("order") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raw = []
    order: list[int] = []
    for value in raw:
        try:
            place = int(value) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= place < count and place not in order:
            order.append(place)
    return order


class Backend(ABC):
    """Движок отбора фрагментов."""

    name = "base"
    # Локальные модели держат меньше контекста — окно подбирается под движок.
    default_chunk_minutes = 8.0
    free = False

    @abstractmethod
    def propose_chunk(self, body: str, meta: dict, cfg: Config) -> list[dict]:
        """Возвращает сырые предложения для одного окна расшифровки."""

    def describe(self) -> str:
        return self.name

    def compare_group(self, items: list[dict], cfg: Config) -> list[int]:
        return list(range(len(items)))


class LLMBackend(Backend):
    """Движок на языковой модели: общий промпт, разный транспорт."""

    @abstractmethod
    def complete(self, system: str, user: str, cfg: Config, schema=None) -> str:
        """Возвращает текст ответа модели."""

    def propose_chunk(self, body: str, meta: dict, cfg: Config) -> list[dict]:
        user = USER_TEMPLATE.format(body=body, min_s=cfg.min_clip_s,
                                    max_s=cfg.max_clip_s, **meta)
        text = self.complete(with_taste(SYSTEM, cfg), user, cfg, SCHEMA)
        return coerce_clips(extract_json(text))

    def compare_group(self, items: list[dict], cfg: Config) -> list[int]:
        user = COMPARE_TEMPLATE.format(body=render_items(items))
        text = self.complete(with_taste(COMPARE_SYSTEM, cfg), user, cfg, COMPARE_SCHEMA)
        return coerce_order(extract_json(text), len(items))
