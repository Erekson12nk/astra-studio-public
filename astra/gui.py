import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
from tkinter import colorchooser, filedialog, font as tkfont, messagebox, ttk

try:
    import winsound
except ImportError:
    winsound = None

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auto_vertical_editor as ave
from core.ui import (ACCENT, ACCENT2, BAD, BG, BIG_FONT, DIM, LINE, MICRO_FONT,
                     MONO_FONT, OK, PANEL, PANEL2, SMALL_FONT, SOFT, STAGE,
                     TEXT, UI_FONT, WARN, WARN_BG, WHITE, round_rect)
from core.ui import apply as apply_theme
from core.settings import Render
from core import asr as _asr

ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")


def note_error(text):
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as handle:
            handle.write("%s\n%s\n\n" % (time.strftime("%d.%m.%Y %H:%M:%S"),
                                         text.rstrip()))
    except Exception:
        pass


PREVIEW_W = 248
PREVIEW_H = 441
SOURCE_W = 400
SOURCE_H = 225
STAGE_W = 404
STAGE_H = 447
LEFT_W = 374
RIGHT_W = 552
PAGE_H = 690
MIN_W = LEFT_W + RIGHT_W + 36
MIN_H = 700
AUDIO_CHUNK = 120.0


def _profile_whisper():
    try:
        from core import profiles as _pro

        profile = _pro.active()
    except Exception:
        return ave.DEFAULT_WHISPER
    if profile and profile.get("whisper"):
        return profile["whisper"]
    return ave.DEFAULT_WHISPER


LAYOUTS = (("Сама решит", "auto"), ("Вебка и экран", "split"),
           ("Только вебка", "cam"), ("Только экран", "screen"))
LAYOUT_NAMES = tuple(name for name, _ in LAYOUTS)
LAYOUT_CODES = dict(LAYOUTS)


def spaced(text):
    return " ".join(text.upper())


