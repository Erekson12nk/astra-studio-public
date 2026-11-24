# -*- coding: utf-8 -*-
"""Реакции: кривая смены видео, выбор границ, уточнение моделью, ключевые кадры.

Стрим синтетический: три «видео» разной картинки подряд. Модель подставная —
отвечает JSON, как ollama. Ключевые кадры читает настоящий ffmpeg.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _REPO)

import json
import shutil
import subprocess
import tempfile
import time

import numpy as np

work = tempfile.mkdtemp(prefix="reactions_test_")
_os.environ["ASTRA_PROFILES"] = _os.path.join(work, "profiles")

from core import autopilot as ap
from core import ffmpeg as _ff
from core import profiles as _pro
from core import reactions as rx

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


print("кривая смены")
rng = np.random.default_rng(1)
times = np.arange(0, 600, 2.0)
feats = rng.random((times.size, 4)).astype(np.float32)
grid = np.arange(60, 540, 10.0)
fast = rx.change_curve(times, feats, grid, span=60)
slow = []
for at in grid:
    a = feats[(times >= at - 60) & (times < at)].mean(axis=0)
    b = feats[(times >= at) & (times < at + 60)].mean(axis=0)
    slow.append(np.abs(a - b).mean())
check("быстрая кривая совпадает с прямым счётом", np.allclose(fast, slow, atol=1e-5))

SWITCHES = (1800.0, 4200.0)
step = 2.0
ktimes = np.arange(0, 6000, step, dtype=np.float32)
frames = np.zeros((ktimes.size, 90, 160), np.uint8)
for index, at in enumerate(ktimes):
    part = sum(at >= s for s in SWITCHES)
    scene = int(at // 45)
    base = (40, 140, 220)[part] + (scene * 37 % 50) - 25
    frames[index] = np.clip(base + rng.normal(0, 25, (90, 160)), 0, 255)
    frames[index, :, :40 + 40 * part] = 255 - base
look = {"time": ktimes, "frames": frames}
parts = rx.split_region(0.0, 6000.0, look, span=300, min_len=600)
cuts = [a for a, _b in parts[1:]]
check("смены видео найдены", len(cuts) == 2 and all(abs(c - s) <= 30 for c, s in
                                                    zip(cuts, SWITCHES)), str(cuts))
check("короче минимума не режется", rx.split_region(0, 500, look, min_len=600) == [])
found = [(1000.0, 5.0), (1100.0, 9.0), (3000.0, 4.0), (5800.0, 6.0)]
check("из близких вершин берётся сильная, края не режутся",
      rx.choose(found, 0, 6000, min_len=600, penalty=1.0, edge=120) == [1100.0, 3000.0])

print("модель")
LINES = [(1700.0, 1702.0, "ну короче"), (1703.0, 1704.0, "так, ладно"),
         (1705.0, 1707.0, "Мертвое полушарие клиентов ВБ"), (1708.0, 1710.0, "Всем привет"),
         (1790.0, 1795.0, "и вот это видео"), (4100.0, 4110.0, "чат привет как дела")]
picked, text = rx.numbered(LINES, 1650, 1800)
check("огрызки склеены в строки", len(picked) == 3 and picked[0][1].startswith("ну короче")
      and "[1]" in text and "[2]" in text, text)


class Backend:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def complete(self, system, user, cfg, schema=None):
        self.asked.append((system, user))
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


backend = Backend([{"switch": True, "line": 1}, {"title": "Мертвое полушарие клиентов WB"}])
answer = rx.switch_with(backend, None)(LINES, 1750.0)
check("модель нашла строку перехода и назвала видео",
      answer == {"at": 1700.0, "title": "Мертвое полушарие клиентов WB"}, str(answer))
check("название спрашивается отдельно, по началу видео",
      len(backend.asked) == 2 and backend.asked[1][0] == rx.TITLE_SYSTEM)
check("нет перехода — нет ответа",
      rx.switch_with(Backend([{"switch": False, "line": 0}]), None)(LINES, 1750.0) is None)
check("номер строки мимо — нет ответа",
      rx.switch_with(Backend([{"switch": True, "line": 99}]), None)(LINES, 1750.0) is None)
judge = rx.judge_with(Backend([{"reaction": True, "title": "Про ПВЗ"}]), None)
check("оценка куска", judge(LINES, 1600, 2400) == {"reaction": True, "title": "Про ПВЗ",
                                                    "reason": ""})

moved = rx.refine(LINES, [1810.0, 4200.0],
                  lambda lines, at: {"at": 1703.0, "title": "А"} if at < 2000 else
                  {"at": at + 500, "title": "далеко"})
check("модель сдвигает границу к строке, далёкий ответ не верится",
      moved == [(1703.0, "А"), (4200.0, None)], str(moved))
check("молчание модели границу не отменяет",
      rx.refine(LINES, [1810.0], lambda lines, at: None) == [(1810.0, None)])

print("разметка стрима")
chapters = [{"start": 0.0, "end": 6000.0, "game": "Just Chatting"},
            {"start": 6000.0, "end": 9000.0, "game": "Minecraft"}]
check("разговорные главы", rx.chatting(chapters) == [(0.0, 6000.0)])
verdicts = []


def fake_judge(lines, left, right):
    verdicts.append(left)
    return {"reaction": False, "title": "болтовня", "reason": "чат"}


def fake_switch(lines, at):
    return {"at": at - 10.0, "title": "Видео %d" % int(at)} if at < 3000 else None


notes = []
found = rx.find("", chapters, lines=LINES, look=look, judge=fake_judge, switch=fake_switch,
                streamer="Стример", template="{стример} смотрит: {видео}", min_len=600,
                span=300, screen=lambda path, pieces: ["", "С экрана"][:len(pieces)],
                log=notes.append)
titles = [item["title"] for item in found]
check("болтовня в начале стрима выкинута", all(item["start"] > 0 for item in found)
      and any("не реакция" in n for n in notes), str(notes))
check("реакция названа по прочитанному названию, с экрана — важнее",
      titles == ["Стример смотрит: Видео 1800", "Стример смотрит: С экрана"], str(titles))
check("сомнительный кусок не в начале — оставлен на ваше решение",
      len(found) == 2 and any("на ваше решение" in n for n in notes), str(titles))
check("реакции не заходят в игру", all(item["end"] <= 6000.0 for item in found))

print("заголовок с экрана")
check("хвост после черты срезан, как у нарезчиков",
      rx.tidy_title("ВЛЮБИЛ В СЕБЯ ДОТЕРА *Он думал что я девушка* | Марк и Кира (фильм)")
      == "ВЛЮБИЛ В СЕБЯ ДОТЕРА *Он думал что я девушка*")
check("короткое до черты не режется", rx.tidy_title("GTA | обзор") == "GTA | обзор")
shots = []


def grab(path, at, crop):
    shots.append((at, crop))
    return b"img%d" % int(at)


import numpy as np

strips = {1040.0: np.full((270, 1920, 3), 1, np.uint8), 1100.0: np.full((270, 1920, 3), 2, np.uint8),
          1160.0: np.full((270, 1920, 3), 3, np.uint8), 5040.0: np.full((270, 1920, 3), 4, np.uint8)}
asked_shots = []


def fake_strips(path, times):
    asked_shots.append(sorted(times))
    return {at: strips[at] for at in times if at in strips}


def fake_ask(image):
    import cv2

    value = int(cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)[0, 0, 0])
    return {1: "Это был ПОЗОР - АКТЕР МАЙКЛА", 2: "субтитр из видео",
            3: "это был позор – актер майкла", 4: "одна строка"}.get(value, "")


titles = rx.screen_titles("", [(1000.0, 3000.0), (5000.0, 5100.0)], fake_ask,
                          strips=fake_strips)
check("два кадра совпали — заголовок принят, один кадр — нет",
      titles == ["Это был ПОЗОР - АКТЕР МАЙКЛА", ""], str(titles))
check("все кадры — одним проходом, только внутри кусков",
      len(asked_shots) == 1 and asked_shots[0] == [1040.0, 1100.0, 1160.0, 5040.0],
      str(asked_shots))
strip = np.zeros((270, 1920, 3), np.uint8)
check("полоса заголовка вырезается из нижней четверти",
      rx.crop_png(strip, rx.TITLE_CROPS[0])[1:4] == b"PNG")
said = []


def dead(image):
    raise OSError("модели нет")


import core.reactions as _rxmod

_real_vision = _rxmod.vision_with
_rxmod.vision_with = lambda *a, **k: dead
silent = _rxmod.screen_with(log=said.append)
_rxmod.vision_with = _real_vision
_real_strips = _rxmod.strips_at
_rxmod.strips_at = lambda path, times: {at: strip for at in times}
check("модели нет — пустые названия, сказано, без ошибки",
      silent("", [(0.0, 900.0), (900.0, 1800.0)]) == ["", ""] and len(said) == 1, str(said))
_rxmod.strips_at = _real_strips

print("ключевые кадры")
video = _os.path.join(work, "keys.mp4")
subprocess.run([_ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                "-i", "testsrc=size=320x180:rate=25:duration=12", "-c:v", "libx264",
                "-preset", "ultrafast", "-g", "50", video], check=True)
keys = rx.keyframes(video, cache=False)
check("ключевые кадры всей записи с временем",
      keys["frames"].shape[1:] == (90, 160) and 5 <= keys["time"].size <= 7
      and abs(float(keys["time"][-1]) - 10.0) < 0.1, str(keys["time"]))

print("автопилот")
_pro.save({"name": "стример", "created": time.time(), "face": "", "keep_days": 3})
profile = _pro.load("стример")
check("реакции включены по умолчанию, название с {видео}",
      ap.settings(profile)["reactions"] and "{видео}" in ap.settings(profile)["reaction_title"])
asked = []


class Quiet(ap.Pilot):
    def _plan_reactions(self, stream, path):
        asked.append(stream["vod"])
        return [{"start": 50.0, "end": 900.0, "title": "Стример смотрит: X", "kind": "reaction",
                 "game": ""}]


import core.roliks as _rol

_rol_plan, _rol.plan_games = _rol.plan_games, lambda *a, **k: [
    {"start": 1000.0, "end": 3000.0, "title": "Стример играет в Y", "kind": "game", "game": "Y"}]
plan = Quiet(profile, lister=lambda c, l: [])._plan_roliks({"vod": "1"}, "x.mp4")
check("игры и реакции в одном плане по порядку",
      [p["kind"] for p in plan] == ["reaction", "game"] and asked == ["1"])
ap.save_settings(profile, {"reactions": False})
asked.clear()
plan = Quiet(_pro.load("стример"), lister=lambda c, l: [])._plan_roliks({"vod": "1"}, "x.mp4")
check("галочка выключает реакции", asked == [] and len(plan) == 1)
overlap = ap.trim_overlaps([
    {"start": 6570.0, "end": 10586.0, "kind": "reaction", "title": "Р"},
    {"start": 10528.0, "end": 18900.0, "kind": "game", "title": "И"}])
check("реакция не залезает в игру", overlap[0]["end"] == 10528.0
      and overlap[1]["start"] == 10528.0, str(overlap))
_rol.plan_games = _rol_plan

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
