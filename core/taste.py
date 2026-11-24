from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

FILE_NAME = "taste_rules.json"
LEAST = 6
SIDE = 5
FRESH_DAYS = 2.0
TEXT_LIMIT = 700
RULES_LIMIT = 7

SYSTEM = """Ты — аналитик коротких видео одного канала. Тебе дают клипы с числом
просмотров: самые сильные и самые слабые. Найди, чем сильные отличаются от слабых
по содержанию: тема, тип момента (реакция, спор, шутка, история, донат, игра),
как начинается, есть ли развязка, длина.

Сформулируй до {limit} коротких правил для редактора, который будет выбирать новые
моменты из стримов этого же канала. Каждое правило — одна строка, конкретная,
проверяемая по расшифровке: «брать …», «не брать …». Не выдумывай того, чего нет
в данных. Если разницы не видно или данных мало — верни меньше правил и скажи
об этом в note.

Отвечай на русском языке."""

USER = """Канал: {channel}

СИЛЬНЫЕ (больше всего просмотров в сутки):
{strong}

СЛАБЫЕ (меньше всего просмотров в сутки):
{weak}

Ответ — только JSON: {{"rules": ["…"], "note": "коротко, насколько уверен"}}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["rules", "note"],
    "additionalProperties": False,
}


def path_of(profile) -> Path:
    return Path(profile["folder"]) / FILE_NAME


def load(profile) -> dict:
    if not profile or not profile.get("folder"):
        return {}
    try:
        data = json.loads(path_of(profile).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def rules_text(profile) -> str:
    rules = [str(rule).strip() for rule in load(profile).get("rules") or () if str(rule).strip()]
    return "\n".join("- " + rule for rule in rules[:RULES_LIMIT])


def save(profile, rules, note, based_on) -> dict:
    data = {"rules": [str(rule).strip() for rule in rules if str(rule).strip()][:RULES_LIMIT],
            "note": str(note or "").strip(), "based_on": based_on, "made_at": time.time()}
    place = path_of(profile)
    place.parent.mkdir(parents=True, exist_ok=True)
    place.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def posted(db, now=None) -> list:
    now = time.time() if now is None else now
    if not os.path.isfile(str(db)):
        return []
    link = sqlite3.connect(str(db))
    link.row_factory = sqlite3.Row
    try:
        rows = link.execute(
            "SELECT p.title, p.posted_at, p.video_id, c.file, c.source, c.start, c.finish,"
            " (SELECT views FROM stats s WHERE s.video_id = p.video_id"
            "  ORDER BY s.at DESC LIMIT 1) AS views"
            " FROM posts p JOIN clips c ON c.id = p.clip_id"
            " WHERE p.dry = 0 AND p.service = 'youtube' AND p.video_id <> ''").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        link.close()
    out = []
    seen = set()
    for row in rows:
        item = dict(row)
        if item["views"] is None or item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        days = (now - float(item["posted_at"] or now)) / 86400.0
        if days < FRESH_DAYS:
            continue
        item["days"] = days
        item["per_day"] = float(item["views"]) / max(days, 1.0)
        out.append(item)
    return out


SOURCES = ("own",)
SOURCE_TITLES = {"own": "свои выгрузки"}


def gather(profile, db, sources=("own",), now=None) -> list:
    """Клипы для сравнения из выбранных мест; у каждого — сила внутри своего канала.

    Просмотры в тиктоке и на ютубе, на большом и маленьком канале несравнимы,
    поэтому клип сравнивается с серединой своего канала: rank = в сутки / медиана.
    """
    items = []
    if "own" in sources:
        items += [dict(item, group="own") for item in posted(db, now=now)]
    groups = {}
    for item in items:
        groups.setdefault(item["group"], []).append(item["per_day"])
    for item in items:
        values = sorted(groups[item["group"]])
        middle = values[len(values) // 2] or 1.0
        item["rank"] = item["per_day"] / max(middle, 1e-9)
    return items


def split(items, side=SIDE):
    ordered = sorted(items, key=lambda item: item.get("rank", item["per_day"]), reverse=True)
    if len(ordered) < 2:
        return ordered, []
    half = min(side, len(ordered) // 2)
    return ordered[:half], ordered[-half:]


def clip_text(item, transcribe=None) -> str:
    from . import donations as _don

    file = str(item.get("file") or "")
    if file:
        lines = _don.load_transcript(file)
        if lines:
            return " ".join(text for _l, _r, text in lines)[:TEXT_LIMIT]
    source, start = item.get("source"), item.get("start")
    if source and start is not None:
        finish = item.get("finish") if item.get("finish") is not None else float(start) + 60.0
        lines = _don.load_transcript(source)
        text = _don.text_between(lines, float(start), float(finish))
        if text:
            return text[:TEXT_LIMIT]
    if transcribe and file and os.path.isfile(file):
        try:
            return str(transcribe(file) or "")[:TEXT_LIMIT]
        except Exception:
            return ""
    return ""


def render(items, transcribe=None) -> str:
    blocks = []
    for item in items:
        head = "«%s» — %d просм., %.1f в сутки" % (str(item.get("title") or "").strip(),
                                                 int(item["views"]), item["per_day"])
        text = clip_text(item, transcribe) or "(расшифровки нет)"
        blocks.append(head + "\n" + text)
    return "\n\n".join(blocks)


def build(profile, db, backend, cfg, channel="", transcribe=None, now=None, log=None,
          sources=("own",)) -> dict:
    complete = getattr(backend, "complete", None)
    if not callable(complete):
        raise RuntimeError("нужен движок с языковой моделью (LM Studio, Ollama или Claude), "
                           "а не heuristic")
    items = gather(profile, db, sources, now=now)
    if len(items) < LEAST:
        raise RuntimeError("мало данных: клипов с просмотрами старше %d дней — %d, нужно "
                           "хотя бы %d. Нажмите «Обновить просмотры» позже или подключите "
                           "каналы со статистикой." % (int(FRESH_DAYS), len(items), LEAST))
    strong, weak = split(items)
    if log:
        log("сравниваю %d сильных и %d слабых клипов" % (len(strong), len(weak)))
    from . import detect as _detect

    user = USER.format(channel=channel or profile.get("name") or "",
                       strong=render(strong, transcribe), weak=render(weak, transcribe))
    answer = _detect.extract_json(complete(SYSTEM.format(limit=RULES_LIMIT), user, cfg, SCHEMA))
    if not isinstance(answer, dict) or not isinstance(answer.get("rules"), list):
        raise RuntimeError("модель не вернула правила")
    data = save(profile, answer["rules"], answer.get("note"), len(items))
    return data


def whisper_reader(model_name, language="ru"):
    state = {}

    def read(file):
        from . import asr as _asr

        key = _asr.cache_key(file, model_name, language or "")
        cached = _asr.load_cache(file, key)
        if cached is None:
            if "model" not in state:
                state["model"] = _asr.load_model(model_name)
            cached = _asr.transcribe(str(file), state["model"], language=language or None)
            _asr.save_cache(file, key, cached)
        return " ".join(segment.text.strip() for segment in cached.segments)

    return read
