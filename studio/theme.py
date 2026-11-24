from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core.ui import (ACCENT, ACCENT2, BG, BIG_FONT, DIM, LINE, MONO_FONT, OK,
                     ON_ACCENT, PANEL, PANEL2, SMALL_FONT, SOFT, TEXT, UI_FONT, WARN,
                     apply)


class Button(tk.Label):
    """Плоская кнопка: иконка по тексту, плавная подсветка при наведении."""

    def __init__(self, master, text, command, primary=False, width=None, icon=None, **kw):
        from . import icons as _icons

        self.enabled = True
        self.primary = primary
        self.command = command
        colors = (ACCENT, ACCENT2) if primary else (PANEL2, SOFT)
        self.rest, self.hover = colors
        self.ink = ON_ACCENT if primary else TEXT
        self.icon_name = _icons.name_for(text) if icon is None else icon
        if self.icon_name:
            text = _icons.bare_text(text)
        options = dict(text=text, background=self.rest, foreground=self.ink,
                       font=SMALL_FONT if primary else UI_FONT,
                       padx=12, pady=6, cursor="hand2")
        if width:
            options["width"] = width
        options.update(kw)
        super().__init__(master, **options)
        self.images = {}
        if self.icon_name:
            for state, color in (("on", self.ink), ("off", DIM)):
                self.images[state] = _icons.photo(self, self.icon_name, color)
            if self.images.get("on") is not None:
                self.configure(image=self.images["on"], compound="left" if text else "center",
                               padx=10 if text else 8)
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        if self.enabled and self.command:
            from . import motion

            motion.fade(self, "background", self.rest if self.primary else ACCENT,
                        self.hover, ms=220, slot="bg")
            self.command()

    def _enter(self, _event=None):
        if self.enabled:
            from . import motion

            motion.fade(self, "background", self.cget("background"), self.hover, slot="bg")

    def _leave(self, _event=None):
        if self.enabled:
            from . import motion

            motion.fade(self, "background", self.cget("background"), self.rest, ms=200,
                        slot="bg")

    def set_enabled(self, value):
        from . import motion

        self.enabled = bool(value)
        motion.run(self, "bg", 0, lambda t: None)        # недоигранная подсветка не вернётся
        self.configure(background=self.rest if value else PANEL,
                       foreground=self.ink if value else DIM,
                       cursor="hand2" if value else "arrow")
        image = self.images.get("on" if value else "off")
        if image is not None:
            self.configure(image=image)


class Tab(tk.Label):
    """Вкладка: иконка, плавный цвет и общая полоска, что переезжает к активной."""

    def __init__(self, master, text, command):
        from . import icons as _icons
        from . import motion

        self.command = command
        self.active = False
        big = master.winfo_parent() == str(master.winfo_toplevel()) or \
            master.master is master.winfo_toplevel()
        super().__init__(master, text=text, background=BG, foreground=DIM,
                         font=BIG_FONT, padx=22 if big else 18, pady=10, cursor="hand2")
        name = _icons.name_for(text)
        self.images = {}
        if name:
            size = 18 if big else 16
            for state, color in (("off", DIM), ("hover", TEXT), ("on", ACCENT2)):
                self.images[state] = _icons.photo(self, name, color, size)
            if self.images.get("off") is not None:
                self.configure(image=self.images["off"], compound="left")
        if not hasattr(master, "_astra_indicator"):
            master._astra_indicator = motion.Indicator(master, ACCENT, 3 if big else 2)
        self.indicator = master._astra_indicator
        self.bind("<Button-1>", lambda _e: self.command())
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))

    def _hover(self, inside):
        from . import motion

        if self.active:
            return
        start = self.cget("foreground")
        motion.fade(self, "foreground", start, TEXT if inside else DIM, ms=140, slot="fg")
        image = self.images.get("hover" if inside else "off")
        if image is not None:
            self.configure(image=image)

    def set_active(self, value):
        from . import motion

        was = self.active
        self.active = bool(value)
        motion.run(self, "fg", 0, lambda t: None)
        self.configure(foreground=TEXT if value else DIM)
        if value and not was:
            motion.fade(self, "background", BG, PANEL, ms=220, slot="bg")
        elif not value:
            motion.run(self, "bg", 0, lambda t: None)
            self.configure(background=BG)
        image = self.images.get("on" if value else "off")
        if image is not None:
            self.configure(image=image)
        if value:
            self.indicator.go(self)


