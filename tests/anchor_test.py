# -*- coding: utf-8 -*-
"""Якорь вебки собирается до цикла рендера, а не по ходу него."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

from media import build as _build

_MEDIA = _build()

import argparse, os, sys

ROOT = _REPO
sys.path.insert(0, os.path.join(ROOT, "astra"))

import auto_vertical_editor as AVE
from core import webcam as CAM

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


CORNER = (1500.0, 40.0, 400.0, 280.0)
SHIFTED = (1520.0, 52.0, 404.0, 286.0)
FULL = (0.0, 0.0, 1920.0, 1080.0)


def hit(box, inliers=14):
    x, y, w, h = box
    return {"box": box, "inliers": inliers, "matches": inliers + 4,
            "cover": (w * h) / (1920.0 * 1080.0), "width_share": w / 1920.0}


class Locator:
    def __init__(self, plan):
        self.plan = list(plan)
        self.calls = 0

    def usable(self):
        return True

    def find(self, _frame):
        self.calls += 1
        if not self.plan:
            return None
        return self.plan[(self.calls - 1) % len(self.plan)]


def options(**extra):
    values = {"cam_box": None, "layout": "auto", "anchor_samples": 3,
              "no_anchor": False}
    values.update(extra)
    return argparse.Namespace(**values)


video = _MEDIA["sample"]
spans = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]

# --- уверенные рамки из разных клипов дают якорь ---
state = {"locator": Locator([hit(CORNER), hit(SHIFTED)])}
AVE.setup_cam_anchor(video, spans, options(), state)
check("якорь появился до рендера", state.get("cam_anchor") is not None,
      state.get("cam_anchor"))
check("рамки собраны по всем клипам", len(state.get("cam_seen", [])) == 3,
      len(state.get("cam_seen", [])))
check("якорь лежит между найденными рамками",
      state["cam_anchor"] and CORNER[0] <= state["cam_anchor"][0] <= SHIFTED[0],
      state.get("cam_anchor"))
check("слабая рамка на этом месте теперь считается верной",
      CAM.agrees(SHIFTED, state["cam_anchor"]))

# --- крупный план в якорь не идёт: там вебка во весь экран ---
state = {"locator": Locator([hit(FULL)])}
AVE.setup_cam_anchor(video, spans, options(), state)
check("вебка во весь экран якорь не задаёт", state.get("cam_anchor") is None,
      state.get("cam_anchor"))

# --- слабые попадания якорь не задают ---
state = {"locator": Locator([hit(CORNER, inliers=2)])}
AVE.setup_cam_anchor(video, spans, options(), state)
check("неуверенные рамки якорь не задают", state.get("cam_anchor") is None)

# --- когда предпроход не нужен, файл не открывается ---
for label, opts, place in (
        ("рамка задана руками", options(cam_box="1,2,3,4"), None),
        ("раскладка «только экран»", options(layout="screen"), None),
):
    loc = Locator([hit(CORNER)])
    state = {"locator": loc}
    AVE.setup_cam_anchor(video, spans, opts, state)
    check("предпроход пропущен: %s" % label,
          loc.calls == 0 and "cam_anchor" not in state, loc.calls)

loc = Locator([hit(CORNER)])
state = {"locator": loc}
AVE.setup_cam_anchor(video, [(0.0, 1.0)], options(), state)
check("на одном клипе якорь не нужен", loc.calls == 0)

state = {}
AVE.setup_cam_anchor(video, spans, options(), state)
check("без снимка профиля предпроход молчит", state == {})

# --- флаг выключения объявлен ---
source_text = open(os.path.join(ROOT, "astra", "auto_vertical_editor.py"),
                   encoding="utf-8").read()
check("флаг --no-anchor объявлен", "--no-anchor" in source_text)
check("предпроход зовётся до цикла рендера",
      source_text.index("setup_cam_anchor(read_from")
      < source_text.index("render_clip(source, info, start, end, target"))

# --- сколько кадров читается: по anchor_samples на клип ---
loc = Locator([hit(CORNER)])
state = {"locator": loc}
AVE.setup_cam_anchor(video, spans, options(anchor_samples=2), state)
check("кадров прочитано не больше, чем заказано",
      loc.calls <= 2 * len(spans), loc.calls)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
