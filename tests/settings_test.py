# -*- coding: utf-8 -*-
"""Настройки рендера: окно и движок читают один объект, а не строку флагов."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import sys

ROOT = _REPO
sys.path.insert(0, ROOT)
sys.path.insert(0, _os.path.join(ROOT, "astra"))

from core import settings as S
from core.settings import Render

import auto_vertical_editor as ave

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


ENGINE = ["py", "ave.py"]
PROFILE_FILLED = ("model", "language")


def parse(command):
    return ave.parse_args(command[2:] + ["--no-profile"])


def differs(left, right, skip=()):
    out = []
    for name in S.names():
        if name in skip:
            continue
        a, b = getattr(left, name), getattr(right, name)
        if a != b:
            out.append("%s: %r != %r" % (name, a, b))
    return out


CASES = {
    "один клип": Render(source="s.mp4", out="o.mp4", ranges=[(12.5, 48.25)],
                        layout="split", cam_box=(48, 0, 500, 350), cam_fit=False,
                        game_box=(0, 200, 1920, 880), top_height=810),
    "пакет по таймкодам": Render(source="s.mp4", out="папка", timecodes="tc.txt",
                                 layout="cam", subs=False, censor=False),
    "по выбору модели": Render(source="s.mp4", out="папка", select="ans.txt",
                               brief="brief.json", layout="screen",
                               language="ru", model="large-v3-turbo"),
    "несколько кусков": Render(source="s.mp4", out="папка",
                               ranges=[(1, 2), (10.5, 20.25)], blur_bg=True,
                               title="Заголовок", title_size=72,
                               title_color="#FF0000"),
    "сама решит": Render(source="s.mp4", out="o.mp4", ranges=[(0, 30)],
                         layout="auto", cam_box=(1, 2, 3, 4), subs_y=700),
    "субтитры подвинули руками": Render(source="s.mp4", out="o.mp4",
                                        ranges=[(0, 30)], layout="auto",
                                        subs_y=700, subs_moved=True),
    "с вырезами и паузами": Render(source="s.mp4", out="o.mp4", ranges=[(5, 65)],
                                   drop=[(1.5, 3.0)], cut_pauses=True,
                                   pause_min=0.6, font="Impact",
                                   color_base="#EEEEEE", color_highlight="#00FF00"),
}

# --- что окно собрало, то движок и прочитал ---
for label, wanted in CASES.items():
    command = wanted.command(ENGINE)
    back = S.from_args(parse(command))
    skip = tuple(n for n in PROFILE_FILLED if getattr(wanted, n) is None)
    gap = differs(back, wanted.effective(), skip)
    check("«%s» доезжает до движка целиком" % label, not gap, gap)

# --- «сама решит» не получает того, что отменяет автоматику ---
auto = CASES["сама решит"].command(ENGINE)
check("при «сама решит» рамка вебки не шлётся", "--cam-box" not in auto)
check("и подгонка рамки тоже", "--cam-exact" not in auto)
check("и высота субтитров тоже", "--subs-y" not in auto)
moved = CASES["субтитры подвинули руками"].command(ENGINE)
check("подвинутые руками субтитры доезжают", "--subs-y" in moved)
check("но рамка вебки — по-прежнему нет", "--cam-box" not in moved)

manual = CASES["один клип"].command(ENGINE)
check("при ручной раскладке рамка шлётся", "--cam-box" in manual)
check("и её просят взять как есть", "--cam-exact" in manual)

# --- то же правило действует и на набранную руками команду ---
typed = ave.parse_args(["s.mp4", "0", "30", "o.mp4", "--layout", "auto",
                        "--cam-box", "1,2,3,4", "--cam-exact",
                        "--subs-y", "700", "--no-profile"])
check("руками заданная рамка при «сама решит» отбрасывается",
      typed.cam_box is None, typed.cam_box)
check("подгонка рамки вместе с ней", typed.cam_exact is False, typed.cam_exact)
check("а вот высота субтитров, заданная явно, остаётся",
      typed.subs_y == 700, typed.subs_y)
check("движок держит настройки объектом", isinstance(typed.render, Render))

kept = ave.parse_args(["s.mp4", "0", "30", "o.mp4", "--layout", "split",
                       "--cam-box", "1,2,3,4", "--no-profile"])
check("при ручной раскладке рамка не трогается",
      kept.cam_box == "1,2,3,4", kept.cam_box)

# --- противоречия не выражаются ---
try:
    Render(source="s.mp4", timecodes="tc.txt", select="ans.txt")
    check("два источника кусков сразу не берутся", False)
except ValueError as error:
    check("два источника кусков сразу не берутся", "дважды" in str(error), error)
try:
    Render(source="s.mp4", layout="боком")
    check("незнакомая раскладка не берётся", False)
except ValueError as error:
    check("незнакомая раскладка не берётся", "раскладка" in str(error), error)
try:
    Render(source="s.mp4", cam_box=(1, 2, 3))
    check("рамка из трёх чисел не берётся", False)
except ValueError as error:
    check("рамка из трёх чисел не берётся", "четырьмя" in str(error), error)

# --- умолчания живут в одном месте ---
check("движок берёт умолчания из ядра",
      ave.OUT_W == S.OUT_W and ave.BASE_COLOR == S.BASE_COLOR
      and ave.SEAM_SUBS_LIFT == S.SEAM_SUBS_LIFT)
bare = ave.parse_args(["s.mp4", "--no-profile"])
check("умолчания разбора совпадают с умолчаниями объекта",
      (bare.top_height, bare.font, bare.title_size, bare.pause_min)
      == (S.TOP_HEIGHT, S.SUBS_FONT, S.TITLE_SIZE, S.PAUSE_MIN))

# --- окно собирает команду через тот же объект ---
import tkinter as tk

import gui as agui

root = tk.Tk()
root.withdraw()
editor = agui.Editor(root, chrome=False)
editor.source = "s.mp4"
editor.var_layout.set(agui.LAYOUT_NAMES[0])
editor.update_idletasks()
built = editor._build_command([(0.0, 30.0)], "o.mp4")
same = editor._settings([(0.0, 30.0)], "o.mp4").command(editor._engine())
check("окно строит команду объектом", built == same)
check("в команде окна нет рамки при «сама решит»", "--cam-box" not in built)
check("окно и движок сходятся на объекте",
      not differs(S.from_args(parse(built)),
                  editor._settings([(0.0, 30.0)], "o.mp4").effective(),
                  PROFILE_FILLED))
editor.shutdown()
root.destroy()

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