def card(master, title, gap=10):
    """Карточка раздела: панель в тонкой рамке, заголовок со срезанной меткой акцента."""
    sharp = True
    outer = tk.Frame(master, background=LINE if sharp else PANEL)
    outer.pack(fill="x", pady=(0, gap))
    shell = tk.Frame(outer, background=PANEL)
    shell.pack(fill="both", expand=True, padx=1 if sharp else 0, pady=1 if sharp else 0)
    inner = ttk.Frame(shell, style="Panel.TFrame")
    inner.pack(fill="x", padx=14, pady=12)
    if title:
        head = tk.Frame(inner, background=PANEL)
        head.pack(anchor="w", fill="x", pady=(0, 8))
        mark = tk.Canvas(head, width=10, height=12, background=PANEL, highlightthickness=0)
        if sharp:
            mark.create_polygon(0, 0, 7, 0, 7, 9, 4, 12, 0, 12, fill=ACCENT, outline="")
        else:
            mark.create_oval(1, 3, 7, 9, fill=ACCENT, outline="")
        mark.pack(side="left", padx=(0, 6))
        tk.Label(head, text=title, background=PANEL, foreground=DIM,
                 font=SMALL_FONT).pack(side="left")
    return inner

class Folded(tk.Frame):
    def __init__(self, master, text, opened=False):
        super().__init__(master, background=PANEL)
        self.opened = bool(opened)
        self.title = text
        self.head = tk.Label(self, background=PANEL, foreground=DIM,
                             font=SMALL_FONT, cursor="hand2", anchor="w", pady=0)
        self.head.pack(fill="x")
        self.body = tk.Frame(self, background=PANEL)
        for widget in (self.head,):
            widget.bind("<Button-1>", lambda _e: self.toggle())
            widget.bind("<Enter>", lambda _e: self.head.configure(foreground=TEXT))
            widget.bind("<Leave>", lambda _e: self.head.configure(foreground=DIM))
        self._show()

    def _show(self):
        self.head.configure(text="%s  %s" % ("▾" if self.opened else "▸", self.title))
        if self.opened:
            self.body.pack(fill="x", pady=(6, 0))
        else:
            self.body.forget()

    def toggle(self):
        self.opened = not self.opened
        self._show()

class Scrolled(tk.Frame):
    STEP = 20

    def __init__(self, master):
        super().__init__(master, background=BG)
        self.canvas = tk.Canvas(self, background=BG, highlightthickness=0,
                                borderwidth=0, yscrollincrement=self.STEP)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar = ttk.Scrollbar(self, orient="vertical",
                                 command=self.canvas.yview)
        self.shown = False
        self.canvas.configure(yscrollcommand=self._moved)
        self.body = tk.Frame(self.canvas, background=BG)
        self.slot = self.canvas.create_window(0, 0, anchor="nw",
                                              window=self.body)
        self.canvas.bind("<Configure>", self._fit)
        self.body.bind("<Configure>", self._fit)
        self.bind("<Enter>", self._take_wheel)
        self.bind("<Leave>", self._drop_wheel)

    def _fit(self, _event=None):
        width = max(200, self.canvas.winfo_width())
        self.canvas.itemconfigure(self.slot, width=width)
        self.canvas.configure(scrollregion=(0, 0, width,
                                            self.body.winfo_reqheight()))

    def fits(self):
        return self.body.winfo_reqheight() <= self.canvas.winfo_height()

    def _moved(self, first, last):
        self.bar.set(first, last)
        need = float(first) > 0.0 or float(last) < 1.0
        if need and not self.shown:
            self.bar.pack(side="right", fill="y")
        elif not need and self.shown:
            self.bar.forget()
            self.canvas.yview_moveto(0.0)
        self.shown = need

    def _inner_scroller(self, event):
        widget = getattr(event, "widget", None)
        if not isinstance(widget, tk.Misc):
            return None
        while widget is not None and widget is not self.canvas:
            if isinstance(widget, (ttk.Treeview, tk.Text, tk.Listbox)):
                try:
                    first, last = widget.yview()
                except tk.TclError:
                    return None
                if float(first) > 0.0 or float(last) < 1.0:
                    return widget
                return None
            widget = widget.master
        return None

    def _take_wheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _drop_wheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, event):
        inner = self._inner_scroller(event)
        if inner is not None:
            inner.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        if not self.shown:
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
