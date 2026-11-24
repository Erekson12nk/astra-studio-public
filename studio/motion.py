"""Анимации окна: плавные цвета, бегущая полоска под вкладкой, въезд страницы.

Всё на after(): каждый шаг — короткий вызов в главном потоке, окно не замирает.
Новая анимация того же места отменяет прежнюю. ASTRA_MOTION=0 выключает всё.
"""
from __future__ import annotations

import os
import time
import tkinter as tk

FRAME = 15                         # мс между кадрами, около 60 в секунду


def enabled() -> bool:
    return os.environ.get("ASTRA_MOTION", "1") != "0"


def ease(t: float) -> float:
    """Быстрый старт и мягкая остановка (cubic out)."""
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


def blend(first, second, t) -> str:
    a = [int(first[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(second[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


class _Run:
    def __init__(self, widget, slot, ms, step, done):
        self.widget, self.slot, self.ms, self.step, self.done = widget, slot, ms, step, done
        self.started = time.monotonic()
        self.job = None
        self.alive = True

    def tick(self):
        if not self.alive:
            return
        share = (time.monotonic() - self.started) * 1000.0 / max(1.0, self.ms)
        try:
            self.step(ease(share))
            if share >= 1.0:
                self.finish()
                return
            self.job = self.widget.after(FRAME, self.tick)
        except tk.TclError:
            self.alive = False

    def finish(self):
        if not self.alive:
            return
        self.alive = False
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
        try:
            self.step(1.0)
            if self.done:
                self.done()
        except tk.TclError:
            pass


def run(widget, slot, ms, step, done=None):
    """Анимация: step(t) получает долю от 0 до 1 уже со сглаживанием."""
    runs = widget.__dict__.setdefault("_astra_runs", {})
    old = runs.pop(slot, None)
    if old is not None:
        old.alive = False
        if old.job is not None:
            try:
                widget.after_cancel(old.job)
            except tk.TclError:
                pass
    if not enabled() or ms <= 0:
        step(1.0)
        if done:
            done()
        return None
    job = runs[slot] = _Run(widget, slot, ms, step, done)
    job.tick()
    return job


def fade(widget, option, start, end, ms=140, slot=None):
    """Плавная смена цвета: фон кнопки при наведении, цвет текста вкладки."""
    run(widget, slot or option, ms, lambda t: widget.configure(**{option: blend(start, end, t)}))


def reveal(page, shift=26, ms=190):
    """Страница въезжает чуть сбоку, а не появляется скачком.

    Страница уже уложена pack: её место запоминается, на время анимации она
    ставится place с этим же размером, в конце возвращается в pack как была.
    Соседняя страница, ещё не доехавшая, убирается: на месте одна страница.
    """
    if not enabled():
        return
    parent = page.master
    moving = parent.__dict__.setdefault("_astra_moving", {})
    for other, (info, _x) in list(moving.items()):
        if other is not page:
            moving.pop(other, None)
            try:
                other.place_forget()
            except tk.TclError:
                pass
    if page in moving:
        info, _x = moving.pop(page)
        page.place_forget()
        page.pack(**info)
    try:
        info = page.pack_info()
    except tk.TclError:
        return
    info.pop("in", None)
    try:
        page.update_idletasks()
        x, y = page.winfo_x(), page.winfo_y()
        width, height = page.winfo_width(), page.winfo_height()
    except tk.TclError:
        return
    if width <= 1 or height <= 1:
        return
    page.pack_forget()
    page.place(x=x + shift, y=y, width=width, height=height)
    moving[page] = (info, x)

    def step(t):
        if page in moving:
            page.place_configure(x=int(round(x + shift * (1.0 - t))))

    def done():
        if moving.pop(page, None) is None:
            return
        page.place_forget()
        page.pack(**info)

    run(page, "reveal", ms, step, done)


class Indicator:
    """Полоска под активной вкладкой, переезжает к новой вкладке."""

    def __init__(self, bar, color, height=2):
        self.bar = bar
        self.line = tk.Frame(bar, background=color, height=height, width=1)
        self.height = height
        self.x = None
        self.width = None
        self.target = None

    def go(self, tab):
        self.target = tab
        try:
            self.bar.after_idle(self._move)
        except tk.TclError:
            pass

    def _mapped(self, _event=None):
        if self.target is not None:
            self.bar.after_idle(self._move)

    def _move(self):
        tab = self.target
        try:
            if tab is None:
                return
            if not tab.winfo_ismapped():         # окно или страница скрыты — встать, когда покажутся
                if not getattr(tab, "_astra_wait_map", False):
                    tab._astra_wait_map = True
                    tab.bind("<Map>", self._mapped, add="+")
                return
            x, width = tab.winfo_x(), tab.winfo_width()
            y = tab.winfo_y() + tab.winfo_height() - self.height
        except tk.TclError:
            return
        if width <= 1:
            self.bar.after(30, self._move)
            return
        if tab is not self.target:
            return
        start_x = self.x if self.x is not None else x
        start_w = self.width if self.width is not None else width
        self.line.lift()

        def step(t):
            self.x = int(round(start_x + (x - start_x) * t))
            self.width = int(round(start_w + (width - start_w) * t))
            self.line.place(x=self.x, y=y, width=self.width, height=self.height)

        run(self.line, "move", 240 if self.x is not None else 0, step)
