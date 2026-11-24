"""Иконки Студии: рисуются по линиям в цвет стиля, без файлов картинок.

Каждая иконка — несколько линий на сетке 24×24. Рисуется крупно и уменьшается:
так края гладкие. У киберпанка линии толще и с прямыми концами, у остальных
стилей — тоньше и скруглённые. Кнопка сама находит иконку по своему тексту.
"""
from __future__ import annotations

import math

from core import ui as _ui

try:
    from PIL import Image, ImageDraw, ImageTk
except Exception:                  # без Pillow кнопки просто без иконок
    Image = None

GRID = 24.0
ZOOM = 4                           # рисуется вчетверо крупнее, потом уменьшается
SIZE = 16


def _star(points, outer, inner, cx=12.0, cy=12.0, turn=-90.0):
    out = []
    for n in range(points * 2):
        radius = outer if n % 2 == 0 else inner
        angle = math.radians(turn + n * 180.0 / points)
        out.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return out


# ("line", точки) — ломаная; ("shape", точки) — замкнутая; ("fill", точки) — залитая;
# ("ring", cx, cy, r); ("dot", cx, cy, r); ("arc", cx, cy, r, от, до)
SHAPES = {
    "folder": [("shape", [(3, 6), (9.5, 6), (11.5, 8.5), (21, 8.5), (21, 19), (3, 19)])],
    "open": [("line", [(3, 19), (3, 6), (9.5, 6), (11.5, 8.5), (19, 8.5), (19, 11)]),
             ("shape", [(3, 19), (6.5, 11), (22, 11), (18.5, 19)])],
    "download": [("line", [(12, 3.5), (12, 15)]), ("line", [(7, 10), (12, 15), (17, 10)]),
                 ("line", [(4, 20), (20, 20)])],
    "upload": [("line", [(12, 15), (12, 3.5)]), ("line", [(7, 8.5), (12, 3.5), (17, 8.5)]),
               ("line", [(4, 20), (20, 20)])],
    "paste": [("shape", [(6, 5), (18, 5), (18, 21), (6, 21)]),
              ("shape", [(9, 3), (15, 3), (15, 7), (9, 7)]),
              ("line", [(9, 12), (15, 12)]), ("line", [(9, 16), (13, 16)])],
    "trash": [("line", [(4, 7), (20, 7)]), ("shape", [(6, 7), (7, 21), (17, 21), (18, 7)]),
              ("line", [(9.5, 4), (14.5, 4)]), ("line", [(10, 11), (10, 17)]),
              ("line", [(14, 11), (14, 17)])],
    "stop": [("fill", [(6.5, 6.5), (17.5, 6.5), (17.5, 17.5), (6.5, 17.5)])],
    "play": [("fill", [(8, 5), (19, 12), (8, 19)])],
    "wave": [("line", [(4, 10), (4, 14)]), ("line", [(8, 7), (8, 17)]),
             ("line", [(12, 4), (12, 20)]), ("line", [(16, 8), (16, 16)]),
             ("line", [(20, 10.5), (20, 13.5)])],
    "plus": [("line", [(12, 5), (12, 19)]), ("line", [(5, 12), (19, 12)])],
    "save": [("shape", [(4, 4), (17, 4), (20, 7), (20, 20), (4, 20)]),
             ("line", [(8, 4), (8, 9), (15, 9), (15, 4)]),
             ("shape", [(7.5, 13.5), (16.5, 13.5), (16.5, 20), (7.5, 20)])],
    "check": [("line", [(5, 12.5), (10, 17.5), (19.5, 7)])],
    "close": [("line", [(6, 6), (18, 18)]), ("line", [(18, 6), (6, 18)])],
    "refresh": [("arc", 12, 12, 7.5, 40, 330), ("line", [(19.5, 4.5), (19.5, 9.5), (14.5, 9.5)])],
    "search": [("ring", 10.5, 10.5, 6.2), ("line", [(15, 15), (20.5, 20.5)])],
    "spark": [("shape", _star(4, 9.5, 2.6)), ("dot", 19.5, 4.5, 1.3)],
    "volume": [("shape", [(4, 9.5), (8, 9.5), (13, 5), (13, 19), (8, 14.5), (4, 14.5)]),
               ("arc", 13, 12, 4, -45, 45), ("arc", 13, 12, 7.5, -50, 50)],
    "film": [("shape", [(3.5, 5), (20.5, 5), (20.5, 19), (3.5, 19)]),
             ("line", [(7.5, 5), (7.5, 19)]), ("line", [(16.5, 5), (16.5, 19)]),
             ("line", [(3.5, 12), (7.5, 12)]), ("line", [(16.5, 12), (20.5, 12)])],
    "link": [("line", [(9.5, 14.5), (14.5, 9.5)]),
             ("line", [(11, 6.5), (13, 4.5), (16, 3.5), (19, 5), (20.5, 8), (19.5, 11), (17.5, 13)]),
             ("line", [(13, 17.5), (11, 19.5), (8, 20.5), (5, 19), (3.5, 16), (4.5, 13), (6.5, 11)])],
    "copy": [("shape", [(9, 9), (20, 9), (20, 20), (9, 20)]),
             ("line", [(15, 5.5), (15, 4), (4, 4), (4, 15), (5.5, 15)])],
    "tray": [("shape", [(3, 5), (21, 5), (21, 19), (3, 19)]), ("line", [(3, 14.5), (21, 14.5)]),
             ("line", [(12, 7.5), (12, 12)]), ("line", [(9.5, 9.5), (12, 12), (14.5, 9.5)])],
    "mic": [("shape", [(9, 3.5), (15, 3.5), (15, 13.5), (9, 13.5)]),
            ("arc", 12, 11, 6, 0, 180), ("line", [(12, 17), (12, 21)]),
            ("line", [(8.5, 21), (15.5, 21)])],
    "star": [("shape", _star(5, 9, 3.8))],
    "eye": [("line", [(2.5, 12), (6, 8), (12, 6), (18, 8), (21.5, 12), (18, 16), (12, 18),
                      (6, 16), (2.5, 12)]), ("ring", 12, 12, 3)],
    "next": [("line", [(4, 12), (19, 12)]), ("line", [(13, 6), (19, 12), (13, 18)])],
    "login": [("line", [(14, 4), (20, 4), (20, 20), (14, 20)]), ("line", [(3, 12), (14, 12)]),
              ("line", [(10, 8), (14, 12), (10, 16)])],
    "palette": [("ring", 12, 12, 8.5), ("dot", 8.5, 9, 1.4), ("dot", 12.5, 7.5, 1.4),
                ("dot", 15.8, 10, 1.4), ("dot", 15, 14.5, 1.4)],
    "cut": [("ring", 6.5, 17.5, 3), ("ring", 17.5, 17.5, 3), ("line", [(8.5, 15), (18, 4)]),
            ("line", [(15.5, 15), (6, 4)])],
    "mute": [("shape", [(4, 9.5), (8, 9.5), (13, 5), (13, 19), (8, 14.5), (4, 14.5)]),
             ("line", [(16, 9.5), (21, 14.5)]), ("line", [(21, 9.5), (16, 14.5)])],
    "chart": [("line", [(4, 4), (4, 20), (20, 20)]), ("line", [(7.5, 15.5), (11.5, 11),
                                                             (14.5, 13.5), (19.5, 7)])],
    "robot": [("shape", [(5, 8), (19, 8), (19, 19), (5, 19)]), ("line", [(12, 8), (12, 4.5)]),
              ("dot", 12, 4, 1.3), ("dot", 9, 13, 1.5), ("dot", 15, 13, 1.5),
              ("line", [(9.5, 16.5), (14.5, 16.5)])],
    "grid": [("shape", [(4, 4), (10.5, 4), (10.5, 10.5), (4, 10.5)]),
             ("shape", [(13.5, 4), (20, 4), (20, 10.5), (13.5, 10.5)]),
             ("shape", [(4, 13.5), (10.5, 13.5), (10.5, 20), (4, 20)]),
             ("shape", [(13.5, 13.5), (20, 13.5), (20, 20), (13.5, 20)])],
    "phone": [("shape", [(7, 2.5), (17, 2.5), (17, 21.5), (7, 21.5)]),
              ("line", [(10.5, 18.5), (13.5, 18.5)])],
    "user": [("ring", 12, 8, 4), ("line", [(4.5, 21), (5.5, 17), (8.5, 14.5), (12, 14),
                                           (15.5, 14.5), (18.5, 17), (19.5, 21)])],
    "image": [("shape", [(3, 5), (21, 5), (21, 19), (3, 19)]),
              ("line", [(3, 17), (9, 11), (13, 15), (16, 12.5), (21, 17)]),
              ("dot", 16, 9, 1.6)],
}

