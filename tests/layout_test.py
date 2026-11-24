# -*- coding: utf-8 -*-
"""Раскладки кадра: два блока, только вебка, только экран."""
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


CAM = (0, 100, 500, 350)
GAME = (400, 0, 1000, 1080)


def graph(layout, ass=None, blur=False):
    return AVE.build_filter(CAM, GAME, 760, AVE.OUT_H - 760, ass, blur,
                            None, layout)


# --- два блока: как было ---
split = graph("split")
check("split складывает два блока", "vstack=inputs=2" in split)
check("split берёт и вебку, и экран",
      split.count("[0:v]crop=") == 2, split.count("[0:v]crop="))

# --- только вебка: заполнить кадр, бока обрезать ---
cam = graph("cam")
check("cam не складывает блоки", "vstack" not in cam)
check("cam режет только вебку", cam.count("[0:v]crop=") == 1)
check("cam заполняет кадр целиком", "force_original_aspect_ratio=increase" in cam)
check("cam обрезает до 1080x1920",
      "crop=%d:%d" % (AVE.OUT_W, AVE.OUT_H) in cam)
check("cam не размывает", "boxblur" not in cam)

# --- только экран: вписать, полосы размытые ---
screen = graph("screen")
check("screen не складывает блоки", "vstack" not in screen)
check("screen вписывает целиком", "force_original_aspect_ratio=decrease" in screen)
check("screen делает размытый фон", "boxblur" in screen)
check("screen кладёт картинку по центру", "overlay=(W-w)/2:(H-h)/2" in screen)

# --- субтитры прикручиваются во всех раскладках ---
for name in ("split", "cam", "screen"):
    text = graph(name, ass="C:/tmp/subs.ass")
    check("%s принимает субтитры" % name, "[stack]ass=" in text and "[v]" in text)

# --- вебка на весь кадр в split остаётся вертикальной парой ---
check("blur-bg работает только в split",
      "boxblur" in graph("split", blur=True))

# --- умолчания положения субтитров ---
check("SEAM_SUBS_LIFT поднимает строку над стыком",
      0.0 < AVE.SEAM_SUBS_LIFT < 1.5, AVE.SEAM_SUBS_LIFT)
check("в split строка выше стыка",
      760 - int(96 * AVE.SEAM_SUBS_LIFT) < 760)
check("без разделения строка ниже середины кадра",
      AVE.FULL_SUBS_Y > AVE.OUT_H // 2, AVE.FULL_SUBS_Y)
check("крупный план берёт лицо плотнее обычного",
      AVE.CAM_FACE_FILL > AVE.FACE_FILL,
      "%s > %s" % (AVE.CAM_FACE_FILL, AVE.FACE_FILL))

# --- «Сама решит»: вебки нет — значит экран, а не чужое лицо сверху ---


def spot(confident, box=(1500, 40, 500, 350), cover=0.08, width_share=0.26):
    return {"box": box, "confident": confident, "seen": 6, "sampled": 8,
            "inliers": 12, "cover": cover, "width_share": width_share}


def look(chosen=True, asked=True, recognised=True):
    group = {"boxes": [(100, 100, 200, 200)], "frames": 5} if chosen else None
    return {"groups": [], "frames": 8, "size": (1920, 1080), "chosen": group,
            "recognised": recognised, "score": 0.7, "asked": asked}


full = spot(True, (0, 0, 1920, 1080), cover=0.95, width_share=1.0)
check("вебка во весь экран — крупный план", AVE.pick_layout(full, None)[0] == "cam")
check("на крупный план лицо не ищется", AVE.needs_survey(full) is False)

corner = spot(True)
check("уверенная рамка в углу — два блока",
      AVE.pick_layout(corner, None)[0] == "split")
check("при уверенной рамке лицо не ищется", AVE.needs_survey(corner) is False)

check("без рамки лицо приходится искать", AVE.needs_survey(None) is True)
check("неуверенная рамка тоже ведёт к поиску лица",
      AVE.needs_survey(spot(False)) is True)

check("лица нет вовсе — только экран",
      AVE.pick_layout(None, look(chosen=False))[0] == "screen")
check("лицо есть, но чужое — только экран",
      AVE.pick_layout(None, look(recognised=False))[0] == "screen")
check("узнанный стример — два блока",
      AVE.pick_layout(None, look())[0] == "split")
check("без эталона чужое лицо не отличить, поведение прежнее",
      AVE.pick_layout(None, look(asked=False, recognised=False))[0] == "split")
check("совсем без снимка и без поиска — экран",
      AVE.pick_layout(None, None)[0] == "screen")
check("решение объясняется словами",
      "экран" in AVE.pick_layout(None, look(chosen=False))[1],
      AVE.pick_layout(None, look(chosen=False))[1])


print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
