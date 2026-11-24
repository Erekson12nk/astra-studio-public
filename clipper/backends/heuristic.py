"""Движок без модели вообще. Нулевая стоимость, нулевые зависимости.

Никакого ИИ здесь нет — только правила по тексту расшифровки. Качество
заметно ниже, чем у языковой модели, но:

- работает мгновенно и не требует ни видеопамяти, ни интернета, ни ключей;
- служит нижней границей: любой обученный реранкер обязан обыгрывать эти
  правила, иначе он не нужен;
- позволяет прогнать весь конвейер и убедиться, что резка и рендер в порядке,
  не потратив ни копейки и не скачивая модель.
"""

from __future__ import annotations

import math
import re

from ..config import Config
from .base import Backend

# Организационный мусор — то, что почти никогда не работает как отдельный ролик.
BOILERPLATE = re.compile(
    r"\b(подпис\w+|лайк\w*|колокольчик\w*|донат\w*|реклам\w+|спонсор\w*|"
    r"всем привет|здравствуйте|доброго времени|с вами снова|не забудь\w*|"
    r"ссылк\w+ в описании|промокод\w*|стрим начн\w+|сейчас расскажу|"
    r"о чём мы сегодня|погнали|поехали)\b",
    re.I,
)

# Признаки живого содержания: спор, история, неожиданность, конкретика.
STRONG = re.compile(
    r"\b(потому что|на самом деле|оказалось|представь\w*|проблема в том|"
    r"главное|секрет|ошибк\w+|никогда|всегда|впервые|внезапно|"
    r"смотри|важно|например|история|случай|однажды|результат|вывод|"
    r"я думаю|по-моему|считаю|уверен|спор\w*|против|наоборот)\b",
    re.I,
)

FILLER = re.compile(r"\b(ну|это|как бы|типа|короче|вот|значит|э+|м+)\b", re.I)


class HeuristicBackend(Backend):
    name = "heuristic"
    free = True
    default_chunk_minutes = 8.0

    def propose_chunk(self, body: str, meta: dict, cfg: Config) -> list[dict]:
        # Разбираем пронумерованные строки обратно в реплики.
        lines: list[tuple[int, str]] = []
        for raw in body.splitlines():
            m = re.match(r"\[(\d+)\]\s*(.*)", raw)
            if m:
                lines.append((int(m.group(1)), m.group(2)))
        if len(lines) < 2:
            return []

        sec_per = float(meta.get("sec_per_sentence", 3.0)) or 3.0
        lo_n = max(1, int(cfg.min_clip_s / sec_per))
        hi_n = max(lo_n + 1, int(cfg.max_clip_s / sec_per))

        scored: list[dict] = []
        step = max(1, lo_n // 3)

        for i in range(0, len(lines) - lo_n + 1, step):
            for n in (lo_n, (lo_n + hi_n) // 2, hi_n):
                j = i + n
                if j > len(lines):
                    continue
                window = lines[i:j]
                text = " ".join(t for _, t in window)
                if len(text) < 80:
                    continue
                score = self._score(text, window)
                scored.append({
                    "start_sentence": window[0][0],
                    "end_sentence": window[-1][0],
                    "title": self._title(window[0][1]),
                    "hook": window[0][1][:120],
                    "reason": "эвристика: плотность речи и содержательные маркеры",
                    "score": score,
                })

        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored[: int(meta.get("limit", 4))]

    def _score(self, text: str, window: list[tuple[int, str]]) -> float:
        words = text.split()
        if not words:
            return 0.0

        score = 5.0

        # Организационный мусор — сильный штраф.
        score -= 2.5 * len(BOILERPLATE.findall(text))

        # Начало важнее середины: первая реплика — это зацепка, и она же
        # становится заголовком. Открываться приветствием или рекламой — плохо.
        if BOILERPLATE.search(window[0][1]):
            score -= 3.0

        # Содержательные маркеры — плюс, но с насыщением: десять «потому что»
        # не делают фрагмент вдесятеро лучше.
        score += 1.8 * math.log1p(len(STRONG.findall(text)))

        # Вопрос в первой реплике — хорошая зацепка.
        if "?" in window[0][1]:
            score += 1.0

        # Слова-паразиты на единицу текста.
        score -= 3.0 * (len(FILLER.findall(text)) / len(words))

        # Лексическое разнообразие: пересказ по кругу читается как вода.
        variety = len({w.lower() for w in words}) / len(words)
        score += 3.0 * (variety - 0.55)

        # Законченность: фрагмент, обрывающийся на середине фразы, хуже.
        if window[-1][1].rstrip().endswith((".", "!", "?", "…")):
            score += 0.8
        else:
            score -= 0.8

        # Плавное сжатие в 0..10 вместо жёсткой обрезки.
        # Обрезка через min(10, x) схлопывала все хорошие фрагменты в ровно
        # 10.00 и убивала ранжирование — а нам важен именно порядок.
        return 10.0 / (1.0 + math.exp(-(score - 5.0) / 2.5))

    @staticmethod
    def _title(first_line: str) -> str:
        t = first_line.strip().rstrip(".!?…")
        words = t.split()
        return " ".join(words[:7]) if words else "фрагмент"
