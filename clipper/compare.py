from __future__ import annotations

import random

from .backends import Backend
from .config import Config
from .models import Candidate, Transcript

PRIOR = 1.0
STEPS = 80


def power_of(row):
    try:
        return row["power"]
    except (IndexError, KeyError, TypeError):
        return None


def by_power(rows) -> list:
    return sorted(rows, key=lambda r: (-(power_of(r) if power_of(r) is not None
                                         else -1.0), -(r["model_score"] or 0.0)))


def excerpt(cand: Candidate, transcript: Transcript, limit: int) -> str:
    sents = transcript.sentences
    a, b = cand.start_sentence, cand.end_sentence
    if 0 <= a <= b < len(sents):
        picked = sents[a:b + 1]
    else:
        picked = [s for s in sents if s.end > cand.start and s.start < cand.end]
    text = " ".join(s.text.strip() for s in picked if s.text.strip())
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def item(no: int, cand: Candidate, transcript: Transcript, limit: int) -> dict:
    return {"no": no, "title": cand.title, "hook": cand.hook,
            "text": excerpt(cand, transcript, limit)}


def bands(order: list[int], size: int) -> list[list[int]]:
    size = max(2, int(size))
    out = []
    for start in range(0, len(order), size):
        group = order[start:start + size]
        if len(group) >= 2:
            out.append(group)
    return out


def strengths(count: int, duels: list[tuple[int, int]], steps: int = STEPS,
              prior: float = PRIOR) -> list[float]:
    wins = [prior] * count
    rivals: list[list[int]] = [[] for _ in range(count)]
    for winner, loser in duels:
        wins[winner] += 1.0
        rivals[winner].append(loser)
        rivals[loser].append(winner)
    power = [1.0] * count
    for _ in range(steps):
        fresh = []
        for i in range(count):
            share = 2.0 * prior / (power[i] + 1.0)
            for j in rivals[i]:
                share += 1.0 / (power[i] + power[j])
            fresh.append(wins[i] / share if share > 0 else power[i])
        middle = sum(fresh) / count if count else 1.0
        power = [value / middle for value in fresh] if middle > 0 else fresh
    return power


def rank(pairs, transcript: Transcript, cfg: Config, backend: Backend, *,
         verbose: bool = True, cancel=None, on_duel=None):
    if not cfg.compare or len(pairs) < 3:
        return list(pairs), {}
    edge = max(3, cfg.compare_max)
    pool = list(pairs[:edge])
    tail = list(pairs[edge:])
    rnd = random.Random(cfg.compare_seed)
    order = list(range(len(pool)))
    rnd.shuffle(order)
    times = max(1, int(cfg.compare_rounds))
    total = len(bands(order, cfg.compare_group)) * times
    duels: list[tuple[int, int]] = []
    power = [1.0] * len(pool)
    failures = 0
    done = 0
    stop = False
    for circle in range(times):
        for group in bands(order, cfg.compare_group):
            if cancel is not None and cancel():
                if verbose:
                    print("      сравнение остановлено пользователем")
                stop = True
                break
            done += 1
            items = [item(place + 1, pool[who][0], transcript, cfg.compare_chars)
                     for place, who in enumerate(group)]
            if verbose:
                print(f"      [{done}/{total}] сравниваю {len(group)}…",
                      end="", flush=True)
            try:
                verdict = backend.compare_group(items, cfg)
            except Exception as exc:
                verdict = []
                if verbose:
                    print(f" сбой: {exc}", end="")
            if len(verdict) < 2:
                failures += 1
                if verbose:
                    print(" ответ не разобран")
                if failures >= 3 and not duels:
                    if verbose:
                        print("      сравнение не пошло, остаюсь на оценках движка")
                    return list(pairs), {}
                continue
            for better in range(len(verdict)):
                for worse in range(better + 1, len(verdict)):
                    winner, loser = group[verdict[better]], group[verdict[worse]]
                    duels.append((winner, loser))
                    if on_duel is not None:
                        on_duel(pool[winner][1], pool[loser][1])
            if verbose:
                print(" готово")
        if duels:
            power = strengths(len(pool), duels)
            # Следующий круг сводит соседей по силе: случайные группы решают,
            # кто в середине, но не различают первого и третьего.
            order = sorted(range(len(pool)),
                           key=lambda i: (-power[i], -pool[i][0].score))
        if stop:
            break
    if not duels:
        return list(pairs), {}
    ranked = [pool[i] for i in order] + tail
    return ranked, {pool[i][1]: power[i] for i in range(len(pool))}
