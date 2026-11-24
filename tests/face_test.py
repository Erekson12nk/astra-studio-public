# -*- coding: utf-8 -*-
"""Выбор вебки: побеждает устойчивое лицо, а не самое крупное."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os, sys

ROOT = _REPO
sys.path.insert(0, os.path.join(ROOT, "astra"))

import auto_vertical_editor as AVE

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


def pick(per_frame):
    groups = AVE.group_faces(per_frame)
    if not groups:
        return None
    group = AVE.steadiest_face(groups, len(per_frame))
    return round(group["cx"]), round(group["cy"]), round(group["size"])


def steady(x, y, size):
    return (float(x), float(y), float(size), float(size))


# --- накладка вебки против чужого лица на экране ---
# Вебка: маленькая, стоит на месте во всех кадрах.
# Чужое лицо: крупнее, но ездит по экрану от кадра к кадру.
frames = []
for i in range(20):
    frames.append([steady(140, 180, 130),
                   steady(900 + 90 * (i % 5), 500 + 70 * (i % 3), 380)])
check("устойчивая вебка бьёт крупное дёрганое лицо", pick(frames)[0] < 400, pick(frames))

# --- одно лицо: поведение как раньше ---
one = [[steady(300, 300, 200)] for _ in range(20)]
check("единственное лицо берётся как есть", pick(one) == (400, 400, 200), pick(one))

# --- два одинаково устойчивых: берём крупное ---
both = [[steady(140, 180, 130), steady(800, 400, 420)] for _ in range(20)]
check("при равной устойчивости побеждает крупное", pick(both)[0] > 400, pick(both))

# --- редкий гость проигрывает постоянному ---
rare = []
for i in range(20):
    boxes = [steady(140, 180, 130)]
    if i < 3:
        boxes.append(steady(1200, 700, 500))
    rare.append(boxes)
check("лицо из пары кадров не перебивает постоянное", pick(rare)[0] < 400, pick(rare))

# --- крупное лицо в большинстве кадров всё ещё выигрывает ---
big = []
for i in range(20):
    boxes = [steady(1200, 700, 500)]
    if i % 7 == 0:
        boxes.append(steady(140, 180, 130))
    big.append(boxes)
check("постоянное крупное лицо остаётся выбором", pick(big)[0] > 400, pick(big))

# --- дрожание в пределах своего размера не разваливает группу ---
jitter = [[steady(140 + (i % 3) * 8, 180 + (i % 2) * 6, 130)] for i in range(20)]
check("мелкое дрожание не рвёт группу на части",
      len(AVE.group_faces(jitter)) == 1, len(AVE.group_faces(jitter)))

# --- лиц нет вовсе ---
check("пустые кадры дают ноль групп", pick([[] for _ in range(10)]) is None)

# --- опознание стримера ---
import numpy as np
from core import faceid as FID


def vec(*values):
    a = np.array(values, dtype=np.float32)
    return a / np.linalg.norm(a)


STREAMER = vec(1, 0, 0)
STRANGER = vec(0, 1, 0)
BLURRED = vec(0.9, 0.4, 0.1)

check("схожесть с самим собой равна единице",
      abs(FID.similarity(STREAMER, STREAMER) - 1.0) < 1e-5)
check("чужой даёт около нуля", abs(FID.similarity(STREAMER, STRANGER)) < 1e-5)
check("пустой вектор не ломает сравнение", FID.similarity(None, STREAMER) < 0)
check("среднее нескольких снимков остаётся единичным",
      abs(float(np.linalg.norm(FID.average([STREAMER, BLURRED]))) - 1.0) < 1e-5)
check("среднее из пустого списка — ничто", FID.average([None, None]) is None)


def scene(boxes_per_frame):
    return AVE.group_faces(boxes_per_frame)


# Крупное чужое лицо стоит на месте, вебка стримера мельче и слегка ездит.
frames = []
for i in range(20):
    frames.append([
        (120, 160, 130, 130, None, STREAMER if i % 2 else BLURRED),
        (700, 400, 420, 420, None, STRANGER),
    ])
groups = scene(frames)
found, score = AVE.recognised_face(groups, STREAMER, 0.4)
check("узнаёт стримера среди чужих лиц", found is not None and found["cx"] < 400,
      "%.2f" % score)

# Стримера в кадре нет вовсе — опознание обязано отказаться, а не взять кого попало.
absent = [[(700, 400, 420, 420, None, STRANGER)] for _ in range(20)]
missing, _ = AVE.recognised_face(scene(absent), STREAMER, 0.4)
check("без стримера в кадре опознание отказывается", missing is None)

# Лица без признака (мелкие, без ключевых точек) опознание пропускает.
blind = [[(120, 160, 130, 130, None, None)] for _ in range(20)]
skipped, _ = AVE.recognised_face(scene(blind), STREAMER, 0.4)
check("лица без признака не участвуют", skipped is None)

check("порог отсева мелких лиц задан", FID.MIN_FACE >= 32, FID.MIN_FACE)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
