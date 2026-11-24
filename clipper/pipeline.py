"""Оркестрация: один Task от файла до готовых клипов.

`process` намеренно не обращается к глобальному состоянию и получает всё через
аргументы. Это та граница, по которой позже втыкается распределение между
машинами: работнику достаточно передать сериализованный Task и вернуть
кандидатов. Пока цикл по задачам последовательный — и этого достаточно.
"""

from __future__ import annotations

import time
from pathlib import Path

from .backends import Backend, get_backend
from .config import Config
from .ffmpeg_util import is_fragmented_mp4, make_seekable, probe
from .models import Candidate, Task, Transcript
from .refine import refine_all
from .render import render
from . import compare as _cmp
from . import scoring as _sc
from . import store as _st
from .scoring import propose
from .store import Store
from .transcribe import transcribe


class Result:
    def __init__(self, task: Task, candidates: list[Candidate],
                 transcript: Transcript, rendered: list[Path], seconds: float):
        self.task = task
        self.candidates = candidates
        self.transcript = transcript
        self.rendered = rendered
        self.seconds = seconds


def process(
    task: Task,
    cfg: Config,
    store: Store,
    *,
    backend: Backend | None = None,
    do_render: bool = True,
    force_transcribe: bool = False,
    verbose: bool = True,
    cancel=None,
) -> Result:
    t0 = time.time()
    src = task.source
    backend = backend or get_backend(cfg)

    if not src.exists():
        raise FileNotFoundError(f"Файл не найден: {src}")

    info = probe(src)
    if not info["has_audio"]:
        raise RuntimeError(f"В файле нет звуковой дорожки, расшифровывать нечего: {src}")

    if verbose:
        mins = info["duration"] / 60
        print(f"\n=== {src.name} ({mins:.1f} мин) ===")
        print("[1/5] Расшифровка…", flush=True)

    transcript = transcribe(src, cfg, force=force_transcribe)
    if not transcript.sentences:
        raise RuntimeError("Расшифровка пустая — речь не распознана")

    video_id = store.upsert_video(src, info["duration"], transcript.language)
    store.save_transcript(video_id, transcript)

    if verbose:
        print(f"      реплик: {len(transcript.sentences)}, язык: {transcript.language}")
        print(f"[2/5] Отбор фрагментов — {backend.describe()}…", flush=True)

    candidates = propose(transcript, cfg, backend=backend, verbose=verbose,
                         cancel=cancel, cache_key=src.stem)
    if verbose:
        print(f"      кандидатов после отсева дублей: {len(candidates)}")

    if verbose:
        print("[3/5] Уточнение границ по паузам…", flush=True)
    candidates = refine_all(candidates, transcript, cfg, media_duration=info["duration"])

    # Пишем именно движок: датасет должен помнить, кто выставил оценку,
    # иначе позже не отличить суждения разных моделей друг от друга.
    ids = store.save_candidates(video_id, candidates, backend.describe())

    # Идентификатор носим вместе с кандидатом: после фильтрации по порогу
    # позиции в списках разъезжаются, и zip со списком id спарил бы клип
    # с чужой записью в базе.
    paired = list(zip(candidates, ids))
    # Порог оценки отсеивает мусор: если движок счёл фрагмент слабым,
    # сравнивать его не с чем. Слабые остаются в базе — как отрицательные
    # примеры они не менее полезны.
    worthy = [(c, i) for c, i in paired if c.score >= cfg.min_score]
    if verbose and len(worthy) < len(paired):
        print(f"      ниже порога {cfg.min_score:.0f} отсеяно: "
              f"{len(paired) - len(worthy)}")

    if cfg.compare and len(worthy) > 2:
        if verbose:
            print(f"[4/5] Сравнение {len(worthy)} фрагментов между собой…",
                  flush=True)
        worthy, power = _cmp.rank(
            worthy, transcript, cfg, backend, verbose=verbose, cancel=cancel,
            on_duel=lambda win, lose: store.add_comparison(win, lose, win,
                                                           source=_st.MODEL))
        store.save_power(power)
        if verbose and power:
            print("      порядок задан сравнением, а не оценками")

    rendered: list[Path] = []
    if do_render and worthy:
        top = _sc.spread(worthy, cfg.top_n, transcript.duration,
                         key=lambda pair: pair[0].start)

        # Фрагментированный контейнер (типично для записей стримов) не имеет
        # индекса перемотки, и каждый вырезаемый клип обходится в лишние
        # ~95 секунд на пятичасовом файле. Один раз перекладываем контейнер —
        # дальше перемотка мгновенная. Перекодирования нет, качество то же.
        render_src = src
        if cfg.remux_fragmented and len(top) > 1 and is_fragmented_mp4(src):
            seekable = cfg.cache_dir / f"{src.stem}.seekable.mp4"
            if not seekable.exists():
                if verbose:
                    gb = src.stat().st_size / 1024 ** 3
                    print(f"      контейнер фрагментирован — перекладываю "
                          f"({gb:.1f} ГБ, один раз)…", flush=True)
                make_seekable(src, seekable)
            render_src = seekable

        if verbose:
            print(f"[5/5] Рендер {len(top)} клипов…", flush=True)
        for i, (cand, cid) in enumerate(top, start=1):
            if cancel is not None and cancel():
                if verbose:
                    print("      рендер остановлен пользователем")
                break
            if verbose:
                print(f"      {i}/{len(top)}  {cand.score:.1f}  {cand.title}", flush=True)
            out = render(src, cand, transcript, cfg, index=i, read_from=render_src)
            store.record_render(cid, out)
            rendered.append(out)
    elif verbose:
        print("[5/5] Рендер пропущен")

    return Result(task, candidates, transcript, rendered, time.time() - t0)


