"""Отбор фрагментов: нарезка расшифровки на окна и сведение результатов.

Сам выбор делает движок (см. clipper/backends). Здесь — общая для всех
движков механика: как резать расшифровку, как превращать номера реплик
в тайм-коды и как отсеивать дубли.

Модель возвращает **номера реплик**, а не секунды. Тайм-коды подставляются
из расшифровки: если просить модель оперировать секундами, она их выдумывает,
и это одинаково верно и для облачных, и для локальных моделей.
"""

from __future__ import annotations

import json
from pathlib import Path

from .backends import Backend, get_backend
from .config import Config
from .models import Candidate, Transcript


class _ProposalCache:
    """Кеш ответов движка по окнам.

    Отбор на многочасовом файле — это сотня отдельных запросов и полчаса
    работы. Раньше результат писался в базу только в самом конце, и обрыв
    на девяносто первом окне обнулял всё. Теперь каждое окно сохраняется
    сразу, и повторный запуск продолжает с места обрыва.
    """

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, list] = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.data = {}

    def get(self, lo: int, hi: int):
        return self.data.get(f"{lo}-{hi}")

    def put(self, lo: int, hi: int, items: list) -> None:
        if not self.path:
            return
        self.data[f"{lo}-{hi}"] = items
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)   # атомарно: обрыв не оставит битый файл
        except OSError:
            pass


def _chunks(transcript: Transcript, window_s: float, cfg: Config) -> list[tuple[int, int]]:
    """Режет расшифровку на окна по времени, с нахлёстом в несколько реплик.

    Нахлёст нужен, чтобы удачный фрагмент, попавший на стык окон, не потерялся.
    Дубли потом отсеются по IoU.
    """
    spans: list[tuple[int, int]] = []
    sents = transcript.sentences
    if not sents:
        return spans

    lo = 0
    while lo < len(sents):
        t0 = sents[lo].start
        hi = lo
        while hi < len(sents) and sents[hi].end - t0 < window_s:
            hi += 1
        hi = max(hi, lo + 1)
        spans.append((lo, hi))
        if hi >= len(sents):
            break
        lo = max(lo + 1, hi - cfg.chunk_overlap_sentences)
    return spans


def propose(
    transcript: Transcript,
    cfg: Config,
    *,
    backend: Backend | None = None,
    verbose: bool = True,
    cancel=None,
    cache_key: str | None = None,
) -> list[Candidate]:
    backend = backend or get_backend(cfg)

    cache_path = None
    if cache_key:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in cache_key)
        cache_path = cfg.cache_dir / f"{safe}-{backend.name}.proposals.json"
    cache = _ProposalCache(cache_path)

    # Окно: явная настройка перевешивает, иначе берём рекомендованное движком.
    window_min = cfg.chunk_minutes or backend.default_chunk_minutes
    window_s = window_min * 60.0

    sents = transcript.sentences
    spans = _chunks(transcript, window_s, cfg)
    avg_len = sum(s.duration for s in sents) / len(sents) if sents else 3.0

    out: list[Candidate] = []
    failures = 0

    for n, (lo, hi) in enumerate(spans, start=1):
        # Отмена проверяется между окнами: прервать сам запрос к модели нельзя,
        # но ждать все оставшиеся окна не придётся.
        if cancel is not None and cancel():
            if verbose:
                print("  остановлено пользователем")
            break

        body = transcript.text_window(lo, hi)
        if not body.strip():
            continue

        meta = {
            "chunk_no": n,
            "chunk_total": len(spans),
            "lo": lo,
            "hi": hi - 1,
            "sec_per_sentence": avg_len,
            "limit": max(2, int((sents[hi - 1].end - sents[lo].start) // 120) + 2),
        }

        if verbose:
            print(f"  [{n}/{len(spans)}] реплики {lo}-{hi - 1}…", end="", flush=True)

        cached = cache.get(lo, hi)
        if cached is not None:
            good = 0
            for item in cached:
                cand = _to_candidate(item, transcript, cfg)
                if cand is not None:
                    out.append(cand)
                    good += 1
            if verbose:
                print(f" +{good} (из кеша)")
            continue

        try:
            raw = backend.propose_chunk(body, meta, cfg)
            cache.put(lo, hi, raw)
        except Exception as exc:  # noqa: BLE001
            # Одно неудачное окно не должно ронять всю обработку:
            # локальные модели периодически отдают мусор или отваливаются.
            failures += 1
            if verbose:
                print(f" сбой: {exc}")
            if failures >= 3 and not out:
                raise RuntimeError(
                    f"Движок '{backend.name}' не смог обработать ни одного окна. "
                    f"Последняя ошибка: {exc}"
                ) from exc
            continue

        good = 0
        for item in raw:
            cand = _to_candidate(item, transcript, cfg)
            if cand is not None:
                out.append(cand)
                good += 1
        if verbose:
            print(f" +{good}")

    return dedupe(out, cfg)


def _to_candidate(raw: dict, transcript: Transcript, cfg: Config) -> Candidate | None:
    sents = transcript.sentences
    a = int(raw["start_sentence"])
    b = int(raw["end_sentence"])
    if a > b:
        a, b = b, a
    if a < 0 or b >= len(sents):
        return None

    start, end = sents[a].start, sents[b].end
    dur = end - start
    # Допуск: движок ошибается в длительности чаще, чем в выборе места.
    if dur < cfg.min_clip_s * 0.6 or dur > cfg.max_clip_s * 1.6:
        return None

    return Candidate(
        start=start,
        end=end,
        title=str(raw.get("title", "")).strip(),
        hook=str(raw.get("hook", "")).strip(),
        reason=str(raw.get("reason", "")).strip(),
        score=float(raw.get("score", 0.0)),
        start_sentence=a,
        end_sentence=b,
        features={
            "duration": dur,
            "n_sentences": b - a + 1,
            "chars": sum(len(s.text) for s in sents[a:b + 1]),
            "position_ratio": start / transcript.duration if transcript.duration else 0.0,
            "words_per_sec": (
                sum(len(s.text.split()) for s in sents[a:b + 1]) / dur if dur > 0 else 0.0
            ),
        },
    )


def dedupe(cands: list[Candidate], cfg: Config) -> list[Candidate]:
    """Оставляет лучший из сильно пересекающихся — они приходят со стыков окон."""
    kept: list[Candidate] = []
    for c in sorted(cands, key=lambda x: x.score, reverse=True):
        if all(c.overlap_ratio(k) < cfg.dedupe_iou for k in kept):
            kept.append(c)
    return sorted(kept, key=lambda x: x.score, reverse=True)


SPREAD_BUCKETS = 8


def spread(rows, top: int, duration: float, buckets: int = SPREAD_BUCKETS,
           key=None) -> list:
    at = key or (lambda row: row["start"])
    if top <= 0:
        return []
    if top >= len(rows) or duration <= 0:
        return list(rows[:top])
    buckets = max(1, min(buckets, top))
    width = duration / buckets
    lanes: dict[int, list] = {}
    for row in rows:
        slot = min(buckets - 1, int(max(0.0, float(at(row))) / width))
        lanes.setdefault(slot, []).append(row)
    taken: list = []
    while len(taken) < top:
        moved = False
        for slot in sorted(lanes):
            lane = lanes[slot]
            if not lane:
                continue
            taken.append(lane.pop(0))
            moved = True
            if len(taken) == top:
                return taken
        if not moved:
            break
    return taken
