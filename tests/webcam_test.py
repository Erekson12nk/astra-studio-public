# -*- coding: utf-8 -*-
"""Поиск вебки по снимку профиля и распознавание крупного плана."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os, sys, tempfile

import cv2
import numpy as np

from core import webcam as W

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


work = tempfile.mkdtemp()
rng = np.random.default_rng(7)

# «Вид с вебки»: пёстрая картинка, на такой ORB находит много примет.
scene = rng.integers(0, 255, (272, 504, 3), dtype=np.uint8)
scene = cv2.GaussianBlur(scene, (3, 3), 0)
photo = os.path.join(work, "face.png")
cv2.imwrite(photo, scene)

locator = W.Locator(photo)
check("на пёстром снимке примет достаточно", locator.usable(), len(locator.keypoints))


def screen(box_w, box_h, at_x, at_y, size=(1080, 1920)):
    frame = rng.integers(0, 60, (size[0], size[1], 3), dtype=np.uint8)
    piece = cv2.resize(scene, (box_w, box_h), interpolation=cv2.INTER_AREA)
    frame[at_y:at_y + box_h, at_x:at_x + box_w] = piece
    return frame


# --- вебка в углу ---
corner = screen(464, 252, 20, 80)
hit = locator.find(corner)
check("вебка в углу находится", hit is not None)
if hit:
    x, y, w, h = hit["box"]
    check("рамка попадает в место", abs(x - 20) < 40 and abs(y - 80) < 40,
          "%d,%d" % (x, y))
    check("рамка попадает в размер", abs(w - 464) < 60 and abs(h - 252) < 60,
          "%dx%d" % (w, h))
    check("угловая вебка — не крупный план", not W.closeup(W.consensus([hit])))

# --- вебка во весь экран ---
full = screen(1920, 1040, 0, 40)
big = locator.find(full)
check("вебка во весь экран находится", big is not None)
if big:
    check("во весь экран считается крупным планом",
          W.closeup(W.consensus([big])),
          "покрытие %.2f ширина %.2f" % (big["cover"], big["width_share"]))

# --- вебки нет вовсе ---
noise = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
check("на чужом кадре вебка не мерещится", locator.find(noise) is None)

# --- согласие по кадрам ---
sample = [{"box": (10.0, 80.0, 460.0, 250.0), "inliers": 30, "matches": 90,
           "cover": 0.06, "width_share": 0.24},
          {"box": (12.0, 82.0, 464.0, 252.0), "inliers": 25, "matches": 88,
           "cover": 0.06, "width_share": 0.24},
          {"box": (900.0, 400.0, 100.0, 60.0), "inliers": 3, "matches": 20,
           "cover": 0.00, "width_share": 0.05}]
agreed = W.consensus(sample)
check("согласие отбрасывает слабый кадр", abs(agreed["box"][0] - 11.0) < 3.0,
      agreed["box"])
check("согласие считает уверенным", agreed["confident"])
check("согласие помнит, сколько кадров смотрели", agreed["sampled"] == 3)

weak = W.consensus([sample[2]])
check("только слабые кадры — не уверены", not weak["confident"])
check("пустой список согласия не даёт", W.consensus([]) is None)
check("без отчёта крупного плана нет", not W.closeup(None))

# --- слабая рамка не объявляет крупный план ---
# На реакционном стриме чужое видео во весь экран давало рамку с пятью
# опорными точками, и клип уходил в крупный план по чужому лицу.
shaky = W.consensus([{"box": (-504.0, -28.0, 1921.0, 1487.0), "inliers": 5,
                      "matches": 60, "cover": 0.92, "width_share": 1.0}])
check("во весь экран, но неуверенно — не крупный план", not W.closeup(shaky),
      "покрытие %.2f, опорных %d" % (shaky["cover"], shaky["inliers"]))

# --- якорь: обычное место вебки на этом стриме ---
corner = [(1.0, 83.0, 465.0, 252.0), (0.0, 82.0, 467.0, 253.0),
          (2.0, 84.0, 463.0, 250.0)]
anchor = W.median_box(corner)
check("якорь считается по нескольким клипам", abs(anchor[2] - 465.0) < 3.0, anchor)
check("такая же рамка совпадает с якорем",
      W.agrees((1.0, 83.0, 466.0, 251.0), anchor))
check("сдвинутая на полразмера — не совпадает",
      not W.agrees((900.0, 83.0, 465.0, 252.0), anchor))
check("рамка вчетверо крупнее — не совпадает",
      not W.agrees((0.0, 0.0, 1900.0, 1030.0), anchor))
check("чуть меньшая рамка всё ещё совпадает",
      W.agrees((10.0, 90.0, 400.0, 216.0), anchor))
check("без якоря совпадать не с чем", not W.agrees(corner[0], None))

# --- рамка режется под пропорции блока, а не вписывается с полями ---
BLOCK = 1080 / 760.0
wide = (0.0, 82.0, 465.0, 252.0)
cut_box = W.crop_to_aspect(wide, BLOCK)
check("после обрезки пропорции блока", abs(cut_box[2] / cut_box[3] - BLOCK) < 0.01,
      "%.2f" % (cut_box[2] / cut_box[3]))
check("обрезка не вылезает за исходную рамку",
      cut_box[0] >= wide[0] and cut_box[0] + cut_box[2] <= wide[0] + wide[2])
check("высота сохранена, режется ширина", abs(cut_box[3] - wide[3]) < 1)

по_лицу = W.crop_to_aspect(wide, BLOCK, focus=(110.0, 206.0, 98.0, 98.0))
центр_лица = 110.0 + 49.0
check("обрезка тянется к лицу",
      по_лицу[0] <= центр_лица <= по_лицу[0] + по_лицу[2], по_лицу)
у_края = W.crop_to_aspect(wide, BLOCK, focus=(0.0, 206.0, 40.0, 40.0))
check("лицо у края не выталкивает рамку наружу", у_края[0] >= wide[0], у_края)

узкая = W.crop_to_aspect((0.0, 0.0, 300.0, 400.0), BLOCK)
check("узкая рамка режется по высоте", узкая[3] < 400, узкая)

# --- рамки, которых не бывает у вебки ---
check("рамка, наполовину вылезшая за кадр, отбраковывается",
      not W.plausible((0, -303, 1458, 827), 1920, 1080))
check("вебка во весь экран остаётся годной",
      W.plausible((0, 0, 1920, 1040), 1920, 1080))
check("обычная угловая рамка годная", W.plausible((0, 82, 465, 252), 1920, 1080))
check("вырожденная рамка отбраковывается", not W.plausible((0, 0, 4, 4), 1920, 1080))

# --- при разнобое размеров берётся согласное большинство ---
def hit(box, inliers=20):
    return {"box": box, "inliers": inliers, "cover": 0.1, "width_share": 0.2}

смесь = [hit((0, 80, 460, 250)), hit((2, 82, 470, 255)), hit((0, 79, 465, 252)),
         hit((0, 0, 1900, 1060))]
итог = W.consensus(смесь)
check("огромная рамка не тянет медиану на себя", итог["box"][2] < 600,
      tuple(int(v) for v in итог["box"]))
check("согласных рамок осталось трое", len(W.agreeing(смесь)) == 3,
      len(W.agreeing(смесь)))
check("на двух рамках отбор не работает", len(W.agreeing(смесь[:2])) == 2)

# --- поиск идёт по нескольким масштабам ---
check("масштабы поиска объявлены и меньше единицы",
      any(k < 1.0 for k in W.SEARCH_SCALES), W.SEARCH_SCALES)
мелкая = screen(465, 252, 0, 82)
найдено = locator.find(мелкая)
check("вебка находится и на полном кадре", найдено is not None)


print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
