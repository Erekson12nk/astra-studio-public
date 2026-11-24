"""Первое окно при запуске: фоновый режим в трее или работа со стримом."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core import fetch as _fetch
from core import launch as _launch
from core import live as _live
from core import ui as _ui

from . import theme
from . import tray as _tray

WIDE = 640


class Launcher(tk.Toplevel):
    def __init__(self, master, cfg):
        super().__init__(master)
        self.title("Astra Studio")
        self.configure(background=_ui.BG)
        self.resizable(False, False)
        theme.apply(self)
        _ui.dark_frame(self)
        self.cfg = cfg
        self.choice = None
        self.v_channel = tk.StringVar(value=cfg.get("live_channel", _live.DEFAULT_CHANNEL))
        self.v_link = tk.StringVar()
        self.v_analyze = tk.BooleanVar(value=bool(cfg.get("launch_analyze", True)))
        self.v_auto = tk.BooleanVar(value=bool(cfg.get("learn_auto", True)))
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._leave)
        self.bind("<Escape>", lambda _e: self._leave())
        # Ни transient, ни grab_set: главное окно в это время скрыто, перехватывать
        # нечего, а с ними Windows не даёт свернуть окно и не показывает его на панели
        # задач. Студия и так ждёт ответа через wait_window.
        self.after(10, self._center)

    def _center(self):
        self.update_idletasks()
        width, height = self.winfo_width(), self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 3)
        self.geometry("+%d+%d" % (x, y))

    def _card(self, title, note=""):
        holder = ttk.Frame(self)
        holder.pack(fill="x", padx=18)
        inner = theme.card(holder, title)
        if note:
            ttk.Label(inner, text=note, style="PanelDim.TLabel", wraplength=WIDE - 40,
                      justify="left").pack(anchor="w", pady=(4, 8))
        return inner

    def _row(self, card, gap=0):
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=(gap, 0))
        return row

    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=18, pady=(16, 12))
        from . import icons as _icons

        mark = _icons.photo(self, "logo", "", 28)
        if mark is not None:
            tk.Label(head, image=mark, background=_ui.BG).pack(side="left", padx=(0, 10))
        ttk.Label(head, text="Astra Studio", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="как будем работать?").pack(side="left", padx=(12, 0))

        passive = self._card(
            "ФОН — СЛЕДИТЬ ЗА ЭФИРОМ",
            "Студия уходит в трей возле часов. Как только эфир начнётся, придёт "
            "уведомление, и Студия сама начнёт писать звук — до того, как твич заглушит "
            "музыку в записи. Если эфир уже идёт, начало докачается.")
        row = self._row(passive)
        ttk.Label(row, text="Канал твича", style="PanelDim.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.v_channel, width=28).pack(side="left", padx=(8, 8))
        self.btn_passive = theme.Button(row, "Свернуть в трей", self._passive, primary=True)
        self.btn_passive.pack(side="right")
        if not _tray.available():
            ttk.Label(passive, style="PanelDim.TLabel", wraplength=WIDE - 40, justify="left",
                      text="Для трея нужен pystray: py -m pip install pystray").pack(
                anchor="w", pady=(8, 0))

        active = self._card(
            "РАБОТА СО СТРИМОМ",
            "Откройте запись стрима или вставьте ссылку — Студия скачает её, найдёт "
            "моменты и скажет, сколько примерно выйдет тиктоков и больших роликов.")
        row = self._row(active)
        theme.Button(row, "Открыть файл стрима", self._open_file,
                     primary=True).pack(side="left")
        theme.Button(row, "Просто открыть Студию", self._just_open).pack(side="right")
        row = self._row(active, 8)
        ttk.Label(row, text="Ссылка", style="PanelDim.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.v_link).pack(side="left", fill="x", expand=True,
                                                      padx=(8, 8))
        theme.Button(row, "Вставить", self._paste).pack(side="left")
        theme.Button(row, "Скачать", self._download).pack(side="left", padx=(8, 0))
        row = self._row(active, 8)
        ttk.Checkbutton(row, text="сразу найти моменты и посчитать, сколько выйдет",
                        variable=self.v_analyze).pack(side="left")

        stats = self._card(
            "УЧЁБА НА ПРОСМОТРАХ",
            "Студия раз в сутки сама смотрит просмотры ваших выгрузок и учится, какие "
            "моменты заходят, а какие нет, — поиск моментов потом берёт это в расчёт.")
        row = self._row(stats)
        ttk.Checkbutton(row, text="учиться автоматически на новых выгрузках",
                        variable=self.v_auto).pack(side="left")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=18, pady=(4, 16))
        theme.Button(bottom, "Выйти", self._leave).pack(side="right")

    # ---------------------------------------------------------------- выбор

    def _keep(self):
        self.cfg["live_channel"] = self.v_channel.get().strip()
        self.cfg["launch_analyze"] = bool(self.v_analyze.get())
        self.cfg["learn_from"] = ["own"]
        self.cfg["learn_auto"] = bool(self.v_auto.get())

    def _finish(self, **choice):
        self._keep()
        self.choice = dict({"analyze": bool(self.v_analyze.get())}, **choice)
        self.cfg["launch_mode"] = self.choice["mode"]
        self.grab_release()
        self.destroy()

    def _passive(self):
        if not _tray.available():
            messagebox.showerror("Трей", "Не установлен pystray:\n\n  py -m pip install pystray",
                                 parent=self)
            return
        if not _live.channel_of(self.v_channel.get()):
            messagebox.showerror("Канал", "Укажите канал твича: имя или ссылку.", parent=self)
            return
        if not _live.streamlink():
            messagebox.showerror("streamlink", "Для записи эфира нужен streamlink:\n\n"
                                 "  py -m pip install streamlink", parent=self)
            return
        self._finish(mode=_launch.PASSIVE)

    def _open_file(self):
        chosen = filedialog.askopenfilename(
            parent=self, title="Запись стрима",
            filetypes=[("Видео и звук", "*.mp4 *.mkv *.mov *.ts *.flv *.webm *.m4a *.mp3"),
                       ("Все файлы", "*.*")])
        if chosen:
            self._finish(mode=_launch.ACTIVE, action="open", path=chosen)

    def _paste(self):
        try:
            self.v_link.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def _download(self):
        link = self.v_link.get().strip()
        if not _fetch.looks_like_link(link):
            messagebox.showerror("Ссылка", "Вставьте ссылку на стрим — она начинается с http.",
                                 parent=self)
            return
        self._finish(mode=_launch.ACTIVE, action="download", link=link)

    def _just_open(self):
        self._finish(mode=_launch.ACTIVE, action=None, analyze=False)

    def _leave(self):
        self.choice = None
        self.grab_release()
        self.destroy()


def choose(master, cfg):
    window = Launcher(master, cfg)
    master.wait_window(window)
    return window.choice
