from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
for _extra in ("clipmaker",):
    _path = os.path.join(_ROOT, _extra)
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.append(_path)

from core import asr as _asr
from core import fetch as _fetch
from core import profiles as _pro
from core import ffmpeg as _ff
from core import handoff as _hand
from core import launch as _launch
from core import live as _live
from core import loudness as _loud
from core import timecode as _tc
from core import ui as _ui

from . import icons as _icons
from . import motion as _motion
from . import theme
from .runner import Runner

SETTINGS = os.environ.get("ASTRA_STUDIO_SETTINGS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.json")
MODELS = _asr.MODELS
TITLE = "Astra Studio"
LONG_TASK = 60.0
ROUGH_DIR = "с матом"        # ролики как есть
CLEAN_DIR = "без мата"       # те же ролики с заглушённым матом, под тем же именем
HANDOFF_TRIES = 2400
KEEP_CHOICES = ((3, "3 дня"), (7, "7 дней"), (14, "14 дней"), (30, "30 дней"),
                (0, "не удалять"))
KEEP_TITLES = [title for _, title in KEEP_CHOICES]


def keep_title(days):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = _pro.DEFAULT_KEEP_DAYS
    for value, title in KEEP_CHOICES:
        if value == days:
            return title
    return "%d дн." % days if days > 0 else "не удалять"


def keep_days(title):
    for value, name in KEEP_CHOICES:
        if name == title:
            return value
    digits = "".join(ch for ch in str(title) if ch.isdigit())
    return int(digits) if digits else 0
