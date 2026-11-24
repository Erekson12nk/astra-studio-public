from __future__ import annotations

import os
import re

from . import timecode as _tc

PATTERN = re.compile(
    r"(\d{1,3}:\d{1,2}(?::\d{1,2})?(?:[.,]\d+)?)"
    r"\s*(?:-{1,2}|–|—|>|\.\.\.|\bдо\b)\s*"
    r"(\d{1,3}:\d{1,2}(?::\d{1,2})?(?:[.,]\d+)?)"
    r"\s*(.*)$"
)

HEADER = ("# Моменты для монтажа. Формат: начало - конец  заголовок\n"
          "# Строки можно править руками, лишние удалять.\n")

DROP_WORD = "вырезать"
DROP_LINE = re.compile(r"^#\s*" + DROP_WORD + r"\s+(.*)$", re.IGNORECASE)


STAMP_IN_NAME = re.compile(r"(?<!\d)(\d{1,2})-(\d{2})-(\d{2})(?!\d)")


def start_from_name(name):
    found = STAMP_IN_NAME.search(os.path.basename(str(name)))
    if not found:
        return None
    hours, minutes, seconds = (int(part) for part in found.groups())
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600.0 + minutes * 60.0 + seconds


def match_candidate(start, rows, slack=6.0):
    best = None
    best_gap = slack
    for row in rows:
        gap = abs(float(row["start"]) - float(start))
        if gap <= best_gap:
            best = row
            best_gap = gap
    return best, best_gap


def stamp(seconds) -> str:
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(seconds, 3600.0)
    minutes, secs = divmod(rest, 60.0)
    return "%d:%02d:%06.3f" % (int(hours), int(minutes), secs)


def format_timecodes(items, header: bool = True) -> str:
    lines = [HEADER] if header else []
    for item in items:
        start = item["start"] if isinstance(item, dict) else item.start
        end = item["end"] if isinstance(item, dict) else item.end
        title = (item.get("title", "") if isinstance(item, dict)
                 else getattr(item, "title", "")) or ""
        score = (item.get("score") if isinstance(item, dict)
                 else getattr(item, "score", None))
        if score is not None:
            lines.append("# оценка %.1f" % float(score))
        drops = (item.get("drops") if isinstance(item, dict)
                 else getattr(item, "drops", None)) or ()
        for drop in drops:
            note = str(drop[2]).replace("\n", " ").strip() if len(drop) > 2 else ""
            lines.append(("# %s %s - %s  %s" % (DROP_WORD, stamp(drop[0]), stamp(drop[1]),
                                                note)).rstrip())
        lines.append("%s - %s  %s"
                     % (stamp(start), stamp(end), title.replace("\n", " ").strip()))
    return "\n".join(lines).rstrip() + "\n"


def parse_timecodes_text(text: str):
    items = []
    skipped = 0
    pending = []
    for raw in text.splitlines():
        line = raw.strip()
        marked = DROP_LINE.match(line)
        if marked:
            drop = _drop_range(marked.group(1))
            if drop is not None:
                pending.append(drop)
            continue
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        found = PATTERN.search(line)
        if not found:
            skipped += 1
            continue
        try:
            start = _tc.require_time(found.group(1).replace(",", "."))
            end = _tc.require_time(found.group(2).replace(",", "."))
        except ValueError:
            skipped += 1
            continue
        if end <= start:
            skipped += 1
            continue
        title = found.group(3).strip()
        title = re.sub(r"^[\(\[\{«\"'|:\-–—\s]+", "", title)
        title = re.sub(r"[\)\]\}»\"'|\s]+$", "", title)
        item = {"index": len(items) + 1, "start": start, "end": end,
                "title": title.strip()}
        inside = [drop for drop in pending if drop[1] > start and drop[0] < end]
        if inside:
            item["drops"] = inside
        pending = []
        items.append(item)
    return items, skipped


def _drop_range(text):
    found = PATTERN.search(text)
    if not found:
        return None
    try:
        start = _tc.require_time(found.group(1).replace(",", "."))
        end = _tc.require_time(found.group(2).replace(",", "."))
    except ValueError:
        return None
    if end <= start:
        return None
    return (start, end, found.group(3).strip())


def relative_drops(drops, start, end):
    out = []
    for drop in drops or ():
        left = max(float(drop[0]), float(start)) - float(start)
        right = min(float(drop[1]), float(end)) - float(start)
        if right - left > 0.05:
            out.append((left, right))
    return out


ORIGIN_SUFFIX = ".origin.json"


def origin_path(path) -> str:
    return str(path) + ORIGIN_SUFFIX


def write_origin(path, source, start: float, end: float) -> str:
    import json
    target = origin_path(path)
    payload = {"source": os.path.abspath(str(source)),
               "start": float(start), "end": float(end)}
    try:
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except OSError:
        return ""
    return target


def read_origin(path):
    import json
    target = origin_path(path)
    if not os.path.isfile(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    source = data.get("source")
    if not source or not os.path.isfile(source):
        return None
    try:
        return {"source": source, "start": float(data["start"]),
                "end": float(data["end"])}
    except (KeyError, TypeError, ValueError):
        return None


def write_timecodes(path, items) -> str:
    folder = os.path.dirname(os.path.abspath(str(path)))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(format_timecodes(items))
    return str(path)