def short_time(seconds):
    seconds = max(0, int(round(float(seconds))))
    hours = seconds // 3600
    rest = seconds % 3600
    if hours:
        return "%d:%02d:%02d" % (hours, rest // 60, rest % 60)
    return "%d:%02d" % (rest // 60, rest % 60)


class Cursor(tk.DoubleVar):
    def __init__(self, master, on_change):
        super().__init__(master, value=0.0)
        self.on_change = on_change

    def set(self, value):
        super().set(value)
        self.on_change(float(value))


class Card(tk.Canvas):
    def __init__(self, master, pad=12, radius=12, fill=PANEL, outline=LINE,
                 back=BG, width=None, stretch=False):
        super().__init__(master, background=back, highlightthickness=0,
                         borderwidth=0, width=10, height=10)
        self.pad = pad
        self.radius = radius
        self.fill = fill
        self.outline = outline
        self.fixed = width
        self.stretch = stretch
        self.shape = None
        self.drawn = None
        self.body = tk.Frame(self, background=fill)
        self.slot = self.create_window(pad, pad, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._fit)
        if stretch:
            self.bind("<Configure>", self._grown)
        self.after_idle(self._fit)

    def _grown(self, event):
        self.itemconfigure(self.slot, width=event.width - self.pad * 2,
                           height=event.height - self.pad * 2)
        self._paint(event.width, event.height)

    def _fit(self, _event=None):
        inner_w = self.body.winfo_reqwidth()
        if self.fixed:
            inner_w = self.fixed - self.pad * 2
            if self.stretch:
                inner_w = max(inner_w, self.winfo_width() - self.pad * 2)
            self.itemconfigure(self.slot, width=inner_w)
        width = inner_w + self.pad * 2
        height = self.body.winfo_reqheight() + self.pad * 2
        self.configure(width=width, height=height)
        if self.stretch:
            width = max(width, self.winfo_width())
            height = max(height, self.winfo_height())
        self._paint(width, height)

    def _paint(self, width, height):
        if self.drawn == (width, height):
            return
        self.drawn = (width, height)
        if self.shape is not None:
            self.delete(self.shape)
        self.shape = round_rect(self, 1, 1, width - 1, height - 1, self.radius,
                                fill=self.fill, outline=self.outline)
        self.tag_lower(self.shape)


def section(master, icon, text, back=PANEL, right=None):
    row = tk.Frame(master, background=back)
    tk.Label(row, text=icon, background=back, foreground=ACCENT,
             font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
    tk.Label(row, text=spaced(text), background=back, foreground=DIM,
             font=MICRO_FONT).pack(side="left")
    if right is not None:
        tk.Label(row, textvariable=right, background=back, foreground=ACCENT2,
                 font=MICRO_FONT).pack(side="right")
    return row


class Btn(tk.Canvas):
    def __init__(self, master, text, command, width=110, height=30, accent=False,
                 back=PANEL, radius=8, danger=False):
        super().__init__(master, width=width, height=height, background=back,
                         highlightthickness=0, borderwidth=0, cursor="hand2")
        self.label = text
        self.command = command
        self.accent = accent
        self.danger = danger
        self.radius = radius
        self.enabled = True
        self.hot = False
        self.box = (width, height)
        self.bind("<Button-1>", self._fire)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.redraw()

    def _enter(self, _event=None):
        self.hot = True
        self.redraw()

    def _leave(self, _event=None):
        self.hot = False
        self.redraw()

    def _fire(self, _event=None):
        if self.enabled and self.command is not None:
            self.command()

    def redraw(self):
        width, height = self.box
        self.delete("all")
        fill = PANEL2
        edge = LINE
        colour = TEXT
        if self.accent:
            fill = ACCENT2 if self.hot else ACCENT
            edge = fill
            colour = WHITE
        elif self.danger:
            fill = "#3a1b23" if self.hot else PANEL2
            edge = "#5a2440"
            colour = BAD
        elif self.hot:
            fill = LINE
        if not self.enabled:
            fill = PANEL2
            edge = LINE
            colour = DIM
        round_rect(self, 1, 1, width - 1, height - 1, self.radius,
                   fill=fill, outline=edge)
        self.create_text(width / 2, height / 2 + 1, text=self.label,
                         fill=colour, font=SMALL_FONT)

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf or {})
        options.update(kwargs)
        state = options.pop("state", None)
        text = options.pop("text", None)
        if state is not None:
            self.enabled = str(state) != "disabled"
            self.configure_cursor()
        if text is not None:
            self.label = text
        if state is not None or text is not None:
            self.redraw()
        if options:
            return super().configure(**options)
        return None

    config = configure

    def configure_cursor(self):
        super().configure(cursor="hand2" if self.enabled else "arrow")


class Glyph(tk.Canvas):
    def __init__(self, master, text, command, size=30, back=PANEL, accent=False,
                 font=("Segoe UI", 10)):
        super().__init__(master, width=size, height=size, background=back,
                         highlightthickness=0, borderwidth=0, cursor="hand2")
        self.label = text
        self.command = command
        self.accent = accent
        self.size = size
        self.font = font
        self.enabled = True
        self.hot = False
        self.bind("<Button-1>", self._fire)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.redraw()

    def _enter(self, _event=None):
        self.hot = True
        self.redraw()

    def _leave(self, _event=None):
        self.hot = False
        self.redraw()

    def _fire(self, _event=None):
        if self.enabled and self.command is not None:
            self.command()

    def redraw(self):
        size = self.size
        self.delete("all")
        if self.accent:
            fill = ACCENT2 if self.hot else ACCENT
            colour = WHITE
            self.create_oval(1, 1, size - 1, size - 1, fill=fill, outline=fill)
        else:
            fill = LINE if self.hot else PANEL2
            colour = TEXT if self.enabled else DIM
            round_rect(self, 1, 1, size - 1, size - 1, 8, fill=fill, outline=LINE)
        self.create_text(size / 2 + (1 if self.accent else 0), size / 2 + 1,
                         text=self.label, fill=colour, font=self.font)

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf or {})
        options.update(kwargs)
        state = options.pop("state", None)
        text = options.pop("text", None)
        if state is not None:
            self.enabled = str(state) != "disabled"
        if text is not None:
            self.label = text
        if state is not None or text is not None:
            self.redraw()
        if options:
            return super().configure(**options)
        return None

    config = configure


class Tabs(tk.Canvas):
    def __init__(self, master, items, command, width, back=PANEL, kind="pill"):
        super().__init__(master, width=width, height=34, background=back,
                         highlightthickness=0, borderwidth=0, cursor="hand2")
        self.items = list(items)
        self.command = command
        self.kind = kind
        self.width = width
        self.current = 0
        self.boxes = []
        self.font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        height = self._layout()
        self.configure(height=height)
        self.bind("<Button-1>", self._click)
        self.redraw()

    def _layout(self):
        self.boxes = []
        pad = 24 if self.kind == "pill" else 4
        row_h = 28 if self.kind == "pill" else 30
        gap = 4 if self.kind == "pill" else 18
        x = 0
        y = 0
        for icon, label in self.items:
            text = ("%s  %s" % (icon, label)) if icon else label
            width = self.font.measure(text) + pad
            if x and x + width > self.width:
                x = 0
                y += row_h + 4
            self.boxes.append([x, y, width, row_h, text])
            x += width + gap
        return y + row_h + (4 if self.kind == "pill" else 6)

    def redraw(self):
        self.delete("all")
        if self.kind == "pill":
            rows = {}
            for x, y, w, h, _text in self.boxes:
                rows[y] = max(rows.get(y, 0), x + w)
            for y, right in rows.items():
                round_rect(self, 0, y - 3, right + 6, y + 31, 10,
                           fill=PANEL2, outline=PANEL2)
        for index, (x, y, w, h, text) in enumerate(self.boxes):
            picked = index == self.current
            if self.kind == "pill":
                if picked:
                    round_rect(self, x + 2, y, x + w + 2, y + h, 8,
                               fill=ACCENT, outline=ACCENT)
                self.create_text(x + 2 + w / 2, y + h / 2 + 1, text=text,
                                 fill=WHITE if picked else DIM, font=SMALL_FONT)
            else:
                self.create_text(x + w / 2, y + h / 2 - 2, text=text,
                                 fill=TEXT if picked else DIM, font=SMALL_FONT)
                if picked:
                    self.create_line(x, y + h - 1, x + w, y + h - 1,
                                     fill=ACCENT, width=2)

    def _click(self, event):
        for index, (x, y, w, h, _text) in enumerate(self.boxes):
            if x <= event.x <= x + w + 4 and y <= event.y <= y + h:
                self.select(index)
                return

    def select(self, index, notify=True):
        if not 0 <= index < len(self.boxes):
            return
        self.current = index
        self.redraw()
        if notify and self.command is not None:
            self.command(index)


class Choices(tk.Canvas):
    def __init__(self, master, items, command, width, back=PANEL, height=52):
        super().__init__(master, width=width, height=height, background=back,
                         highlightthickness=0, borderwidth=0, cursor="hand2")
        self.items = list(items)
        self.command = command
        self.width = width
        self.height = height
        self.current = 0
        self.bind("<Button-1>", self._click)
        self.redraw()

    def _cell(self, index):
        gap = 6
        count = len(self.items)
        cell = (self.width - gap * (count - 1)) / float(count)
        return index * (cell + gap), cell

    def redraw(self):
        self.delete("all")
        for index, (icon, label) in enumerate(self.items):
            x, cell = self._cell(index)
            picked = index == self.current
            round_rect(self, x + 1, 1, x + cell - 1, self.height - 1, 9,
                       fill=SOFT if picked else PANEL2,
                       outline=ACCENT if picked else LINE)
            self.create_text(x + cell / 2, 18, text=icon,
                             fill=ACCENT2 if picked else DIM, font=("Segoe UI", 10))
            self.create_text(x + cell / 2, 37, text=label,
                             fill=TEXT if picked else DIM, font=SMALL_FONT)

    def _click(self, event):
        for index in range(len(self.items)):
            x, cell = self._cell(index)
            if x <= event.x <= x + cell:
                self.select(index)
                return

    def select(self, index, notify=True):
        if not 0 <= index < len(self.items):
            return
        self.current = index
        self.redraw()
        if notify and self.command is not None:
            self.command(index)


class Moments(tk.Frame):
    ROW = 31

    def __init__(self, master, width, rows=5, back=PANEL, on_pick=None, on_check=None):
        super().__init__(master, background=back)
        self.width = width
        self.visible = rows
        self.items = []
        self.order = []
        self.checked = set()
        self.cursor = None
        self.offset = 0
        self.sort_key = "time"
        self.sort_down = False
        self.on_pick = on_pick
        self.on_check = on_check
        self.head = tk.Canvas(self, width=width, height=21, background=back,
                              highlightthickness=0, borderwidth=0, cursor="hand2")
        self.head.pack(fill="x")
        self.head.bind("<Button-1>", self._head_click)
        self.body = tk.Canvas(self, width=width, height=self.ROW * rows,
                              background=back, highlightthickness=0, borderwidth=0)
        self.body.pack(fill="x")
        self.body.bind("<Button-1>", self._click)
        self.body.bind("<MouseWheel>", self._wheel)
        self._draw_head()
        self.redraw()

    def _draw_head(self):
        canvas = self.head
        canvas.delete("all")
        full = self.items and len(self.checked) == len(self.items)
        round_rect(canvas, 9, 5, 21, 17, 3,
                   fill=ACCENT if full else PANEL2, outline=ACCENT if full else LINE)
        if full:
            canvas.create_text(15, 11, text="✓", fill=WHITE, font=("Segoe UI", 7, "bold"))
        mark = " ↓" if self.sort_down else " ↑"
        canvas.create_text(31, 11, text=spaced("фрагмент") + (mark if self.sort_key == "time" else ""),
                           anchor="w", fill=DIM, font=MICRO_FONT)
        canvas.create_text(self.width - 14, 11,
                           text=spaced("оценка") + (mark if self.sort_key == "score" else ""),
                           anchor="e", fill=DIM, font=MICRO_FONT)

    def _head_click(self, event):
        if event.x <= 26:
            if len(self.checked) == len(self.items):
                self.checked = set()
            else:
                self.checked = set(range(len(self.items)))
        elif event.x > self.width - 90:
            self._sort("score")
        else:
            self._sort("time")
        self._draw_head()
        self.redraw()
        if self.on_check is not None:
            self.on_check()

    def _sort(self, key):
        if self.sort_key == key:
            self.sort_down = not self.sort_down
        else:
            self.sort_key = key
            self.sort_down = key == "score"
        if key == "score":
            self.order.sort(key=lambda i: self.items[i]["score"])
        else:
            self.order.sort(key=lambda i: self.items[i]["start"])
        if self.sort_down:
            self.order.reverse()
        self.offset = 0

    def set_items(self, items):
        self.items = list(items)
        self.order = list(range(len(self.items)))
        self.checked = set()
        self.cursor = None
        self.offset = 0
        self.sort_key = "time"
        self.sort_down = False
        self._draw_head()
        self.redraw()

    def clear(self):
        self.set_items([])

    def selection(self):
        return sorted(self.checked)

    def check_all(self):
        self.checked = set(range(len(self.items)))
        self._draw_head()
        self.redraw()

    def check_only(self, indexes):
        self.checked = set(i for i in indexes if 0 <= i < len(self.items))
        self._draw_head()
        self.redraw()

    def step(self, delta):
        if not self.order:
            return
        place = 0
        if self.cursor is not None and self.cursor in self.order:
            place = self.order.index(self.cursor) + delta
        place = max(0, min(len(self.order) - 1, place))
        self.cursor = self.order[place]
        if place < self.offset:
            self.offset = place
        elif place >= self.offset + self.visible:
            self.offset = place - self.visible + 1
        self.redraw()
        if self.on_pick is not None:
            self.on_pick(self.cursor)

    def redraw(self):
        canvas = self.body
        canvas.delete("all")
        width = self.width
        if not self.items:
            canvas.create_text(width / 2, self.ROW * self.visible / 2,
                               text="Моментов пока нет", fill=DIM, font=UI_FONT)
            return
        top = max(0.0, max(item["score"] for item in self.items))
        peak = top * 0.95
        for slot in range(self.visible):
            place = self.offset + slot
            if place >= len(self.order):
                break
            index = self.order[place]
            item = self.items[index]
            y = slot * self.ROW
            if self.cursor == index:
                round_rect(canvas, 2, y + 1, width - 6, y + self.ROW - 2, 7,
                           fill=PANEL2, outline=PANEL2)
                canvas.create_rectangle(3, y + 6, 5, y + self.ROW - 7,
                                        fill=ACCENT, outline=ACCENT)
            picked = index in self.checked
            round_rect(canvas, 9, y + 10, 21, y + 22, 3,
                       fill=ACCENT if picked else PANEL2,
                       outline=ACCENT if picked else LINE)
            if picked:
                canvas.create_text(15, y + 16, text="✓", fill=WHITE,
                                   font=("Segoe UI", 7, "bold"))
            stamp = "%s — %s" % (format_time(item["start"])[:8], format_time(item["end"])[:8])
            canvas.create_text(31, y + 16, text=stamp, anchor="w",
                               fill=ACCENT2, font=MONO_FONT)
            if top > 0 and item["score"] >= peak:
                round_rect(canvas, 148, y + 9, 176, y + 23, 5, fill=SOFT, outline=SOFT)
                canvas.create_text(162, y + 16, text="пик", fill=ACCENT2, font=MICRO_FONT)
            canvas.create_text(width - 14, y + 16, text="›", anchor="e",
                               fill=DIM, font=("Segoe UI", 11))
            if top > 0:
                share = max(0.06, min(1.0, item["score"] / top))
                bar = width - 108
                canvas.create_line(bar, y + 16, bar + 52, y + 16, fill=LINE, width=3)
                canvas.create_line(bar, y + 16, bar + 52 * share, y + 16,
                                   fill=ACCENT, width=3)
                canvas.create_text(width - 26, y + 16, text="%.1f" % item["score"],
                                   anchor="e", fill=TEXT, font=SMALL_FONT)
            else:
                title = (item.get("title") or "").strip()
                canvas.create_text(width - 26, y + 16, text=title[:22] or "—",
                                   anchor="e", fill=DIM, font=UI_FONT)
        if len(self.order) > self.visible:
            span = self.ROW * self.visible
            height = span * self.visible / float(len(self.order))
            start = span * self.offset / float(len(self.order))
            canvas.create_line(width - 3, start + 2, width - 3, start + height - 2,
                               fill=LINE, width=3)

    def _click(self, event):
        place = self.offset + int(event.y // self.ROW)
        if place >= len(self.order):
            return
        index = self.order[place]
        if event.x <= 26:
            if index in self.checked:
                self.checked.discard(index)
            else:
                self.checked.add(index)
            self._draw_head()
            self.redraw()
            if self.on_check is not None:
                self.on_check()
            return
        self.cursor = index
        self.redraw()
        if self.on_pick is not None:
            self.on_pick(index)

    def _wheel(self, event):
        limit = max(0, len(self.order) - self.visible)
        step = -1 if event.delta > 0 else 1
        self.offset = max(0, min(limit, self.offset + step))
        self.redraw()


class Wave(tk.Canvas):
    STEP = 3

    def __init__(self, master, width, height, back=PANEL, on_seek=None, on_select=None):
        super().__init__(master, width=width, height=height, background=back,
                         highlightthickness=0, borderwidth=0, cursor="hand2")
        self.w = width
        self.h = height
        self.bars = []
        self.position = 0.0
        self.cuts = []
        self.pending = None
        self.picked = None
        self.on_seek = on_seek
        self.on_select = on_select
        self.bar_ids = []
        self.cursor_id = None
        self._anchor = None
        self.bind("<Button-1>", self._seek)
        self.bind("<B1-Motion>", self._seek)
        for press, motion, release in (("<Shift-Button-1>", "<Shift-B1-Motion>",
                                        "<Shift-ButtonRelease-1>"),
                                       ("<Button-3>", "<B3-Motion>", "<ButtonRelease-3>")):
            self.bind(press, self._pick_press)
            self.bind(motion, self._pick_drag)
            self.bind(release, self._pick_release)
        self.bind("<Configure>", self._resized)
        self.redraw()

    def _resized(self, event):
        if event.width == self.w:
            return
        self.w = event.width
        self.redraw()

    def set_bars(self, bars):
        self.bars = list(bars)
        self.redraw()

    def set_picked(self, picked):
        self.picked = picked
        self.redraw()

    def _share(self, event):
        return max(0.0, min(1.0, event.x / float(self.w)))

    def _seek(self, event):
        if self.on_seek is not None:
            self.on_seek(self._share(event))

    def _pick_press(self, event):
        self._anchor = self._share(event)
        self.picked = None
        self.redraw()
        return "break"

    def _pick_drag(self, event):
        if self._anchor is None:
            return "break"
        first, last = sorted((self._anchor, self._share(event)))
        self.picked = (first, last)
        self.redraw()
        return "break"

    def _pick_release(self, event):
        if self._anchor is None:
            return "break"
        first, last = sorted((self._anchor, self._share(event)))
        self._anchor = None
        if last - first < 0.002:
            self.picked = None
            self.redraw()
            return "break"
        self.picked = (first, last)
        self.redraw()
        if self.on_select is not None:
            self.on_select(first, last)
        return "break"

    def _colour(self, share):
        for first, last in self.cuts:
            if first <= share <= last:
                return BAD if share <= self.position else "#5a2440"
        if share <= self.position:
            return ACCENT2
        return "#2f2f47"

    def set_position(self, share):
        share = max(0.0, min(1.0, share))
        previous = self.position
        self.position = share
        if not self.bar_ids:
            self.redraw()
            return
        count = len(self.bar_ids)
        low, high = sorted((previous, share))
        first = max(0, int(low * count) - 1)
        last = min(count - 1, int(high * count) + 1)
        for slot in range(first, last + 1):
            self.itemconfigure(self.bar_ids[slot], fill=self._colour(slot / float(count)))
        x = share * self.w
        if self.cursor_id is not None:
            self.coords(self.cursor_id, x, 0, x, self.h)

    def redraw(self):
        self.delete("all")
        self.bar_ids = []
        count = max(1, int(self.w // self.STEP))
        middle = self.h / 2.0
        source = self.bars or [0.07] * count
        if self.picked is not None:
            self.create_rectangle(self.picked[0] * self.w, 0, self.picked[1] * self.w, self.h,
                                  fill=SOFT, outline="")
        for slot in range(count):
            level = source[int(slot * len(source) / count)]
            x = slot * self.STEP + 1
            half = max(1.0, level * (middle - 3))
            self.bar_ids.append(self.create_line(x, middle - half, x, middle + half,
                                                 fill=self._colour(slot / float(count)), width=2))
        if self.picked is not None:
            for edge in self.picked:
                self.create_line(edge * self.w, 0, edge * self.w, self.h, fill=ACCENT, width=2)
        if self.pending is not None:
            x = self.pending * self.w
            self.create_line(x, 2, x, self.h - 2, fill=BAD, width=1)
        x = self.position * self.w
        self.cursor_id = self.create_line(x, 0, x, self.h, fill=WHITE, width=1)


def chips(master, pairs, back=PANEL, width=300):
    canvas = tk.Canvas(master, width=width, height=20, background=back,
                       highlightthickness=0, borderwidth=0)
    measure = tkfont.Font(family="Segoe UI", size=7, weight="bold")
    plain = tkfont.Font(family="Segoe UI", size=8)
    x = 0
    for key, text in pairs:
        key_w = measure.measure(key) + 12
        round_rect(canvas, x, 3, x + key_w, 19, 4, fill=PANEL2, outline=LINE)
        canvas.create_text(x + key_w / 2, 11, text=key, fill=ACCENT2, font=MICRO_FONT)
        x += key_w + 4
        canvas.create_text(x, 11, text=text, anchor="w", fill=DIM, font=plain)
        x += plain.measure(text) + 12
    return canvas

class TitleBar(tk.Canvas):
    def __init__(self, master, window, width):
        super().__init__(master, width=width, height=44, background=BG,
                         highlightthickness=0, borderwidth=0)
        self.window = window
        self.width = width
        self.owner = ""
        self.badge = ""
        self.bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.plain = tkfont.Font(family="Segoe UI", size=9)
        self.tiny = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        self.bind("<Configure>", self._resized)
        self.redraw()

    def _resized(self, event):
        if event.width == self.width:
            return
        self.width = event.width
        self.redraw()

    def set_owner(self, owner, badge):
        self.owner = owner
        self.badge = badge
        self.redraw()

    def redraw(self):
        self.delete("all")
        round_rect(self, 14, 11, 40, 37, 8, fill=ACCENT, outline=ACCENT)
        self.create_text(27, 24, text="✂", fill=WHITE, font=("Segoe UI", 11))
        x = 50
        self.create_text(x, 24, text="Astra", anchor="w", fill=TEXT, font=self.bold)
        x += self.bold.measure("Astra") + 8
        self.create_text(x, 25, text="вертикальные 9:16", anchor="w", fill=DIM,
                         font=self.plain)
        x += self.plain.measure("вертикальные 9:16") + 16
        if self.owner:
            self.create_text(x, 25, text=self.owner, anchor="w", fill=DIM, font=self.plain)
            x += self.plain.measure(self.owner) + 12
        if self.badge:
            text = "⏱ %s" % self.badge
            width = self.tiny.measure(text) + 18
            round_rect(self, x, 14, x + width, 34, 9, fill=WARN_BG, outline=WARN_BG)
            self.create_text(x + width / 2, 24, text=text, fill=WARN, font=self.tiny)


FONT_FILES = {
    "Arial Black": "ariblk.ttf",
    "Impact": "impact.ttf",
    "Arial": "arialbd.ttf",
    "Verdana": "verdanab.ttf",
    "Tahoma": "tahomabd.ttf",
    "Georgia": "georgiab.ttf",
    "Trebuchet MS": "trebucbd.ttf",
    "Segoe UI": "segoeuib.ttf",
    "Calibri": "calibrib.ttf",
}


def font_folder():
    return os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")


class Tip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def show(self, _event=None):
        if self.window is not None:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.window, text=self.text, background=PANEL, foreground=TEXT,
                 relief="solid", borderwidth=1, justify="left", padx=7, pady=5,
                 font=("Segoe UI", 8)).pack()

    def hide(self, _event=None):
        if self.window is not None:
            self.window.destroy()
            self.window = None


def available_fonts():
    folder = font_folder()
    found = [name for name, file in FONT_FILES.items()
             if os.path.isfile(os.path.join(folder, file))]
    return found or ["Arial"]


def clip_title(path):
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"^\d{1,3}[_\-]", "", name)
    name = re.sub(r"^\d{1,3}-\d{1,2}-\d{1,2}[_\-]", "", name)
    name = name.replace("_", " ").strip()
    if not name:
        name = os.path.splitext(os.path.basename(path))[0]
    return name[:100]


def format_time(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return "%02d:%02d:%06.3f" % (hours, minutes, secs)


def to_photo(bgr, width, height, fast=False):
    resized = cv2.resize(bgr, (width, height),
                         interpolation=cv2.INTER_LINEAR if fast else cv2.INTER_AREA)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return ImageTk.PhotoImage(Image.fromarray(rgb))


class Editor(tk.Frame):
    def __init__(self, master, chrome=True):
        super().__init__(master, background=BG)
        self.chrome = chrome

        self.preview_w = PREVIEW_W
        self.preview_h = PREVIEW_H
        self.source_w = SOURCE_W
        self.source_h = SOURCE_H
        self.capture = None
        self.result_capture = None
        self.source = None
        self.info = None
        self.cam_box = None
        self.game_box = None
        self.face_center = None
        self.result_path = None
        self.batch_folder = None
        self.mode = "layout"
        self.playing = False
        self.play_job = None
        self.worker = None
        self.messages = queue.Queue()
        self.photo = None

        self.var_source = tk.StringVar()
        self.var_start = tk.StringVar(value="00:00:00")
        self.var_end = tk.StringVar(value="00:00:30")
        self.var_output = tk.StringVar()
        self.var_top = tk.IntVar(value=760)
        self.var_blur = tk.BooleanVar(value=True)
        self.var_subs = tk.BooleanVar(value=True)
        self.var_lang = tk.StringVar(value="ru")
        self.var_model = tk.StringVar(value=_profile_whisper())
        self.var_fill = tk.DoubleVar(value=0.42)
        self.var_lock = tk.BooleanVar(value=True)
        self.var_gamey = tk.DoubleVar(value=0.5)
        self.var_scan_count = tk.IntVar(value=8)
        self.var_scan_mode = tk.StringVar(value="По громкости")
        self.var_scan_pool = tk.IntVar(value=40)
        self.brief_path = None
        self.answer_path = None
        self.timecodes_path = None
        self.var_scan_min = tk.IntVar(value=35)
        self.var_scan_max = tk.IntVar(value=75)
        self.var_font = tk.StringVar(value=available_fonts()[0])
        self.var_color_base = tk.StringVar(value=ave.BASE_COLOR)
        self.var_color_high = tk.StringVar(value=ave.HIGHLIGHT_COLOR)
        self.var_color_title = tk.StringVar(value=ave.TITLE_COLOR)
        self.drag = None
        self.var_layout = tk.StringVar(value=LAYOUT_NAMES[0])
        self.subs_moved = False
        self.var_subs_x = tk.IntVar(value=ave.OUT_W // 2)
        self.var_subs_y = tk.IntVar(value=760 - int(96 * ave.SEAM_SUBS_LIFT))
        self.var_title = tk.StringVar(value="")
        self.var_title_size = tk.IntVar(value=64)
        self.var_censor = tk.BooleanVar(value=True)
        self.var_cut_pauses = tk.BooleanVar(value=True)
        self.var_pause_min = tk.DoubleVar(value=0.45)
        self.candidates = []
        self._fonts = {}
        self.var_cam = [tk.IntVar(value=0) for _ in range(4)]
        self.var_game = [tk.IntVar(value=0) for _ in range(4)]
        self.var_game_lock = tk.BooleanVar(value=True)
        self.var_target = tk.StringVar(value="cam")
        self.var_sound = tk.BooleanVar(value=True)
        self.cuts = []
        self.cut_pending = None
        self.play_from = 0.0
        self.play_end = 0.0
        self.play_t0 = 0.0
        self.audio_token = 0
        self.audio_end = None
        self.pick_range = None
        self.var_pick = tk.StringVar(value="момент не выделен")
        self.cuts_drawn = None
        self.scrub_job = None
        self.var_status = tk.StringVar(value="")
        self.var_hint = tk.StringVar(value="Шаг 1. Выберите видео стрима кнопкой «Обзор»." if chrome
                                    else "Шаг 1. Выберите стрим в поле «Источник» сверху.")
        self.view = 0
        self.wave_key = None
        self.wave_job = None
        self.var_found = tk.StringVar(value="")
        self.var_stat = [tk.StringVar(value="—") for _ in range(4)]
        self.var_clock = tk.StringVar(value="00:00:00 / 00:00:00")
        self.var_badge = tk.StringVar(value="исходник")
        self.var_frame_cam = tk.StringVar(value="вебка —")
        self.var_frame_game = tk.StringVar(value="геймплей —")
        self.scrub = Cursor(self, self._on_cursor)

        self._build()
        self.var_scan_mode.trace_add("write", self._sync_mode)
        self._sync_mode()
        self._sync_pauses()
        self._update_stats()
        top = self.winfo_toplevel()
        top.bind("<KeyPress>", self._on_key, add="+")
        top.bind("<Control-KeyPress>", self._on_control_key, add="+")
        self.watch_mark = time.perf_counter()
        self.after(120, self._pump)
        self.after(250, self._watchdog)

    def _build(self):
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        outer = tk.Frame(self, background=BG)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(1, weight=1)

        if self.chrome:
            self.bar = TitleBar(outer, self, MIN_W)
            self.bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        else:
            self.bar = None

        left = tk.Frame(outer, background=BG)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        left.rowconfigure(2, weight=1)
        right = tk.Frame(outer, background=BG)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1, minsize=240)

        self.steps = Tabs(left, (("✂", "Нарезать"), ("▣", "Кадр"), ("T", "Надписи")),
                          self._on_step_changed, LEFT_W, back=BG)
        self.steps.grid(row=0, column=0, sticky="w")

        stats = Card(left, width=LEFT_W, pad=11)
        stats.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for index, caption in enumerate(("стрим", "клипов", "ср. оценка", "экспорт")):
            cell = tk.Frame(stats.body, background=PANEL)
            cell.grid(row=0, column=index, sticky="w", padx=(0, 20))
            tk.Label(cell, textvariable=self.var_stat[index], background=PANEL,
                     foreground=TEXT, font=BIG_FONT).pack(anchor="w")
            tk.Label(cell, text=spaced(caption), background=PANEL, foreground=DIM,
                     font=MICRO_FONT).pack(anchor="w")

        self.page_index = 0
        deck = tk.Canvas(left, background=BG, width=LEFT_W,
                         height=PAGE_H if self.chrome else 470,
                         highlightthickness=0, borderwidth=0, yscrollincrement=18)
        deck.grid(row=2, column=0, sticky="nsew", pady=(9, 0))
        deck.bind("<Configure>", self._deck_resized)
        self.deck = deck
        self.deck_bar = tk.Canvas(left, width=4,
                                  height=PAGE_H if self.chrome else 470, background=BG,
                                  highlightthickness=0, borderwidth=0)
        if self.chrome:
            self.deck_bar.grid(row=2, column=1, sticky="ns", pady=(9, 0), padx=(3, 0))
        self.pages = []
        self.page_slots = []
        for _ in range(3):
            page = tk.Frame(deck, background=BG)
            self.page_slots.append(deck.create_window(0, 0, anchor="nw", window=page,
                                                      width=LEFT_W, state="hidden"))
            self.pages.append(page)
        page_find, page_frame, page_text = self.pages

        source = Card(page_find, width=LEFT_W, pad=11)
        if self.chrome:
            source.grid(row=0, column=0, sticky="ew")
        source.body.columnconfigure(0, weight=1)
        section(source.body, "▤", "источник видео").grid(row=0, column=0, sticky="ew")
        source_row = tk.Frame(source.body, background=PANEL)
        source_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        source_row.columnconfigure(0, weight=1)
        ttk.Entry(source_row, textvariable=self.var_source).grid(row=0, column=0, sticky="ew")
        pick = Btn(source_row, "Обзор", self.pick_source, width=84)
        pick.grid(row=0, column=1, padx=(7, 0))
        Tip(pick, "Длинная запись стрима, из которой будем резать клипы")

        target = Card(page_find, width=LEFT_W, pad=11)
        target.grid(row=1, column=0, sticky="ew", pady=(9, 0) if self.chrome else (0, 0))
        target.body.columnconfigure(0, weight=1)
        section(target.body, "▥", "платформа").grid(row=0, column=0, sticky="ew")
        self.platform = Choices(target.body,
                                (("♪", "TikTok"), ("▷", "Shorts"),
                                 ("◎", "Reels"), ("✎", "Свой")),
                                self.on_platform, LEFT_W - 22)
        self.platform.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        Tip(self.platform, "Подставляет привычные для площадки границы длины клипа.\n"
                           "«Свой» ничего не меняет")

        finder = Card(page_find, width=LEFT_W, pad=11)
        finder.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        finder.body.columnconfigure(1, weight=1)
        section(finder.body, "⚡", "автопоиск моментов" if self.chrome else "моменты",
                right=self.var_found).grid(row=0, column=0, columnspan=4, sticky="ew")

        self.search_only = []
        lbl_mode = tk.Label(finder.body, text="Метод", background=PANEL, foreground=DIM,
                            font=UI_FONT)
        lbl_mode.grid(row=1, column=0, sticky="w", pady=(9, 0))
        mode = ttk.Combobox(finder.body, textvariable=self.var_scan_mode, state="readonly",
                            values=("По громкости", "По расшифровке"))
        mode.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(9, 0))
        Tip(mode, "По громкости - быстро, по всплескам звука.\n"
                  "По расшифровке - дольше, но распознаёт речь и готовит\n"
                  "текст для нейросети, чтобы она выбрала лучшее")
        self.search_only += [lbl_mode, mode]

        counts = tk.Frame(finder.body, background=PANEL)
        counts.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(7, 0))
        tk.Label(counts, text="Сколько", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(counts, from_=1, to=30, width=5,
                    textvariable=self.var_scan_count).grid(row=0, column=1, padx=(8, 0))
        self.lbl_pool = tk.Label(counts, text="Кандидаты", background=PANEL,
                                 foreground=DIM, font=UI_FONT)
        self.lbl_pool.grid(row=0, column=2, sticky="e", padx=(26, 0))
        self.spin_pool = ttk.Spinbox(counts, from_=5, to=200, increment=5, width=5,
                                     textvariable=self.var_scan_pool)
        self.spin_pool.grid(row=0, column=3, padx=(8, 0))
        Tip(self.spin_pool, "Сколько кусков расшифровать и показать нейросети")

        lbl_len = tk.Label(finder.body, text="Длина, с", background=PANEL, foreground=DIM,
                           font=UI_FONT)
        lbl_len.grid(row=3, column=0, columnspan=4, sticky="w", pady=(7, 0))
        self.search_only += [counts, lbl_len]
        length_row = tk.Frame(finder.body, background=PANEL)
        length_row.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(3, 0))
        length_row.columnconfigure(0, weight=1)
        length_row.columnconfigure(2, weight=1)
        ttk.Spinbox(length_row, from_=3, to=600, increment=5,
                    textvariable=self.var_scan_min).grid(row=0, column=0, sticky="ew")
        tk.Label(length_row, text="—", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=0, column=1, padx=8)
        ttk.Spinbox(length_row, from_=3, to=600, increment=5,
                    textvariable=self.var_scan_max).grid(row=0, column=2, sticky="ew")
        Tip(length_row, "От и до. Длина клипа подстроится под сам момент,\n"
                        "но не выйдет за эти границы")

        self.btn_scan = Btn(finder.body, "Найти моменты", self.scan_moments,
                            width=LEFT_W - 22, accent=True)
        self.btn_scan.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(9, 0))
        Tip(self.btn_scan, "Программа сама просмотрит запись и предложит интересные куски")
        self.search_only += [length_row, self.btn_scan]

        self.moments = Moments(finder.body, LEFT_W - 22, rows=5,
                               on_pick=self.on_moment_pick, on_check=self._update_stats)
        self.moments.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(9, 0))
        Tip(self.moments.body, "Щелчок по строке - показать момент в предпросмотре.\n"
                               "Щелчок по квадратику - отметить для пакетной нарезки")

        ai_row = tk.Frame(finder.body, background=PANEL)
        ai_row.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(9, 0))
        self.btn_brief = Btn(ai_row, "Скопировать для ИИ", self.copy_brief, width=168)
        self.btn_brief.grid(row=0, column=0)
        self.btn_brief.configure(state="disabled")
        Tip(self.btn_brief, "Копирует текст с расшифровками.\nВставьте его в чат нейросети")
        self.btn_answer = Btn(ai_row, "Вставить ответ", self.paste_answer, width=168)
        self.btn_answer.grid(row=0, column=1, padx=(8, 0))
        self.btn_answer.configure(state="disabled")
        Tip(self.btn_answer, "Вставьте сюда ответ нейросети - она сама отметит нужные моменты")
        self.search_only.append(ai_row)
        if not self.chrome:
            for widget in self.search_only:
                widget.grid_remove()

        file_row = tk.Frame(finder.body, background=PANEL)
        file_row.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        load = Btn(file_row, "Тайм-коды из файла", self.load_timecodes, width=168)
        load.grid(row=0, column=0)
        Tip(load, "Текстовый файл, по одному фрагменту на строку:\n"
                  "00:01:23 - 00:02:33 (название)\n\n"
                  "Название необязательно. Строки с # пропускаются")
        self.lbl_timecodes = tk.Label(file_row, text="", background=PANEL,
                                      foreground=OK, font=UI_FONT)
        self.lbl_timecodes.grid(row=0, column=1, sticky="w", padx=(10, 0))

        manual = Card(page_find, width=LEFT_W, pad=11)
        manual.grid(row=3, column=0, sticky="ew", pady=(9, 0))
        manual.body.columnconfigure(1, weight=1)
        section(manual.body, "◷", "вручную").grid(row=0, column=0, columnspan=3, sticky="ew")
        for line, (caption, var) in enumerate((("С", self.var_start), ("По", self.var_end))):
            tk.Label(manual.body, text=caption, background=PANEL, foreground=DIM,
                     font=UI_FONT, width=3, anchor="w").grid(row=line + 1, column=0,
                                                             sticky="w", pady=(8, 0))
            ttk.Entry(manual.body, textvariable=var).grid(row=line + 1, column=1,
                                                          sticky="ew", pady=(8, 0))
            grab = Btn(manual.body, "С ползунка", lambda box=var: self._grab_time(box),
                       width=112)
            grab.grid(row=line + 1, column=2, padx=(8, 0), pady=(8, 0))
            Tip(grab, "Подставить текущее место ползунка предпросмотра")
        show = Btn(manual.body, "Показать в превью", self.apply_range, width=LEFT_W - 22)
        show.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(9, 0))

        export = Card(page_find, width=LEFT_W, pad=11)
        export.grid(row=4, column=0, sticky="ew", pady=(9, 0))
        export.body.columnconfigure(0, weight=1)
        section(export.body, "⇩", "экспорт").grid(row=0, column=0, sticky="ew")
        out_row = tk.Frame(export.body, background=PANEL)
        out_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        out_row.columnconfigure(0, weight=1)
        ttk.Entry(out_row, textvariable=self.var_output).grid(row=0, column=0, sticky="ew")
        Btn(out_row, "Обзор", self.pick_output, width=84).grid(row=0, column=1, padx=(7, 0))

        buttons = tk.Frame(export.body, background=PANEL)
        buttons.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.btn_render = Btn(buttons, "✂  Нарезать", self.start_render, width=170, accent=True)
        self.btn_render.grid(row=0, column=0, sticky="ew")
        Tip(self.btn_render, "Собрать один ролик по заданному времени")
        self.btn_batch = Btn(buttons, "▤  Пакетно", self.start_batch, width=170)
        self.btn_batch.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        Tip(self.btn_batch, "Собрать сразу все отмеченные моменты в выбранную папку.\n"
                            "Имена придумаются автоматически")
        self.btn_result = Btn(buttons, "Посмотреть", self.load_result, width=170)
        self.btn_result.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.btn_result.configure(state="disabled")
        self.btn_open = Btn(buttons, "Открыть папку", self.open_result, width=170)
        self.btn_open.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        self.btn_open.configure(state="disabled")

        self.progress = ttk.Progressbar(export.body, mode="indeterminate",
                                        style="Thin.Horizontal.TProgressbar")
        self.progress.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        tk.Label(export.body, textvariable=self.var_hint, background=PANEL,
                 foreground=ACCENT2, font=UI_FONT, wraplength=LEFT_W - 26,
                 justify="left").grid(row=4, column=0, sticky="w", pady=(8, 0))

        webcam = Card(page_frame, width=LEFT_W, pad=11)
        webcam.grid(row=0, column=0, sticky="ew")
        section(webcam.body, "☺", "вебкамера стримера").grid(row=0, column=0, columnspan=5,
                                                             sticky="ew", pady=(0, 8))
        self.btn_face = Btn(webcam.body, "Найти лицо", self.detect_face, width=132, accent=True)
        self.btn_face.grid(row=1, column=0, columnspan=2, sticky="w")
        Tip(self.btn_face, "Программа сама найдёт стримера и подгонит рамку")
        tk.Label(webcam.body, text="крупность", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=1, column=2, sticky="e", padx=(10, 0))
        scale = ttk.Scale(webcam.body, from_=0.20, to=0.75, orient="horizontal", length=140,
                          variable=self.var_fill, command=self.on_fill_change)
        scale.grid(row=1, column=3, columnspan=2, sticky="ew", padx=(6, 0))
        Tip(scale, "Насколько крупно лицо в верхнем блоке.\nПравее - ближе, но мягче картинка")

        tk.Label(webcam.body, text="Рамку можно тянуть мышью на вкладке «Исходник и рамки»",
                 background=PANEL, foreground=DIM, font=UI_FONT).grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(9, 0))
        for index, label in enumerate(("X", "Y", "Ширина", "Высота")):
            tk.Label(webcam.body, text=label, background=PANEL, foreground=DIM,
                     font=MICRO_FONT).grid(row=3, column=index, sticky="w", padx=1, pady=(6, 0))
            spin = ttk.Spinbox(webcam.body, from_=0, to=9999, increment=2, width=7,
                               textvariable=self.var_cam[index],
                               command=lambda position=index: self.on_cam_change(position))
            spin.grid(row=4, column=index, padx=(0, 4), sticky="ew")
            spin.bind("<Return>", lambda _event, position=index: self.on_cam_change(position))
            spin.bind("<FocusOut>", lambda _event, position=index: self.on_cam_change(position))
        lock = ttk.Checkbutton(webcam.body, text="Держать пропорции блока",
                               variable=self.var_lock, command=self.on_lock_change)
        lock.grid(row=5, column=0, columnspan=5, sticky="w", pady=(8, 0))
        Tip(lock, "Включено - высота считается от ширины, картинка не искажается.\n"
                  "Выключено - размеры свободные")

        game = Card(page_frame, width=LEFT_W, pad=11)
        game.grid(row=1, column=0, sticky="ew", pady=(9, 0))
        section(game.body, "▦", "игровая область").grid(row=0, column=0, columnspan=4,
                                                        sticky="ew", pady=(0, 8))
        auto = Btn(game.body, "По центру", self.auto_game_box, width=132)
        auto.grid(row=1, column=0, columnspan=2, sticky="w")
        Tip(auto, "Вернуть область в середину кадра, во всю его высоту")
        tk.Label(game.body, text="выше/ниже", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=1, column=2, sticky="e", padx=(10, 0))
        game_spin = ttk.Spinbox(game.body, from_=0.0, to=1.0, increment=0.05, width=6,
                                format="%.2f", textvariable=self.var_gamey,
                                command=self.auto_game_box)
        game_spin.grid(row=1, column=3, sticky="ew", padx=(6, 0))
        Tip(game_spin, "Сдвиг для кнопки «По центру»: 0 - верх кадра, 1 - низ")
        for index, label in enumerate(("X", "Y", "Ширина", "Высота")):
            tk.Label(game.body, text=label, background=PANEL, foreground=DIM,
                     font=MICRO_FONT).grid(row=2, column=index, sticky="w", padx=1, pady=(9, 0))
            spin = ttk.Spinbox(game.body, from_=0, to=9999, increment=2, width=7,
                               textvariable=self.var_game[index],
                               command=lambda position=index: self.on_game_change(position))
            spin.grid(row=3, column=index, padx=(0, 4), sticky="ew")
            spin.bind("<Return>", lambda _event, position=index: self.on_game_change(position))
            spin.bind("<FocusOut>", lambda _event, position=index: self.on_game_change(position))
        game_lock = ttk.Checkbutton(game.body, text="Держать пропорции блока",
                                    variable=self.var_game_lock,
                                    command=self.on_game_lock_change)
        game_lock.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        Tip(game_lock, "Включено - высота считается от ширины, картинка не искажается")

        layout = Card(page_frame, width=LEFT_W, pad=11)
        layout.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        section(layout.body, "▤", "раскладка").grid(row=0, column=0, columnspan=4,
                                                    sticky="ew", pady=(0, 8))
        tk.Label(layout.body, text="Вид", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=1, column=0, sticky="w")
        kind = ttk.Combobox(layout.body, textvariable=self.var_layout, state="readonly",
                            values=LAYOUT_NAMES)
        kind.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0))
        kind.bind("<<ComboboxSelected>>", lambda _event: self.refresh_preview())
        Tip(kind, "Сама решит - смотрит, где вебка: в углу - два блока,\n"
                  "во весь экран - крупный план и субтитры вниз кадра,\n"
                  "вебки нет совсем - только экран.\n"
                  "Вебка и экран - два блока друг над другом.\n"
                  "Только вебка - крупный план, бока обрезаются.\n"
                  "Только экран - экран целиком, сверху и снизу размытые полосы.\n"
                  "Предпросмотр для «Сама решит» показывает два блока")
        tk.Label(layout.body, text="Высота вебки", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=2, column=0, sticky="w", pady=(9, 0))
        top_spin = ttk.Spinbox(layout.body, from_=200, to=1600, increment=20, width=7,
                               textvariable=self.var_top, command=self.on_geometry_change)
        top_spin.grid(row=2, column=1, padx=(8, 0), pady=(9, 0))
        Tip(top_spin, "Сколько пикселей из 1920 занимает верхний блок с вебкой")
        blur = ttk.Checkbutton(layout.body, text="Размытый фон", variable=self.var_blur,
                               command=self.refresh_preview)
        blur.grid(row=2, column=2, columnspan=2, sticky="w", padx=(14, 0), pady=(9, 0))
        tk.Label(layout.body, text="Мышью правим", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=3, column=0, sticky="w", pady=(9, 0))
        aim = tk.Frame(layout.body, background=PANEL)
        aim.grid(row=3, column=1, columnspan=3, sticky="w", pady=(9, 0), padx=(8, 0))
        ttk.Radiobutton(aim, text="вебку", variable=self.var_target, value="cam",
                        command=self.refresh_preview).grid(row=0, column=0)
        ttk.Radiobutton(aim, text="геймплей", variable=self.var_target, value="game",
                        command=self.refresh_preview).grid(row=0, column=1, padx=(10, 0))

        titles = Card(page_text, width=LEFT_W, pad=11)
        titles.grid(row=0, column=0, sticky="ew")
        titles.body.columnconfigure(0, weight=1)
        section(titles.body, "T", "название сверху").grid(row=0, column=0, columnspan=2,
                                                          sticky="ew", pady=(0, 8))
        title_entry = ttk.Entry(titles.body, textvariable=self.var_title)
        title_entry.grid(row=1, column=0, sticky="ew")
        title_entry.bind("<KeyRelease>", lambda _event: self.refresh_preview())
        Tip(title_entry, "Подпись у верхнего края кадра.\nНа стыке теперь субтитры.\n"
                         "Оставьте пустым, если не нужна")
        ttk.Spinbox(titles.body, from_=24, to=140, increment=4, width=5,
                    textvariable=self.var_title_size,
                    command=self.refresh_preview).grid(row=1, column=1, padx=(8, 0))

        subs = Card(page_text, width=LEFT_W, pad=11)
        subs.grid(row=1, column=0, sticky="ew", pady=(9, 0))
        subs.body.columnconfigure(1, weight=1)
        section(subs.body, "≡", "субтитры").grid(row=0, column=0, columnspan=4,
                                                 sticky="ew", pady=(0, 8))
        subs_on = ttk.Checkbutton(subs.body, text="Делать субтитры", variable=self.var_subs)
        subs_on.grid(row=1, column=0, columnspan=2, sticky="w")
        lbl_lang = tk.Label(subs.body, text="язык", background=PANEL, foreground=DIM,
                            font=UI_FONT)
        lbl_lang.grid(row=1, column=2, sticky="e", padx=(10, 0))
        ent_lang = ttk.Entry(subs.body, textvariable=self.var_lang, width=6)
        ent_lang.grid(row=1, column=3, padx=(6, 0))
        lbl_model = tk.Label(subs.body, text="Качество распознавания", background=PANEL,
                             foreground=DIM, font=UI_FONT)
        lbl_model.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        model = ttk.Combobox(subs.body, textvariable=self.var_model, width=11, state="readonly",
                             values=_asr.MODELS)
        model.grid(row=2, column=2, columnspan=2, sticky="ew", pady=(8, 0))
        Tip(model, "Ниже в списке - точнее, но дольше и требует больше памяти")
        if not self.chrome:
            for widget in (lbl_lang, ent_lang, lbl_model, model):
                widget.grid_remove()

        tk.Label(subs.body, text="Шрифт", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(subs.body, textvariable=self.var_font, state="readonly",
                     values=available_fonts()).grid(row=3, column=1, columnspan=3,
                                                    sticky="ew", pady=(8, 0))
        tk.Label(subs.body, text="Цвета", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=4, column=0, sticky="w", pady=(8, 0))
        colors = tk.Frame(subs.body, background=PANEL)
        colors.grid(row=4, column=1, columnspan=3, sticky="w", pady=(8, 0))
        self.swatches = {}
        for column, (key, var, caption) in enumerate((
            ("base", self.var_color_base, "текст"),
            ("high", self.var_color_high, "подсветка"),
            ("title", self.var_color_title, "название"),
        )):
            button = tk.Button(colors, text=caption, width=9, relief="flat",
                               borderwidth=0, highlightthickness=0,
                               activeforeground=TEXT, cursor="hand2", font=SMALL_FONT,
                               command=lambda name=key: self.pick_color(name))
            button.grid(row=0, column=column, padx=(0, 5))
            self.swatches[key] = (button, var)
        self._paint_swatches()

        position = tk.Frame(subs.body, background=PANEL)
        position.grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))
        tk.Label(position, text="Место: X", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(position, from_=0, to=ave.OUT_W, increment=10, width=6,
                    textvariable=self.var_subs_x,
                    command=self.refresh_preview).grid(row=0, column=1, padx=(6, 0))
        tk.Label(position, text="Y", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=0, column=2, padx=(10, 0))
        ttk.Spinbox(position, from_=0, to=ave.OUT_H, increment=10, width=6,
                    textvariable=self.var_subs_y,
                    command=self.refresh_preview).grid(row=0, column=3, padx=(6, 0))
        tk.Label(subs.body, text="Проще: щёлкните по кадру предпросмотра", background=PANEL,
                 foreground=DIM, font=UI_FONT).grid(row=6, column=0, columnspan=4,
                                                    sticky="w", pady=(6, 0))

        sound = Card(page_text, width=LEFT_W, pad=11)
        sound.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        section(sound.body, "♪", "обработка звука").grid(row=0, column=0, columnspan=3,
                                                         sticky="ew", pady=(0, 8))
        censor = ttk.Checkbutton(sound.body, text="Заглушать мат по списку censor.txt",
                                 variable=self.var_censor)
        censor.grid(row=1, column=0, columnspan=3, sticky="w")
        Tip(censor, "Слово закрывается символами в субтитрах,\nа звук глушится после его начала")
        pauses = ttk.Checkbutton(sound.body, text="Вырезать паузы", variable=self.var_cut_pauses,
                                 command=self._sync_pauses)
        pauses.grid(row=2, column=0, sticky="w", pady=(8, 0))
        Tip(pauses, "Убирает тишину между фразами и склеивает клип.\n"
                    "Ролик становится плотнее и короче")
        tk.Label(sound.body, text="от, сек", background=PANEL, foreground=DIM,
                 font=UI_FONT).grid(row=2, column=1, sticky="e", padx=(12, 0), pady=(8, 0))
        self.spin_pause = ttk.Spinbox(sound.body, from_=0.15, to=5.0, increment=0.05, width=6,
                                      format="%.2f", textvariable=self.var_pause_min)
        self.spin_pause.grid(row=2, column=2, sticky="w", padx=(6, 0), pady=(8, 0))
        Tip(self.spin_pause, "Паузы короче этой длины не трогаются.\n"
                             "Меньше значение - плотнее монтаж, но рванее")

        self.view_tabs = Tabs(right, ((None, "Результат 9:16"), (None, "Исходник и рамки")),
                              self.on_view, RIGHT_W, back=BG, kind="line")
        self.view_tabs.grid(row=0, column=0, sticky="w")

        stage = Card(right, width=RIGHT_W, pad=12, stretch=True)
        stage.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        stage.body.columnconfigure(0, weight=1)
        stage.body.rowconfigure(0, weight=1)
        board = tk.Frame(stage.body, background=PANEL, width=STAGE_W, height=STAGE_H)
        board.grid(row=0, column=0, sticky="nsew")
        board.grid_propagate(False)
        board.bind("<Configure>", self._stage_resized)

        holder_out = tk.Frame(board, width=PREVIEW_W, height=PREVIEW_H, background=STAGE)
        holder_out.place(relx=0.5, rely=0.5, anchor="center")
        holder_out.pack_propagate(False)
        self.canvas_out = tk.Label(holder_out, background=STAGE, borderwidth=0,
                                   cursor="crosshair")
        self.canvas_out.pack(fill="both", expand=True)
        self.canvas_out.bind("<Button-1>", self.on_preview_click)
        self.canvas_out.bind("<B1-Motion>", self.on_preview_click)
        Tip(self.canvas_out, "Щелчок ставит субтитры в это место")
        for relx, xoff, rely, yoff in ((0.0, 6, 0.0, 6), (1.0, -20, 0.0, 6),
                                       (0.0, 6, 1.0, -8), (1.0, -20, 1.0, -8)):
            tk.Frame(holder_out, background=ACCENT, width=14, height=2).place(
                relx=relx, x=xoff, rely=rely, y=yoff)
        for relx, xoff, rely, yoff in ((0.0, 6, 0.0, 6), (1.0, -8, 0.0, 6),
                                       (0.0, 6, 1.0, -20), (1.0, -8, 1.0, -20)):
            tk.Frame(holder_out, background=ACCENT, width=2, height=14).place(
                relx=relx, x=xoff, rely=rely, y=yoff)
        tk.Label(holder_out, textvariable=self.var_badge, background=STAGE,
                 foreground=BAD, font=MICRO_FONT).place(x=14, y=14)
        self.status_note = tk.Label(holder_out, textvariable=self.var_status,
                                    background=STAGE, foreground=DIM, font=MICRO_FONT,
                                    wraplength=PREVIEW_W - 28, justify="right", anchor="ne")
        self.status_note.place(relx=1.0, x=-14, y=34, anchor="ne")
        self.veil = Glyph(holder_out, "▶", self.on_veil, size=54, back=STAGE, accent=True,
                          font=("Segoe UI", 16))
        self.veil.place(relx=0.5, rely=0.5, anchor="center")

        holder_src = tk.Frame(board, width=SOURCE_W, height=SOURCE_H, background=STAGE)
        holder_src.pack_propagate(False)
        self.canvas_src = tk.Label(holder_src, background=STAGE, borderwidth=0, cursor="fleur")
        self.canvas_src.pack(fill="both", expand=True)
        self.canvas_src.bind("<Button-1>", self.on_src_press)
        self.canvas_src.bind("<B1-Motion>", self.on_src_drag)
        self.canvas_src.bind("<ButtonRelease-1>", self.on_src_release)
        self.canvas_src.bind("<MouseWheel>", self.on_src_wheel)
        Tip(self.canvas_src, "Протянуть - нарисовать рамку вебки.\n"
                             "Потянуть изнутри - подвинуть. Колесо - размер")
        self.holders = (holder_out, holder_src)
        self._show_holder(self.view)

        player = Card(right, width=RIGHT_W, pad=12, stretch=True)
        player.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        player.body.columnconfigure(0, weight=1)
        self.wave = Wave(player.body, RIGHT_W - 24, 54, on_seek=self.on_wave_seek,
                         on_select=self.on_wave_select)
        self.wave.grid(row=0, column=0, sticky="ew")
        Tip(self.wave, "Звук клипа. Красным - что будет вырезано.\n"
                       "Щелчок перематывает. Протянуть правой кнопкой\n"
                       "(или с Shift) - выделить момент")

        transport = tk.Frame(player.body, background=PANEL)
        transport.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        transport.columnconfigure(3, weight=1)
        Glyph(transport, "|◀", lambda: self.moments.step(-1), size=30).grid(row=0, column=0)
        self.btn_play = Glyph(transport, "▶", self.toggle_play, size=34, accent=True,
                              font=("Segoe UI", 11))
        self.btn_play.grid(row=0, column=1, padx=8)
        Glyph(transport, "▶|", lambda: self.moments.step(1), size=30).grid(row=0, column=2)
        tk.Label(transport, textvariable=self.var_clock, background=PANEL, foreground=DIM,
                 font=MONO_FONT).grid(row=0, column=3, sticky="e")

        pick = tk.Frame(player.body, background=PANEL)
        pick.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        pick.columnconfigure(2, weight=1)
        self.btn_pick_in = Btn(pick, "⟨ начало", lambda: self.pick_edge("in"), width=92)
        self.btn_pick_in.grid(row=0, column=0)
        Tip(self.btn_pick_in, "Начало момента по текущему месту ползунка (клавиша I)")
        self.btn_pick_out = Btn(pick, "конец ⟩", lambda: self.pick_edge("out"), width=92)
        self.btn_pick_out.grid(row=0, column=1, padx=(6, 0))
        Tip(self.btn_pick_out, "Конец момента по текущему месту ползунка (клавиша O)")
        tk.Label(pick, textvariable=self.var_pick, background=PANEL, foreground=ACCENT2,
                 font=MONO_FONT, anchor="w").grid(row=0, column=2, sticky="ew", padx=(10, 0))
        self.btn_pick_send = Btn(pick, "На монтаж", self.pick_to_montage, width=104, accent=True)
        self.btn_pick_send.grid(row=0, column=3, padx=(6, 0))
        Tip(self.btn_pick_send, "Подставить выделенное в поля времени\nи открыть вкладку «Нарезать»")
        self.btn_pick_add = Btn(pick, "＋ в список", self.pick_to_list, width=104)
        self.btn_pick_add.grid(row=0, column=4, padx=(6, 0))
        Tip(self.btn_pick_add, "Добавить выделенное в список моментов\nдля пакетной нарезки")

        chips(player.body, (("Space", "пауза"), ("I/O", "выделить момент"),
                            ("J/L", "фрагменты"), ("↑/↓", "навигация")),
              width=RIGHT_W - 24).grid(row=3, column=0, sticky="w", pady=(8, 0))

        cuts = tk.Frame(player.body, background=PANEL)
        cuts.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(cuts, text="Звук", variable=self.var_sound,
                        command=self.on_sound_toggle).grid(row=0, column=0, sticky="w")
        self.btn_mark = Btn(cuts, "Вырезать с", self.mark_cut, width=120)
        self.btn_mark.grid(row=0, column=1, padx=(12, 0))
        Tip(self.btn_mark, "Первое нажатие - начало выреза, второе - конец.\n"
                           "Отмеченное не попадёт в готовый ролик")
        Btn(cuts, "Отменить", self.undo_cut, width=96).grid(row=0, column=2, padx=(8, 0))
        Btn(cuts, "Сброс", self.clear_cuts, width=84, danger=True).grid(row=0, column=3,
                                                                        padx=(8, 0))

        data = Card(right, width=RIGHT_W, pad=12, stretch=True)
        data.grid(row=3, column=0, sticky="ew", pady=(9, 0))
        data.body.columnconfigure(0, weight=1)
        section(data.body, "⚙", "данные кадра").grid(row=0, column=0, sticky="ew")
        for line, var in enumerate((self.var_frame_cam, self.var_frame_game)):
            tk.Label(data.body, textvariable=var, background=PANEL, foreground=TEXT,
                     font=MONO_FONT, anchor="w").grid(row=line + 1, column=0, sticky="ew",
                                                      pady=(7 if not line else 3, 0))
        section(data.body, "▤", "журнал").grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.log = tk.Text(data.body, height=6, state="disabled", wrap="word",
                           background=PANEL2, foreground=DIM, relief="flat",
                           borderwidth=6, highlightthickness=0, font=MONO_FONT)
        self.log.grid(row=4, column=0, sticky="ew", pady=(7, 0))

        self._wheel_scroll(self.deck)
        self._wheel_scroll(self.deck_bar)
        self._show_page(0)

    def _wheel_scroll(self, widget):
        if not isinstance(widget, (ttk.Spinbox, ttk.Combobox, ttk.Scale)):
            if not widget.bind("<MouseWheel>"):
                widget.bind("<MouseWheel>", self._deck_wheel)
        for child in widget.winfo_children():
            self._wheel_scroll(child)

    def _deck_wheel(self, event):
        if not self.chrome:
            return None
        page = self.pages[self.steps.current]
        if page.winfo_reqheight() <= self.deck.winfo_height():
            return None
        self.deck.yview_scroll(-3 if event.delta > 0 else 3, "units")
        self._draw_deck_bar()
        return "break"

    def _draw_deck_bar(self):
        self.deck_bar.delete("all")
        if not self.chrome:
            return
        first, last = self.deck.yview()
        if last - first >= 0.999:
            return
        span = max(1, self.deck.winfo_height())
        self.deck_bar.create_line(2, 0, 2, span, fill=PANEL2, width=4)
        self.deck_bar.create_line(2, first * span + 2, 2, last * span - 2,
                                  fill=ACCENT, width=4)

    def _deck_resized(self, _event=None):
        width = max(160, self.deck.winfo_width())
        for slot in self.page_slots:
            self.deck.itemconfigure(slot, width=width)
        self._sync_deck_region()

    def _sync_deck_region(self):
        page = self.pages[self.page_index]
        page.update_idletasks()
        span = max(1, self.deck.winfo_height())
        width = max(160, self.deck.winfo_width())
        if not self.chrome:
            self.deck.configure(height=page.winfo_reqheight())
        self.deck.configure(scrollregion=(0, 0, width,
                                          max(span, page.winfo_reqheight())))
        self._draw_deck_bar()

    def _show_page(self, index):
        self.page_index = index
        for place, slot in enumerate(self.page_slots):
            self.deck.itemconfigure(slot, state="normal" if place == index else "hidden")
        self._sync_deck_region()
        self.deck.yview_moveto(0.0)
        self._draw_deck_bar()

    def _on_step_changed(self, index):
        self._show_page(index)

    def _show_holder(self, index):
        for place, holder in enumerate(self.holders):
            if place == index:
                holder.place(relx=0.5, rely=0.5, anchor="center")
                holder.lift()
            else:
                holder.place_forget()

    def _stage_resized(self, event):
        room_w = max(120, event.width - 24)
        room_h = max(120, event.height - 24)
        out_h = room_h
        out_w = int(round(out_h * ave.OUT_W / float(ave.OUT_H)))
        if out_w > room_w:
            out_w = room_w
            out_h = int(round(out_w * ave.OUT_H / float(ave.OUT_W)))
        src_w = room_w
        src_h = int(round(src_w * 9.0 / 16.0))
        if src_h > room_h:
            src_h = room_h
            src_w = int(round(src_h * 16.0 / 9.0))
        out_w = ave.even(out_w)
        out_h = ave.even(out_h)
        src_w = ave.even(src_w)
        src_h = ave.even(src_h)
        if (out_w, out_h, src_w, src_h) == (self.preview_w, self.preview_h,
                                            self.source_w, self.source_h):
            return
        self.preview_w, self.preview_h = out_w, out_h
        self.source_w, self.source_h = src_w, src_h
        self.holders[0].configure(width=out_w, height=out_h)
        self.holders[1].configure(width=src_w, height=src_h)
        self.status_note.configure(wraplength=max(60, out_w - 28))
        self.refresh_preview()

    def on_view(self, index):
        self.view = index
        self._show_holder(index)
        self.refresh_preview()

    def on_platform(self, index):
        limits = ((35, 75), (20, 58), (30, 90), None)
        bounds = limits[index]
        if bounds is None:
            self._log("Длину клипа задаёте сами")
            return
        self.var_scan_min.set(bounds[0])
        self.var_scan_max.set(bounds[1])
        self._log("Длина клипа под площадку: %d-%d с" % bounds)

    def on_veil(self):
        if self.source is None:
            self.pick_source()
        else:
            self.toggle_play()

    def _on_key(self, event):
        if not self.winfo_ismapped():
            return
        if isinstance(self.focus_get(), (tk.Entry, tk.Text, ttk.Entry, ttk.Spinbox,
                                         ttk.Combobox)):
            return
        key = event.keysym.lower()
        if key == "space":
            self.toggle_play()
        elif key == "i":
            self.pick_edge("in")
        elif key == "o":
            self.pick_edge("out")
        elif key == "j":
            self.moments.step(-1)
        elif key == "l":
            self.moments.step(1)
        elif key == "up":
            self.moments.step(-1)
        elif key == "down":
            self.moments.step(1)

    def _on_control_key(self, event):
        if not self.winfo_ismapped():
            return
        if event.keysym.lower() in ("v", "c", "x", "a"):
            return None
        virtual = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>",
                   65: "<<SelectAll>>"}.get(event.keycode)
        if virtual is None:
            return None
        widget = event.widget
        if not isinstance(widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Spinbox, ttk.Combobox)):
            return None
        try:
            widget.event_generate(virtual)
        except tk.TclError:
            return None
        return "break"

    def _on_cursor(self, value):
        if not hasattr(self, "wave"):
            return
        share = max(0.0, min(1.0, value))
        self.wave.set_position(share)
        bounds = self._play_bounds()
        if bounds is None:
            return
        first, last = bounds
        total = last
        if self.mode == "layout" and self.info is not None:
            total = self.info.get("duration") or last
        self.var_clock.set("%s / %s" % (format_time(first + share * (last - first))[:8],
                                        format_time(total)[:8]))

    def _update_stats(self):
        duration = self.info.get("duration") if self.info else 0
        self.var_stat[0].set(short_time(duration) if duration else "—")
        self.var_stat[1].set(str(len(self.candidates)) if self.candidates else "—")
        scores = [item["score"] for item in self.candidates if item.get("score")]
        self.var_stat[2].set("%.2f" % (sum(scores) / len(scores)) if scores else "—")
        picked = self.moments.selection()
        chosen = [self.candidates[i] for i in picked if i < len(self.candidates)]
        if not chosen:
            bounds = self._range()
            span = (bounds[1] - bounds[0]) if bounds else 0
        else:
            span = sum(item["end"] - item["start"] for item in chosen)
        self.var_stat[3].set(short_time(span) if span else "—")
        self.var_found.set("%d найдено" % len(self.candidates) if self.candidates else "")

    def _busy(self, active):
        if active:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _hint(self, text):
        self.var_hint.set(text)

    def _sync_pauses(self):
        self.spin_pause.configure(state="normal" if self.var_cut_pauses.get() else "disabled")

    def _sync_mode(self, *_args):
        deep = self.var_scan_mode.get().startswith("По расш")
        state = "normal" if deep else "disabled"
        self.spin_pool.configure(state=state)
        self.lbl_pool.configure(foreground=TEXT if deep else DIM)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _watchdog(self):
        now = time.perf_counter()
        lag = now - self.watch_mark - 0.25
        self.watch_mark = now
        if lag > 1.5:
            note_error("интерфейс не отвечал %.1f с" % lag)
            try:
                self._log("Интерфейс замер на %.1f с, запись в error.log" % lag)
            except Exception:
                pass
        self.after(250, self._watchdog)

    def _pump(self):
        try:
            self._drain()
        except Exception:
            note_error(traceback.format_exc())
            self._log("Сбой при обработке события, подробности в error.log")
        finally:
            self.after(120, self._pump)

    def _drain(self):
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(payload)
            elif kind == "status":
                self.var_status.set(payload)
            elif kind == "face":
                self.face_center = payload
                self._apply_face_frame()
                self.var_status.set("Лицо найдено, вебка подогнана под верхний блок")
                self._hint("Кадр настроен. Можно жать «Смонтировать ролик».")
            elif kind == "brief":
                path, items = payload
                self.brief_path = path
                self.answer_path = None
                self.btn_brief.configure(state="normal")
                self.btn_answer.configure(state="normal")
                self.messages.put(("moments", items))
                self._log("Бриф готов: %s" % os.path.basename(path))
                self._log("Жмите «Бриф для ИИ», вставьте в чат, ответ верните кнопкой «Ответ ИИ».")
            elif kind == "moments":
                self.candidates = payload
                self.moments.set_items(payload)
                self._update_stats()
                if payload and payload[0].get("text") is not None:
                    self._log("Клик по строке показывает расшифровку фрагмента.")
                if payload:
                    self.var_status.set("Найдено моментов: %d" % len(payload))
                    self._hint("Шаг 3. Щёлкните момент в списке, чтобы увидеть его в предпросмотре.")
                    self._log("Кандидаты по громкости: %d" % len(payload))
                else:
                    self.var_status.set("Заметных всплесков не нашлось")
            elif kind == "cam":
                for var, value in zip(self.var_cam, payload):
                    var.set(int(value))
                self.on_cam_change()
            elif kind == "audio_ready":
                token, target, start = payload
                if token == self.audio_token and self.playing and winsound is not None:
                    try:
                        winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC)
                        self.play_from = start
                        self.play_t0 = time.perf_counter()
                    except Exception:
                        pass
            elif kind == "error":
                messagebox.showerror("Ошибка", payload)
            elif kind == "render_done":
                self._busy(False)
                self.btn_render.configure(state="normal")
                self.btn_batch.configure(state="normal")
                if not payload:
                    self.var_status.set("Монтаж завершился с ошибкой")
                elif self.result_path:
                    self.btn_result.configure(state="normal")
                    self.btn_open.configure(state="normal")
                    self.var_status.set("Готово: %s" % os.path.basename(self.result_path))
                    self._hint("Готово. Нажмите «Посмотреть», чтобы проверить результат.")
                else:
                    self.btn_open.configure(state="normal")
                    self.var_status.set("Пакет готов, файлы в выбранной папке")
                    self._hint("Готово. Нажмите «Открыть папку».")
            elif kind == "opened":
                self._finish_open(payload)
            elif kind == "busy_done":
                self.btn_render.configure(state="normal")
                self.btn_batch.configure(state="normal")
                self.btn_scan.configure(state="normal")
                self._busy(False)

    def pick_source(self):
        path = filedialog.askopenfilename(
            title="Выберите видео",
            filetypes=[("Видео", "*.mp4 *.mkv *.mov *.ts *.flv *.avi *.webm"), ("Все файлы", "*.*")],
        )
        if path:
            self.load_source(path)

    def load_source(self, path):
        self.var_source.set(path)
        self.var_status.set("Открываю файл, для длинного стрима это может занять минуту...")
        self._busy(True)
        self._hint("Идёт открытие. Большие стримы индексируются один раз.")
        self.update_idletasks()

        def job():
            try:
                capture = ave.open_capture(path)
                if not capture.isOpened():
                    self.messages.put(("error", "Не удалось открыть файл: %s" % path))
                    self.messages.put(("opened", None))
                    return
                info = ave.probe_video(path)
                fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
                frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
                seen = frames / fps if fps > 0 else 0.0
                if info["duration"] > 0 and seen > 0 and seen < info["duration"] * 0.98:
                    self.messages.put(("log", "Внимание: просмотр видит %s из %s — файл "
                                              "прочитан не полностью. Скопируйте стрим на "
                                              "быстрый диск."
                                       % (format_time(seen)[:8],
                                          format_time(info["duration"])[:8])))
                self.messages.put(("opened", (path, capture, info)))
            except Exception as error:
                self.messages.put(("error", str(error)))
                self.messages.put(("opened", None))

        threading.Thread(target=job, daemon=True).start()

    def _finish_open(self, payload):
        self._busy(False)
        if payload is None:
            self.var_status.set("Файл не открыт")
            return
        path, capture, info = payload
        if self.capture is not None:
            self.capture.release()
        self.capture = capture
        self.source = path
        self.info = info
        self.mode = "layout"

        duration = info["duration"]
        self.var_start.set(format_time(0))
        self.var_end.set(format_time(duration if duration else 30.0))
        base = os.path.splitext(os.path.basename(path))[0]
        self.var_output.set(os.path.join(os.path.dirname(path), "%s_short.mp4" % base))

        self.game_box = None
        self.cam_box = None
        self.face_center = None
        self.cuts = []
        self.cut_pending = None
        self.candidates = []
        self.brief_path = None
        self.answer_path = None
        self.timecodes_path = None
        self.lbl_timecodes.configure(text="")
        self.btn_brief.configure(state="disabled")
        self.btn_answer.configure(state="disabled")
        self.moments.clear()
        self.var_target.set("cam")
        self._reset_geometry()
        self._log("Загружено: %s (%dx%d, %.1f fps, %s)" % (
            os.path.basename(path), info["width"], info["height"],
            info["fps"], format_time(duration)))
        self._hint("Шаг 2. Нажмите «Найти моменты» или задайте время вручную.")
        self.apply_range()

    def _reset_geometry(self):
        if self.info is None:
            return
        top = max(2, int(self.var_top.get()))
        bottom = ave.OUT_H - top
        if bottom < 2:
            return
        aspect = ave.OUT_W / float(top)
        if self.game_box is None:
            self.game_box = ave.gameplay_box(
                self.info["width"], self.info["height"],
                ave.OUT_W / float(bottom), min(max(self.var_gamey.get(), 0.0), 1.0),
            )
            for var, value in zip(self.var_game, self.game_box):
                var.set(int(value))
        if self.cam_box is None:
            self.cam_box = ave.fallback_cam_box(
                self.info["width"], self.info["height"], aspect, "top-right"
            )
            for var, value in zip(self.var_cam, self.cam_box):
                var.set(int(value))

    def _range(self):
        try:
            start = ave.parse_time(self.var_start.get())
            end = ave.parse_time(self.var_end.get())
        except Exception:
            return None
        if end <= start:
            return None
        return start, end

    def apply_range(self):
        if self.capture is None:
            return
        bounds = self._range()
        if bounds is None:
            messagebox.showwarning("Диапазон", "Проверьте время начала и конца.")
            return
        self.mode = "layout"
        self.scrub.set(0.0)
        self._show_pick()
        self.refresh_preview()

    def _current_time(self):
        position = float(self.scrub.get())
        if self.mode == "result" and self.result_capture is not None:
            frames = self.result_capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            fps = self.result_capture.get(cv2.CAP_PROP_FPS) or 30.0
            return position * (frames / fps if fps else 0.0)
        bounds = self._range()
        if bounds is None:
            return 0.0
        start, end = bounds
        return start + position * (end - start)

    def _grab_time(self, target):
        if self.capture is None or self.mode != "layout":
            return
        target.set(format_time(self._current_time()))

    def on_scrub(self, _value=None):
        if self.playing:
            return
        if self.scrub_job is not None:
            self.after_cancel(self.scrub_job)
        self.scrub_job = self.after(50, self._scrub_settled)

    def _scrub_settled(self):
        self.scrub_job = None
        self.refresh_preview()

    def on_cam_change(self, index=None):
        if self.info is None:
            return
        top = max(2, int(self.var_top.get()))
        aspect = ave.OUT_W / float(top)
        try:
            x, y, w, h = [max(0, int(var.get())) for var in self.var_cam]
        except (tk.TclError, ValueError):
            return
        if self.var_lock.get():
            if index == 3:
                w = h * aspect
            else:
                h = w / aspect
        self.cam_box = ave.clamp_box(x, y, w, h, self.info["width"], self.info["height"])
        for var, value in zip(self.var_cam, self.cam_box):
            var.set(int(value))
        self.refresh_preview()

    def _active_box(self):
        if self.var_target.get() == "game":
            return self.game_box
        return self.cam_box

    def _set_active(self, x, y, w, h, keep_size=False):
        if self.info is None:
            return
        if self.var_target.get() == "game":
            aspect = self._game_aspect()
            locked = self.var_game_lock.get()
            variables = self.var_game
        else:
            aspect = ave.OUT_W / float(max(2, int(self.var_top.get())))
            locked = self.var_lock.get()
            variables = self.var_cam
        if locked and not keep_size:
            h = w / aspect
        box = ave.clamp_box(x, y, w, h, self.info["width"], self.info["height"])
        if self.var_target.get() == "game":
            self.game_box = box
        else:
            self.cam_box = box
        for var, value in zip(variables, box):
            var.set(int(value))
        self.refresh_preview()

    def _to_frame(self, x, y):
        return (x * self.info["width"] / float(self.source_w),
                y * self.info["height"] / float(self.source_h))

    def on_src_press(self, event):
        box = self._active_box()
        if self.info is None or box is None:
            return
        fx, fy = self._to_frame(event.x, event.y)
        x, y, w, h = box
        if x <= fx <= x + w and y <= fy <= y + h:
            self.drag = ("move", fx - x, fy - y)
        else:
            self.drag = ("new", fx, fy)

    def on_src_drag(self, event):
        box = self._active_box()
        if self.drag is None or self.info is None or box is None:
            return
        fx, fy = self._to_frame(event.x, event.y)
        if self.drag[0] == "move":
            self._set_active(fx - self.drag[1], fy - self.drag[2],
                             box[2], box[3], keep_size=True)
        else:
            sx, sy = self.drag[1], self.drag[2]
            self._set_active(min(sx, fx), min(sy, fy), abs(fx - sx), abs(fy - sy))

    def on_src_release(self, _event=None):
        self.drag = None

    def on_src_wheel(self, event):
        box = self._active_box()
        if box is None:
            return "break"
        locked = (self.var_game_lock.get() if self.var_target.get() == "game"
                  else self.var_lock.get())
        step = 1.08 if event.delta > 0 else 1.0 / 1.08
        x, y, w, h = box
        new_w = w * step
        new_h = h * step
        self._set_active(x + (w - new_w) / 2.0, y + (h - new_h) / 2.0, new_w, new_h,
                         keep_size=not locked)
        return "break"

    def on_lock_change(self):
        if self.cam_box is not None:
            self.on_cam_change(2)

    def _game_aspect(self):
        bottom = max(2, ave.OUT_H - max(2, int(self.var_top.get())))
        return ave.OUT_W / float(bottom)

    def on_game_change(self, index=None):
        if self.info is None:
            return
        try:
            x, y, w, h = [max(0, int(var.get())) for var in self.var_game]
        except (tk.TclError, ValueError):
            return
        if self.var_game_lock.get():
            if index == 3:
                w = h * self._game_aspect()
            else:
                h = w / self._game_aspect()
        self.game_box = ave.clamp_box(x, y, w, h, self.info["width"], self.info["height"])
        for var, value in zip(self.var_game, self.game_box):
            var.set(int(value))
        self.refresh_preview()

    def on_game_lock_change(self):
        if self.game_box is not None:
            self.on_game_change(2)

    def auto_game_box(self):
        if self.info is None:
            return
        self.game_box = ave.gameplay_box(
            self.info["width"], self.info["height"], self._game_aspect(),
            min(max(self.var_gamey.get(), 0.0), 1.0),
        )
        for var, value in zip(self.var_game, self.game_box):
            var.set(int(value))
        self.refresh_preview()

    def on_geometry_change(self):
        self._reset_geometry()
        if self.game_box is not None and self.var_game_lock.get():
            self.on_game_change(2)
        if self.face_center is not None:
            self._apply_face_frame()
        elif self.cam_box is not None and self.var_lock.get():
            self.on_cam_change(2)
        else:
            self.refresh_preview()

    def _read_at(self, capture, seconds):
        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000.0)
        ok, frame = capture.read()
        if not ok:
            return None
        return frame

    def _paint_swatches(self):
        for button, var in self.swatches.values():
            value = var.get()
            red, green, blue = (int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            button.configure(background=value, activebackground=value,
                             foreground="#000000" if red + green + blue > 380 else "#FFFFFF")

    def pick_color(self, name):
        button, var = self.swatches[name]
        chosen = colorchooser.askcolor(color=var.get(), title="Цвет: %s" % button["text"])
        if chosen and chosen[1]:
            var.set(chosen[1].upper())
            self._paint_swatches()
            self.refresh_preview()

    def _font(self, size):
        family = self.var_font.get()
        key = (family, size)
        if key not in self._fonts:
            candidate = os.path.join(font_folder(), FONT_FILES.get(family, "arialbd.ttf"))
            self._fonts[key] = (ImageFont.truetype(candidate, size)
                                if os.path.isfile(candidate) else ImageFont.load_default())
        return self._fonts[key]

    def _rgb(self, value):
        return tuple(int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))

    def _draw_overlays(self, image):
        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil, "RGBA")

        title = self.var_title.get().strip()
        if title:
            size = max(16, int(self.var_title_size.get()))
            font = self._font(size)
            seam = max(2, int(self.var_top.get()))
            anchor = (ave.OUT_W // 2, seam)
            bounds = draw.textbbox(anchor, title, font=font, anchor="mm")
            pad = max(8, size // 5)
            draw.rectangle(
                [bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad],
                fill=(16, 16, 16, 205),
            )
            draw.text(anchor, title, font=font, fill=self._rgb(self.var_color_title.get()),
                      anchor="mm")

        sx = max(0, min(ave.OUT_W, int(self.var_subs_x.get())))
        sy = max(0, min(ave.OUT_H, int(self.var_subs_y.get())))
        draw.text((sx - 130, sy), "ЭТО", font=self._font(96),
                  fill=self._rgb(self.var_color_base.get()), anchor="mm",
                  stroke_width=8, stroke_fill=(0, 0, 0))
        draw.text((sx + 90, sy), "ТЕКСТ", font=self._font(107),
                  fill=self._rgb(self.var_color_high.get()), anchor="mm",
                  stroke_width=10, stroke_fill=(0, 0, 0))
        draw.line([sx - 46, sy, sx + 46, sy], fill=(255, 70, 70, 230), width=5)
        draw.line([sx, sy - 46, sx, sy + 46], fill=(255, 70, 70, 230), width=5)
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    def on_preview_click(self, event):
        if self.mode != "layout" or self.info is None:
            return
        self.subs_moved = True
        self.var_subs_x.set(int(round(event.x * ave.OUT_W / float(self.preview_w))))
        self.var_subs_y.set(int(round(event.y * ave.OUT_H / float(self.preview_h))))
        self.refresh_preview()

    def _compose_preview(self, frame, fast=False):
        smooth = cv2.INTER_LINEAR if fast else cv2.INTER_AREA
        kind = LAYOUT_CODES.get(self.var_layout.get(), "split")
        if kind not in ("split", "auto"):
            x, y, w, h = self.cam_box if kind == "cam" else self.game_box
            piece = frame[y:y + h, x:x + w]
            if piece.size == 0:
                return None
            if kind == "cam":
                ratio = max(self.preview_w / float(w), self.preview_h / float(h))
            else:
                ratio = min(self.preview_w / float(w), self.preview_h / float(h))
            fw = max(2, int(round(w * ratio)))
            fh = max(2, int(round(h * ratio)))
            fitted = cv2.resize(piece, (fw, fh), interpolation=smooth)
            if kind == "cam":
                ox = (fw - self.preview_w) // 2
                oy = (fh - self.preview_h) // 2
                return fitted[oy:oy + self.preview_h, ox:ox + self.preview_w]
            canvas = cv2.blur(cv2.resize(piece, (self.preview_w, self.preview_h),
                                         interpolation=cv2.INTER_NEAREST), (11, 11))
            ox = (self.preview_w - fw) // 2
            oy = (self.preview_h - fh) // 2
            canvas[oy:oy + fh, ox:ox + fw] = fitted
            return canvas
        top = max(2, int(self.var_top.get()))
        bottom = ave.OUT_H - top
        if bottom < 2:
            return None
        small_top = max(2, int(round(self.preview_h * top / float(ave.OUT_H))))
        small_bottom = self.preview_h - small_top
        if small_bottom < 2:
            return None
        cx, cy, cw, ch = self.cam_box
        gx, gy, gw, gh = self.game_box
        cam = frame[cy:cy + ch, cx:cx + cw]
        game = frame[gy:gy + gh, gx:gx + gw]
        if cam.size == 0 or game.size == 0:
            return None
        if self.var_blur.get():
            backdrop = cv2.resize(game, (self.preview_w, small_top), interpolation=cv2.INTER_NEAREST)
            backdrop = cv2.blur(backdrop, (11, 11))
            ratio = min(self.preview_w / float(cw), small_top / float(ch))
            fw = max(2, int(cw * ratio))
            fh = max(2, int(ch * ratio))
            fitted = cv2.resize(cam, (fw, fh), interpolation=smooth)
            oy = (small_top - fh) // 2
            ox = (self.preview_w - fw) // 2
            backdrop[oy:oy + fh, ox:ox + fw] = fitted
            head = backdrop
        else:
            head = cv2.resize(cam, (self.preview_w, small_top), interpolation=smooth)
        body = cv2.resize(game, (self.preview_w, small_bottom), interpolation=smooth)
        return np.vstack([head, body])

    def _overlay_preview(self, image):
        scale = self.preview_w / float(ave.OUT_W)
        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil, "RGBA")

        title = self.var_title.get().strip()
        if title:
            size = max(7, int(int(self.var_title_size.get()) * scale))
            font = self._font(size)
            seam = int(round(self.preview_h * max(2, int(self.var_top.get())) / float(ave.OUT_H)))
            anchor = (self.preview_w // 2, seam)
            bounds = draw.textbbox(anchor, title, font=font, anchor="mm")
            pad = max(2, size // 5)
            draw.rectangle([bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad],
                           fill=(16, 16, 16, 205))
            draw.text(anchor, title, font=font, fill=self._rgb(self.var_color_title.get()),
                      anchor="mm")

        sx = int(max(0, min(ave.OUT_W, self.var_subs_x.get())) * scale)
        sy = int(max(0, min(ave.OUT_H, self.var_subs_y.get())) * self.preview_h / float(ave.OUT_H))
        draw.text((sx - int(130 * scale), sy), "ЭТО", font=self._font(max(8, int(96 * scale))),
                  fill=self._rgb(self.var_color_base.get()), anchor="mm",
                  stroke_width=max(1, int(8 * scale)), stroke_fill=(0, 0, 0))
        draw.text((sx + int(90 * scale), sy), "ТЕКСТ", font=self._font(max(8, int(107 * scale))),
                  fill=self._rgb(self.var_color_high.get()), anchor="mm",
                  stroke_width=max(1, int(10 * scale)), stroke_fill=(0, 0, 0))
        draw.line([sx - 12, sy, sx + 12, sy], fill=(255, 70, 70, 230), width=2)
        draw.line([sx, sy - 12, sx, sy + 12], fill=(255, 70, 70, 230), width=2)
        return np.array(pil)

    def _frame_at(self, capture, seconds, sequential=False):
        if sequential:
            position = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            gap = seconds - position
            if 0.0 <= gap <= 2.0:
                steps = 0
                while steps < 150:
                    if capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 >= seconds - 0.001:
                        break
                    if not capture.grab():
                        return None
                    steps += 1
                ok, frame = capture.retrieve()
                return frame if ok else None
        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000.0)
        ok, frame = capture.read()
        return frame if ok else None

    def refresh_preview(self, sequential=False):
        if self.mode == "result":
            if self.result_capture is None:
                return
            frame = self._frame_at(self.result_capture, self._current_time(), sequential)
            if frame is None:
                return
            self.photo = to_photo(frame, self.preview_w, self.preview_h)
            self.canvas_out.configure(image=self.photo)
            if not self.playing:
                self.var_status.set("Результат  %s" % format_time(self._current_time()))
            return

        if self.capture is None or self.info is None:
            return
        self._reset_geometry()
        if self.cam_box is None or self.game_box is None:
            return
        moment = self._current_time()
        frame = self._frame_at(self.capture, moment, sequential)
        if frame is None:
            self.var_status.set("Кадр недоступен на %s" % format_time(moment))
            return

        self._paint_frame(frame, fast=self.playing)
        self._draw_cuts()
        if not self.playing:
            self.var_status.set("Кадр %s   вебка %s   геймплей %s" % (
                format_time(moment), self.cam_box, self.game_box))

    def _paint_frame(self, frame, fast=False):
        if self.view == 1:
            marked = frame.copy()
            cx, cy, cw, ch = self.cam_box
            gx, gy, gw, gh = self.game_box
            active = self.var_target.get()
            cv2.rectangle(marked, (gx, gy), (gx + gw, gy + gh), (80, 220, 80),
                          10 if active == "game" else 4)
            cv2.rectangle(marked, (cx, cy), (cx + cw, cy + ch), (60, 200, 255),
                          10 if active == "cam" else 4)
            self.photo = to_photo(marked, self.source_w, self.source_h, fast=fast)
            self.canvas_src.configure(image=self.photo)
        else:
            composed = self._compose_preview(frame, fast=fast)
            if composed is None:
                return
            self.photo = ImageTk.PhotoImage(Image.fromarray(self._overlay_preview(composed)))
            self.canvas_out.configure(image=self.photo)

    def _stop_audio(self):
        self.audio_token += 1
        self.audio_end = None
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass

    def _start_audio(self, start, span):
        if winsound is None or not self.var_sound.get() or self.source is None:
            return False
        if self.info is not None and not self.info.get("has_audio", True):
            return False
        span = max(1.0, min(float(span), AUDIO_CHUNK))
        self.audio_token += 1
        self.audio_end = start + span
        token = self.audio_token
        source = self.source
        target = os.path.join(tempfile.gettempdir(), "ave_preview_%d.wav" % (token % 2))

        def job():
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % start, "-i", source,
                     "-t", "%.3f" % span, "-vn", "-ac", "2", "-ar", "44100",
                     "-c:a", "pcm_s16le", target],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=ave.NO_WINDOW, timeout=90,
                )
            except Exception:
                return
            self.messages.put(("audio_ready", (token, target, start)))

        threading.Thread(target=job, daemon=True).start()
        return True

    def on_sound_toggle(self):
        if self.playing:
            self.toggle_play()
            self.toggle_play()

    def _play_bounds(self):
        if self.mode == "result" and self.result_capture is not None:
            frames = self.result_capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            fps = self.result_capture.get(cv2.CAP_PROP_FPS) or 30.0
            span = frames / fps if fps else 0.0
            return (0.0, span) if span > 0 else None
        bounds = self._range()
        return bounds if bounds and bounds[1] > bounds[0] else None

    def toggle_play(self):
        if self.capture is None:
            return
        self.playing = not self.playing
        self.btn_play.configure(text="||" if self.playing else "▶")
        if self.play_job is not None:
            self.after_cancel(self.play_job)
            self.play_job = None
        self._stop_audio()
        if not self.playing:
            self.var_status.set("Пауза  %s" % format_time(self._current_time()))
            return

        bounds = self._play_bounds()
        if bounds is None:
            self.playing = False
            self.btn_play.configure(text="▶")
            return
        first, last = bounds
        self.play_from = self._current_time()
        if self.play_from >= last - 0.1:
            self.play_from = first
        self.play_end = last
        if self.mode == "layout":
            self._start_audio(self.play_from, last - self.play_from)
        self.play_t0 = time.perf_counter()
        self._advance()

    def _advance(self):
        try:
            self._advance_step()
        except Exception:
            note_error(traceback.format_exc())
            self.playing = False
            self.btn_play.configure(text="▶")
            self.var_status.set("Воспроизведение остановлено, подробности в error.log")

    def _advance_step(self):
        if not self.playing:
            return
        bounds = self._play_bounds()
        if bounds is None:
            self.playing = False
            self.btn_play.configure(text="▶")
            return
        first, last = bounds
        target = self.play_from + (time.perf_counter() - self.play_t0)
        if target >= last:
            self._stop_audio()
            self.play_from = first
            self.play_t0 = time.perf_counter()
            if self.mode == "layout":
                self._start_audio(first, last - first)
            target = first
        if (self.mode == "layout" and self.audio_end is not None
                and target >= self.audio_end - 0.25):
            self._start_audio(target, last - target)
        span = max(0.001, last - first)
        self.scrub.set(min(1.0, max(0.0, (target - first) / span)))
        clock = time.perf_counter()
        self.refresh_preview(sequential=True)
        cost = int((time.perf_counter() - clock) * 1000)
        self.var_status.set("Идёт  %s" % format_time(target))
        self.play_job = self.after(max(15, cost), self._advance)

    def on_wave_select(self, first, last):
        bounds = self._play_bounds()
        if bounds is None:
            return
        start, end = bounds
        span = end - start
        self.pick_range = (start + first * span, start + last * span)
        self._show_pick()

    def pick_edge(self, side):
        bounds = self._play_bounds()
        if bounds is None:
            return
        moment = self._current_time()
        start, end = self.pick_range or (moment, moment)
        if side == "in":
            start = moment
            if end <= start:
                end = min(bounds[1], start + 1.0)
        else:
            end = moment
            if end <= start:
                start = max(bounds[0], end - 1.0)
        self.pick_range = (start, end)
        self._show_pick()

    def _show_pick(self):
        if self.pick_range is None:
            self.var_pick.set("момент не выделен")
            self.wave.set_picked(None)
            return
        start, end = self.pick_range
        self.var_pick.set("%s — %s   %.1f с"
                          % (format_time(start)[:8], format_time(end)[:8], end - start))
        bounds = self._play_bounds()
        if bounds is None:
            self.wave.set_picked(None)
            return
        first, last = bounds
        span = max(0.001, last - first)
        self.wave.set_picked((max(0.0, min(1.0, (start - first) / span)),
                              max(0.0, min(1.0, (end - first) / span))))

    def pick_to_montage(self):
        if self.pick_range is None:
            messagebox.showinfo("Момент", "Сначала выделите кусок на звуковой дорожке "
                                          "правой кнопкой или клавишами I и O.")
            return
        start, end = self.pick_range
        self.var_start.set(format_time(start))
        self.var_end.set(format_time(end))
        self.pick_range = None
        self.mode = "layout"
        self.scrub.set(0.0)
        self.apply_range()
        self._show_pick()
        self.steps.select(0)
        self._log("Момент на монтаж: %s — %s" % (format_time(start)[:8], format_time(end)[:8]))
        self._hint("Момент подставлен. Проверьте кадр и жмите «Нарезать».")

    def pick_to_list(self):
        if self.pick_range is None:
            messagebox.showinfo("Момент", "Сначала выделите кусок на звуковой дорожке "
                                          "правой кнопкой или клавишами I и O.")
            return
        start, end = self.pick_range
        title = "ручной %s" % format_time(start)[:8]
        kept = {self.candidates[i]["start"] for i in self.moments.selection()
                if i < len(self.candidates)}
        kept.add(start)
        self.candidates = sorted(
            list(self.candidates)
            + [{"start": start, "end": end, "score": 0.0, "text": title, "title": title}],
            key=lambda item: item["start"])
        self.moments.set_items(self.candidates)
        self.moments.check_only([i for i, item in enumerate(self.candidates)
                                 if item["start"] in kept])
        self._update_stats()
        self._log("В список добавлен момент %s — %s (%.1f с)"
                  % (format_time(start)[:8], format_time(end)[:8], end - start))
        self._hint("Момент в списке. Отметьте нужные галочками и жмите «Пакетно».")

    def _draw_cuts(self):
        if not hasattr(self, "wave"):
            return
        bounds = self._play_bounds()
        if bounds is None:
            marks, pending = (), None
        else:
            first, last = bounds
            span = max(0.001, last - first)
            marks = []
            for start, end in self.cuts:
                a = max(0.0, min(1.0, (max(first, start) - first) / span))
                b = max(0.0, min(1.0, (min(last, end) - first) / span))
                if b > a:
                    marks.append((a, b))
            marks = tuple(marks)
            pending = (None if self.cut_pending is None
                       else max(0.0, min(1.0, (self.cut_pending - first) / span)))
        if (marks, pending) == self.cuts_drawn:
            return
        self.cuts_drawn = (marks, pending)
        self.wave.cuts = list(marks)
        self.wave.pending = pending
        self.wave.redraw()

    def on_wave_seek(self, share):
        bounds = self._play_bounds()
        if bounds is None:
            return
        first, last = bounds
        share = max(0.0, min(1.0, share))
        self.scrub.set(share)
        if self.playing:
            self.play_from = first + share * (last - first)
            self.play_t0 = time.perf_counter()
            self._stop_audio()
            if self.mode == "layout":
                self._start_audio(self.play_from, last - self.play_from)
        self.refresh_preview()

    def mark_cut(self):
        bounds = self._play_bounds()
        if bounds is None:
            return
        moment = self._current_time()
        if self.cut_pending is None:
            self.cut_pending = moment
            self.btn_mark.configure(text="по этот миг")
            self.var_status.set("Начало выреза: %s" % format_time(moment)[:8])
        else:
            start, end = sorted((self.cut_pending, moment))
            self.cut_pending = None
            self.btn_mark.configure(text="Вырезать с")
            if end - start < 0.1:
                self.var_status.set("Слишком короткий вырез, отменён")
            else:
                self.cuts.append((start, end))
                self.cuts.sort()
                self._log("Вырез %s - %s" % (format_time(start)[:8], format_time(end)[:8]))
                self.var_status.set("Вырезов: %d, всего %.1f с"
                                    % (len(self.cuts), sum(b - a for a, b in self.cuts)))
        self._draw_cuts()

    def undo_cut(self):
        if self.cut_pending is not None:
            self.cut_pending = None
            self.btn_mark.configure(text="Вырезать с")
        elif self.cuts:
            self.cuts.pop()
        self._draw_cuts()
        self.var_status.set("Вырезов: %d" % len(self.cuts))

    def clear_cuts(self):
        self.cuts = []
        self.cut_pending = None
        self.btn_mark.configure(text="Вырезать с")
        self._draw_cuts()
        self.var_status.set("Вырезы сброшены")

    def on_fill_change(self, _value=None):
        if self.face_center is None:
            return
        self._apply_face_frame()

    def _apply_face_frame(self):
        if self.face_center is None or self.info is None:
            return
        top = max(2, int(self.var_top.get()))
        aspect = ave.OUT_W / float(top)
        cx, cy, face_h = self.face_center
        box_h = face_h / max(0.05, min(0.95, self.var_fill.get()))
        box_w = box_h * aspect
        self.cam_box = ave.fit_box(
            cx, cy + box_h * 0.08, box_w, box_h, aspect,
            self.info["width"], self.info["height"],
        )
        for var, value in zip(self.var_cam, self.cam_box):
            var.set(int(value))
        self.refresh_preview()

    def scan_moments(self):
        if self.source is None:
            messagebox.showwarning("Исходник", "Сначала выберите видео.")
            return
        self.btn_scan.configure(state="disabled")
        self.btn_render.configure(state="disabled")
        self.var_status.set("Анализирую звук...")
        self._busy(True)
        path = self.source
        count = max(1, int(self.var_scan_count.get()))
        low = max(3.0, float(self.var_scan_min.get()))
        high = max(low, float(self.var_scan_max.get()))
        deep = self.var_scan_mode.get().startswith("По расш")
        pool = max(count, int(self.var_scan_pool.get())) if deep else count
        language = self.var_lang.get().strip() or None
        whisper = self.var_model.get()

        def job():
            shown = [-1]

            def report(done, total):
                if not total:
                    return
                share = int(100.0 * done / total)
                if share != shown[0]:
                    shown[0] = share
                    self.messages.put(("status", "Анализирую звук: %d%%" % min(100, share)))

            heard = tempfile.mkdtemp(prefix="vscan_")
            spare = os.path.join(heard, "audio.raw")
            try:
                found = ave.scan_highlights(path, pool, low, high, progress=report,
                                            samples=spare if deep else None)
                if not deep or not found:
                    self.messages.put(("moments", found))
                    return
                self.messages.put(("status", "Расшифровываю кандидатов..."))
                model = ave.build_model(whisper, "auto", "default")
                items = ave.scan_transcript(
                    path, found, model, language,
                    lambda done, total: self.messages.put(
                        ("status", "Расшифровка %d из %d" % (done, total))),
                    ready=spare,
                )
                brief = os.path.splitext(path)[0] + "_brief.txt"
                ave.write_brief(brief, path, items, count)
                self.messages.put(("brief", (brief, items)))
            except Exception as error:
                self.messages.put(("error", str(error)))
            finally:
                shutil.rmtree(heard, ignore_errors=True)
                self.messages.put(("busy_done", None))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def load_timecodes(self):
        path = filedialog.askopenfilename(
            title="Файл с тайм-кодами",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        self.adopt_timecodes(path)

    def adopt_timecodes(self, path):
        try:
            items = ave.parse_timecodes(path)
        except Exception as error:
            messagebox.showerror("Тайм-коды", str(error))
            return
        if not items:
            messagebox.showwarning(
                "Тайм-коды",
                "Не нашёл ни одной строки с диапазоном.\n\n"
                "Ожидаемый вид строки:\n00:01:23 - 00:02:33 (название)",
            )
            return

        self.timecodes_path = path
        self.answer_path = None
        self.candidates = [
            {"start": item["start"], "end": item["end"], "score": 0.0,
             "text": item["title"], "title": item["title"]}
            for item in items
        ]
        self.moments.set_items(self.candidates)
        self.moments.check_all()
        self.lbl_timecodes.configure(text="загружено: %d" % len(items))
        self._log("Тайм-коды из файла: %d фрагментов" % len(items))
        self.var_status.set("Тайм-коды загружены: %d" % len(items))
        self._hint("Проверьте кадр на вкладке «Кадр», затем «Пакетно».")
        self._update_stats()

    def copy_brief(self):
        if not self.brief_path or not os.path.isfile(self.brief_path):
            messagebox.showwarning("Бриф", "Сначала выполните поиск в режиме расшифровки.")
            return
        with open(self.brief_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log("Бриф скопирован в буфер (%d символов)" % len(text))
        self._log("Файл: %s" % self.brief_path)
        messagebox.showinfo(
            "Бриф готов",
            "Текст скопирован в буфер обмена.\n\nВставьте его в чат нейросети, "
            "затем скопируйте ответ и нажмите «Ответ ИИ».",
        )

    def paste_answer(self):
        if not self.brief_path:
            messagebox.showwarning("Ответ", "Сначала выполните поиск в режиме расшифровки.")
            return
        window = tk.Toplevel(self)
        window.title("Ответ нейросети")
        window.configure(background=BG)
        window.transient(self)
        ttk.Label(window, text="Вставьте ответ модели целиком:").pack(anchor="w", padx=8, pady=(8, 4))
        box = tk.Text(window, width=70, height=16, wrap="word",
                      background=PANEL, foreground=TEXT, insertbackground=TEXT,
                      relief="flat", borderwidth=6, highlightthickness=0)
        box.pack(padx=8)
        box.focus_set()

        def apply():
            answer = box.get("1.0", "end").strip()
            window.destroy()
            if not answer:
                return
            try:
                data = ave.load_brief(self.brief_path)
                picked = ave.parse_selection(answer, data)
            except Exception as error:
                messagebox.showerror("Ответ", str(error))
                return
            if not picked:
                messagebox.showwarning("Ответ", "Не нашёл ни одной строки с номером вида #12.")
                return
            self.answer_path = os.path.splitext(self.brief_path)[0] + "_answer.txt"
            with open(self.answer_path, "w", encoding="utf-8") as handle:
                handle.write(answer)
            self.moments.check_only([item["index"] - 1 for item in picked])
            self._update_stats()
            self._log("Модель выбрала %d фрагментов:" % len(picked))
            for item in picked:
                self._log("  #%02d %s — %s" % (item["index"],
                                               format_time(item["start"])[:8], item["title"]))
            self.var_status.set("Выбрано моделью: %d. Жмите «Все выбранные»." % len(picked))

        ttk.Button(window, text="Применить", command=apply).pack(pady=8)

    def on_moment_pick(self, index):
        if index is None or index >= len(self.candidates):
            return
        item = self.candidates[index]
        if item.get("text"):
            self._log("#%02d: %s" % (index + 1, item["text"][:220]))
        self.var_start.set(format_time(item["start"]))
        self.var_end.set(format_time(item["end"]))
        self.mode = "layout"
        self.scrub.set(0.0)
        self.refresh_preview()

    def detect_face(self):
        if self.source is None:
            messagebox.showwarning("Исходник", "Сначала выберите видео.")
            return
        bounds = self._range()
        if bounds is None:
            messagebox.showwarning("Диапазон", "Проверьте время начала и конца.")
            return
        self.btn_render.configure(state="disabled")
        self.var_status.set("Ищу лицо...")
        self._busy(True)
        top = max(2, int(self.var_top.get()))
        aspect = ave.OUT_W / float(top)
        start, end = bounds
        fill = self.var_fill.get()

        def job():
            try:
                box, _size = ave.detect_face_region(
                    self.source, 24, fill, aspect, None, start, end
                )
                if box is None:
                    self.messages.put(("log", "Лицо не найдено, оставляю текущую рамку."))
                    self.messages.put(("status", "Лицо не найдено"))
                else:
                    bx, by, bw, bh = box
                    center = (bx + bw / 2.0, by + bh / 2.0 - bh * 0.08, bh * fill)
                    self.messages.put(("log", "Лицо найдено, рамка %s" % (box,)))
                    self.messages.put(("face", center))
            except Exception as error:
                self.messages.put(("error", str(error)))
            finally:
                self.messages.put(("busy_done", None))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def pick_output(self):
        path = filedialog.asksaveasfilename(
            title="Куда сохранить", defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4")],
        )
        if path:
            self.var_output.set(path)

    def _engine(self):
        if getattr(sys, "frozen", False):
            return [sys.executable, "--engine"]
        return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "auto_vertical_editor.py")]

    def _settings(self, ranges, destination):
        cuts = ()
        if self.cuts and ranges and len(ranges) == 1:
            base = ranges[0][0]
            cuts = tuple((max(0.0, a - base), max(0.0, b - base))
                         for a, b in self.cuts if b > base)
        return Render(
            source=self.source,
            out=destination,
            ranges=tuple(ranges or ()),
            timecodes=self.timecodes_path if ranges is None else None,
            select=self.answer_path if ranges is None and not self.timecodes_path else None,
            brief=self.brief_path if ranges is None and not self.timecodes_path else None,
            layout=LAYOUT_CODES.get(self.var_layout.get(), "split"),
            top_height=self.var_top.get(),
            cam_box=[var.get() for var in self.var_cam],
            cam_fit=self.var_lock.get(),
            game_box=[var.get() for var in self.var_game],
            game_fit=self.var_game_lock.get(),
            model=self.var_model.get(),
            language=self.var_lang.get(),
            subs=self.var_subs.get(),
            subs_x=self.var_subs_x.get(),
            subs_y=self.var_subs_y.get(),
            subs_moved=self.subs_moved,
            font=self.var_font.get(),
            color_base=self.var_color_base.get(),
            color_highlight=self.var_color_high.get(),
            title=self.var_title.get(),
            title_size=self.var_title_size.get(),
            title_color=self.var_color_title.get(),
            censor=self.var_censor.get(),
            blur_bg=self.var_blur.get(),
            drop=cuts,
            cut_pauses=self.var_cut_pauses.get(),
            pause_min=self.var_pause_min.get(),
        )

    def _build_command(self, ranges, destination):
        return self._settings(ranges, destination).command(self._engine())

    def start_render(self):
        if self.source is None:
            messagebox.showwarning("Исходник", "Сначала выберите видео.")
            return
        bounds = self._range()
        if bounds is None:
            messagebox.showwarning("Диапазон", "Проверьте время начала и конца.")
            return
        output = self.var_output.get().strip()
        if not output:
            messagebox.showwarning("Результат", "Укажите имя выходного файла.")
            return
        self._launch(self._build_command([bounds], output), output)

    def start_batch(self):
        if self.source is None:
            messagebox.showwarning("Исходник", "Сначала выберите видео.")
            return
        picked = [self.candidates[index] for index in self.moments.selection()
                  if index < len(self.candidates)]
        if not picked:
            picked = list(self.candidates)
        if not picked:
            messagebox.showwarning("Моменты", "Сначала найдите моменты и выберите нужные.")
            return
        folder = filedialog.askdirectory(title="Папка для готовых клипов")
        if not folder:
            return
        self.batch_folder = folder
        if self.timecodes_path and os.path.isfile(self.timecodes_path):
            self._log("Пакет по файлу тайм-кодов -> %s" % folder)
            self._launch(self._build_command(None, folder), None)
            return
        if self.answer_path and os.path.isfile(self.answer_path):
            self._log("Пакет по выбору нейросети -> %s" % folder)
            self._launch(self._build_command(None, folder), None)
            return
        ranges = [(item["start"], item["end"]) for item in picked]
        self._log("Пакет: %d клипов -> %s" % (len(ranges), folder))
        self._log("Имена будут назначены автоматически по таймкоду и речи.")
        self._launch(self._build_command(ranges, folder), None)

    def _launch(self, command, output):
        self.result_path = output
        if output is not None:
            self.batch_folder = None
        self.btn_render.configure(state="disabled")
        self.btn_batch.configure(state="disabled")
        self.btn_result.configure(state="disabled")
        self.btn_open.configure(state="disabled")
        self.var_status.set("Монтирую...")
        self._busy(True)
        self._log("> " + " ".join(command[1:]))

        def job():
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            for raw in process.stdout:
                self.messages.put(("log", raw.decode("utf-8", "ignore")))
            process.wait()
            self.messages.put(("render_done", process.returncode == 0))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def load_result(self):
        if not self.result_path or not os.path.isfile(self.result_path):
            messagebox.showwarning("Результат", "Файл ещё не собран.")
            return
        capture = ave.open_capture(self.result_path)
        if not capture.isOpened():
            messagebox.showerror("Ошибка", "Не удалось открыть результат.")
            return
        if self.result_capture is not None:
            self.result_capture.release()
        self.result_capture = capture
        self.mode = "result"
        self.view_tabs.select(0)
        self.scrub.set(0.0)
        self.refresh_preview()

    def open_result(self):
        if self.result_path and os.path.isfile(self.result_path):
            os.startfile(self.result_path)
        elif self.batch_folder and os.path.isdir(self.batch_folder):
            os.startfile(self.batch_folder)

    def shutdown(self):
        self.playing = False
        self.mode = "idle"
        self._stop_audio()
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        if self.result_capture is not None:
            self.result_capture.release()
            self.result_capture = None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Astra — вертикальные ролики 9:16")
        self.resizable(True, True)
        self.minsize(MIN_W, MIN_H)
        apply_theme(self)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.editor = Editor(self)
        self.editor.grid(row=0, column=0, sticky="nsew")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._settle)

    def load_source(self, path):
        self.editor.load_source(path)

    def _work_area(self):
        width = self.winfo_screenwidth()
        height = self.winfo_screenheight()
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                          ctypes.byref(rect), 0):
                return rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            pass
        return width, max(400, height - 48)

    def _frame_extra(self):
        try:
            import ctypes
            from ctypes import wintypes

            handle = int(self.wm_frame(), 16)
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect)):
                extra_w = rect.right - rect.left - self.winfo_width()
                extra_h = rect.bottom - rect.top - self.winfo_height()
                if 0 <= extra_w < 200 and 0 <= extra_h < 200:
                    return extra_w, extra_h
        except Exception:
            pass
        return 0, 40

    def _settle(self):
        self.update_idletasks()
        area_w, area_h = self._work_area()
        extra_w, extra_h = self._frame_extra()
        room_w = max(MIN_W, area_w - extra_w)
        room_h = max(MIN_H, area_h - extra_h)
        width = max(MIN_W, min(self.winfo_reqwidth(), room_w))
        height = max(MIN_H, min(self.winfo_reqheight(), room_h))
        x = max(0, (area_w - width - extra_w) // 2)
        y = max(0, (area_h - height - extra_h) // 2)
        self.geometry("%dx%d+%d+%d" % (width, height, x, y))
        self.after(150, self._clamp)

    def _clamp(self):
        area_w, area_h = self._work_area()
        extra_w, extra_h = self._frame_extra()
        width = min(self.winfo_width(), max(MIN_W, area_w - extra_w))
        height = min(self.winfo_height(), max(MIN_H, area_h - extra_h))
        if (width, height) == (self.winfo_width(), self.winfo_height()):
            return
        self.geometry("%dx%d" % (width, height))
        self.centre_window()

    def centre_window(self):
        self.update_idletasks()
        area_w, area_h = self._work_area()
        extra_w, extra_h = self._frame_extra()
        x = max(0, (area_w - self.winfo_width() - extra_w) // 2)
        y = max(0, (area_h - self.winfo_height() - extra_h) // 2)
        self.geometry("+%d+%d" % (x, y))

    def report_callback_exception(self, kind, value, trace):
        note_error("".join(traceback.format_exception(kind, value, trace)))
        try:
            self.editor._log("Сбой: %s" % str(value).strip()[:180])
            self.editor.var_status.set("Сбой, подробности в error.log рядом с программой")
        except Exception:
            pass

    def _on_close(self):
        self.editor.shutdown()
        self.destroy()


def main():
    app = App()
    app.title("Astra — вертикальные ролики 9:16")
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.load_source(sys.argv[1])
    app.mainloop()


if __name__ == "__main__":
    main()