VIDEO_TYPES = [("Видео", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.ts"),
               ("Все файлы", "*.*")]


def _load_astra_gui():
    import importlib.util

    name = "astra_gui"
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(_ROOT, "astra", "gui.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    return module


def load_settings():
    try:
        with open(SETTINGS, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        with open(SETTINGS, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
    except OSError:
        pass


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.minsize(920, 620)
        self.geometry(self._start_size())
        theme.apply(self)

        self.cfg = load_settings()
        self.source = tk.StringVar(value=self.cfg.get("source", ""))
        self.model = tk.StringVar(value=self.cfg.get("model", _asr.DEFAULT_MODEL))
        self.language = tk.StringVar(value=self.cfg.get("language", "ru"))
        self.mode = "rolik"
        self.clip_page = "search"
        self.rolik_page = "build"
        self.cleaner = None
        self.editor = None
        self.editor_source = None
        self.rating = None
        self.uploader = None
        self.info = None
        self.probe_ticket = 0
        self.probe_box = queue.Queue()
        self.moments = []
        self.tray = None
        self.then_analyze = False
        self.learning = None
        self.learn_box = queue.Queue()
        self.live_was = None
        self._live_ends = 0

        self.runner = Runner(self)
        self.runner.listen(self._on_event)
        self.bind("<Control-KeyPress>", self._on_control_key)

        self._build()
        self._refresh_source()
        _icons.set_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---------------------------------------------------------------- каркас

    def _start_size(self):
        want_w, want_h = 1180, 940
        try:
            area_w = self.winfo_screenwidth()
            area_h = self.winfo_screenheight() - 64
        except tk.TclError:
            return "%dx%d" % (want_w, want_h)
        width = max(920, min(want_w, area_w - 40))
        height = max(620, min(want_h, area_h))
        x = max(0, (area_w - width) // 2)
        y = max(0, (area_h - height) // 2)
        return "%dx%d+%d+%d" % (width, height, x, y)

    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=16, pady=(14, 0))
        mark = _icons.photo(self, "logo", "", 28)
        if mark is not None:
            tk.Label(head, image=mark, background=theme.BG).pack(side="left", padx=(0, 10))
        ttk.Label(head, text="Astra Studio", style="Head.TLabel").pack(side="left")
        self._build_profiles(head)

        tabs = ttk.Frame(self)
        tabs.pack(fill="x", padx=16, pady=(12, 0))
        self.tab_clips = theme.Tab(tabs, "Клипы", lambda: self._switch("clip"))
        self.tab_clips.pack(side="left")
        self.tab_rolik = theme.Tab(tabs, "Ролики", lambda: self._switch("rolik"))
        self.tab_rolik.pack(side="left")

        self._build_source()

        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        self.page_rolik = self._build_rolik(self.body)
        self.page_clip = self._build_clip(self.body)

        self._build_log()
        self._switch("rolik")
        if self.v_live_on.get():
            self._live_toggle()

    def _build_profiles(self, head):
        box = ttk.Frame(head)
        box.pack(side="right")
        theme.Button(box, "Удалить", self._drop_profile).pack(side="right",
                                                              padx=(8, 0))
        theme.Button(box, "Новый", self._new_profile).pack(side="right",
                                                           padx=(8, 0))
        theme.Button(box, "Голос и донаты", self._open_voice).pack(side="right",
                                                                   padx=(8, 0))
        self.v_profile = tk.StringVar(value=_pro.active_name())
        self.pick_profile = ttk.Combobox(box, textvariable=self.v_profile,
                                         state="readonly", width=18,
                                         values=_pro.names())
        self.pick_profile.pack(side="right")
        self.pick_profile.bind("<<ComboboxSelected>>", self._pick_profile)
        ttk.Label(box, text="Профиль").pack(side="right", padx=(0, 8))

    def _refresh_profiles(self):
        kept = _pro.names()
        self.pick_profile.configure(values=kept)
        current = _pro.active_name()
        self.v_profile.set(current if current in kept else (kept[0] if kept else ""))

    def _pick_profile(self, _event=None):
        name = self.v_profile.get().strip()
        if not name or name == _pro.active_name():
            return
        try:
            _pro.set_active(name)
        except (FileNotFoundError, ValueError) as error:
            messagebox.showerror("Профиль", str(error), parent=self)
            self._refresh_profiles()
            return
        self._profile_changed(name)

    def _open_voice(self):
        if not _pro.active():
            messagebox.showinfo("Профиль", "Сначала создайте профиль стримера.",
                                parent=self)
            return
        from .voice_ui import open_window

        self.voice_window = open_window(self)

    def _checks(self):
        profile = _pro.active()
        if not profile:
            return None, False, False
        from core import streamcheck as _check

        return (profile, bool(profile.get(_check.CHECK_DONATIONS)),
                bool(profile.get(_check.CHECK_AWAY)))

    def _judge(self):
        from core import donations as _don

        try:
            from clipper.backends import get_backend

            cfg = self._clipper_config()
            return _don.judge_with(get_backend(cfg), cfg)
        except Exception as error:
            self._say("модель для донатов недоступна (%s), решаю по словам"
                      % str(error).splitlines()[0])
            return _don.judge_by_words

    def _check_moments(self, run, source, profile, moments, donations, away, judge):
        from core import streamcheck as _check

        context = _check.Context(source, profile, log=run.log, progress=run.progress,
                                 cancel=lambda: run.cancelled)
        run.stage("Проверяю донаты и уходы…")
        kept, report, _events, _gone = _check.check_moments(
            context, moments, judge=judge, donations=donations, away=away)
        _pro.save(profile)
        for line in report:
            run.log("  " + line)
        cut = sum(len(item.get("drops") or ()) for item in kept)
        run.log("после проверки: моментов %d из %d, вырезов внутри %d"
                % (len(kept), len(moments), cut))
        return kept

    def _new_profile(self):
        from .profiles_ui import ask

        name = ask(self)
        if not name:
            return
        self._refresh_profiles()
        self._profile_changed(name)

    def _drop_profile(self):
        name = self.v_profile.get().strip()
        if not name:
            return
        if len(_pro.names()) < 2:
            messagebox.showinfo(
                "Профиль", "Это единственный профиль, удалять его нечем "
                "заменить.", parent=self)
            return
        agreed = messagebox.askyesno(
            "Удалить профиль",
            "Вместе с профилем «%s» удалятся его клипы, оценки и сравнения."
            "\n\nТочно удалить?" % name, parent=self, icon="warning")
        if not agreed:
            return
        _pro.remove(name)
        kept = _pro.names()
        if kept:
            _pro.set_active(kept[0])
        self._refresh_profiles()
        self._profile_changed(_pro.active_name())

    def _profile_changed(self, name):
        profile = _pro.active()
        if profile:
            if profile.get("whisper"):
                self.model.set(profile["whisper"])
            if profile.get("language"):
                self.language.set(profile["language"])
            self._say("профиль: %s, клипы и оценки — %s"
                      % (name, _pro.clips_dir(profile)))
        else:
            self._say("профиль: %s" % name)

    def _card(self, master, title):
        return theme.card(master, title)

    def _build_source(self):
        wrap = ttk.Frame(self)
        wrap.pack(fill="x", padx=16, pady=(12, 0))
        card = self._card(wrap, "ИСТОЧНИК — общий для обоих режимов")

        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=self.source)
        entry.pack(side="left", fill="x", expand=True)
        theme.Button(row, "Выбрать", self._pick_source).pack(side="left", padx=(8, 0))
        self.v_quality = tk.StringVar(
            value=self.cfg.get("quality", _fetch.QUALITY_TITLES[0]))
        ttk.Combobox(row, textvariable=self.v_quality,
                     values=_fetch.QUALITY_TITLES, state="readonly",
                     width=12).pack(side="left", padx=(8, 0))
        theme.Button(row, "Вставить", self._paste_link).pack(side="left",
                                                             padx=(8, 0))
        self.btn_grab = theme.Button(row, "Скачать", self._grab_stream)
        self.btn_grab.pack(side="left", padx=(8, 0))

        self.lbl_info = ttk.Label(card, text="Файл не выбран. Можно вставить "
                                  "ссылку на стрим и нажать «Скачать»",
                                  style="PanelDim.TLabel")
        self.lbl_info.pack(anchor="w", pady=(6, 0))
        self.lbl_estimate = ttk.Label(card, text="", style="PanelDim.TLabel")

        line = ttk.Frame(card, style="Panel.TFrame")
        line.pack(fill="x", pady=(10, 0))
        ttk.Label(line, text="Модель", style="PanelDim.TLabel").pack(side="left")
        box = ttk.Combobox(line, textvariable=self.model, values=MODELS,
                           width=10, state="readonly")
        box.pack(side="left", padx=(6, 14))
        box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_transcript())
        ttk.Label(line, text="Язык", style="PanelDim.TLabel").pack(side="left")
        lang = ttk.Entry(line, textvariable=self.language, width=6)
        lang.pack(side="left", padx=(6, 14))
        lang.bind("<FocusOut>", lambda _e: self._refresh_transcript())

        self.lbl_speech = ttk.Label(line, text="", style="PanelDim.TLabel")
        self.lbl_speech.pack(side="left")
        self.btn_speech = theme.Button(line, "Расшифровать", self._transcribe)
        self.btn_speech.pack(side="right")

        live = ttk.Frame(card, style="Panel.TFrame")
        live.pack(fill="x", pady=(10, 0))
        ttk.Label(live, text="Эфир", style="PanelDim.TLabel").pack(side="left")
        self.v_live = tk.StringVar(value=self.cfg.get("live_channel", _live.DEFAULT_CHANNEL))
        self.ent_live = ttk.Entry(live, textvariable=self.v_live, width=30)
        self.ent_live.pack(side="left", padx=(6, 8))
        self.ent_live.bind("<Return>", self._live_from_link)   # вставил ссылку, Enter — пишем
        theme.Button(live, "Писать звук", self._live_from_link).pack(side="left",
                                                                     padx=(0, 8))
        self.v_live_on = tk.BooleanVar(value=bool(self.cfg.get("live_watch", False)))
        ttk.Checkbutton(live, text="следить и писать звук эфира", variable=self.v_live_on,
                        command=self._live_toggle).pack(side="left")
        self.lbl_live = ttk.Label(live, text="", style="PanelDim.TLabel")
        self.lbl_live.pack(side="left", padx=(10, 0))
        self.live_watcher = None
        self.live_text = ""

    def _build_log(self):
        wrap = ttk.Frame(self)
        wrap.pack(side="bottom", fill="x", padx=16, pady=(10, 14), before=self.body)
        self.log_wrap = wrap

        bar = ttk.Frame(wrap)
        bar.pack(fill="x", pady=(0, 6))
        self.lbl_stage = ttk.Label(bar, text="готов", style="Dim.TLabel")
        self.lbl_stage.pack(side="left")
        self.btn_stop = theme.Button(bar, "Остановить", self.runner.cancel)
        self.btn_stop.pack(side="right")
        self.btn_stop.set_enabled(False)

        self.progress = ttk.Progressbar(wrap, maximum=1000)
        self.progress.pack(fill="x", pady=(0, 6))

        self.log = tk.Text(wrap, height=8, background=theme.PANEL2,
                           foreground=theme.DIM, insertbackground=theme.TEXT,
                           font=theme.MONO_FONT, relief="flat", wrap="word",
                           borderwidth=0, highlightthickness=0)
        self.log.pack(fill="x")
        self.log.configure(state="disabled")

    def _switch(self, mode):
        self.mode = mode
        self.tab_clips.set_active(mode == "clip")
        self.tab_rolik.set_active(mode == "rolik")
        self.page_rolik.pack_forget()
        self.page_clip.pack_forget()
        page = self.page_rolik if mode == "rolik" else self.page_clip
        page.pack(fill="both", expand=True)
        _motion.reveal(page)
        if mode == "clip" and self.clip_page == "edit":
            self._need_editor()
        if mode == "rolik" and self.rolik_page == "clean":
            self._need_cleaner()
        self._sync_log()

    def _sync_log(self):
        wrap = getattr(self, "log_wrap", None)
        if wrap is None:
            return
        hide = ((self.mode == "clip" and self.clip_page in ("edit", "upload"))
                or (self.mode == "rolik" and self.rolik_page == "clean"))
        if hide:
            wrap.pack_forget()
        elif not wrap.winfo_ismapped():
            wrap.pack(side="bottom", fill="x", padx=16, pady=(10, 14), before=self.body)

    # ---------------------------------------------------------------- ролики

    def _build_rolik(self, master):
        outer = ttk.Frame(master)
        subtabs = ttk.Frame(outer)
        subtabs.pack(fill="x")
        self.tab_build = theme.Tab(subtabs, "Сборка",
                                   lambda: self._switch_rolik("build"))
        self.tab_build.pack(side="left")
        self.tab_clean = theme.Tab(subtabs, "Цензура",
                                   lambda: self._switch_rolik("clean"))
        self.tab_clean.pack(side="left")

        self.rolik_build = theme.Scrolled(outer)
        self.rolik_clean = ttk.Frame(outer)
        page = self.rolik_build.body

        card = self._card(page, "ТАЙМКОДЫ — что вырезать из стрима")
        self.tree = ttk.Treeview(card, columns=("start", "end", "title"),
                                 show="headings", height=7)
        for name, title, width in (("start", "начало", 110), ("end", "конец", 110),
                                   ("title", "название", 420)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, anchor="w")
        self.tree.pack(fill="x")

        add = ttk.Frame(card, style="Panel.TFrame")
        add.pack(fill="x", pady=(10, 0))
        self.v_start = tk.StringVar()
        self.v_end = tk.StringVar()
        self.v_title = tk.StringVar()
        for label, var, width in (("начало", self.v_start, 12),
                                  ("конец", self.v_end, 12),
                                  ("название", self.v_title, 34)):
            ttk.Label(add, text=label, style="PanelDim.TLabel").pack(side="left")
            ttk.Entry(add, textvariable=var, width=width).pack(side="left", padx=(6, 12))
        theme.Button(add, "Добавить", self._add_moment).pack(side="left")
        theme.Button(add, "Удалить", self._remove_moment).pack(side="left", padx=(8, 0))
        theme.Button(add, "Из файла", self._load_moments).pack(side="left", padx=(8, 0))

        card2 = self._card(page, "КОНЦОВКА И ЗВУК")
        self.v_outro = tk.StringVar(value=self.cfg.get("outro", ""))
        self.v_music = tk.StringVar(value=self.cfg.get("music", ""))
        self.v_music_start = tk.StringVar(value=str(self.cfg.get("music_start", "0")))
        self.v_outdir = tk.StringVar(value=self.cfg.get("outdir", ""))
        self.v_keep = tk.StringVar(value=keep_title(
            self.cfg.get("keep_days", _pro.DEFAULT_KEEP_DAYS)))
        for label, var, picker in (("Концовка", self.v_outro, self._pick_outro),
                                   ("Музыка", self.v_music, self._pick_music),
                                   ("Куда сохранять", self.v_outdir, self._pick_outdir)):
            row = ttk.Frame(card2, style="Panel.TFrame")
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, style="PanelDim.TLabel", width=15).pack(side="left")
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
            theme.Button(row, "…", picker, width=3).pack(side="left", padx=(8, 0))
            if var is self.v_music:
                ttk.Label(row, text="с секунды", style="PanelDim.TLabel").pack(
                    side="left", padx=(14, 6))
                ttk.Entry(row, textvariable=self.v_music_start, width=7).pack(side="left")
            if var is not self.v_outdir:
                continue
            ttk.Label(row, text="хранить", style="PanelDim.TLabel").pack(
                side="left", padx=(14, 6))
            ttk.Combobox(row, textvariable=self.v_keep, values=KEEP_TITLES,
                         state="readonly", width=11).pack(side="left")

        self.fine = theme.Folded(card2, "Тонкие настройки",
                                 opened=bool(self.cfg.get("fine_open", False)))
        self.fine.pack(fill="x", pady=(4, 0))
        opts = self.fine.body
        self.v_vol = tk.StringVar(value=str(self.cfg.get("music_volume", "0.25")))
        self.v_fade = tk.StringVar(value=str(self.cfg.get("fade", "1.5")))
        self.v_blur = tk.StringVar(value=str(self.cfg.get("blur", "20")))
        self.v_crf = tk.StringVar(value=str(self.cfg.get("crf", "20")))
        for label, var in (("громкость музыки", self.v_vol), ("затемнение, с", self.v_fade),
                           ("размытие", self.v_blur), ("качество (crf)", self.v_crf)):
            ttk.Label(opts, text=label, style="PanelDim.TLabel").pack(side="left")
            ttk.Entry(opts, textvariable=var, width=7).pack(side="left", padx=(6, 14))
        self.v_gpu = tk.BooleanVar(value=bool(self.cfg.get("gpu", False)))
        ttk.Checkbutton(opts, text="на видеокарте", variable=self.v_gpu).pack(side="left")

        card3 = self._card(page, "ПОСЛЕ СБОРКИ")
        self.v_censor = tk.BooleanVar(value=bool(self.cfg.get("censor", True)))
        ttk.Checkbutton(card3, text="заглушить мат в готовых роликах",
                        variable=self.v_censor).pack(anchor="w")

        go = ttk.Frame(page)
        go.pack(fill="x")
        self.btn_build = theme.Button(go, "Собрать ролики", self._build_roliks,
                                      primary=True)
        self.btn_build.pack(side="right")
        self._switch_rolik("build")
        return outer

    def _switch_rolik(self, page):
        self.rolik_page = page
        self.tab_build.set_active(page == "build")
        self.tab_clean.set_active(page == "clean")
        self.rolik_build.pack_forget()
        self.rolik_clean.pack_forget()
        if page == "clean":
            self.rolik_clean.pack(fill="both", expand=True)
            self._need_cleaner()
        else:
            self.rolik_build.pack(fill="both", expand=True)
        self._sync_log()
        _motion.reveal(self.rolik_clean if page == "clean" else self.rolik_build)

    def _need_cleaner(self):
        if self.cleaner is not None:
            self._sync_cleaner_source()
            return self.cleaner
        try:
            from audioclean.gui import Cleaner
        except Exception as error:
            for child in self.rolik_clean.winfo_children():
                child.destroy()
            ttk.Label(self.rolik_clean, style="Dim.TLabel", justify="left",
                      text="Цензура не загрузилась: %s" % error).pack(anchor="w",
                                                                      padx=4, pady=12)
            self._say("Цензура не загрузилась: %s" % error)
            return None
        ttk.Label(self.rolik_clean, style="Dim.TLabel", justify="left", text=(
            "При сборке мат глушится сам — это галка «после сборки» на соседней "
            "вкладке. Здесь то же самое руками: посмотреть находки и снять лишние."
        )).pack(anchor="w", pady=(10, 8))
        self.cleaner = Cleaner(self.rolik_clean, chrome=False, background=theme.BG)
        self.cleaner.pack(fill="both", expand=True)
        self._sync_cleaner_source()
        return self.cleaner

    def _sync_cleaner_source(self):
        if self.cleaner is None or self.cleaner.v_video.get().strip():
            return
        made = self._made_roliks()
        if made:
            self.cleaner.set_source(made[0])

    def _made_roliks(self):
        folder = self.v_outdir.get().strip()
        if not folder or not os.path.isdir(folder):
            return []
        found = []
        for name in os.listdir(folder):
            if name.lower().endswith((".mp4", ".mkv", ".mov")):
                path = os.path.join(folder, name)
                found.append((os.path.getmtime(path), path))
        return [path for _, path in sorted(found, reverse=True)]

    def _add_moment(self):
        try:
            start = _tc.require_time(self.v_start.get())
            end = _tc.require_time(self.v_end.get())
        except ValueError as error:
            messagebox.showerror("Время", str(error), parent=self)
            return
        if end <= start:
            messagebox.showerror("Время", "Конец должен быть позже начала.", parent=self)
            return
        self.moments.append({"start": start, "end": end,
                             "title": self.v_title.get().strip()})
        self.v_start.set("")
        self.v_end.set("")
        self.v_title.set("")
        self._redraw_moments()

    def _remove_moment(self):
        for item in self.tree.selection():
            index = self.tree.index(item)
            if 0 <= index < len(self.moments):
                del self.moments[index]
        self._redraw_moments()

    def _load_moments(self):
        path = filedialog.askopenfilename(
            parent=self, title="Список таймкодов",
            filetypes=[("Текст", "*.txt"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                items, skipped = _hand.parse_timecodes_text(handle.read())
        except OSError as error:
            messagebox.showerror("Файл", str(error), parent=self)
            return
        self.moments.extend({"start": i["start"], "end": i["end"], "title": i["title"],
                             "drops": list(i.get("drops") or ())} for i in items)
        self._redraw_moments()
        self._say("Загружено моментов: %d%s"
                  % (len(items), (", пропущено строк: %d" % skipped) if skipped else ""))

    def _redraw_moments(self):
        self.tree.delete(*self.tree.get_children())
        for item in self.moments:
            title = item.get("title", "")
            if item.get("drops"):
                title = "%s  (вырезов внутри: %d)" % (title, len(item["drops"]))
            self.tree.insert("", "end", values=(
                _tc.format_hms(item["start"]), _tc.format_hms(item["end"]), title))

    # ---------------------------------------------------------------- клипы

    def _build_clip(self, master):
        outer = ttk.Frame(master)
        subtabs = ttk.Frame(outer)
        subtabs.pack(fill="x")
        self.tab_search = theme.Tab(subtabs, "Поиск моментов",
                                    lambda: self._switch_clip("search"))
        self.tab_search.pack(side="left")
        self.tab_edit = theme.Tab(subtabs, "Монтаж 9:16",
                                  lambda: self._switch_clip("edit"))
        self.tab_edit.pack(side="left")
        self.tab_rate = theme.Tab(subtabs, "Оценка",
                                  lambda: self._switch_clip("rate"))
        self.tab_rate.pack(side="left")
        self.tab_upload = theme.Tab(subtabs, "Выгрузка",
                                    lambda: self._switch_clip("upload"))
        self.tab_upload.pack(side="left")
        self.tab_pilot = theme.Tab(subtabs, "Автопилот",
                                   lambda: self._switch_clip("pilot"))
        self.tab_pilot.pack(side="left")

        self.clip_search = ttk.Frame(outer)
        self.clip_edit = ttk.Frame(outer)
        self.clip_rate = ttk.Frame(outer)
        self.clip_upload = ttk.Frame(outer)
        self.clip_pilot = ttk.Frame(outer)
        # Кнопки «Передать моменты» и «Сохранить список» прибиты к низу страницы и видны
        # всегда; поиск над ними прокручивается, если окну не хватает высоты.
        bottom = ttk.Frame(self.clip_search)
        bottom.pack(side="bottom", fill="x")
        self.clip_search_scroll = theme.Scrolled(self.clip_search)
        self.clip_search_scroll.pack(fill="both", expand=True)
        page = self.clip_search_scroll.body

        card = self._card(page, "ПОИСК МОМЕНТОВ")
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="сколько", style="PanelDim.TLabel").pack(side="left")
        self.v_count = tk.StringVar(value="10")
        ttk.Entry(row, textvariable=self.v_count, width=5).pack(side="left", padx=(6, 14))
        ttk.Label(row, text="длина, с: от", style="PanelDim.TLabel").pack(side="left")
        self.v_min = tk.StringVar(value="20")
        ttk.Entry(row, textvariable=self.v_min, width=5).pack(side="left", padx=(6, 6))
        ttk.Label(row, text="до", style="PanelDim.TLabel").pack(side="left")
        self.v_max = tk.StringVar(value="60")
        ttk.Entry(row, textvariable=self.v_max, width=5).pack(side="left", padx=(6, 0))

        loud = ttk.Frame(card, style="Panel.TFrame")
        loud.pack(fill="x", pady=(10, 0))
        theme.Button(loud, "По громкости", self._scan_loud).pack(side="left")
        ttk.Label(loud, style="PanelDim.TLabel",
                  text="мгновенно, без расшифровки и без модели").pack(side="left", padx=(10, 0))

        sense = ttk.Frame(card, style="Panel.TFrame")
        sense.pack(fill="x", pady=(8, 0))
        theme.Button(sense, "По смыслу", self._scan_sense, primary=True).pack(side="left")
        self.v_backend = tk.StringVar(value=self.cfg.get("backend", "heuristic"))
        box = ttk.Combobox(sense, textvariable=self.v_backend, width=15,
                           state="readonly",
                           values=("heuristic", "ollama", "openai-compat", "claude"))
        box.pack(side="left", padx=(10, 8))
        box.bind("<<ComboboxSelected>>", lambda _e: self._backend_hint())
        self.backend_models = {
            "ollama": self.cfg.get("local_model", "qwen3:8b"),
            "openai-compat": self.cfg.get("openai_model", ""),
        }
        self.model_slot = self.v_backend.get()
        self.v_local_model = tk.StringVar(
            value=self.backend_models.get(self.model_slot, self.backend_models["ollama"]))
        self.ent_local = ttk.Combobox(sense, textvariable=self.v_local_model, width=28)
        self.ent_local.pack(side="left")
        theme.Button(sense, "Проверить", self._check_backend).pack(side="left", padx=(8, 0))

        server = ttk.Frame(card, style="Panel.TFrame")
        self.server_row = server
        self.sense_row = sense
        ttk.Label(server, text="сервер", style="PanelDim.TLabel").pack(side="left")
        self.v_openai_url = tk.StringVar(
            value=self.cfg.get("openai_url", "") or "http://localhost:1234")
        self.ent_openai_url = ttk.Entry(server, textvariable=self.v_openai_url, width=30)
        self.ent_openai_url.pack(side="left", padx=(6, 14))
        ttk.Label(server, text="ключ API", style="PanelDim.TLabel").pack(side="left")
        self.v_openai_key = tk.StringVar(value=self.cfg.get("openai_key", ""))
        self.ent_openai_key = ttk.Entry(server, textvariable=self.v_openai_key,
                                        width=22, show="•")
        self.ent_openai_key.pack(side="left", padx=(6, 0))
        ttk.Label(server, style="PanelDim.TLabel", text=(
            "LM Studio — без ключа; OpenRouter, DeepSeek, Groq — адрес и ключ сервиса")
        ).pack(side="left", padx=(10, 0))

        weigh = ttk.Frame(card, style="Panel.TFrame")
        weigh.pack(fill="x", pady=(8, 0))
        self.v_compare = tk.BooleanVar(value=bool(self.cfg.get("compare", True)))
        ttk.Checkbutton(weigh, text="сравнивать найденное между собой",
                        variable=self.v_compare).pack(side="left")
        ttk.Label(weigh, style="PanelDim.TLabel", text=(
            "оценка отсеивает слабое, а порядок среди оставшихся даёт сравнение")
        ).pack(side="left", padx=(10, 0))

        self.lbl_backend = ttk.Label(card, text="", style="PanelDim.TLabel")
        self.lbl_backend.pack(anchor="w", pady=(6, 0))

        self.clip_tree = ttk.Treeview(card, columns=("start", "end", "score", "title"),
                                      show="headings", height=9)
        for name, title, width in (("start", "начало", 100), ("end", "конец", 100),
                                   ("score", "оценка", 70), ("title", "название", 420)):
            self.clip_tree.heading(name, text=title)
            self.clip_tree.column(name, width=width, anchor="w")
        self.clip_tree.pack(fill="both", expand=True, pady=(10, 0))
        self._backend_hint()

        card2 = theme.card(bottom, "ДАЛЬШЕ — МОНТАЖ 9:16", gap=0)
        row2 = ttk.Frame(card2, style="Panel.TFrame")
        row2.pack(fill="x")
        theme.Button(row2, "Передать моменты", self._to_editor,
                     primary=True).pack(side="left")
        theme.Button(row2, "Сохранить список",
                     self._save_moments).pack(side="left", padx=(8, 0))
        theme.Button(row2, "Оценить клипы",
                     self._open_rating).pack(side="left", padx=(8, 0))
        ttk.Label(card2, style="PanelDim.TLabel", wraplength=900, justify="left", text=(
            "«Передать моменты» переносит найденное на вкладку «Монтаж 9:16» — там кадр, "
            "субтитры и цензура. «Оценить клипы» открывает «Оценку»: на ней учится "
            "модель вкуса.")
        ).pack(anchor="w", pady=(8, 0))

        self._switch_clip("search")
        return outer

    # ------------------------------------------------------------ монтаж 9:16

    def _switch_clip(self, page):
        self.clip_page = page
        self.tab_search.set_active(page == "search")
        self.tab_edit.set_active(page == "edit")
        self.tab_rate.set_active(page == "rate")
        self.tab_upload.set_active(page == "upload")
        self.tab_pilot.set_active(page == "pilot")
        self.clip_pilot.pack_forget()
        self.clip_search.pack_forget()
        self.clip_edit.pack_forget()
        self.clip_rate.pack_forget()
        self.clip_upload.pack_forget()
        if page == "pilot":
            self.clip_pilot.pack(fill="both", expand=True)
            self._need_pilot()
        elif page == "upload":
            self.clip_upload.pack(fill="both", expand=True)
            self._need_uploader()
        elif page == "edit":
            self.clip_edit.pack(fill="both", expand=True)
            self._need_editor()
        elif page == "rate":
            self.clip_rate.pack(fill="both", expand=True)
            self._need_rating()
        else:
            self.clip_search.pack(fill="both", expand=True)
        self._sync_log()
        _motion.reveal({"pilot": self.clip_pilot,
                        "upload": self.clip_upload, "edit": self.clip_edit,
                        "rate": self.clip_rate}.get(page, self.clip_search))

    def _need_rating(self):
        if self.rating is not None:
            return self.rating
        try:
            from clipper.gui import Rating
        except Exception as error:
            for child in self.clip_rate.winfo_children():
                child.destroy()
            ttk.Label(self.clip_rate, style="Dim.TLabel", justify="left",
                      text="Оценка не загрузилась: %s" % error).pack(anchor="w",
                                                                     padx=4, pady=12)
            self._say("Оценка не загрузилась: %s" % error)
            return None
        self.rating = Rating(self.clip_rate, chrome=False, background=theme.BG)
        self.rating.pack(fill="both", expand=True)
        return self.rating

    def _need_pilot(self):
        if getattr(self, "pilot", None) is None:
            from .autopilot_ui import AutopilotPanel

            self.pilot = AutopilotPanel(self.clip_pilot, self)
            self.pilot.pack(fill="both", expand=True)
        else:
            self.pilot.refresh()
        return self.pilot

    def _need_uploader(self):
        if self.uploader is None:
            from .upload_ui import UploadPanel

            self.uploader = UploadPanel(self.clip_upload, self)
            self.uploader.pack(fill="both", expand=True)
        self.uploader.refresh()
        return self.uploader

    def _need_editor(self):
        if self.editor is not None:
            self._sync_editor_source()
            return self.editor
        try:
            _astra = _load_astra_gui()
        except Exception as error:
            for child in self.clip_edit.winfo_children():
                child.destroy()
            ttk.Label(self.clip_edit, style="Dim.TLabel", justify="left", text=(
                "Монтаж не загрузился: %s. "
                "Ему нужны cv2, mediapipe и PIL — запускайте Студию через "
                "«Студия.bat», она берёт окружение astra." % error)
            ).pack(anchor="w", padx=4, pady=12)
            self._say("Монтаж не загрузился: %s" % error)
            return None
        canvas = tk.Canvas(self.clip_edit, background=theme.BG,
                           highlightthickness=0, borderwidth=0,
                           yscrollincrement=20)
        canvas.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(self.clip_edit, orient="vertical",
                            command=canvas.yview)
        bar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=bar.set)

        self.editor = _astra.Editor(canvas, chrome=False)
        slot = canvas.create_window(0, 0, anchor="nw", window=self.editor)

        def fit(_event=None):
            canvas.itemconfigure(slot, width=max(200, canvas.winfo_width()))
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(),
                                           self.editor.winfo_reqheight()))

        def wheel(event):
            first, last = canvas.yview()
            if last - first >= 0.999:
                return
            canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")

        canvas.bind("<Configure>", fit)
        self.editor.bind("<Configure>", fit)
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        self.editor_canvas = canvas
        self.model.trace_add("write", lambda *_a: self.editor.var_model.set(self.model.get()))
        self.language.trace_add(
            "write", lambda *_a: self.editor.var_lang.set(self.language.get()))
        self.editor.var_model.set(self.model.get())
        self.editor.var_lang.set(self.language.get())
        self._sync_editor_source()
        return self.editor

    def _sync_editor_source(self):
        path = self.source.get().strip()
        if not path or not os.path.isfile(path):
            return
        if self.editor_source == path:
            return
        self.editor_source = path
        self.editor.load_source(path)

    def _to_editor(self):
        items = getattr(self, "clip_moments", None)
        if not self._need_source():
            return
        self._switch_clip("edit")
        if self.editor is None or not items:
            return
        profile, donations, away = self._checks()
        if donations or away:
            source = self.source.get()
            judge = self._judge() if donations else None

            def work(run):
                return self._check_moments(run, source, profile, list(items),
                                           donations, away, judge)

            def done(kind, kept):
                if kind == "done" and kept is not None:
                    self._hand_over(kept)

            self._go(work, done)
            return
        self._hand_over(items)

    def _hand_over(self, items):
        if self.editor is None or not items:
            self._say("Передавать нечего: все моменты убраны проверкой.")
            return
        path = os.path.join(_ROOT, "studio", "_моменты.txt")
        _hand.write_timecodes(path, items)
        self._say("Жду, пока монтаж откроет стрим…")
        self._push_timecodes(path, items, 0)

    def _push_timecodes(self, path, items, tries):
        if self.editor is None:
            return
        want = self.source.get().strip()
        if self.editor.source != want:
            if tries < HANDOFF_TRIES:
                self.after(50, lambda: self._push_timecodes(path, items, tries + 1))
            else:
                self._say("Монтаж не открыл стрим за %d с, моменты не переданы. "
                          "Нажмите «Передать моменты» ещё раз." % (HANDOFF_TRIES // 20))
            return
        self.editor.adopt_timecodes(path)
        for found, item in zip(self.editor.candidates, items):
            found["score"] = float(item.get("score") or 0.0)
        self.editor.moments.set_items(self.editor.candidates)
        self.editor.moments.check_all()
        self.editor._update_stats()
        self._say("В монтаж передано моментов: %d" % len(items))

    def _scan_loud(self):
        if not self._need_source():
            return
        try:
            count = int(self.v_count.get())
            low = float(self.v_min.get())
            high = float(self.v_max.get())
        except ValueError:
            messagebox.showerror("Числа", "Проверьте количество и длину.", parent=self)
            return
        source = self.source.get()

        def work(run):
            run.stage("Читаю звук…")
            data = _loud.audio_levels(
                source, progress=lambda done, total: run.progress(
                    (done / total) if total else 0.0))
            run.stage("Выбираю моменты…")
            return _loud.pick_moments(data, count, low, high)

        def done(kind, payload):
            if kind != "done" or payload is None:
                return
            self.clip_moments = [{"start": item["start"], "end": item["end"],
                                  "score": item["score"], "title": ""}
                                 for item in payload]
            self._redraw_clips()
            self._say("Найдено по громкости: %d" % len(payload))

        self._go(work, done)

    HINTS = {
        "heuristic": "правила по тексту, без модели — мгновенно и бесплатно, "
                     "но заметно слабее",
        "ollama": "локальная модель через Ollama — бесплатно, нужна установка",
        "openai-compat": "LM Studio, llama.cpp или любой OpenAI-совместимый сервис; "
                         "«Проверить» загрузит список моделей",
        "claude": "Claude API — платно, самое точное качество, нужен ключ "
                  "в ANTHROPIC_API_KEY",
    }

    def _backend_hint(self):
        name = self.v_backend.get()
        self.lbl_backend.configure(text=self.HINTS.get(name, ""),
                                   style="PanelDim.TLabel")
        local = name in ("ollama", "openai-compat")
        if name != self.model_slot:
            if self.model_slot in self.backend_models:
                self.backend_models[self.model_slot] = self.v_local_model.get().strip()
            if name in self.backend_models:
                self.v_local_model.set(self.backend_models[name])
                self.ent_local.configure(values=())
            self.model_slot = name
        remote = "normal" if name == "openai-compat" else "disabled"
        try:
            self.ent_local.configure(state="normal" if local else "disabled")
            self.ent_openai_url.configure(state=remote)
            self.ent_openai_key.configure(state=remote)
        except tk.TclError:
            pass
        if name == "openai-compat":
            if not self.server_row.winfo_manager():
                self.server_row.pack(fill="x", pady=(8, 0), after=self.sense_row)
        else:
            self.server_row.pack_forget()

    def _model_names(self):
        names = dict(self.backend_models)
        if self.model_slot in names:
            names[self.model_slot] = self.v_local_model.get().strip()
        return names

    def _clipper_config(self):
        from clipper.config import Config, with_profile
        cfg = Config()
        cfg.backend = self.v_backend.get()
        if cfg.backend == "openai-compat":
            cfg.local_model = self.v_local_model.get().strip()
            cfg.openai_base_url = self.v_openai_url.get().strip() or cfg.openai_base_url
            cfg.openai_api_key = self.v_openai_key.get().strip() or cfg.openai_api_key
        else:
            cfg.local_model = self.v_local_model.get().strip() or cfg.local_model
        cfg.whisper_model = self.model.get()
        cfg.language = self.language.get().strip() or None
        cfg.min_clip_s = _number(self.v_min.get(), 20.0)
        cfg.max_clip_s = _number(self.v_max.get(), 90.0)
        cfg.top_n = int(_number(self.v_count.get(), 10))
        cfg.compare = bool(self.v_compare.get())
        return with_profile(cfg)

    def _check_backend(self):
        try:
            from clipper.backends import get_backend
            backend = get_backend(self._clipper_config())
        except Exception as error:
            self.lbl_backend.configure(text=str(error).splitlines()[0],
                                       style="PanelBad.TLabel")
            return
        listing = getattr(backend, "models", None)
        if callable(listing):
            self._fill_models(backend, listing)
            return
        check = getattr(backend, "check", None)
        if not callable(check):
            self.lbl_backend.configure(text="движок готов: %s" % backend.describe(),
                                       style="PanelOk.TLabel")
            return
        try:
            check()
        except Exception as error:
            self.lbl_backend.configure(text=str(error).splitlines()[0],
                                       style="PanelBad.TLabel")
            return
        self.lbl_backend.configure(text="движок готов: %s" % backend.describe(),
                                   style="PanelOk.TLabel")

    def _fill_models(self, backend, listing):
        try:
            names = listing()
        except Exception as error:
            self.lbl_backend.configure(text=str(error).splitlines()[0],
                                       style="PanelBad.TLabel")
            return
        self.ent_local.configure(values=tuple(names))
        if not names:
            self.lbl_backend.configure(
                text="сервер отвечает, но моделей нет — загрузите модель на сервере",
                style="PanelBad.TLabel")
            return
        wanted = self.v_local_model.get().strip()
        note = ""
        if wanted not in names:
            if wanted:
                note = " (модели «%s» на сервере нет)" % wanted
            self.v_local_model.set(names[0])
            backend.model = names[0]
        self.lbl_backend.configure(
            text="движок готов: %s, моделей на сервере: %d%s"
                 % (backend.describe(), len(names), note),
            style="PanelOk.TLabel")

    def _scan_sense(self):
        if not self._need_source():
            return
        source = self.source.get()
        cfg = self._clipper_config()
        count = cfg.top_n

        def work(run):
            import contextlib
            import io
            from pathlib import Path
            from clipper.backends import get_backend
            from clipper.models import Task
            from clipper.pipeline import process
            from clipper.store import Store

            run.stage("Готовлю движок…")
            backend = get_backend(cfg)
            check = getattr(backend, "check", None)
            if callable(check):
                check()

            cfg.ensure_dirs()
            store = Store(cfg.db_path)
            run.stage("Расшифровка и отбор — это долго…")

            stream = _Relay(run)
            with contextlib.redirect_stdout(stream):
                result = process(Task(source=Path(source)), cfg, store,
                                 backend=backend, do_render=False,
                                 verbose=True, cancel=lambda: run.cancelled)
            stream.flush()
            ranked = sorted(result.candidates, key=lambda c: c.score, reverse=True)
            return ranked[:count], _launch.estimate(
                [{"start": c.start, "end": c.end, "score": c.score} for c in ranked],
                least_score=cfg.min_score)

        def done(kind, payload):
            if kind != "done" or payload is None:
                return
            payload, found = payload
            self._show_estimate(found)
            self.clip_moments = [{"start": c.start, "end": c.end,
                                  "score": c.score, "title": c.title}
                                 for c in payload]
            self._redraw_clips()
            if self.clip_moments:
                self._say("Найдено по смыслу: %d" % len(self.clip_moments))
            else:
                self._say("Движок не нашёл ничего стоящего. Это нормальный ответ: "
                          "попробуйте уменьшить минимальную длину, либо взять "
                          "движок посильнее — heuristic заметно слабее модели.")
            self._remember()

        self._go(work, done)

    def _redraw_clips(self):
        self.clip_tree.delete(*self.clip_tree.get_children())
        for item in getattr(self, "clip_moments", []):
            self.clip_tree.insert("", "end", values=(
                _tc.format_hms(item["start"]), _tc.format_hms(item["end"]),
                "%.1f" % item.get("score", 0.0), item.get("title", "")))

    def _save_moments(self):
        items = getattr(self, "clip_moments", None)
        if not items:
            messagebox.showinfo("Пусто", "Сначала найдите моменты.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".txt", initialfile="moments.txt",
            filetypes=[("Текст", "*.txt")])
        if not path:
            return
        _hand.write_timecodes(path, items)
        self._say("Список записан: %s" % path)

    def _clip_folders(self):
        places = []
        if self.editor is not None:
            if getattr(self.editor, "batch_folder", None):
                places.append(self.editor.batch_folder)
            single = self.editor.var_output.get().strip()
            if single:
                places.append(os.path.dirname(single) or single)
        try:
            from core import profiles as _pro

            profile = _pro.active()
        except Exception:
            profile = None
        if profile and profile.get("out_dir"):
            places.append(profile["out_dir"])
        kept = []
        seen = set()
        for place in places:
            real = os.path.abspath(place)
            if real in seen or not os.path.isdir(real):
                continue
            seen.add(real)
            kept.append(real)
        return kept

    def _adopt_clips(self):
        cli = os.path.join(_ROOT, "clipper", "cli.py")
        if not os.path.isfile(cli):
            return 0
        folders = self._clip_folders()
        if not folders:
            self._say("Не нашёл, куда сложены клипы: сперва смонтируйте их.")
            return 0
        taken = 0
        for place in folders:
            try:
                done = subprocess.run([sys.executable, cli, "adopt", place],
                                      cwd=os.path.join(_ROOT, "clipper"), timeout=90,
                                      capture_output=True, text=True,
                                      encoding="utf-8", errors="replace")
            except (OSError, subprocess.SubprocessError):
                continue
            for line in (done.stdout or "").splitlines():
                if line.startswith("Привязано клипов:"):
                    try:
                        taken += int(line.split(":")[1])
                    except ValueError:
                        pass
        if taken:
            self._say("В базу оценок добавлено клипов: %d" % taken)
        else:
            self._say("Новых клипов не нашлось — смотрю в %s"
                      % ", ".join(folders))
        return taken

    def _open_rating(self):
        self._say("Подбираю смонтированные клипы…")
        threading.Thread(target=self._rating_worker, daemon=True).start()

    def _rating_worker(self):
        self._adopt_clips()
        self.after(0, self._show_rating)

    def _show_rating(self):
        self._switch_clip("rate")
        if self.rating is not None:
            self.rating.reload()
            self._say("Оценка: пары клипов и таблица со всеми клипами.")

    def _on_control_key(self, event):
        if event.keycode != 86 and str(event.keysym).lower() not in ("v", "cyrillic_em"):
            return None
        widget = self.focus_get()
        if isinstance(widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Spinbox, ttk.Combobox)):
            if str(event.keysym).lower() == "v":
                return None
            try:
                widget.event_generate("<<Paste>>")
            except tk.TclError:
                return None
            return "break"
        self._paste_link()
        return "break"

    def _paste_link(self, and_grab=False):
        try:
            text = str(self.clipboard_get()).strip()
        except tk.TclError:
            text = ""
        if not text:
            self._say("В буфере обмена пусто.")
            return False
        if not _fetch.looks_like_link(text):
            if os.path.isfile(text):
                self.source.set(text)
                self._refresh_source()
                return True
            self._say("В буфере не ссылка и не файл: %s" % text[:80])
            return False
        self.source.set(text)
        self.lbl_info.configure(text="Ссылка вставлена — нажмите «Скачать»")
        if and_grab:
            self._grab_stream()
        return True

    # ---------------------------------------------------------------- эфир

    def _live_folder(self, source=""):
        base = self.cfg.get("dldir") or ""
        if not base or not os.path.isdir(base):
            base = os.path.dirname(os.path.abspath(source)) if source else ""
        if not base:                              # папку ещё не выбирали — пишем к себе
            base = _live.default_dldir()
            try:
                os.makedirs(base, exist_ok=True)
            except OSError:
                return ""
        return _live.folder_of(base) if base else ""

    def _live_from_link(self, _event=None):
        """Ссылка на трансляцию в поле «Эфир» — и запись звука начинается.

        Эфир ещё не начался — сторож дождётся начала сам. Ответ: пошла ли запись.
        """
        if not _live.channel_of(self.v_live.get()):
            self.lbl_live.configure(text="нужна ссылка на трансляцию или имя канала")
            return "break"
        if self.live_watcher is not None:
            self.live_watcher.stop()              # другая ссылка — сторож перезапустится
            self.live_watcher = None
        self.v_live_on.set(True)
        self._live_toggle()
        if self.live_watcher is not None:
            self._say("Слежу за эфиром %s — как начнётся, запишу звук и скажу, "
                      "когда он кончится." % self.live_watcher.channel)
        return "break"

    def _live_toggle(self):
        """Галочка «следить»: включает и выключает сторожа эфира."""
        if self.live_watcher is not None:
            self.live_watcher.stop()
            self.live_watcher = None
        if not self.v_live_on.get():
            self.lbl_live.configure(text="")
            return
        channel = _live.channel_of(self.v_live.get())
        folder = self._live_folder(self.source.get().strip())
        if not channel or not folder:
            self.v_live_on.set(False)
            self.lbl_live.configure(text="нужен канал (ссылка или имя) и папка для стримов"
                                    if not channel else "сначала выберите папку для стримов")
            return
        if not _live.streamlink():
            self.v_live_on.set(False)
            self.lbl_live.configure(text="нет streamlink: py -m pip install streamlink")
            return
        self.v_live.set(channel)
        self.live_text = "проверяю, идёт ли эфир %s…" % channel
        self.live_was = None
        self._live_ends = 0
        self.live_watcher = _live.Watcher(channel, folder, on_state=self._live_state)
        self.live_watcher.start()
        self._live_pump()

    def _live_state(self, text):
        self.live_text = text                       # из потока сторожа — окно заберёт само

    def _live_pump(self):
        if self.live_watcher is None:
            if self.tray is not None:
                self.tray.status("за эфиром не слежу")
            return
        try:
            self.lbl_live.configure(text=self.live_text)
            self._live_news()
            self.after(1000, self._live_pump)
        except tk.TclError:
            pass

    def _live_notify(self, text, ring=False):
        """Уведомление об эфире: в трее — всплывашкой, без трея — в окне.

        Окно моргает в панели задач, чтобы конец эфира не пропустить, даже когда
        Студия не свёрнута и на неё никто не смотрит.
        """
        if self.tray is not None:
            self.tray.notify(text[:200])
        self._say(text)
        if ring and not _ui.watching(self):
            _ui.attention(self)

    def _live_news(self):
        """Начало и конец эфира, докачанное начало — уведомлением."""
        watcher = self.live_watcher
        if watcher is None:
            return
        now = watcher.current is not None
        if self.tray is not None:
            self.tray.status(self.live_text)
        if self.live_was is not None and now != self.live_was and now:
            self._live_notify("Эфир %s начался — пишу звук." % watcher.channel)
        self.live_was = now
        if watcher.finishes != getattr(self, "_live_ends", 0):
            self._live_ends = watcher.finishes
            self._live_notify(_live.finished_text(watcher.finished), ring=True)
            self._pilot_after_live(watcher.finished)
        caught = watcher.catch_text
        if caught != getattr(self, "_caught_said", "") and (
                caught.startswith("начало эфира докачано") or "не докачалось" in caught):
            self._live_notify(caught[:200])
        self._caught_said = caught

    def _pilot_after_live(self, done):
        """Конец эфира канала автопилота — автопилот сразу ждёт запись и режет её."""
        from core import autopilot as _ap
        from core import profiles as _pro

        from core import service as _svc

        profile = _pro.active()
        if not done or not profile or not _ap.follows(profile, done.get("channel")):
            return
        if _svc.running():
            return                                # запись после эфира режет служба

        try:
            self._need_pilot().after_live(profile, done)
        except Exception as error:
            self._say("автопилот после эфира не запустился: %s" % str(error).splitlines()[0])
            return
        self._live_notify("Жду запись эфира на твиче — как появится, автопилот её нарежет.")

    def _live_patches(self, run, source) -> list:
        """Звук мута из своей записи эфира, если она есть для этого стрима."""
        folder = self._live_folder(source)
        if not folder or not os.path.isdir(folder):
            return []
        try:
            return _live.patches_for(source, folder, log=run.log) or []
        except Exception as error:
            run.log("запись эфира не подошла: %s" % str(error).splitlines()[0])
            return []

    def _grab_folder(self):
        saved = self.cfg.get("dldir", "")
        if saved and os.path.isdir(saved):
            return saved
        source = self.source.get().strip()
        if source and os.path.isfile(source):
            return os.path.dirname(source)
        chosen = filedialog.askdirectory(title="Куда скачивать стримы",
                                         parent=self)
        if chosen:
            self.cfg["dldir"] = chosen
        return chosen

    def _grab_stream(self):
        if not self.btn_grab.enabled:
            return
        url = self.source.get().strip()
        if not _fetch.looks_like_link(url):
            messagebox.showerror(
                "Ссылка",
                "Вставьте в поле источника ссылку на стрим — "
                "она начинается с http.", parent=self)
            return
        if not _fetch.available():
            messagebox.showerror(
                "yt-dlp",
                "yt-dlp не найден. Установите его командой:"
                + os.linesep * 2
                + "  py -m pip install --upgrade yt-dlp",
                parent=self)
            return
        folder = self._grab_folder()
        if not folder:
            return
        quality = self.v_quality.get()
        self.btn_grab.set_enabled(False)
        self._say("Качаю %s (%s)" % (url, quality))

        def work(run):
            run.stage("Скачиваю стрим…")
            try:
                return _fetch.download(
                    url, folder, quality, log=run.log,
                    progress=lambda part, note="": run.progress(part, note),
                    cancel=lambda: run.cancelled)
            except RuntimeError:
                if not run.cancelled:
                    raise
                run.log("Скачивание остановлено. Недокачанное лежит в папке "
                        "и докачается, если нажать «Скачать» ещё раз.")
                return None

        def done(kind, payload):
            self.btn_grab.set_enabled(True)
            if kind != "done" or not payload:
                return
            self.source.set(payload)
            self._refresh_source()
            self._say("Скачано: %s" % payload)
            self._remember()
            if self.then_analyze:
                self.then_analyze = False
                self.after(200, self._analyze)

        self._go(work, done)

    def _build_roliks(self):
        if not self._need_source():
            return
        if not self.moments:
            messagebox.showinfo("Пусто", "Добавьте хотя бы один таймкод.", parent=self)
            return
        outdir = self.v_outdir.get().strip() or os.path.dirname(self.source.get())
        if not os.path.isdir(outdir):
            messagebox.showerror("Папка", "Папка вывода не найдена.", parent=self)
            return

        source = self.source.get()
        moments = list(self.moments)
        setup = self._roliks_setup()
        keep = keep_days(self.v_keep.get())

        def work(run):
            made = [item["video"] for item in self._make_roliks(run, source, moments, outdir,
                                                                setup)]
            if keep > 0 and made and not run.cancelled:
                run.stage("Убираю старые ролики")
                spared = {os.path.abspath(path) for path in made}
                spared |= {os.path.join(outdir, CLEAN_DIR, os.path.basename(path))
                           for path in made}
                gone = _pro.sweep(outdir, keep, report=run.log, protect=spared)
                run.log("по сроку %d дн. удалено: %d" % (keep, len(gone))
                        if gone else "старых роликов нет")
            run.progress(1.0)
            return made

        def done(kind, payload):
            if kind == "done":
                self._say("Собрано роликов: %d" % len(payload or []))
            self._remember()

        self._go(work, done)

    def _roliks_setup(self) -> dict:
        """Всё, что сборке нужно из окна: читается в главном потоке до запуска."""
        profile, donations, away = self._checks()
        return {
            "options": {
                "outro": self.v_outro.get().strip() or None,
                "music": self.v_music.get().strip() or None,
                "music_volume": _number(self.v_vol.get(), 1.0),
                "music_start": _number(self.v_music_start.get(), 0.0),
                "fade": _number(self.v_fade.get(), 1.5),
                "blur": _number(self.v_blur.get(), 20.0),
                "crf": int(_number(self.v_crf.get(), 20)),
                "gpu": bool(self.v_gpu.get()),
            },
            "censor": bool(self.v_censor.get()),
            "model": self.model.get(),
            "language": self.language.get().strip(),
            "profile": profile, "donations": donations, "away": away,
            "judge": self._judge() if donations else None,
        }

    def _make_roliks(self, run, source, moments, outdir, setup, fix=None) -> list:
        """Собирает ролики и чистые версии. Ответ: [{"video", "clean", "title"}].

        Ролики ложатся в «с матом», чистые версии — в «без мата» под тем же именем.
        fix(item, run) правит собранный ролик до цензуры (звук на месте мута твича).
        """
        from core import roliks as _rol

        if setup["donations"] or setup["away"]:
            moments = self._check_moments(run, source, setup["profile"], moments,
                                          setup["donations"], setup["away"], setup["judge"])
        return _rol.make(source, moments, outdir, setup, log=run.log, stage=run.stage,
                         progress=run.progress, cancel=lambda: run.cancelled,
                         live=self._live_patches(run, source),
                         fix=(lambda item: fix(item, run)) if fix else None)

    # ---------------------------------------------------------------- общее

    def _pick_source(self):
        path = filedialog.askopenfilename(parent=self, title="Стрим",
                                          filetypes=VIDEO_TYPES)
        if path:
            self.source.set(path)
            self._refresh_source()

    def _pick_outro(self):
        path = filedialog.askopenfilename(parent=self, title="Концовка",
                                          filetypes=VIDEO_TYPES)
        if path:
            self.v_outro.set(path)

    def _pick_music(self):
        path = filedialog.askopenfilename(
            parent=self, title="Музыка",
            filetypes=[("Звук", "*.mp3 *.wav *.m4a *.ogg *.flac"),
                       ("Все файлы", "*.*")])
        if path:
            self.v_music.set(path)

    def _pick_outdir(self):
        path = filedialog.askdirectory(parent=self, title="Куда сохранять")
        if path:
            self.v_outdir.set(path)

    def _refresh_source(self):
        path = self.source.get().strip()
        if self.editor is not None and path != self.editor_source:
            self._sync_editor_source()
        if not path or not os.path.isfile(path):
            self.lbl_info.configure(text="Файл не выбран")
            self.info = None
            self._refresh_transcript()
            return
        self.info = None
        self.probe_ticket = ticket = self.probe_ticket + 1
        self.lbl_info.configure(text="Читаю файл…")
        self._refresh_transcript()

        def job():
            try:
                info = _ff.probe(path)
            except Exception as error:
                info = error
            self.probe_box.put((ticket, path, info))

        threading.Thread(target=job, daemon=True).start()
        self.after(100, self._probe_pump)

    def _probe_pump(self):
        try:
            ticket, path, info = self.probe_box.get_nowait()
        except queue.Empty:
            if self.probe_ticket and self.info is None and self.winfo_exists():
                self.after(100, self._probe_pump)
            return
        self._probed(ticket, path, info)

    def _probed(self, ticket, path, info):
        if ticket != self.probe_ticket:
            return
        if isinstance(info, Exception):
            self.lbl_info.configure(text="Не прочитал файл: %s" % str(info)[:80])
            self.info = None
            return
        self.info = info
        size = os.path.getsize(path) / 1024.0 ** 3
        self.lbl_info.configure(text="%s · %dx%d · %.0f fps · %.1f ГБ · звук: %s"
                                % (_tc.format_hms(info.duration),
                                   info.width, info.height, info.fps, size,
                                   "есть" if info.has_audio else "нет"))

    def _transcript_key(self):
        return _asr.cache_key(self.source.get(), self.model.get(),
                              self.language.get().strip(), 5, True)

    def _refresh_transcript(self):
        path = self.source.get().strip()
        if not path or not os.path.isfile(path):
            self.lbl_speech.configure(text="")
            self.btn_speech.set_enabled(False)
            return
        found = _asr.load_cache(path, self._transcript_key())
        if found is not None:
            words = sum(len(s.words) for s in found.segments)
            self.lbl_speech.configure(text="расшифровка готова · слов %d" % words,
                                      style="PanelOk.TLabel")
            self.btn_speech.set_enabled(False)
        else:
            self.lbl_speech.configure(text="расшифровки нет", style="PanelDim.TLabel")
            self.btn_speech.set_enabled(True)

    def _transcribe(self):
        if not self._need_source():
            return
        source = self.source.get()
        model = self.model.get()
        language = self.language.get().strip()
        key = self._transcript_key()
        duration = self.info.duration if self.info else 0.0

        def work(run):
            import tempfile
            run.stage("Достаю звук…")
            wav = os.path.join(tempfile.mkdtemp(prefix="studio_"), "audio.wav")
            _ff.extract_audio(source, wav, 16000,
                              progress=lambda value, note: run.progress(value * 0.1),
                              total=duration)
            run.stage("Распознаю речь…")
            model_obj = _asr.load_model(model, "auto", "default", log=run.log)
            result = _asr.transcribe(
                wav, model_obj, language=language, beam_size=5, vad=True,
                vad_min_silence_ms=400, condition_on_previous_text=False,
                duration=duration,
                progress=lambda value, note: run.progress(0.1 + value * 0.9, note),
                log=run.log, cancel=lambda: run.cancelled)
            _asr.save_cache(source, key, result)
            try:
                os.remove(wav)
            except OSError:
                pass
            return result

        def done(kind, payload):
            self._refresh_transcript()
            if kind == "done" and payload is not None:
                self._say("Расшифровка готова: сегментов %d" % len(payload.segments))

        self._go(work, done)

    def _need_source(self):
        path = self.source.get().strip()
        if path and os.path.isfile(path):
            return True
        messagebox.showinfo("Источник", "Сначала выберите файл стрима.", parent=self)
        return False

    def _go(self, work, done):
        if self.runner.busy():
            messagebox.showinfo("Занято", "Дождитесь окончания текущей работы.",
                                parent=self)
            return
        self.btn_stop.set_enabled(True)
        self.btn_build.set_enabled(False)
        self.progress.configure(value=0)

        def wrapped(kind, payload):
            self.btn_stop.set_enabled(False)
            self.btn_build.set_enabled(True)
            self.lbl_stage.configure(text="готов")
            if kind == "error":
                self._say("ОШИБКА: %s" % payload)
                messagebox.showerror("Ошибка", str(payload), parent=self)
            done(kind, payload)

        self.runner.start(work, wrapped)

    def _on_event(self, kind, payload):
        if kind in ("done", "error"):
            self._finished(kind)
        if kind == "log":
            self._say(payload)
        elif kind == "stage":
            self.lbl_stage.configure(text=payload)
        elif kind == "progress":
            value, note = payload
            share = max(0.0, min(1.0, value))
            self.progress.configure(value=share * 1000)
            self._show_share(share)
            if note:
                self.lbl_stage.configure(text="%s  %s" % (self.lbl_stage["text"].split("  ")[0], note))

    def _show_share(self, share=None):
        if share is None or not self.runner.busy():
            self.title(TITLE)
            return
        self.title("%s — %d%%" % (TITLE, int(share * 100)))

    def _finished(self, kind):
        self._show_share(None)
        if self.runner.started <= 0:
            return
        spent = time.time() - self.runner.started
        if spent < LONG_TASK or _ui.watching(self):
            return
        _ui.attention(self)
        self._say("Работа закончена (%d мин), окно мигнуло в панели задач."
                  % max(1, int(spent // 60)))

    def _say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text).rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _remember(self):
        self.cfg.update({
            "source": self.source.get(), "model": self.model.get(),
            "language": self.language.get(), "outro": self.v_outro.get(),
            "music": self.v_music.get(), "outdir": self.v_outdir.get(),
            "music_start": self.v_music_start.get(),
            "music_volume": self.v_vol.get(), "fade": self.v_fade.get(),
            "blur": self.v_blur.get(), "crf": self.v_crf.get(),
            "gpu": bool(self.v_gpu.get()), "censor": bool(self.v_censor.get()),
            "backend": self.v_backend.get(),
            "compare": bool(self.v_compare.get()),
            "keep_days": keep_days(self.v_keep.get()),
            "quality": self.v_quality.get(),
            "dldir": self.cfg.get("dldir", ""),
            "live_channel": self.v_live.get().strip(),
            "live_watch": bool(self.v_live_on.get()),
            "fine_open": bool(self.fine.opened),
            "local_model": self._model_names().get("ollama", ""),
            "openai_model": self._model_names().get("openai-compat", ""),
            "openai_url": self.v_openai_url.get().strip(),
            "openai_key": self.v_openai_key.get().strip(),
        })
        if self.uploader is not None:
            self.uploader.remember(self.cfg)
        save_settings(self.cfg)

    def _close(self):
        if self.tray is not None:              # фоновый режим: окно прячется, трей остаётся
            self._remember()
            self.withdraw()
            if not getattr(self, "_hid_once", False):
                self._hid_once = True
                self.tray.notify("Студия свёрнута в трей и следит за эфиром. "
                                 "Выход — в меню значка.")
            return
        self._quit()

    def _quit(self):
        if self.live_watcher is not None and self.live_watcher.current is not None:
            if not messagebox.askyesno("Идёт запись эфира", "Студия пишет звук эфира. "
                                       "Закрыть и остановить запись?", parent=self):
                return
        self._remember()
        if self.live_watcher is not None:
            self.live_watcher.stop()
            self.live_watcher = None
        if self.editor is not None:
            self.editor.shutdown()
        if self.rating is not None:
            self.rating.shutdown()
        if self.cleaner is not None:
            self.cleaner.shutdown()
        if self.uploader is not None:
            self.uploader.stop()
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        self.destroy()


class _Launch:
    """Режимы запуска: трей, анализ стрима, учёба по просмотрам."""

    # -------------------------------------------------------- фон

    def _new_tray(self):
        from . import tray as _tray

        self.tray = _tray.Tray(recording=lambda: self.live_watcher is not None
                               and self.live_watcher.current is not None,
                               watching=lambda: self.live_watcher is not None)
        self.tray.start()
        self._tray_pump()

    def start_passive(self):
        self.v_live.set(self.cfg.get("live_channel") or self.v_live.get())
        if (self.live_watcher is None
                or self.live_watcher.channel != _live.channel_of(self.v_live.get())):
            self.v_live_on.set(True)
            self._live_toggle()
        if self.live_watcher is None:            # не хватило канала или папки — показать почему
            self.show()
            messagebox.showwarning("Фоновый режим", "Следить за эфиром не получилось: %s"
                                   % self.lbl_live.cget("text"), parent=self)
            return
        self._new_tray()
        self.withdraw()
        self.tray.notify("Слежу за эфиром %s. Как начнётся — напишу и начну писать звук."
                         % self.live_watcher.channel)
        self._remember()

    def _tray_pump(self):
        if self.tray is None:
            return
        for action in self.tray.take():
            if action == "show":
                self.show()
            elif action == "watch":
                self.v_live_on.set(not self.v_live_on.get())
                self._live_toggle()
                self.tray.notify("Слежу за эфиром." if self.live_watcher else
                                 "Больше не слежу за эфиром.")
            elif action == "quit":
                self.tray.stop()
                self.tray = None
                self._quit()
                try:
                    if self.winfo_exists():      # передумали закрывать — трей обратно
                        self._new_tray()
                except tk.TclError:
                    pass
                return
        try:
            self.after(250, self._tray_pump)
        except tk.TclError:
            pass

    def show(self):
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except tk.TclError:
            pass

    # -------------------------------------------------------- работа

    def start_active(self, choice):
        self.show()
        action = choice.get("action")
        if action == "open":
            self.source.set(choice["path"])
            self._refresh_source()
            self._remember()
            if choice.get("analyze"):
                self.after(300, self._analyze)
        elif action == "download":
            self.source.set(choice["link"])
            self.then_analyze = bool(choice.get("analyze"))
            self.after(300, self._grab_stream)

    def _analyze(self):
        """Найти моменты в стриме: по ним видно, сколько выйдет тиктоков и роликов."""
        self._switch("clip")
        self._switch_clip("search")
        self._estimate_line("Ищу моменты — потом скажу, сколько примерно выйдет "
                            "тиктоков и роликов…")
        self._scan_sense()

    def _estimate_line(self, text):
        """Строка прогноза под источником — появляется, только когда есть что сказать."""
        self.lbl_estimate.configure(text=text)
        if not self.lbl_estimate.winfo_ismapped():
            self.lbl_estimate.pack(anchor="w", pady=(2, 0), after=self.lbl_info)

    def _show_estimate(self, found):
        text = _launch.estimate_text(found)
        self._estimate_line(text)
        self._say(text + ". Тиктоки — хорошие моменты по отдельности, ролики — куски стрима, "
                  "где такие моменты идут один за другим.")
        if self.tray is not None:
            self.tray.notify(text)

    # -------------------------------------------------------- учёба

    def start_learning(self):
        self.after(15000, self._learn_tick)

    def _learn_engine(self):
        try:
            from clipper.backends import get_backend

            cfg = self._clipper_config()
            backend = get_backend(cfg)
        except Exception:
            return None
        if not callable(getattr(backend, "complete", None)):
            return None
        return backend, cfg

    def _learn_tick(self):
        try:
            self._learn_said()
            busy = self.learning is not None and self.learning.is_alive()
            if not busy and _launch.learn_due(self.cfg):
                self._learn_now()
                busy = True
            self.after(2000 if busy else 30 * 60 * 1000, self._learn_tick)
        except tk.TclError:
            pass

    def _learn_now(self):
        profile = _pro.active()
        if not profile:
            return
        from core import upload as _up

        engine = self._learn_engine()
        db = _up.db_for(profile)
        cfg = dict(self.cfg)
        self.cfg["learned_at"] = time.time()
        save_settings(self.cfg)
        say = self.learn_box.put

        def work():
            try:
                report = _launch.learn(profile, cfg, db, engine=engine)
            except Exception as error:
                say("Учёба по просмотрам не вышла: %s" % str(error).splitlines()[0][:150])
                return
            parts = []
            if report["channels"]:
                parts.append("обновлены просмотры каналов: %d" % report["channels"])
            if report["rules"]:
                parts.append("правила вкуса пересобраны по %d клипам"
                             % report["rules"].get("based_on", 0))
            elif report["skipped"]:
                parts.append("правила не пересобраны — %s" % report["skipped"])
            say("Учёба по просмотрам: " + ("; ".join(parts) or "нового нет") + ".")
            for rule in (report["rules"] or {}).get("rules") or ():
                say("  • " + rule)

        self.learning = threading.Thread(target=work, daemon=True)
        self.learning.start()

    def _learn_said(self):
        while True:
            try:
                self._say(self.learn_box.get_nowait())
            except queue.Empty:
                return


for _name, _value in list(vars(_Launch).items()):
    if not _name.startswith("__"):
        setattr(Studio, _name, _value)


class _Relay:
    def __init__(self, run):
        self.run = run
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.run.log(line.rstrip())
        return len(text)

    def flush(self):
        if self.buffer.strip():
            self.run.log(self.buffer.rstrip())
        self.buffer = ""


def _number(text, fallback):
    try:
        return float(str(text).replace(",", "."))
    except (TypeError, ValueError):
        return fallback


def _safe_name(text):
    text = (text or "").strip()
    if not text:
        return ""
    keep = []
    for char in text:
        keep.append(char if (char.isalnum() or char in " -_") else "_")
    return "_".join("".join(keep).split())[:60]


def _same_file(path, target):
    """Копия ролика без мата: жёсткая ссылка, если можно, иначе обычная копия."""
    try:
        os.link(path, target)
    except OSError:
        import shutil
        shutil.copy2(path, target)
    return target


def _unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    index = 2
    while os.path.exists("%s_%d%s" % (base, index, ext)):
        index += 1
    return "%s_%d%s" % (base, index, ext)


def main():
    from .launcher import choose
    from .welcome import greet

    single = _launch.Single()
    if not single.claim():                     # Студия уже открыта — разбудить её
        single.wake_other()
        return
    _pro.migrate(report=print)
    app = Studio()
    app.withdraw()
    wake = queue.Queue()
    single.on_show = lambda: wake.put(1)

    def woken():
        try:
            while True:
                wake.get_nowait()
                app.show()
        except queue.Empty:
            pass
        try:
            app.after(500, woken)
        except tk.TclError:
            pass

    if not greet(app):
        app.destroy()
        single.release()
        return
    choice = choose(app, app.cfg)
    if choice is None:
        app._quit()
        single.release()
        return
    app.after(500, woken)
    if choice["mode"] == _launch.PASSIVE:
        app.start_passive()
    else:
        app.start_active(choice)
    app.start_learning()
    app.mainloop()
    single.release()
