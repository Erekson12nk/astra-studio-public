"""clipper — командная строка.

    py cli.py run video.mp4              нарезать
    py cli.py list                       показать кандидатов
    py cli.py rate 12 5                  оценить клип (1-5)
    py cli.py pairs                      какие пары стоит сравнить
    py cli.py compare 12 15 --winner a   записать сравнение
    py cli.py ref clip.mp4               добавить референс
    py cli.py stats                      сколько накоплено данных
    py cli.py export --video 1 -o m.txt  выгрузить моменты для astra
    py cli.py learn                      связать с базой astra: что зашло
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Консоль Windows по умолчанию не в UTF-8, и русский вывод превращается в кашу.
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

import os  # noqa: E402
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import cache as _cache  # noqa: E402
from core import ffmpeg as _ff  # noqa: E402
from core import handoff as _hand  # noqa: E402
from core import learning as _learn  # noqa: E402
from core import profiles as _pro  # noqa: E402
from core import learnlog as _learnlog  # noqa: E402
from core import rerank as _rr  # noqa: E402

from clipper.config import Config  # noqa: E402
from clipper.pipeline import process_many
from clipper import compare as _cmp
from clipper import scoring as _sc
from clipper import store as _st
from clipper.store import Store

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv"}


def collect(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            out.extend(sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXT))
        elif p.exists():
            out.append(p)
        else:
            print(f"!! пропускаю, файла нет: {p}")
    return out


def cmd_run(args, cfg: Config, store: Store) -> int:
    sources = collect(args.sources)
    if not sources:
        print("Нечего обрабатывать.")
        return 1

    cfg.vertical = args.vertical
    cfg.burn_subs = args.subs
    cfg.top_n = args.top
    if args.model:
        cfg.model = args.model
    if args.backend:
        cfg.backend = args.backend
    if args.local_model:
        cfg.local_model = args.local_model

    cfg.rerank = use_rerank(args, cfg)
    if args.no_compare:
        cfg.compare = False
    if args.compare_rounds:
        cfg.compare_rounds = args.compare_rounds
    if args.compare_group:
        cfg.compare_group = args.compare_group

    results = process_many(
        sources, cfg, store,
        do_render=not args.no_render,
        force_transcribe=args.force,
    )

    print("\n" + "=" * 60)
    for r in results:
        print(f"{r.task.source.name}: {len(r.candidates)} кандидатов, "
              f"{len(r.rendered)} отрендерено, {r.seconds:.0f} сек")
    if results and results[0].rendered:
        print(f"\nКлипы: {cfg.out_dir}")
    print("Дальше: py cli.py list, затем rate / compare — это копит датасет.")
    return 0


def cmd_render(args, cfg: Config, store: Store) -> int:
    """Рендер по уже найденным кандидатам — без повторного отбора."""
    from clipper.pipeline import render_existing

    cfg.top_n = args.top
    cfg.vertical = args.vertical
    cfg.burn_subs = args.subs
    cfg.rerank = use_rerank(args, cfg)
    paths = render_existing(args.video, cfg, store)
    print(f"\nготово: {len(paths)} клипов в {cfg.out_dir}")
    return 0


def use_rerank(args, cfg: Config):
    demanded = getattr(args, "rerank", False)
    if getattr(args, "no_rerank", False):
        return None
    model = _rr.load(cfg.rerank_path)
    if model is None:
        if demanded:
            print("Модели пока нет. Обучите: py cli.py train")
        return None
    report = model.get("report") or {}
    if report and not report.get("better_than_engine", True) and not demanded:
        print(f"Модель вкуса есть, но пока не обыгрывает движок "
              f"({report.get('holdout_accuracy', 0):.2f} против "
              f"{report.get('baseline_accuracy', 0):.2f}) — беру порядок движка.")
        print("Настоять:  --rerank.  Обновить модель:  py cli.py train")
        return None
    if report.get("pairs"):
        note = f"модель вкуса на {report['pairs']} парах"
        if report.get("holdout_accuracy") is not None:
            note += f", точность {report['holdout_accuracy']:.2f}"
        if report.get("shaky"):
            note += " (выборка мала)"
        print(note)
    return model


def cmd_list(args, cfg: Config, store: Store) -> int:
    rows = store.list_candidates(args.video)
    if not rows:
        print("Кандидатов пока нет. Запустите: py cli.py run <видео>")
        return 0
    if args.video is None and store.videos_with_candidates() > 1:
        print("это кандидаты от %d стримов вперемешку; "
              "один стрим — это --video <id>" % store.videos_with_candidates())
    rows, _model = ranked(rows, args, cfg, store)
    top = getattr(args, "top", 0) or 0
    chosen = set(id(r) for r in choose(rows, top)) if top else None
    print(f"{'':>3}{'id':>5}  {'оценка':>6}  {'начало':>9}  {'длит':>6}  название")
    print("-" * 78)
    for r in rows:
        mm, ss = divmod(int(r["start"]), 60)
        mark = "" if chosen is None else ("  * " if id(r) in chosen else "    ")
        print(f"{mark}{r['id']:>5}  {r['model_score']:>6.1f}  {mm:>6d}:{ss:02d}  "
              f"{r['end'] - r['start']:>5.0f}с  {r['title']}")
    if chosen is not None:
        print("* — то, что уйдёт в export --top %d" % top)
    return 0


def cmd_export(args, cfg: Config, store: Store) -> int:
    video = args.video
    if video is None:
        latest = store.latest_video()
        if latest is None:
            print("Кандидатов нет. Сначала: py cli.py run <видео>")
            return 1
        video = latest["id"]
        print("видео не указано, беру последнее обработанное: %d — %s"
              % (video, Path(latest["path"]).name))
        if store.videos_with_candidates() > 1:
            print("в базе есть кандидаты и от других стримов; "
                  "чтобы взять их, укажите --video")
    rows = store.list_candidates(video)
    if not rows:
        print("У видео %d кандидатов нет. Сначала: py cli.py run <видео>" % video)
        return 1
    rows, _model = ranked(rows, args, cfg, store)
    rows = [r for r in rows if r["model_score"] >= args.min_score]
    picked = choose(rows, args.top)
    if not picked:
        print(f"Ни один кандидат не набрал {args.min_score:.1f}. "
              f"Лучший: {rows[0]['model_score']:.1f}")
        return 1
    items = [{"start": r["start"], "end": r["end"], "title": r["title"],
              "score": r["model_score"]} for r in picked]
    out = Path(args.out).expanduser()
    _hand.write_timecodes(out, items)
    print(f"Записано моментов: {len(items)} -> {out}")
    print("Дальше в astra: открыть стрим и указать этот файл как список "
          "таймкодов (--timecodes), либо перетащить в окно.")
    return 0


def _astra_db(given) -> Path:
    if given:
        return Path(given).expanduser()
    return Path(_ROOT) / "astra" / "clips.db"


def cmd_learn(args, cfg: Config, store: Store) -> int:
    astra_db = _astra_db(args.astra_db)
    report = _learn.link_databases(cfg.db_path, astra_db, args.min_overlap)
    if report.get("error"):
        print("Не вышло: %s" % report["error"])
        if "astra" in report["error"]:
            print("Базу astra заполняла публикация. Её больше нет, "
                  "так что связей не появится.")
        return 1

    print(f"Кандидатов в базе clipper : {report['candidates']}")
    print(f"Опубликовано через astra  : {report['published']}")
    print(f"Совпало по времени        : {report['matched']}")
    print(f"Записано связей           : {report['written']}")
    if report["no_stats"]:
        print(f"  из них без статистики   : {report['no_stats']} "
              f"(просмотры ещё не собраны)")
    if report["rendered_matched"]:
        print(f"Смонтировано, но не опубликовано: {report['rendered_matched']}")
    for item in report["unmatched"][:5]:
        print(f"  не нашёл кандидата: {item['source']} "
              f"{item['start']:.0f}-{item['end']:.0f} с  {item['file']}")
    if len(report["unmatched"]) > 5:
        print(f"  ... и ещё {len(report['unmatched']) - 5}")

    rows = [r for r in _learn.training_signal(cfg.db_path) if r.get("views")]
    if rows:
        print("")
        print("Что зашло лучше всего:")
        for row in rows[:10]:
            print(f"  {row['views']:>8} просмотров  оценка модели "
                  f"{row['model_score'] or 0:.1f}  {row['title'] or ''}")
    return 0


def cmd_rate(args, cfg: Config, store: Store) -> int:
    if not 1 <= args.rating <= 5:
        print("Оценка должна быть от 1 до 5.")
        return 1
    store.add_rating(args.candidate, args.rating)
    print(f"Записано: клип {args.candidate} → {args.rating}")
    return 0


def cmd_pairs(args, cfg: Config, store: Store) -> int:
    """Какие пары стоит сравнить в первую очередь.

    Зачаток активного обучения: спрашивать имеет смысл там, где модель
    сомневается, то есть у кандидатов с близкими оценками. Сравнение
    очевидно лучшего с очевидно худшим не несёт информации.
    """
    rows = store.list_candidates(args.video)
    if len(rows) < 2:
        print("Нужно хотя бы два кандидата.")
        return 0
    pairs = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            gap = abs(rows[i]["model_score"] - rows[j]["model_score"])
            pairs.append((gap, rows[i], rows[j]))
    pairs.sort(key=lambda p: p[0])

    print("Пары, где модель менее всего уверена — начните с них:\n")
    for gap, a, b in pairs[: args.limit]:
        print(f"  py cli.py compare {a['id']} {b['id']} --winner a|b|tie")
        print(f"      A [{a['id']}] {a['model_score']:.1f}  {a['title']}")
        print(f"      B [{b['id']}] {b['model_score']:.1f}  {b['title']}")
        print(f"      разрыв оценок: {gap:.2f}\n")
    return 0


def cmd_compare(args, cfg: Config, store: Store) -> int:
    winner = {"a": args.left, "b": args.right, "tie": None}[args.winner]
    store.add_comparison(args.left, args.right, winner)
    label = "ничья" if winner is None else f"победил {winner}"
    print(f"Записано сравнение {args.left} vs {args.right}: {label}")
    return 0


def cmd_ref(args, cfg: Config, store: Store) -> int:
    for p in collect(args.paths):
        store.add_reference(p, args.note)
        print(f"Референс добавлен: {p.name}")
    return 0


def cmd_backends(args, cfg: Config, store: Store) -> int:
    """Показывает доступные движки и проверяет, какие реально готовы работать."""
    from clipper.backends import AVAILABLE, get_backend

    print(f"Сейчас выбран: {cfg.backend}\n")
    for name, desc in AVAILABLE.items():
        probe = Config()
        probe.backend = name
        probe.local_model = cfg.local_model
        try:
            b = get_backend(probe)
        except Exception as exc:  # noqa: BLE001
            print(f"  [--] {name:<14} {desc}\n       {exc}")
            continue

        check = getattr(b, "check", None)
        if not callable(check):
            print(f"  [ГОТОВ] {name:<14} {desc}")
            continue
        try:
            check()
            print(f"  [ГОТОВ] {name:<14} {desc}")
        except Exception as exc:  # noqa: BLE001
            first = str(exc).splitlines()[0]
            print(f"  [ ---- ] {name:<14} {desc}")
            print(f"           {first}")
    print("\nВыбрать:  py cli.py run видео.mp4 --backend ollama")
    return 0


def cmd_stats(args, cfg: Config, store: Store) -> int:
    s = store.training_stats()
    print("Накоплено данных:")
    for k, v in s.items():
        print(f"  {k:<14} {v}")
    print()
    n = s["comparisons"]
    if n < 50:
        print(f"Сравнений {n}. Для обучения реранкера нужны сотни — фаза 2 продолжается.")
    elif n < 300:
        print(f"Сравнений {n}. Уже можно пробовать первую модель, но выборка мала.")
    else:
        print(f"Сравнений {n}. Достаточно, чтобы обучать реранкер (фаза 3).")
    return 0


def cmd_adopt(args, cfg: Config, store: Store) -> int:
    """Возвращает в базу клипы, смонтированные в astra.

    astra рендерит по выгруженному списку таймкодов и про базу clipper не
    знает, поэтому её файлы не попадали в таблицу renders — а окно оценок
    показывает именно её. Без этого свежие клипы просто не видно.
    """
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"Нет такой папки: {folder}")
        return 1
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXT)
    if not files:
        print(f"В {folder} нет видео.")
        return 1
    rows = store.list_candidates(args.video)
    if not rows:
        print("Кандидатов нет — сначала py cli.py run <видео>")
        return 1

    lost = store.drop_duplicate_renders()
    if lost:
        print(f"Убрано повторных записей о рендере: {lost}")

    known = {r["path"] for r in store.db.execute("SELECT path FROM renders")}
    taken = 0
    skipped = []
    for path in files:
        if str(path) in known:
            continue
        start = _hand.start_from_name(path)
        if start is None:
            skipped.append((path.name, "в имени нет таймкода"))
            continue
        row, gap = _hand.match_candidate(start, rows, args.slack)
        if row is None:
            skipped.append((path.name, f"нет кандидата на {int(start)} с"))
            continue
        store.record_render(row["id"], path)
        taken += 1
        print(f"  клип {row['id']:>4}  расхождение {gap:.1f} с  {path.name[:52]}")

    print(f"\nПривязано клипов: {taken}")
    for name, why in skipped[:8]:
        print(f"  мимо: {name[:52]} — {why}")
    if len(skipped) > 8:
        print(f"  ... и ещё {len(skipped) - 8}")
    if taken:
        print("Теперь они видны в окне оценок: py gui.py, вкладка «Все клипы».")
    return 0


def cmd_picks(args, cfg: Config, store: Store) -> int:
    if args.forget:
        gone_c, gone_r = store.forget_source(_st.PICKS)
        print("убрано выведенных сравнений: %d, оценок: %d" % (gone_c, gone_r))
        gone_m = store.drop_unused_missed()
        if gone_m:
            print("убрано пропущенных поиском моментов: %d" % gone_m)
        return 0

    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print("Нет такой папки: %s" % folder)
        return 1
    video = args.video
    if video is None:
        latest = store.latest_video()
        if latest is None:
            print("Кандидатов нет — сначала py cli.py run <видео>")
            return 1
        video = latest["id"]
        print("видео не указано, беру последнее обработанное: %d — %s"
              % (video, Path(latest["path"]).name))
    rows = store.list_candidates(video)
    if not rows:
        print("У видео %d кандидатов нет." % video)
        return 1

    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXT)
    sentences = store.load_sentences(video)
    middle = engine_median(rows)
    picked, missed, added = {}, [], 0
    for path in files:
        start = _hand.start_from_name(path)
        if start is None:
            missed.append((path.name, "в имени нет таймкода"))
            continue
        row, gap = _hand.match_candidate(start, rows, args.slack)
        if row is None:
            length = _ff.duration(path)
            if length <= 0:
                missed.append((path.name, "нет кандидата рядом, длину не прочитать"))
                continue
            cid = store.add_missed(
                video, start, start + length, "пропущено поиском: %s" % path.stem,
                middle, missed_features(sentences, start, start + length,
                                        rows[0]["video_duration"]))
            rows = store.list_candidates(video)
            row, gap = next(r for r in rows if r["id"] == cid), 0.0
            added += 1
        picked.setdefault(row["id"], (row, gap, path.name))

    print("файлов в папке: %d, узнано кандидатов: %d" % (len(files), len(picked) - added))
    if added:
        print("пропущено поиском, добавлено из ваших клипов: %d" % added)
    for name, why in missed[:6]:
        print("  мимо: %s — %s" % (name[:52], why))
    if len(missed) > 6:
        print("  ... и ещё %d" % (len(missed) - 6))
    if not picked:
        print("Ничего не сошлось. Проверьте --video и --slack.")
        return 1

    rest = [r for r in rows if r["id"] not in picked and r["model"] != _st.HAND]
    if not rest:
        print("Все кандидаты оказались выбранными — сравнивать не с чем.")
        return 1

    import random

    rng = random.Random(args.seed)
    made = 0
    for cid, (row, _gap, _name) in picked.items():
        for loser in rng.sample(rest, min(args.pairs, len(rest))):
            store.add_comparison(cid, loser["id"], cid, source=_st.PICKS)
            made += 1
    hand_c, _hand_r = store.count_source(_st.HAND)
    print()
    print("выведено сравнений «взято лучше невзятого»: %d" % made)
    print("ваших, сделанных руками: %d — они помечены отдельно и не тронуты" % hand_c)
    print("это допущение: невзятое считается худшим, хотя вы могли до него")
    print("просто не дойти. Убрать всё выведенное:  py cli.py picks --forget")
    print("Дальше:  py cli.py train")
    return 0


def engine_median(rows) -> float:
    fill = _rr.engine_fill([{"model_score": r["model_score"], "hand": r["model"] == _st.HAND}
                            for r in rows])
    return 0.0 if fill is None else fill


def missed_features(sentences, start: float, end: float, total) -> dict:
    inside = [s for s in sentences if s.end > start and s.start < end]
    length = end - start
    words = sum(len(s.text.split()) for s in inside)
    return {
        "duration": length,
        "n_sentences": len(inside),
        "chars": sum(len(s.text) for s in inside),
        "position_ratio": start / float(total) if total else 0.0,
        "words_per_sec": words / length if length > 0 else 0.0,
    }


def ranked(rows, args, cfg: Config, store: Store):
    model = use_rerank(args, cfg)
    if model is not None:
        rows = sorted(rows, key=lambda r: -_rr.score(model, row_features(r)))
        print("порядок по обученной модели, не по движку")
    elif any(_cmp.power_of(r) is not None for r in rows):
        rows = _cmp.by_power(rows)
        print("порядок по сравнению фрагментов между собой")
    else:
        rows = sorted(rows, key=lambda r: -r["model_score"])
    return rows, model


def choose(rows, top: int):
    duration = rows[0]["video_duration"] if rows else 0.0
    picked = _sc.spread(rows, top, float(duration or 0.0))
    if picked and len(rows) > top:
        span = (min(r["start"] for r in picked), max(r["start"] for r in picked))
        print("взято %d из %d, по стриму: %s — %s"
              % (len(picked), len(rows), _clock(span[0]), _clock(span[1])))
    return picked


def _clock(seconds) -> str:
    seconds = int(seconds or 0)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def row_features(row) -> dict:
    try:
        features = json.loads(row["features_json"] or "{}")
    except (TypeError, ValueError):
        features = {}
    return {"id": row["id"], "model_score": row["model_score"], "features": features,
            "hand": row["model"] == _st.HAND}


def candidate_rows(store: Store) -> list[dict]:
    return [row_features(row) for row in store.list_candidates()]


def cmd_train(args, cfg: Config, store: Store) -> int:
    comparisons = [
        (r["winner_id"], r["left_id"] if r["winner_id"] == r["right_id"] else r["right_id"])
        for r in store.db.execute(
            "SELECT left_id, right_id, winner_id FROM comparisons WHERE winner_id IS NOT NULL")
    ]
    model, report = _rr.train(candidate_rows(store), store.rating_index(), comparisons)
    print(f"кандидатов {report['candidates']}, пар для обучения {report['pairs']}")
    if model is None:
        print(f"Не обучаю: {report['reason']}")
        print("Копите сравнения: py cli.py pairs, затем py cli.py compare A B --winner a")
        return 1
    print(f"  обучающих пар   {report['learning_pairs']}")
    print(f"  отложенных пар  {report['holdout_pairs']}")
    print(f"  на обучении     {report['train_accuracy']:.2f}")
    print(f"  на отложенных   {report['holdout_accuracy']:.2f}")
    print(f"  движок как есть {report['baseline_accuracy']:.2f}")
    print()
    for name, weight in sorted(zip(model["features"], model["weights"]),
                               key=lambda item: -abs(item[1])):
        print(f"  {name:<16} {weight:+.3f}")
    print()
    if not report["better_than_engine"]:
        print("Модель не обыгрывает движок на отложенной выборке — не сохраняю.")
        print("Это нормальный результат опыта, а не поломка: данных пока мало.")
        return 1
    if report["shaky"]:
        print(f"ВНИМАНИЕ: отложенных пар всего {report['holdout_pairs']}. "
              f"На такой выборке разница между {report['holdout_accuracy']:.2f} и "
              f"{report['baseline_accuracy']:.2f} может быть случайной.")
        print("Верить этой модели рано — копите сравнения и переобучайте.\n")
    path = _rr.save(model, cfg.rerank_path)
    _learnlog.write(Path(cfg.db_path).parent,
                    "обучение: пар %d, точность %.2f против %.2f у движка"
                    % (report["pairs"], report["holdout_accuracy"],
                       report["baseline_accuracy"]))
    print(f"Модель сохранена: {path}")
    print("Она подхватится сама:  py cli.py list --rerank,  py cli.py render --rerank")
    return 0


def cmd_cache(args, cfg: Config, store: Store) -> int:
    folders = [cfg.cache_dir]
    if not args.only_clipper:
        folders.append(_cache.ave_folder())
    sources = [row["path"] for row in
               store.db.execute("SELECT path FROM videos").fetchall()]
    items = _cache.scan(*folders)
    if not args.only_clipper:
        known = {os.path.abspath(str(item["path"])) for item in items}
        items += [item for item in _cache.beside(sources)
                  if os.path.abspath(str(item["path"])) not in known]
    if not items:
        print("Кеш пуст.")
        for folder in folders:
            print(f"  {folder}")
        return 0

    groups = _cache.by_kind(items)
    total = sum(cell["size"] for cell in groups.values())
    print("Кеш:")
    for folder in folders:
        print(f"  {folder}")
    if not args.only_clipper:
        for folder in sorted({str(Path(str(p)).absolute().parent) for p in sources
                              if os.path.isfile(str(p))}):
            print(f"  {folder}   (перекладки astra рядом с исходником)")
    print()
    for name in _cache.ORDER:
        cell = groups.get(name)
        if not cell:
            continue
        print(f"  {name:<22} {cell['count']:>4} шт   {_cache.human(cell['size']):>10}")
    print(f"  {'всего':<22} {len(items):>4} шт   {_cache.human(total):>10}")

    stale = _cache.orphans(items, sources)
    stale_size = sum(item["size"] for item in stale)
    print()
    if stale:
        print(f"Перекладок без исходника: {len(stale)}, "
              f"{_cache.human(stale_size)}. Их держать незачем:")
        for item in stale:
            print(f"  {item['path'].name}  ({_cache.human(item['size'])})")
    else:
        print("Перекладок без исходника нет.")

    heavy = [item for item in items if item["kind"] == _cache.REMUX]
    if not (args.clean or args.all or args.remux):
        print()
        if heavy:
            print("Перекладок всего: %d, %s. Они пересоздаются из исходника"
                  % (len(heavy), _cache.human(sum(i["size"] for i in heavy))))
            print("за минуты, но ускоряют повторную нарезку того же стрима.")
            print()
        print("Удалить:  py cli.py cache --clean   только перекладки без исходника")
        print("          py cli.py cache --remux   все перекладки")
        print("          py cli.py cache --all     весь кеш целиком")
        return 0

    doomed = items if args.all else (heavy if args.remux else stale)
    if not doomed:
        print()
        print("Удалять нечего.")
        return 0
    gone, freed = _cache.remove(doomed)
    print()
    print(f"Удалено файлов: {gone}, освобождено {_cache.human(freed)}.")
    print("Расшифровки не тронуты: они лежат рядом со стримом, а не в кеше.")
    if args.remux:
        print("Ответы движка целы — их переспрашивать дорого.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clipper", description="Автонарезка коротких роликов")
    p.add_argument("--profile", default=None,
                   help="имя профиля; без него берётся активный")
    p.add_argument("--no-profile", action="store_true", help="не трогать профили")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="обработать видео или папку")
    r.add_argument("sources", nargs="+")
    r.add_argument("--top", type=int, default=10, help="сколько клипов рендерить")
    r.add_argument("--vertical", action="store_true", help="кадрировать в 9:16")
    r.add_argument("--subs", action="store_true", help="вжечь субтитры")
    r.add_argument("--no-render", action="store_true", help="только найти, не резать")
    r.add_argument("--force", action="store_true", help="перерасшифровать заново")
    r.add_argument("--backend", choices=["heuristic", "ollama", "openai-compat", "claude"],
                   help="чем оценивать фрагменты")
    r.add_argument("--local-model", help="имя локальной модели, напр. qwen2.5:7b-instruct")
    r.add_argument("--model", help="переопределить модель Claude")
    r.add_argument("--rerank", action="store_true",
                   help="настоять на модели вкуса, даже если она слаба")
    r.add_argument("--no-rerank", action="store_true",
                   help="не трогать модель вкуса, брать порядок движка")
    r.add_argument("--no-compare", action="store_true",
                   help="не сравнивать фрагменты между собой, брать оценки")
    r.add_argument("--compare-rounds", type=int, default=0,
                   help="сколько кругов сравнения (по умолчанию 2)")
    r.add_argument("--compare-group", type=int, default=0,
                   help="сколько фрагментов в одном сравнении (по умолчанию 6)")
    r.set_defaults(fn=cmd_run)

    rn = sub.add_parser("render", help="отрендерить клипы по кандидатам из базы")
    rn.add_argument("--video", type=int, required=True, help="id видео (см. list)")
    rn.add_argument("--top", type=int, default=20)
    rn.add_argument("--vertical", action="store_true")
    rn.add_argument("--subs", action="store_true")
    rn.add_argument("--rerank", action="store_true",
                    help="настоять на модели вкуса, даже если она слаба")
    rn.add_argument("--no-rerank", action="store_true",
                    help="не трогать модель вкуса, брать порядок движка")
    rn.set_defaults(fn=cmd_render)

    bk = sub.add_parser("backends", help="какие движки доступны")
    bk.set_defaults(fn=cmd_backends)

    l = sub.add_parser("list", help="показать кандидатов")
    l.add_argument("--video", type=int, default=None)
    l.add_argument("--top", type=int, default=0,
                   help="пометить звёздочкой то, что уйдёт в export --top N")
    l.add_argument("--rerank", action="store_true",
                   help="настоять на модели вкуса, даже если она слаба")
    l.add_argument("--no-rerank", action="store_true",
                   help="не трогать модель вкуса, брать порядок движка")
    l.set_defaults(fn=cmd_list)

    ex = sub.add_parser("export", help="выгрузить моменты для монтажа в astra")
    ex.add_argument("--video", type=int, default=None, help="id видео (см. list)")
    ex.add_argument("--rerank", action="store_true",
                    help="настоять на модели вкуса, даже если она слаба")
    ex.add_argument("--no-rerank", action="store_true",
                    help="не трогать модель вкуса, брать порядок движка")
    ex.add_argument("-o", "--out", default="moments.txt", help="куда записать")
    ex.add_argument("--top", type=int, default=20, help="сколько моментов взять")
    ex.add_argument("--min-score", type=float, default=0.0, help="порог оценки")
    ex.set_defaults(fn=cmd_export)

    ln = sub.add_parser("learn", help="связать с базой astra: что реально зашло")
    ln.add_argument("--astra-db", default=None,
                    help="путь к clips.db (по умолчанию astra/clips.db)")
    ln.add_argument("--min-overlap", type=float, default=0.5,
                    help="насколько клип должен совпасть с кандидатом по времени")
    ln.set_defaults(fn=cmd_learn)

    ra = sub.add_parser("rate", help="оценить клип от 1 до 5")
    ra.add_argument("candidate", type=int)
    ra.add_argument("rating", type=int)
    ra.set_defaults(fn=cmd_rate)

    pp = sub.add_parser("pairs", help="предложить пары для сравнения")
    pp.add_argument("--video", type=int, default=None)
    pp.add_argument("--limit", type=int, default=5)
    pp.set_defaults(fn=cmd_pairs)

    c = sub.add_parser("compare", help="записать попарное сравнение")
    c.add_argument("left", type=int)
    c.add_argument("right", type=int)
    c.add_argument("--winner", choices=["a", "b", "tie"], required=True)
    c.set_defaults(fn=cmd_compare)

    rf = sub.add_parser("ref", help="добавить референсный ролик")
    rf.add_argument("paths", nargs="+")
    rf.add_argument("--note", default="")
    rf.set_defaults(fn=cmd_ref)

    st = sub.add_parser("stats", help="сколько накоплено обучающих данных")
    st.set_defaults(fn=cmd_stats)

    tr = sub.add_parser("train", help="обучить реранкер на ваших оценках")
    tr.set_defaults(fn=cmd_train)

    ca = sub.add_parser("cache", help="что накопилось в кеше и как это убрать")
    ca.add_argument("--clean", action="store_true",
                    help="удалить перекладки, у которых не осталось исходника")
    ca.add_argument("--remux", action="store_true",
                    help="удалить все перекладки, даже если исходник на месте")
    ca.add_argument("--all", action="store_true",
                    help="удалить весь кеш целиком")
    ca.add_argument("--only-clipper", action="store_true",
                    help="не трогать общий ave_cache во временной папке")
    ca.set_defaults(fn=cmd_cache)

    ad = sub.add_parser("adopt",
                        help="взять в базу клипы, смонтированные в astra")
    ad.add_argument("folder")
    ad.add_argument("--video", type=int, default=None)

    pk = sub.add_parser("picks", help="взять ваши клипы как пример вкуса")
    pk.add_argument("folder", nargs="?", default="",
                    help="папка с вашими клипами (таймкод в имени)")
    pk.add_argument("--video", type=int, default=None, help="id видео (см. list)")
    pk.add_argument("--slack", type=float, default=90.0,
                    help="допуск при поиске кандидата, секунд")
    pk.add_argument("--pairs", type=int, default=4,
                    help="сколько невзятых сравнить с каждым взятым")
    pk.add_argument("--seed", type=int, default=1)
    pk.add_argument("--forget", action="store_true",
                    help="убрать всё, что эта команда вывела")
    pk.set_defaults(fn=cmd_picks)
    ad.add_argument("--slack", type=float, default=6.0,
                    help="допуск по времени, сек")
    ad.set_defaults(fn=cmd_adopt)

    return p


# Подметание запускается только там, где и так создаются файлы. На list,
# stats и export оно бы удаляло ролики в ответ на команду «покажи».
SWEEPING = {"run", "render"}


def apply_profile(args, cfg: Config):
    if getattr(args, "no_profile", False):
        return None
    name = getattr(args, "profile", None)
    try:
        profile = _pro.load(name) if name else _pro.active()
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError(f"профиль: {exc}")
    if profile is None:
        return None
    print(f"профиль: {profile['name']}")
    if "CLIPPER_WHISPER" not in os.environ and profile.get("whisper"):
        cfg.whisper_model = profile["whisper"]
    if "CLIPPER_LANG" not in os.environ and profile.get("language"):
        cfg.language = profile["language"]
    if "CLIPPER_OUT" not in os.environ:
        cfg.out_dir = _pro.clips_dir(profile)
    cfg.db_file = _pro.db_path(profile)
    cfg.rerank_file = _pro.rerank_path(profile)
    _pro.adopt_former_rerank(report=print)
    return profile


def run_sweep(profile, cfg: Config, store: Store) -> None:
    keep = int((profile or {}).get("keep_days", 0) or 0)
    if keep <= 0:
        return
    removed = _pro.sweep(cfg.out_dir, keep, protect=_pro.protected(cfg.db_path),
                         report=lambda text: print("  " + text))
    if removed:
        print(f"  по сроку {keep} дн. удалено роликов: {len(removed)}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config()
    profile = apply_profile(args, cfg)
    cfg.ensure_dirs()
    store = Store(cfg.db_path)
    try:
        if args.cmd in SWEEPING:
            run_sweep(profile, cfg, store)
        return args.fn(args, cfg, store)
    except KeyboardInterrupt:
        print("\nПрервано.")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"\nОшибка: {exc}")
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
