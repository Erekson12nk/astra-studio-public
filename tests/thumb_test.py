# -*- coding: utf-8 -*-
"""Вёрстка превью: стример отзеркален и без обводки, текст капсом во всю ширину.

Стример — цветной прямоугольник с разными половинами, фон — однотонный: так видно,
куда смотрит вырезка, где лежит текст и что под ним.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _REPO)

import numpy as np
from PIL import Image, ImageDraw

from thumbmaker import compose as C
from thumbmaker.config import PLATES, ComposeConfig

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


def person():
    """Вырезка 400x300: левая половина красная, правая синяя (BGR), края прозрачные."""
    rgba = np.zeros((400, 300, 4), np.uint8)
    rgba[40:, 30:150, :3] = (0, 0, 255)
    rgba[40:, 150:270, :3] = (255, 0, 0)
    rgba[40:, 30:270, 3] = 255
    return rgba


def config(**extra):
    cfg = ComposeConfig(width=1280, height=720, shadow=False, boost_saturation=1.0)
    for key, value in extra.items():
        setattr(cfg, key, value)
    return cfg


background = np.full((720, 1280, 3), 128, np.uint8)

print("слова и переносы")
words = C.split_words("реклама *не так уж* плохо")
check("звёздочки помечают акцент", words == [[("реклама", False), ("не", True),
                                               ("так", True), ("уж", True),
                                               ("плохо", False)]], words)
check("звёздочка перед знаком закрывает акцент и не рисуется",
      C.split_words("ты их *используешь*? да") == [[("ты", False), ("их", False),
                                                    ("используешь?", True), ("да", False)]])
check("текст без звёздочек — без акцента",
      all(not accent for accent in (a for _, a in C.split_words("а б в")[0])))
font = C.load_font(60)
draw = ImageDraw.Draw(Image.new("L", (1, 1)))
width = draw.textlength("ПОГУБИЛ КАРЬЕРУ В", font=font) - 1
lines = C.wrap_lines(draw, "ПОГУБИЛ КАРЬЕРУ В VALORANT", font, width)
check("предлог не висит в конце строки", lines == ["ПОГУБИЛ КАРЬЕРУ", "В VALORANT"], lines)
width = draw.textlength("СТИНТ И КЕЛЬ", font=font) + 1
lines = C.wrap_lines(draw, "СТИНТ И КЕЛЬ — ГОНЩИКИ", font, width)
check("тире не начинает строку", not any(line.startswith("—") for line in lines)
      and not any(line.endswith(" И") for line in lines), lines)
check("ручной перенос сохраняется",
      C.wrap_lines(draw, "ОДИН\nДВА", font, 5000) == ["ОДИН", "ДВА"])
check("шрифт по умолчанию — читаемый гротеск, а не декоративный",
      "unsightly" not in str(getattr(font, "path", "")).lower(), getattr(font, "path", ""))

print("стример")
plain = C.compose(background, person(), config(caption=""))
mirrored_x = np.where((plain[..., 2] > 200) & (plain[..., 0] < 60))[1]
blue_x = np.where((plain[..., 0] > 200) & (plain[..., 2] < 60))[1]
check("по умолчанию стример отзеркален", mirrored_x.mean() > blue_x.mean(),
      "красный %d, синий %d" % (mirrored_x.mean(), blue_x.mean()))
straight = C.compose(background, person(), config(caption="", mirror=False))
red_x = np.where((straight[..., 2] > 200) & (straight[..., 0] < 60))[1]
check("без зеркала стороны как в кадре", red_x.mean() < np.where(
    (straight[..., 0] > 200) & (straight[..., 2] < 60))[1].mean())
white = (plain > 245).all(axis=2).sum()
check("обводки по умолчанию нет", white == 0, "белых точек %d" % white)
check("обводку можно включить",
      (C.compose(background, person(), config(caption="", outline_px=6)) > 245)
      .all(axis=2).sum() > 1000)

print("текст")
caption = "СТИНТ И КЕЛЬ СТАЛИ ПРОФЕССИОНАЛЬНЫМИ ГОНЩИКАМИ НА ЦЕЛЫЙ ВЕЧЕР"
done = C.compose(background, person(), config(caption=caption.lower()))
ink = (done > 245).all(axis=2)
cols = np.where(ink.any(axis=0))[0]
rows = np.where(ink.any(axis=1))[0]
check("длинный текст тянется почти от края до края",
      cols.min() < 1280 * 0.12 and cols.max() > 1280 * 0.88, "%d..%d" % (cols.min(), cols.max()))
check("и не вылезает за кадр", cols.min() > 0 and cols.max() < 1279)
check("текст внизу кадра", rows.min() > 720 * 0.45 and rows.max() < 720 * 0.98,
      "%d..%d" % (rows.min(), rows.max()))
check("текст лежит и поверх стримера", ink[:, 1280 - 200:].any())
check("под текстом затемнение", done[700, 20].mean() < 60 and done[100, 20].mean() > 100,
      "%d / %d" % (done[700, 20].mean(), done[100, 20].mean()))
accent = C.compose(background, person(), config(caption="ОБЫЧНО *ЖЁЛТО*"))
yellow = ((accent[..., 2] > 220) & (accent[..., 1] > 180) & (accent[..., 0] < 60)).sum()
check("акцентное слово жёлтое", yellow > 500, yellow)
check("строчные буквы становятся капсом",
      np.array_equal(C.compose(background, person(), config(caption="тест")),
                     C.compose(background, person(), config(caption="ТЕСТ"))))

print("подложки")
for name, plate in PLATES.items():
    image = C.compose(background, person(), config(caption="ТЕСТ ПОДЛОЖКИ", plate=plate))
    check("«%s» собирается" % name, image.shape == (720, 1280, 3))
box = C.compose(background, person(), config(caption="ТЕСТ", plate="box"))
ink = (box > 245).all(axis=2)
rows, cols = np.where(ink.any(axis=1))[0], np.where(ink.any(axis=0))[0]
middle = (rows.min() + rows.max()) // 2
check("плашка лежит ровно под строкой", box[middle, cols.min() - 6].mean() < 30
      and box[rows.min() - 4, cols.min() + 4].mean() < 30
      and box[rows.max() + 4, cols.min() + 4].mean() < 30,
      "%d %d %d" % (box[middle, cols.min() - 6].mean(), box[rows.min() - 4, cols.min() + 4].mean(),
                    box[rows.max() + 4, cols.min() + 4].mean()))
check("выше плашки фон не тронут", box[rows.min() - 120, 640].mean() > 100)
none = C.compose(background, person(), config(caption="ТЕСТ", plate="none"))
check("без подложки фон не темнеет", none[715, 20].mean() > 100)

print("стиль нарезчика")
face = (110, 60, 80, 90)           # лицо внутри вырезки person()
close = C.compose(background, person(), config(caption="", face_height=0.6, mirror=False),
                  face_box=face)
colored = (np.abs(close.astype(int) - 128).sum(axis=2) > 200)
check("крупный план: стример занимает почти всю высоту",
      colored.any(axis=1).sum() > 650, colored.any(axis=1).sum())
check("крупный план: лицо справа", np.where(colored.any(axis=0))[0].mean() > 640)
whole = C.compose(background, person(), config(caption="", mirror=False))
check("без лица в вырезке — как раньше, целиком", np.array_equal(
    whole, C.compose(background, person(), config(caption="", mirror=False, face_height=0.6))))
check("auto не зеркалит, если стример уже смотрит в кадр",
      not C.should_mirror(config(mirror="auto"), turn=-0.3)
      and not C.should_mirror(config(mirror="auto"), turn=0.02))
check("auto зеркалит, если стример смотрит из кадра",
      C.should_mirror(config(mirror="auto"), turn=0.3)
      and C.should_mirror(config(mirror="auto", side="left"), turn=-0.3))
preview = np.zeros((720, 1280, 3), np.uint8)
preview[:, :, 1] = 255                                   # зелёное превью
card = C.compose(preview, person(), config(caption="ТЕКСТ ПОД КАРТОЧКОЙ", layout="card",
                                           plate="none"))
green = (card[..., 1] > 200) & (card[..., 0] < 80) & (card[..., 2] < 80)
check("карточка — превью слева сверху", green[100, 300] and not green[680, 300]
      and not green[100, 1000])
right = np.array([np.where(row)[0].max() for row in green[20:400] if row.any()])
check("край карточки рваный, а не ровный", right.std() > 1.5 and right.max() < 1280 * 0.67,
      "разброс %.1f px" % right.std())
ink = (card > 245).all(axis=2)
rows, cols = np.where(ink.any(axis=1))[0], np.where(ink.any(axis=0))[0]
check("текст под карточкой и слева", rows.min() > 720 * 0.62 and cols.max() < 1280 * 0.66,
      "%d %d" % (rows.min(), cols.max()))
check("под карточкой размытый тёмный фон", card[700, 20, 1] < 160)
torn = C.compose(background, person(), config(caption="ТЕКСТ", plate="torn",
                                              caption_area="left"))
ink = (torn > 245).all(axis=2)
rows, cols = np.where(ink.any(axis=1))[0], np.where(ink.any(axis=0))[0]
check("рваная полоса чёрная под текстом", torn[rows.max() + 8, cols.min()].mean() < 30
      and torn[rows.min() - 150, cols.min()].mean() > 100)

print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