def render_existing(
    video_id: int,
    cfg: Config,
    store: Store,
    *,
    verbose: bool = True,
    cancel=None,
) -> list[Path]:
    """Рендерит клипы по кандидатам, уже лежащим в базе.

    Отбор — самая дорогая стадия после расшифровки, и переделывать её ради
    других настроек рендера незачем. Полезно после обрыва, при смене
    вертикального формата или числа клипов.
    """
    rows = store.list_candidates(video_id)
    if not rows:
        raise RuntimeError(f"У видео {video_id} нет кандидатов в базе")

    src = Path(rows[0]["video_path"])
    if not src.exists():
        raise FileNotFoundError(f"Исходник не найден: {src}")

    # Длительность берём из базы, а не из probe(): на фрагментированном
    # контейнере probe стоит ~95 секунд, а значение уже сохранено при отборе.
    sentences = store.load_sentences(video_id)
    transcript = Transcript(sentences=sentences,
                            duration=rows[0]["video_duration"] or 0.0)

    # Отклонённые пользователем не воскрешаем. Оценка 0 — это осознанное
    # решение человека, и повторный рендер молча его отменял бы: файл
    # появлялся бы снова там, откуда его убрали.
    rejected = {cid for cid, r in store.rating_index().items() if r == 0}
    worthy = [r for r in rows
              if r["model_score"] >= cfg.min_score and r["id"] not in rejected]
    if getattr(cfg, "rerank", None):
        import json as _json

        from core import rerank as _rr

        def taste(row):
            try:
                features = _json.loads(row["features_json"] or "{}")
            except (TypeError, ValueError):
                features = {}
            return -_rr.score(cfg.rerank, {"id": row["id"],
                                           "model_score": row["model_score"],
                                           "features": features})

        worthy = sorted(worthy, key=taste)
        if verbose:
            print("порядок по обученной модели вкуса")
    elif any(_cmp.power_of(r) is not None for r in worthy):
        worthy = _cmp.by_power(worthy)
        if verbose:
            print("порядок по сравнению фрагментов между собой")
    worthy = _sc.spread(worthy, cfg.top_n, transcript.duration)
    if verbose:
        skipped = sum(1 for r in rows if r["id"] in rejected)
        print(f"кандидатов в базе: {len(rows)}, к рендеру: {len(worthy)}"
              + (f", пропущено отклонённых вами: {skipped}" if skipped else ""))

    render_src = src
    if cfg.remux_fragmented and len(worthy) > 1 and is_fragmented_mp4(src):
        seekable = cfg.cache_dir / f"{src.stem}.seekable.mp4"
        if not seekable.exists():
            if verbose:
                gb = src.stat().st_size / 1024 ** 3
                print(f"контейнер фрагментирован — перекладываю ({gb:.1f} ГБ, один раз)…",
                      flush=True)
            make_seekable(src, seekable)
        elif verbose:
            print("использую ранее переложенный контейнер")
        render_src = seekable

    out: list[Path] = []
    for i, row in enumerate(worthy, start=1):
        if cancel is not None and cancel():
            print("остановлено пользователем")
            break
        cand = Candidate(
            start=row["start"], end=row["end"], title=row["title"] or "",
            hook=row["hook"] or "", reason=row["reason"] or "",
            score=row["model_score"],
        )
        if verbose:
            t0 = time.time()
            print(f"  {i}/{len(worthy)}  {cand.score:.1f}  {cand.title}", end="", flush=True)
        p = render(src, cand, transcript, cfg, index=i, read_from=render_src)
        store.record_render(row["id"], p)
        out.append(p)
        if verbose:
            print(f"   ({time.time() - t0:.0f} сек)", flush=True)
    return out


def process_many(sources: list[Path], cfg: Config, store: Store, **kw) -> list[Result]:
    # Движок создаётся один раз на всю пачку: проверка доступности и загрузка
    # модели не должны повторяться на каждом файле.
    backend = kw.pop("backend", None) or get_backend(cfg)
    check = getattr(backend, "check", None)
    if callable(check):
        check()

    cancel = kw.get("cancel")
    results: list[Result] = []
    for src in sources:
        if cancel is not None and cancel():
            print("Остановлено пользователем.")
            break
        try:
            results.append(process(Task(source=src), cfg, store, backend=backend, **kw))
        except Exception as exc:  # noqa: BLE001
            # Один плохой файл не должен ронять всю пачку.
            print(f"!! {src.name}: {exc}")
    return results
