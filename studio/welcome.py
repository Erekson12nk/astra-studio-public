from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk

from core import profiles as _pro
from core import ui as _ui

from . import theme

TITLE = "Знакомство"
BREAK = chr(10)


def needed():
    if not _pro.names():
        return True, ""
    return False, ""


class Welcome(tk.Toplevel):
    def __init__(self, master, reason=""):
        super().__init__(master)
        self.title("Astra Studio — знакомство")
        self.configure(background=_ui.BG)
        self.resizable(False, False)
        self.done = False
        theme.apply(self)
        _ui.dark_frame(self)
        self.v_name = tk.StringVar()
        self.v_photo = tk.StringVar()
        self._build(reason)
        self.protocol("WM_DELETE_WINDOW", self._leave)
        # Ни transient, ни grab_set: главное окно в это время скрыто, перехватывать
        # нечего, а с ними Windows не даёт свернуть окно и не показывает его на панели
        # задач. Студия и так ждёт ответа через wait_window.

    def _card(self, title):
        holder = ttk.Frame(self)
        holder.pack(fill="x", padx=18)
        return theme.card(holder, title)

    def _build(self, reason):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=18, pady=(16, 12))
        ttk.Label(head, text="Astra Studio", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="давайте познакомимся — это один раз").pack(
            side="left", padx=(12, 0))

        card = self._card("ПРОФИЛЬ")
        line = ttk.Frame(card, style="Panel.TFrame")
        line.pack(fill="x", pady=(0, 6))
        ttk.Label(line, text="Имя стримера", style="PanelDim.TLabel",
                  width=18).pack(side="left")
        ttk.Entry(line, textvariable=self.v_name).pack(side="left", fill="x",
                                                       expand=True)
        line2 = ttk.Frame(card, style="Panel.TFrame")
        line2.pack(fill="x")
        ttk.Label(line2, text="Снимок с вебки", style="PanelDim.TLabel",
                  width=18).pack(side="left")
        ttk.Entry(line2, textvariable=self.v_photo).pack(side="left", fill="x",
                                                         expand=True)
        theme.Button(line2, "Выбрать", self._pick_photo).pack(side="left",
                                                              padx=(8, 0))
        ttk.Label(card, style="PanelDim.TLabel", wraplength=560, justify="left",
                  text="По снимку программа находит вебку в кадре стрима — "
                       "без него она будет угадывать и часто ошибаться. "
                       "Подойдёт обычный кадр, где видно вас и комнату.").pack(
            anchor="w", pady=(8, 0))

        self.lbl = ttk.Label(self, text="", style="Dim.TLabel", wraplength=580,
                             justify="left")
        self.lbl.pack(anchor="w", padx=18)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=18, pady=(12, 16))
        theme.Button(bottom, "Начать работу", self._accept,
                     primary=True).pack(side="left")
        theme.Button(bottom, "Выйти", self._leave).pack(side="right")

    def _pick_photo(self):
        chosen = filedialog.askopenfilename(
            parent=self, title="Снимок с вебки",
            filetypes=[("Картинки", "*.png *.jpg *.jpeg *.bmp *.webp"),
                       ("Все файлы", "*.*")])
        if chosen:
            self.v_photo.set(chosen)

    def _accept(self):
        name = self.v_name.get().strip()
        if not name:
            self.lbl.configure(text="Впишите имя стримера.")
            return
        photo = self.v_photo.get().strip()
        known = _pro.names()
        if name not in known:
            if not photo:
                self.lbl.configure(text="Выберите снимок с вебки — "
                                        "без него профиль не создать.")
                return
            if not os.path.isfile(photo):
                self.lbl.configure(text="Снимка нет на диске: %s" % photo)
                return

        try:
            if name in known:
                _pro.set_active(name)
            else:
                _pro.set_active(_pro.create(name, photo)["name"])
        except Exception as error:
            self.lbl.configure(text="Профиль не создался: %s" % error)
            return

        self.done = True
        self.grab_release()
        self.destroy()

    def _leave(self):
        self.done = False
        self.grab_release()
        self.destroy()


def greet(master) -> bool:
    must, reason = needed()
    if not must:
        return True
    window = Welcome(master, reason)
    master.wait_window(window)
    return bool(window.done)