# слово в начале текста кнопки или вкладки → иконка
WORDS = (
    ("свернуть в трей", "tray"), ("открыть файл", "open"), ("просто открыть", "next"),
    ("остановить", "stop"), ("стоп", "stop"), ("прослушать", "play"), ("▶", "play"),
    ("скачать", "download"), ("выложить", "upload"), ("выгрузка", "upload"),
    ("вставить", "paste"), ("удалить", "trash"), ("расшифровать", "wave"),
    ("добавить", "plus"), ("новый", "plus"), ("создать", "plus"), ("сохранить", "save"),
    ("запомнить", "save"), ("проверить", "check"), ("одобрить", "check"),
    ("начать", "next"), ("закрыть", "close"), ("выйти", "close"), ("отмена", "close"),
    ("отклонить", "close"), ("не ", "close"), ("✕", "close"), ("обновить", "refresh"),
    ("искать", "search"), ("поиск", "search"), ("по смыслу", "spark"),
    ("правила", "spark"), ("по громкости", "volume"), ("собрать", "film"),
    ("сборка", "film"), ("ролики", "film"), ("клипы", "cut"), ("подключить", "link"),
    ("скопировать", "copy"), ("голос", "mic"), ("это новый звук", "mic"),
    ("оцен", "star"), ("посмотреть", "eye"), ("передать", "next"), ("войти", "login"),
    ("цвет", "palette"), ("выбрать", "folder"), ("из файла", "folder"),
    ("взять из файла", "folder"), ("папки", "folder"), ("из банка", "user"),
    ("взять фото", "user"), ("цензура", "mute"), ("разбор", "search"),
    ("монтаж", "phone"), ("автопилот", "robot"), ("запускать", "robot"),
    ("чужие каналы", "chart"), ("каналы", "chart"), ("превью", "image"),
)


