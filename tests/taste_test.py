# -*- coding: utf-8 -*-
"""Просмотры → правила вкуса → задание нейронке при поиске по смыслу.

Синтетика: база выгрузок профиля с восемью клипами, у каждого просмотры
и расшифровка рядом с файлом. Подставная модель возвращает правила. Проверяется:
сильные и слабые выбираются по просмотрам в сутки, свежие ролики не участвуют,
правила ложатся в профиль, с ними собирается конфиг поиска, и они реально
попадают в системное задание модели при поиске и при сравнении.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import json
import shutil
import sqlite3
import tempfile
import time

work = tempfile.mkdtemp(prefix="taste_test_")
_os.environ["ASTRA_PROFILES"] = _os.path.join(work, "profiles")

from core import taste
from core import upload as up

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


profile = {"name": "тест", "folder": _os.path.join(work, "profile")}
_os.makedirs(profile["folder"])
db = up.db_for(profile)
now = time.time()
day = 86400.0
clips = [
    ("спор про патч", 9000, 3, "ты что несёшь патч сломал всё нет ты не прав"),
    ("реакция на донат", 7000, 4, "ааа что пятьсот рублей спасибо брат"),
    ("крик в игре", 5000, 3, "аааа меня убили как так"),
    ("история про кота", 3000, 5, "короче у меня кот залез в стиралку"),
    ("пересказ новостей", 400, 4, "сегодня вышла новость что компания объявила"),
    ("приветствие", 200, 5, "всем привет как дела сегодня играем"),
    ("ожидание очереди", 150, 3, "ждём очередь ждём ждём"),
    ("настройка звука", 100, 6, "раз раз слышно меня звук нормальный"),
    ("свежий ролик", 50000, 0.5, "только что выложен"),
]
for title, views, age, text in clips:
    path = _os.path.join(work, "%s.mp4" % title)
    with open(path, "wb") as handle:
        handle.write(b"0")
    with open(path + ".speech.json", "w", encoding="utf-8") as handle:
        json.dump({"segments": [{"start": 0.0, "end": 10.0, "text": text}]}, handle,
                  ensure_ascii=False)
    post = up.record(db, {"service": "youtube", "account": "main", "video_id": "v_" + title,
                          "url": "", "file": path, "title": title, "visibility": "public"},
                     origin={})
    link = sqlite3.connect(str(db))
    link.execute("UPDATE posts SET posted_at = ? WHERE id = ?", (now - age * day, post))
    link.execute("INSERT INTO stats (video_id, at, views) VALUES (?, ?, ?)",
                 ("v_" + title, now, views))
    link.commit()
    link.close()

items = taste.posted(db, now=now)
check("свежий ролик (меньше 2 дней) не участвует",
      len(items) == 8 and all(item["title"] != "свежий ролик" for item in items))
strong, weak = taste.split(items)
check("сильные — по просмотрам в сутки",
      [item["title"] for item in strong[:2]] == ["спор про патч", "реакция на донат"])
check("слабые — снизу", "настройка звука" in [item["title"] for item in weak])
check("текст клипа берётся из расшифровки рядом с файлом",
      "кот залез" in taste.clip_text([i for i in items if i["title"] == "история про кота"][0]))


class Model:
    def __init__(self):
        self.asked = []

    def complete(self, system, user, cfg, schema=None):
        self.asked.append((system, user))
        return json.dumps({"rules": ["брать споры и живые реакции", "не брать приветствия",
                                     "не брать настройку звука"],
                           "note": "разница явная"}, ensure_ascii=False)


model = Model()
data = taste.build(profile, db, model, None, now=now)
check("правила получены и сохранены в профиль",
      len(data["rules"]) == 3 and _os.path.isfile(taste.path_of(profile)))
check("модель видела сильные и слабые с просмотрами и текстом",
      "спор про патч" in model.asked[0][1] and "раз раз слышно" in model.asked[0][1]
      and "в сутки" in model.asked[0][1])
check("свежий ролик модели не показан", "свежий ролик" not in model.asked[0][1])
check("правила текстом для задания", taste.rules_text(profile).startswith("- брать споры"))


class Words:
    pass


try:
    taste.build(profile, db, Words(), None, now=now)
    check("без языковой модели — понятный отказ", False)
except RuntimeError as error:
    check("без языковой модели — понятный отказ", "heuristic" in str(error))
try:
    taste.build(profile, db, model, None, now=now - 20 * day)
    check("мало данных — понятный отказ", False)
except RuntimeError as error:
    check("мало данных — понятный отказ", "мало данных" in str(error))

from core import profiles as _pro

_pro.save({"name": "стример", "created": now, "face": "", "keep_days": 3})
active = _pro.load("стример")
taste.save(active, ["брать споры", "не брать приветствия"], "", 8)
_pro.set_active("стример")
from clipper.config import Config, with_profile

cfg = with_profile(Config())
check("конфиг поиска получает правила активного профиля",
      "брать споры" in cfg.taste_rules and "приветствия" in cfg.taste_rules)

from clipper.backends.base import LLMBackend, with_taste


class Spy(LLMBackend):
    name = "spy"

    def __init__(self):
        self.systems = []

    def complete(self, system, user, cfg, schema=None):
        self.systems.append(system)
        return json.dumps({"clips": [], "order": [1, 2]})


spy = Spy()
cfg.min_clip_s, cfg.max_clip_s = 20.0, 60.0
spy.propose_chunk("[1] привет", {"chunk_no": 1, "chunk_total": 1, "lo": 1, "hi": 1,
                                 "limit": 3, "sec_per_sentence": 2.0}, cfg)
spy.compare_group([{"no": 1, "text": "а"}, {"no": 2, "text": "б"}], cfg)
check("правила попадают в задание при поиске моментов",
      "брать споры" in spy.systems[0] and "по просмотрам" in spy.systems[0])
check("и при сравнении фрагментов", "брать споры" in spy.systems[1])
cfg.taste_rules = ""
check("без правил задание прежнее", with_taste("СИСТЕМА", cfg) == "СИСТЕМА")

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
