# -*- coding: utf-8 -*-
"""Окно «Голос и донаты» и проверка донатов при сборке ролика.

Строит настоящую Студию на временных профилях, собирает искусственный стрим
(картинка + звук с двумя звонками доната и «голосом робота» после первого),
кладёт рядом подставную расшифровку. Дальше как человек: открывает окно,
вписывает время звука доната, добавляет его в профиль, ищет донаты по стриму,
включает «убирать донаты не по теме» и собирает большой ролик. Модель, которая
решает «по теме или нет», подставная — без LM Studio.
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
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

WORK = Path(tempfile.mkdtemp(prefix="voiceui_"))
_os.environ["ASTRA_PROFILES"] = str(WORK / "profiles")
_os.environ["ASTRA_STUDIO_SETTINGS"] = str(WORK / "settings.json")

import tkinter.messagebox as _mb

DIALOGS = []
_mb.showerror = lambda *a, **k: DIALOGS.append(("error",) + a)
_mb.showinfo = lambda *a, **k: DIALOGS.append(("info",) + a)
_mb.askyesno = lambda *a, **k: True

from core import donations as _don
from core import ffmpeg as _ff
from core import profiles as _pro
from core import streamcheck as _check

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-56s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


RATE = 16000
random = np.random.default_rng(5)


def bell(seconds=1.2):
    t = np.arange(int(seconds * RATE)) / float(RATE)
    notes = (660.0, 990.0, 1320.0)
    out = np.zeros_like(t)
    part = t.size // len(notes)
    for index, freq in enumerate(notes):
        piece = slice(index * part, (index + 1) * part)
        local = t[piece] - t[piece][0]
        out[piece] = np.sin(2 * np.pi * freq * local) * np.exp(-local * 3.0)
    return out * 0.6


seconds = 120.0
count = int(seconds * RATE)
t = np.arange(count) / float(RATE)
pitch = 150.0 + 30.0 * np.sin(2 * np.pi * 0.4 * t)
audio = sum(np.sin(k * 2 * np.pi * np.cumsum(pitch) / RATE) / k for k in range(1, 6)) * 0.08
audio += random.normal(0.0, 0.03, count)
for start in (40.0, 100.0):
    index = int(start * RATE)
    audio[index:index + int(1.2 * RATE)] += bell() * 0.8
wav = WORK / "stream.wav"
from core.donations import write_wav

write_wav(wav, (np.clip(audio, -1, 1) * 32767).astype(np.int16))
source = WORK / "stream.mp4"
built = subprocess.run([_ff.find("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=320x180:rate=25:duration=%d" % seconds, "-i", str(wav),
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                        "-shortest", str(source)], capture_output=True).returncode == 0
check("искусственный стрим собран", built)
speech = {"segments": [
    {"start": 30.0, "end": 39.5, "text": "патч сломал игру, сохранения пропали"},
    {"start": 41.5, "end": 47.0, "text": "привет, включи трек моргенштерна"},
    {"start": 47.5, "end": 50.0, "text": "не, треки не включаю"},
    {"start": 50.5, "end": 70.0, "text": "так вот патч сломал сохранения"},
]}
Path(str(source) + ".speech.json").write_text(json.dumps(speech, ensure_ascii=False),
                                              encoding="utf-8")

photo = WORK / "face.png"
import cv2

cv2.imwrite(str(photo), np.full((60, 80, 3), 128, np.uint8))
profile = _pro.create("тест", str(photo))
_pro.set_active("тест")

from studio.window import Studio

app = Studio()
app.update()
app.source.set(str(source))


def wait(limit=600.0):
    begin = time.time()
    while app.runner.busy() and time.time() - begin < limit:
        app.update()
        time.sleep(0.05)
    for _ in range(20):
        app.update()
        time.sleep(0.02)


print("окно")
from studio import voice_ui

window = voice_ui.open_window(app)
window.update()
check("окно «Голос и донаты» открылось", window.winfo_exists())
check("в шапке видно профиль", "тест" in window.lbl_head.cget("text"))

print("звук доната в профиль")
window.v_start.set("0:00:40")
window.v_end.set("0:00:41.2")
window.v_label.set("100")
window._add_sound()
saved = _don.sounds(_pro.active())
check("звук добавлен и записан в профиль",
      len(saved) == 1 and saved[0]["label"] == "100", str(saved))
check("в списке окна одна строка", len(window.sounds.get_children()) == 1)
window.v_start.set("0:00:40")
window.v_end.set("0:00:20")
window._add_sound()
check("кривое время не добавляется, а объясняется",
      len(_don.sounds(_pro.active())) == 1 and DIALOGS and DIALOGS[-1][0] == "error")

print("поиск по стриму")
window._scan()
wait()
times = sorted(round(item["start"]) for item in window.found if not item.get("new"))
check("найдены оба доната", times == [40, 100], str(times))
check("в таблице столько же строк", len(window.hits.get_children()) == len(window.found))

print("галочки")
window.v_donations.set(True)
window._save_flags()
check("галочка сохранилась в профиле",
      _pro.active().get(_check.CHECK_DONATIONS) is True)
window._leave()


class Model:
    def complete(self, system, user, cfg, schema=None):
        related = "моргенштерна" not in user
        return json.dumps({"related": related, "reply": 1, "reason": "подставной"},
                          ensure_ascii=False)


print("сборка большого ролика с проверкой")
app._judge = lambda: _don.judge_with(Model(), None)
out = WORK / "out"
out.mkdir()
app.v_outdir.set(str(out))
app.v_outro.set("")
app.v_music.set("")
app.v_censor.set(False)
app.moments = [{"start": 30.0, "end": 90.0, "title": "патч"}]
app._redraw_moments()
app._build_roliks()
wait()
made = sorted((out / "с матом").glob("*.mp4"))
length = _ff.duration(str(made[0])) if made else 0.0
check("ролик собран", len(made) == 1, str([p.name for p in made]))
check("донат не по теме и ответ стримера вырезаны (60 с → ~49.6)",
      48.5 <= length <= 50.8, "%.2f с" % length)
log = app.log.get("1.0", "end")
check("в журнале видно решение по донату", "вырезать" in log and "моргенштерна" in log)

app.destroy()
shutil.rmtree(WORK, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
