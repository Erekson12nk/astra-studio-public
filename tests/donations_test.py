# -*- coding: utf-8 -*-
"""Донаты и «отошёл»: от звука до вырезов в клипе и в большом ролике.

Синтетика: два разных звука доната (под разные суммы) в шумном «стриме»,
подставная расшифровка, подставные отпечатки голоса и подставная модель,
которая решает, по теме донат или нет. Проверяется вся цепочка: звуки
хранятся в профиле, находятся оба, донат не по теме превращается в вырез
вместе с ответом стримера, по теме — остаётся, без расшифровки — не трогается.
Вырезы доходят до файла таймкодов, до движка клипов и до сборки большого
ролика (настоящий ffmpeg на коротком искусственном видео).
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)
_sys.path.insert(0, _os.path.join(_REPO, "clipmaker"))

import json
import shutil
import subprocess
import tempfile

import numpy as np

from core import away as _away
from core import donations as _don
from core import ffmpeg as _ff
from core import handoff as _hand
from core import jingle as _jingle
from core import voiceid as _voice

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-56s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


RATE = _don.RATE
random = np.random.default_rng(11)


def tone(notes, seconds=1.2):
    t = np.arange(int(seconds * RATE)) / float(RATE)
    out = np.zeros_like(t)
    part = t.size // len(notes)
    for index, freq in enumerate(notes):
        piece = slice(index * part, (index + 1) * part)
        local = t[piece] - t[piece][0]
        out[piece] = np.sin(2 * np.pi * freq * local) * np.exp(-local * 3.0)
    return (out * 0.6).astype(np.float32)


def noise(seconds):
    count = int(seconds * RATE)
    t = np.arange(count) / float(RATE)
    pitch = 150.0 + 30.0 * np.sin(2 * np.pi * 0.4 * t)
    phase = 2 * np.pi * np.cumsum(pitch) / RATE
    voice = sum(np.sin(k * phase) / k for k in range(1, 6)) * 0.08
    return (voice + random.normal(0.0, 0.03, count)).astype(np.float32)


small = tone((660.0, 990.0, 1320.0))
big = tone((1760.0, 1480.0, 1175.0, 880.0, 587.0), seconds=1.6)

print("звуки доната в профиле")
work = tempfile.mkdtemp(prefix="don_")
profile = {"name": "тест", "folder": work}
first = _don.add_sound(profile, (small * 32767).astype(np.int16), label="100")
second = _don.add_sound(profile, (big * 32767).astype(np.int16), label="1000")
check("два звука записаны в папку профиля",
      len(_don.sounds(profile)) == 2 and first["file"] != second["file"])
check("подпись суммы сохранилась", [s["label"] for s in _don.sounds(profile)] == ["100", "1000"])
back = _don.read_wav(_don.sounds(profile)[0]["path"])
check("wav читается обратно той же длины", back.size == small.size, str(back.size))

print("поиск обоих звуков в стриме")
stream = noise(300.0)
places = {"100": [40.0, 200.0], "1000": [120.0]}
for label, starts in places.items():
    sound = small if label == "100" else big
    for start in starts:
        index = int(start * RATE)
        stream[index:index + sound.size] += sound * 0.8
spectrum = _jingle.spectrum_of(stream)
events = _don.find_events(spectrum, _don.sounds(profile))
found = {(round(e["start"]), e["label"]) for e in events}
check("найдены все три доната", len(events) == 3, str(sorted(found)))
check("каждый со своей суммой",
      found == {(40, "100"), (200, "100"), (120, "1000")}, str(sorted(found)))
_don.remove_sound(profile, first["file"])
check("звук удаляется вместе с файлом",
      len(_don.sounds(profile)) == 1
      and not _os.path.exists(_os.path.join(work, first["file"])))

print("решение «по теме / не по теме»")
lines = [
    (30.0, 39.5, "короче это обновление игры полный провал, патч сломал всё"),
    (41.5, 47.0, "привет стример включи пожалуйста трек моргенштерна"),
    (47.5, 50.0, "не, треки не включаю"),
    (50.5, 58.0, "так вот патч сломал сохранения в игре и это провал"),
]
event = {"start": 40.0, "end": 41.2, "label": "100", "score": 0.9}


class Model:
    def __init__(self, related, reply):
        self.answer = {"related": related, "reply": reply, "reason": "подставной ответ"}
        self.asked = []

    def complete(self, system, user, cfg, schema=None):
        self.asked.append(user)
        return json.dumps(self.answer, ensure_ascii=False)


off = Model(False, 1)
plan = _don.plan_clip(25.0, 60.0, [event], lines, judge=_don.judge_with(off, None))
decision = plan["decisions"][0]
check("донат не по теме — вырезать", decision["action"] == _don.CUT, decision["reason"])
check("вырез от звука до конца ответа стримера",
      plan["drops"] and abs(plan["drops"][0][0] - 39.8) < 0.05
      and abs(plan["drops"][0][1] - 50.2) < 0.05, str(plan["drops"]))
check("модели показан текст доната",
      off.asked and "моргенштерна" in off.asked[0])
check("и тема клипа без самого доната",
      off.asked and "патч" in off.asked[0] and "не включаю" not in
      off.asked[0].split("Донат")[0])

on = Model(True, 0)
plan = _don.plan_clip(25.0, 60.0, [event], lines, judge=_don.judge_with(on, None))
check("донат по теме — оставить",
      plan["decisions"][0]["action"] == _don.KEEP and not plan["drops"])

plan = _don.plan_clip(25.0, 60.0, [event], [], judge=_don.judge_with(off, None))
check("без расшифровки донат не трогается",
      plan["decisions"][0]["action"] == _don.UNSURE and not plan["drops"])

quiet = [(30.0, 39.5, "патч сломал всё"), (50.5, 58.0, "так вот патч")]
plan = _don.plan_clip(25.0, 60.0, [event], quiet)
check("донат без текста — вырезать", plan["decisions"][0]["action"] == _don.CUT,
      plan["decisions"][0]["reason"])

plan = _don.plan_clip(39.0, 52.0, [event], lines, judge=_don.judge_with(off, None))
check("клип почти целиком из доната помечается", plan["mostly"])


class Broken:
    def complete(self, *args, **kwargs):
        raise RuntimeError("сервер упал")


plan = _don.plan_clip(25.0, 60.0, [event], lines, judge=_don.judge_with(Broken(), None))
check("модель упала — не вырезаем наугад",
      plan["decisions"][0]["action"] == _don.UNSURE, plan["decisions"][0]["reason"])

print("голоса: стример, робот, кто-то ещё")
size = 64
streamer = _voice.unit(random.normal(size=size))
robot = _voice.unit(random.normal(size=size))
stranger = _voice.unit(random.normal(size=size))


def around(base, count):
    return np.asarray([_voice.unit(base + random.normal(scale=0.1, size=size))
                       for _ in range(count)], dtype=np.float32)


timeline = [streamer] * 60 + [robot] * 8 + [streamer] * 30 + [None] * 20 + [stranger] * 10
vec = np.zeros((len(timeline), size), dtype=np.float32)
for index, who in enumerate(timeline):
    if who is not None:
        vec[index] = around(who, 1)[0]
voice = {"time": np.arange(len(timeline), dtype=np.float32) + 1.0, "vec": vec,
         "voiced": np.asarray([who is not None for who in timeline]),
         "level": np.zeros(len(timeline), np.float32), "window": 2.0, "step": 1.0}
ref = _voice.reference(voice)
check("голос стримера собирается сам — самый частый",
      ref is not None and float(ref @ streamer) > 0.9, "%.2f" % float(ref @ streamer))
marks = _voice.labels(voice, {_voice.STREAMER: ref, _voice.ROBOT: robot})
check("робот узнаётся отдельно от стримера",
      list(marks[60:68]).count(_voice.ROBOT) >= 7)
check("тишина и чужой голос не стример",
      set(marks[98:118]) == {_voice.QUIET} and _voice.STREAMER not in set(marks[118:]))
learned = _don.robot_from_event(voice, {"start": 58.0, "end": 60.0}, streamer=ref)
check("голос робота берётся из речи после доната",
      learned is not None and float(learned @ robot) > 0.9)
fake_event = {"start": 58.5, "end": 60.4}
check("конец зачитывания — где робот замолчал",
      abs(_don.reading_end(fake_event, marks, voice) - 68.5) <= 1.0,
      "%.1f" % _don.reading_end(fake_event, marks, voice))
new = _don.discover(voice, marks, [])
check("робот без известного звука — подсказка «новый звук?»",
      len(new) == 1 and abs(new[0]["end"] - 60.5) <= 1.0, str(new))
check("известный звук перед роботом подсказку убирает",
      _don.discover(voice, marks, [fake_event]) == [])

print("отошёл: робот читает чат, а стримера нет")
count = 120
signals = {"time": np.arange(count, dtype=np.float32) * 4.0 + 2.0,
           "motion": np.full(count, 9.0, dtype=np.float32),
           "face": np.asarray([1] * 40 + [0] * 40 + [1] * 40)}
chat = [streamer] * 160 + [robot] * 160 + [streamer] * 160
vec = np.asarray([around(who, 1)[0] for who in chat], dtype=np.float32)
reading = {"time": np.arange(len(chat), dtype=np.float32) + 1.0, "vec": vec,
           "voiced": np.ones(len(chat), dtype=bool),
           "level": np.zeros(len(chat), np.float32), "window": 2.0, "step": 1.0}
loud = np.full(4000, -10.0, np.float32)
loud[:800] = -40.0
levels = {"level": loud, "voice": np.ones(4000, np.float32), "window": 0.25}
old = _away.find_away(signals, levels)
check("по тишине и заставке уход не виден (было так)", old == [], str(old))
spans = _away.find_away(signals, levels, voice=reading, ref=streamer)
check("по голосу стримера уход найден",
      len(spans) == 1 and 150 <= spans[0][0] <= 175 and 310 <= spans[0][1] <= 330,
      str(spans))
said = _away.find_away(signals, levels, voice=reading, ref=streamer,
                       lines=[(150.0, 152.0, "так, щас вернусь")])
check("фраза «щас вернусь» попадает в причину",
      said and "щас вернусь" in said[0][2], said[0][2] if said else "")

print("вырезы в файле таймкодов и в движке клипов")
text = _hand.format_timecodes([{"start": 25.0, "end": 60.0, "title": "патч",
                                "score": 8.0,
                                "drops": [(39.8, 50.2, "донат не по теме")]},
                               {"start": 100.0, "end": 130.0, "title": "без вырезов"}])
items, skipped = _hand.parse_timecodes_text(text)
check("вырез записан строкой-комментарием", "# вырезать 0:00:39.800" in text)
check("вырез читается обратно и цепляется к своему клипу",
      items[0].get("drops") and abs(items[0]["drops"][0][0] - 39.8) < 0.01
      and "drops" not in items[1] and not skipped)
check("для движка вырез переводится во время клипа",
      _hand.relative_drops(items[0]["drops"], 25.0, 60.0) == [(14.8, 25.2)]
      or np.allclose(_hand.relative_drops(items[0]["drops"], 25.0, 60.0), [(14.8, 25.2)]))
check("вырез за границей клипа обрезается",
      np.allclose(_hand.relative_drops([(20.0, 30.0)], 25.0, 60.0), [(0.0, 5.0)]))

print("большой ролик собирается без вырезанных кусков")
import clipmaker as _cm

pieces = _cm.keep_pieces(60.0, 440.0, [(100.0, 130.0, "донат"), (400.0, 420.0)])
check("куски между вырезами", pieces == [(60.0, 40.0), (130.0, 270.0), (420.0, 80.0)],
      str(pieces))
ffmpeg = _ff.find("ffmpeg")
video = _os.path.join(work, "stream.mp4")
made = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                       "testsrc=size=320x180:rate=25:duration=30", "-f", "lavfi", "-i",
                       "sine=frequency=440:duration=30", "-c:v", "libx264", "-preset",
                       "ultrafast", "-c:a", "aac", "-shortest", video],
                      capture_output=True).returncode == 0
check("искусственный стрим собран", made)
target = _os.path.join(work, "rolik.mp4")
code = _cm.make_clip({"input": video, "start": "2", "end": "22", "output": target,
                      "crf": 30, "drops": [(8.0, 12.0, "донат"), (16.0, 18.0, "отошёл")]},
                     on_line=lambda line: None)
length = _ff.duration(target) if code == 0 and _os.path.isfile(target) else 0.0
check("ролик собран из кусков: 20 с минус 6 с вырезов", abs(length - 14.0) < 0.4,
      "%.2f с" % length)
check("происхождение не пишется, раз время рваное",
      not _os.path.isfile(_hand.origin_path(target)))
leftovers = [name for name in _os.listdir(work) if ".part" in name]
check("временные куски убраны", leftovers == [], str(leftovers))

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
