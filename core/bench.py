from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

from . import handoff as _handoff

HIT = 3.0
COUNTS = (15, 30)
LOG_NAME = "bench.log"
UNIFORM = "равномерно"


def span(item):
    if isinstance(item, dict):
        return float(item["start"]), float(item["end"])
    if isinstance(item, (tuple, list)):
        return float(item[0]), float(item[1])
    return float(item.start), float(item.end)


def overlap(left, right) -> float:
    a0, a1 = span(left)
    b0, b1 = span(right)
    return max(0.0, min(a1, b1) - max(a0, b0))


def load_marks(path):
    with open(path, encoding="utf-8-sig") as handle:
        items, _skipped = _handoff.parse_timecodes_text(handle.read())
    return items


def score(marks, candidates, hit=HIT) -> dict:
    found = [mark for mark in marks
             if any(overlap(mark, cand) >= hit for cand in candidates)]
    right = [cand for cand in candidates
             if any(overlap(mark, cand) >= hit for mark in marks)]
    return {"marks": len(marks), "candidates": len(candidates),
            "found": len(found), "right": len(right),
            "coverage": len(found) / len(marks) if marks else 0.0,
            "precision": len(right) / len(candidates) if candidates else 0.0,
            "total": sum(span(c)[1] - span(c)[0] for c in candidates)}


def equalize(candidates, total, duration=None) -> list:
    spans = [span(c) for c in candidates]
    have = sum(end - start for start, end in spans)
    if not spans or have <= 0 or total <= 0:
        return [{"start": s, "end": e} for s, e in spans]
    factor = float(total) / have
    out = []
    for start, end in spans:
        half = (end - start) * factor / 2.0
        middle = (start + end) / 2.0
        left, right = middle - half, middle + half
        if left < 0:
            left, right = 0.0, right - left
        if duration and right > duration:
            left, right = max(0.0, left - (right - duration)), float(duration)
        out.append({"start": left, "end": right})
    return out


def uniform(duration, count, length) -> list:
    if count <= 0 or duration <= 0:
        return []
    step = float(duration) / count
    length = min(float(length), step)
    return [{"start": step * (i + 0.5) - length / 2.0,
             "end": step * (i + 0.5) + length / 2.0} for i in range(count)]


def compare(marks, searches, count, duration, length=None, hit=HIT) -> list:
    picked = [(name, list(found(count) if callable(found) else found)[:count])
              for name, found in searches]
    if length:
        total = float(length) * count
    else:
        total = next((score(marks, found)["total"] for _name, found in picked if found),
                     0.0)
    rows = []
    if total > 0:
        rows.append((UNIFORM, score(marks, uniform(duration, count, total / count), hit)))
    for name, found in picked:
        rows.append((name, score(marks, equalize(found, total, duration), hit)))
    return rows


def sound_search(video, min_len=35.0, max_len=75.0, lead=2.0):
    from . import loudness as _loud
    data = _loud.audio_levels(video)

    def search(count):
        return _loud.pick_moments(data, count, min_len, max_len, lead=lead)

    return search, data["level"].size * data["window"]


def has_sound_cache(video) -> bool:
    from . import loudness as _loud
    store = _loud.cache_path(video, _loud.WINDOW, _loud.RATE)
    return bool(store and os.path.isfile(store))


def percent(value) -> str:
    return "%.1f%%" % (100.0 * value)


def table(results) -> str:
    names = []
    for _count, rows in results:
        for name, _row in rows:
            if name not in names:
                names.append(name)
    head = ["", *names]
    lines = []
    for count, rows in results:
        found = dict(rows)
        for label, key, part in (("покрытие", "coverage", "found"),
                                 ("точность", "precision", "right")):
            cells = []
            for name in names:
                row = found.get(name)
                cells.append("—" if row is None else "%s (%d)" % (percent(row[key]),
                                                                  row[part]))
            lines.append(["%d клипов, %s" % (count, label), *cells])
    widths = [max(len(str(r[i])) for r in [head, *lines]) for i in range(len(head))]
    out = ["  ".join(str(c).ljust(w) for c, w in zip(head, widths)).rstrip()]
    out.append("  ".join("-" * w for w in widths))
    out += ["  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip() for r in lines]
    return "\n".join(out)


def log_path() -> Path:
    from . import profiles as _profiles
    profile = _profiles.active()
    if profile and profile.get("folder"):
        return Path(profile["folder"]) / LOG_NAME
    return _profiles.root() / LOG_NAME


def revision() -> str:
    try:
        done = subprocess.run(["git", "describe", "--always", "--dirty"],
                              cwd=os.path.dirname(os.path.abspath(__file__)),
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5)
        return done.stdout.decode().strip() or "-"
    except Exception:
        return "-"


def write_log(path, video, marks_path, results, now=None, rev=None) -> None:
    now = now or datetime.datetime.now()
    rev = rev or revision()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for count, rows in results:
            for name, row in rows:
                handle.write("%s\t%s\t%s\t%s\t%d\t%s\tпокрытие %s (%d/%d)\t"
                             "точность %s (%d/%d)\tхронометраж %.0f с\n"
                             % (now.strftime("%Y-%m-%d %H:%M"), rev,
                                os.path.basename(str(video)),
                                os.path.basename(str(marks_path)), count, name,
                                percent(row["coverage"]), row["found"], row["marks"],
                                percent(row["precision"]), row["right"],
                                row["candidates"], row["total"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="py -m core.bench",
        description="Замер автопоиска моментов по ручной разметке.")
    parser.add_argument("video")
    parser.add_argument("marks")
    parser.add_argument("--count", type=int, nargs="+", default=list(COUNTS))
    parser.add_argument("--from", dest="extra", action="append", default=[],
                        metavar="ИМЯ=ФАЙЛ",
                        help="кандидаты другого поиска из файла таймкодов")
    parser.add_argument("--no-sound", action="store_true",
                        help="не запускать поиск по звуку")
    parser.add_argument("--length", type=float, default=None,
                        help="длина клипа для уравнивания, с (по умолчанию как у первого поиска)")
    parser.add_argument("--hit", type=float, default=HIT)
    parser.add_argument("--min", dest="min_len", type=float, default=35.0)
    parser.add_argument("--max", dest="max_len", type=float, default=75.0)
    parser.add_argument("--no-log", action="store_true")
    args = parser.parse_args(argv)

    marks = load_marks(args.marks)
    if not marks:
        print("В разметке нет ни одного момента: %s" % args.marks)
        return 2
    print("Разметка: %d моментов на %.1f мин материала"
          % (len(marks), sum(m["end"] - m["start"] for m in marks) / 60.0))

    duration = 0.0
    sound = None
    if not args.no_sound:
        if not has_sound_cache(args.video):
            print("Кеша признаков нет — звук будет прочитан целиком, это долго.")
        sound, duration = sound_search(args.video, args.min_len, args.max_len)
    if duration <= 0:
        from . import ffmpeg as _ff
        duration = _ff.duration(args.video)
    if duration <= 0:
        print("Не узнал длительность видео: %s" % args.video)
        return 2

    searches = []
    if sound is not None:
        searches.append(("звук", sound))
    for given in args.extra:
        name, _sep, path = given.partition("=")
        if not path:
            name, path = os.path.splitext(os.path.basename(given))[0], given
        searches.append((name, load_marks(path)))

    results = [(count, compare(marks, searches, count, duration, args.length, args.hit))
               for count in args.count]
    print(table(results))
    if not args.no_log:
        target = log_path()
        write_log(target, args.video, args.marks, results)
        print("Записано в %s" % target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