def name_for(text) -> str:
    low = str(text or "").strip().lower()
    for word, name in WORDS:
        if low.startswith(word):
            return name
    return ""


def bare_text(text) -> str:
    """Текст без значков-символов: иконка их заменяет."""
    text = str(text or "")
    for mark in ("▶ ", "▶", "✕"):
        if text.startswith(mark):
            return text[len(mark):].strip()
    return text


def _rgba(color, alpha=255):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def draw(name, color, size=SIZE):
    """Картинка PIL с иконкой; None, если такой иконки нет."""
    if Image is None or name not in SHAPES:
        return None
    sharp = True
    big = size * ZOOM
    unit = big / GRID
    width = max(2, int(round(unit * (2.2 if sharp else 1.8))))
    ink = _rgba(color)
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)

    def at(points):
        return [(x * unit, y * unit) for x, y in points]

    def caps(points):
        if sharp:
            return
        for x, y in (points[0], points[-1]):
            pen.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=ink)

    for part in SHAPES[name]:
        kind = part[0]
        if kind == "line":
            points = at(part[1])
            pen.line(points, fill=ink, width=width, joint=None if sharp else "curve")
            caps(points)
        elif kind == "shape":
            points = at(part[1])
            pen.line(points + points[:1], fill=ink, width=width,
                     joint=None if sharp else "curve")
            if sharp:                     # углы без щербин
                for x, y in points:
                    pen.rectangle((x - width / 2, y - width / 2, x + width / 2,
                                   y + width / 2), fill=ink)
        elif kind == "fill":
            pen.polygon(at(part[1]), fill=ink)
        elif kind == "ring":
            _k, cx, cy, radius = part
            box = ((cx - radius) * unit, (cy - radius) * unit, (cx + radius) * unit,
                   (cy + radius) * unit)
            pen.ellipse(box, outline=ink, width=width)
        elif kind == "dot":
            _k, cx, cy, radius = part
            pen.ellipse(((cx - radius) * unit, (cy - radius) * unit, (cx + radius) * unit,
                         (cy + radius) * unit), fill=ink)
        elif kind == "arc":
            _k, cx, cy, radius, start, end = part
            box = ((cx - radius) * unit, (cy - radius) * unit, (cx + radius) * unit,
                   (cy + radius) * unit)
            pen.arc(box, start, end, fill=ink, width=width)
    return image.resize((size, size), Image.LANCZOS)


def logo(size=64):
    """Знак Студии в цветах стиля: для окна и трея."""
    big = size * ZOOM
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    accent = _rgba(_ui.ACCENT)
    cut = big * 0.22                # срезанный угол, как у табличек в играх
    pen.polygon([(cut, 0), (big, 0), (big, big - cut), (big - cut, big), (0, big),
                 (0, cut)], fill=accent)
    ink = _rgba(_ui.ON_ACCENT)
    unit = big / GRID
    width = int(unit * 2.6)
    pen.line([(6.5 * unit, 18.5 * unit), (12 * unit, 5.5 * unit), (17.5 * unit, 18.5 * unit)],
             fill=ink, width=width, joint="curve")
    pen.line([(9 * unit, 13.5 * unit), (15 * unit, 13.5 * unit)], fill=ink, width=width)
    return image.resize((size, size), Image.LANCZOS)


def photo(widget, name, color, size=SIZE):
    """PhotoImage для Tk; хранится при корневом окне, чтобы не пропала."""
    if Image is None or not name:
        return None
    root = widget._root()
    cache = getattr(root, "_astra_icons", None)
    if cache is None:
        cache = root._astra_icons = {}
    key = (name, color, size)
    if key not in cache:
        picture = draw(name, color, size) if name != "logo" else logo(size)
        if picture is None:
            return None
        cache[key] = ImageTk.PhotoImage(picture, master=root)
    return cache[key]


def set_window_icon(window):
    """Значок окна и панели задач — знак Студии в цветах стиля."""
    if Image is None:
        return False
    try:
        big = photo(window, "logo", "", 64)
        small = photo(window, "logo", "", 32)
        window.iconphoto(True, big, small)
        return True
    except Exception:
        return False
