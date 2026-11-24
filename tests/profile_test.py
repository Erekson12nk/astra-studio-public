# -*- coding: utf-8 -*-
"""Профили: фотография обязательна, срок хранения, что подметание не трогает."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os, sqlite3, sys, tempfile, time

work = tempfile.mkdtemp()
os.environ["ASTRA_PROFILES"] = os.path.join(work, "profiles")

from core import profiles as P

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


def fails(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception:
        return True
    return False


photo = os.path.join(work, "лицо.png")
open(photo, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
not_a_photo = os.path.join(work, "заметка.txt")
open(not_a_photo, "w").write("нет")

# --- создание ---
check("без фотографии профиль не создаётся", fails(P.create, "Аня", None))
check("фотографии нет на диске — отказ", fails(P.create, "Аня", photo + ".нет"))
check("не картинка — отказ", fails(P.create, "Аня", not_a_photo))

profile = P.create("Стример", photo, keep_days=3)
check("профиль создан", profile["name"] == "Стример")
check("фотография скопирована в профиль", P.face_path(profile) is not None)
check("срок хранения записан", profile["keep_days"] == 3, profile["keep_days"])
check("расшифровка по умолчанию turbo", profile["whisper"] == "large-v3-turbo",
      profile["whisper"])
check("повторное создание запрещено", fails(P.create, "Стример", photo))
check("пустое имя запрещено", fails(P.create, "   ", photo))

P.set_active("Стример")
check("активный профиль запоминается", P.active()["name"] == "Стример")
check("список профилей находит его", P.names() == ["Стример"], P.names())

profile["keep_days"] = 7
P.save(profile)
check("настройки переживают перезапись", P.load("Стример")["keep_days"] == 7)

# --- срок хранения ---
clips = os.path.join(work, "out")
os.makedirs(os.path.join(clips, "стрим"), exist_ok=True)
os.makedirs(os.path.join(clips, "_оставить"), exist_ok=True)
now = time.time()


def clip(*parts, age_days=0.0):
    path = os.path.join(clips, *parts)
    open(path, "wb").write(b"0" * 10)
    stamp = now - age_days * 86400.0
    os.utime(path, (stamp, stamp))
    return path


fresh = clip("стрим", "свежий.mp4", age_days=1)
stale = clip("стрим", "старый.mp4", age_days=5)
saved = clip("_оставить", "дорогой.mp4", age_days=99)
rated = clip("стрим", "оценённый.mp4", age_days=9)
text = clip("стрим", "заметка.txt", age_days=9)

names = {os.path.basename(p) for p in P.expired(clips, 3, now)}
check("свежий ролик не трогается", "свежий.mp4" not in names)
check("старый ролик попадает под срок", "старый.mp4" in names)
check("папка с подчёркиванием не подметается", "дорогой.mp4" not in names)
check("не-видео не трогается", "заметка.txt" not in names)
check("нулевой срок отключает подметание", P.expired(clips, 0, now) == [])

guarded = {os.path.basename(p) for p in P.expired(clips, 3, now, protect=[rated])}
check("защищённый файл не попадает под срок", "оценённый.mp4" not in guarded)

# --- защита берётся из базы clipper ---
db = os.path.join(work, "clipper.sqlite3")
con = sqlite3.connect(db)
con.executescript("""
CREATE TABLE ratings (id INTEGER PRIMARY KEY, candidate_id INTEGER, rating INTEGER,
                      created_at REAL);
CREATE TABLE comparisons (id INTEGER PRIMARY KEY, left_id INTEGER, right_id INTEGER,
                          winner_id INTEGER, created_at REAL);
CREATE TABLE renders (candidate_id INTEGER, path TEXT, created_at REAL);
""")
con.execute("INSERT INTO ratings VALUES (1, 42, 4, 0)")
con.execute("INSERT INTO renders VALUES (42, ?, 0)", (rated,))
con.execute("INSERT INTO comparisons VALUES (1, 77, 78, 77, 0)")
con.execute("INSERT INTO renders VALUES (77, ?, 0)", (stale,))
con.commit()
con.close()

guard = P.protected(db)
check("оценённый клип попал в защиту", rated in guard)
check("сравнённый клип попал в защиту", stale in guard)
check("нет базы — пустая защита", P.protected(os.path.join(work, "нет.sqlite3")) == set())

survivors = {os.path.basename(p) for p in P.expired(clips, 3, now, protect=guard)}
check("защита из базы работает на подметании",
      "оценённый.mp4" not in survivors and "старый.mp4" not in survivors)

# --- само удаление ---
doomed = clip("стрим", "обречённый.mp4", age_days=5)
removed = P.sweep(clips, 3, now, protect=guard)
check("подметание вернуло список удалённого", doomed in [str(p) for p in removed])
check("файл действительно удалён", not os.path.isfile(doomed))
check("защищённый файл на месте", os.path.isfile(rated))
check("отложенная папка цела", os.path.isfile(saved))
check("свежий на месте", os.path.isfile(fresh))

P.remove("Стример")
check("профиль удаляется", P.names() == [])
check("активным ничего не осталось", P.active() is None)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
