from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core import profiles as _pro
from core import ui as _ui

from . import theme

KEEP_CHOICES = ((3, "3 дня"), (7, "7 дней"), (14, "14 дней"), (30, "30 дней"),
                (0, "не удалять"))
BREAK = chr(10)


class Maker(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Новый профиль")
        self.configure(background=_ui.BG)
        self.resizable(False, False)
        self.made = ""
        theme.apply(self)
        _ui.dark_frame(self)
        self.v_name = tk.StringVar()
        self.v_photo = tk.StringVar()
        self.v_keep = tk.StringVar(value=KEEP_CHOICES[0][1])
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._leave)
        self.transient(master)
        self.grab_set()
        self.bind("<Control-KeyPress>", self._maybe_paste)

    def _card(self, title):
        outer = ttk.Frame(self, style="Panel.TFrame")
        outer.pack(fill="x", padx=18, pady=(0, 10))
        inner = ttk.Frame(outer, style="Panel.TFrame")
        inner.pack(fill="x", padx=14, pady=12)
        if title:
            ttk.Label(inner, text=title, style="PanelDim.TLabel").pack(
                anchor="w", pady=(0, 8))
        return inner

    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=18, pady=(16, 12))
        ttk.Label(head, text="Новый профиль", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="ещё один стример в этой же программе").pack(
            side="left", padx=(12, 0))

        card = self._card("КТО")
        line = ttk.Frame(card, style="Panel.TFrame")
        line.pack(fill="x", pady=(0, 6))
        ttk.Label(line, text="Имя стримера", style="PanelDim.TLabel",
                  width=18).pack(side="left")
        self.entry = ttk.Entry(line, textvariable=self.v_name)
        self.entry.pack(side="left", fill="x", expand=True)
        line2 = ttk.Frame(card, style="Panel.TFrame")
        line2.pack(fill="x")
        ttk.Label(line2, text="Снимок с вебки", style="PanelDim.TLabel",
                  width=18).pack(side="left")
        ttk.Entry(line2, textvariable=self.v_photo).pack(side="left", fill="x",
                                                         expand=True)
        theme.Button(line2, "Выбрать", self._pick_photo).pack(side="left",
                                                              padx=(8, 0))
        ttk.Label(card, style="PanelDim.TLabel", wraplength=520, justify="left",
                  text="По снимку программа находит вебку в кадре стрима. "
                       "Подойдёт обычный кадр, где видно стримера и комнату."
                  ).pack(anchor="w", pady=(8, 0))

        card2 = self._card("ХРАНЕНИЕ")
        line3 = ttk.Frame(card2, style="Panel.TFrame")
        line3.pack(fill="x")
        ttk.Label(line3, text="Хранить клипы", style="PanelDim.TLabel",
                  width=18).pack(side="left")
        ttk.Combobox(line3, textvariable=self.v_keep, state="readonly", width=13,
                     values=[title for _, title in KEEP_CHOICES]).pack(side="left")
        ttk.Label(card2, style="PanelDim.TLabel", wraplength=520, justify="left",
                  text="Клипы, оценки и сравнения этого профиля лягут в его "
                       "собственную папку рядом с программой. Профили друг "
                       "другу не мешают.").pack(anchor="w", pady=(8, 0))

        self.lbl = ttk.Label(self, text="", style="Dim.TLabel", wraplength=540,
                             justify="left")
        self.lbl.pack(anchor="w", padx=18)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=18, pady=(12, 16))
        theme.Button(bottom, "Создать", self._accept, primary=True).pack(side="left")
        theme.Button(bottom, "Отмена", self._leave).pack(side="right")
        self.entry.focus_set()

    def _pick_photo(self):
        chosen = filedialog.askopenfilename(
            parent=self, title="Снимок с вебки",
            filetypes=[("Картинки", "*.png *.jpg *.jpeg *.bmp *.webp"),
                       ("Все файлы", "*.*")])
        if chosen:
            self.v_photo.set(chosen)

    def _maybe_paste(self, event):
        if event.keycode != 86 and str(event.keysym).lower() not in ("v", "cyrillic_em"):
            return None
        if self.focus_get() in (self.entry,):
            return None
        try:
            text = str(self.clipboard_get()).strip()
        except tk.TclError:
            return "break"
        if text:
            self.v_photo.set(text) if _looks_like_file(text) else self.v_name.set(text)
        return "break"

    def _keep(self):
        for days, title in KEEP_CHOICES:
            if title == self.v_keep.get():
                return days
        return _pro.DEFAULT_KEEP_DAYS

    def _accept(self):
        name = self.v_name.get().strip()
        if not name:
            self.lbl.configure(text="Впишите имя стримера.")
            return
        try:
            profile = _pro.create(name, self.v_photo.get().strip(),
                                  keep_days=self._keep())
        except FileExistsError:
            self.lbl.configure(text="Профиль с таким именем уже есть.")
            return
        except (ValueError, FileNotFoundError) as error:
            self.lbl.configure(text=str(error))
            return
        except OSError as error:
            messagebox.showerror("Профиль", str(error), parent=self)
            return
        _pro.set_active(profile["name"])
        self.made = profile["name"]
        self.destroy()

    def _leave(self):
        self.made = ""
        self.destroy()


def _looks_like_file(text: str) -> bool:
    lowered = text.lower()
    return any(lowered.endswith(ext) for ext in _pro.PHOTO_EXT)


def ask(master) -> str:
    window = Maker(master)
    master.wait_window(window)
    return window.made
