from __future__ import annotations

import os
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from core import donations as _don
from core import ffmpeg as _ff
from core import profiles as _pro
from core import streamcheck as _check
from core import timecode as _tc
from core import ui as _ui
from core import voiceid as _voice

from . import theme

LISTEN_PAD = 0.0
LISTEN_AFTER = 6.0


def play(path):
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except Exception:
        try:
            os.startfile(str(path))
            return True
        except OSError:
            return False


def stop_playing():
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def cut_wav(source, start, end, target=None):
    target = target or os.path.join(tempfile.gettempdir(), "astra_listen.wav")
    code = subprocess.run(
        [_ff.find("ffmpeg"), "-v", "error", "-y", "-ss", "%.3f" % max(0.0, start),
         "-t", "%.3f" % max(0.1, end - start), "-i", str(source), "-vn", "-ac", "1",
         "-ar", str(_don.RATE), "-c:a", "pcm_s16le", target],
        capture_output=True, **_ff._NOWIN).returncode
    if code != 0 or not os.path.isfile(target):
        raise RuntimeError("не получилось вырезать звук из стрима")
    return target


def samples_of(source, start, end):
    return _don.read_wav(cut_wav(source, start, end))


class Window(tk.Toplevel):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.profile = _pro.active()
        self.title("Голос и донаты")
        self.configure(background=_ui.BG)
        theme.apply(self)
        _ui.dark_frame(self)
        self.found = []
        self.v_start = tk.StringVar()
        self.v_end = tk.StringVar()
        self.v_label = tk.StringVar()
        self._build()
        self.refresh()
        self.protocol("WM_DELETE_WINDOW", self._leave)
        self.transient(studio)

    def _card(self, title):
        outer = ttk.Frame(self, style="Panel.TFrame")
        outer.pack(fill="x", padx=16, pady=(0, 10))
        inner = ttk.Frame(outer, style="Panel.TFrame")
        inner.pack(fill="x", padx=14, pady=12)
        ttk.Label(inner, text=title, style="PanelDim.TLabel").pack(anchor="w", pady=(0, 8))
        return inner

    def _note(self, master, text):
        label = ttk.Label(master, text=text, style="PanelDim.TLabel", wraplength=640,
                          justify="left")
        label.pack(anchor="w", pady=(6, 0))
        return label

    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=16, pady=(14, 10))
        ttk.Label(head, text="Голос и донаты", style="Head.TLabel").pack(side="left")
        self.lbl_head = ttk.Label(head, text="")
        self.lbl_head.pack(side="left", padx=(12, 0))

        card = self._card("ГОЛОС СТРИМЕРА")
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x")
        self.lbl_voice = ttk.Label(row, text="", style="PanelDim.TLabel")
        self.lbl_voice.pack(side="left")
        theme.Button(row, "Собрать по этому стриму", self._build_voice).pack(side="right")
        self._note(card, "Программа слушает стрим и берёт самый частый голос — это "
                         "стример. По нему видно, когда он отошёл, даже если робот "
                         "в это время читает чат. Лучше брать стрим, где он много "
                         "говорит сам.")

        card = self._card("ЗВУКИ ДОНАТОВ — у каждой суммы свой")
        self.sounds = ttk.Treeview(card, columns=("label", "length", "source"),
                                   show="headings", height=4)
        for name, title, width in (("label", "подпись", 140), ("length", "длина", 80),
                                   ("source", "откуда", 380)):
            self.sounds.heading(name, text=title)
            self.sounds.column(name, width=width, anchor="w")
        self.sounds.pack(fill="x")
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        theme.Button(row, "▶ Прослушать", self._listen_sound).pack(side="left")
        theme.Button(row, "Удалить", self._remove_sound).pack(side="left", padx=(8, 0))

        add = ttk.Frame(card, style="Panel.TFrame")
        add.pack(fill="x", pady=(12, 0))
        for label, var, width in (("начало", self.v_start, 11), ("конец", self.v_end, 11),
                                  ("подпись", self.v_label, 12)):
            ttk.Label(add, text=label, style="PanelDim.TLabel").pack(side="left")
            ttk.Entry(add, textvariable=var, width=width).pack(side="left", padx=(6, 10))
        theme.Button(add, "▶", self._listen_range, width=3).pack(side="left")
        theme.Button(add, "Добавить звук", self._add_sound).pack(side="left", padx=(8, 0))
        self._note(card, "Найдите в стриме один донат, впишите, где начинается и "
                         "кончается его звук (только звук, без голоса робота), "
                         "подпишите сумму. Остальные звуки программа предложит сама.")

        card = self._card("НАЙТИ ВСЕ ДОНАТЫ В ЭТОМ СТРИМЕ")
        self.hits = ttk.Treeview(card, columns=("time", "what"), show="headings",
                                 height=7)
        self.hits.heading("time", text="время")
        self.hits.heading("what", text="что")
        self.hits.column("time", width=110, anchor="w")
        self.hits.column("what", width=490, anchor="w")
        self.hits.pack(fill="x")
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        theme.Button(row, "Искать", self._scan, primary=True).pack(side="left")
        theme.Button(row, "▶ Прослушать", self._listen_hit).pack(side="left", padx=(8, 0))
        theme.Button(row, "Это новый звук доната", self._accept_hit).pack(
            side="left", padx=(8, 0))
        theme.Button(row, "Не донат", self._reject_hit).pack(side="left", padx=(8, 0))
        self._note(card, "«новый звук?» — место, где говорит робот, а знакомого звука "
                         "перед ним нет. Скорее всего, это донат другой суммы: "
                         "прослушайте и, если так, добавьте.")
        self.lbl_robot = self._note(card, "")

        card = self._card("ЧТО ДЕЛАТЬ ПРИ НАРЕЗКЕ")
        self.v_donations = tk.BooleanVar()
        self.v_away = tk.BooleanVar()
        ttk.Checkbutton(card, text="убирать донаты не по теме клипа (решает модель "
                                   "из «По смыслу»)", variable=self.v_donations,
                        command=self._save_flags).pack(anchor="w")
        ttk.Checkbutton(card, text="убирать места, где стример отошёл",
                        variable=self.v_away, command=self._save_flags).pack(anchor="w")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        theme.Button(bottom, "Закрыть", self._leave).pack(side="right")

    def refresh(self):
        self.profile = _pro.active()
        if not self.profile:
            self.lbl_head.configure(text="нет профиля")
            return
        self.lbl_head.configure(text="профиль: %s" % self.profile["name"])
        has_voice = _don.voice_ref(self.profile, _don.VOICE_KEY) is not None
        engine = "" if _voice.available() else " — нет sherpa-onnx, голос не различить"
        self.lbl_voice.configure(text=("голос записан" if has_voice else "голоса нет")
                                 + engine)
        robot = _don.voice_ref(self.profile, _don.ROBOT_KEY) is not None
        self.lbl_robot.configure(text="голос робота: %s" % (
            "запомнен" if robot else "запомнится сам при первом поиске донатов"))
        self.sounds.delete(*self.sounds.get_children())
        for entry in _don.sounds(self.profile):
            origin = entry.get("source") or ""
            if origin:
                origin += " @ " + _tc.format_hms(entry.get("start") or 0.0)
            self.sounds.insert("", "end", values=(entry.get("label") or "—",
                                                  "%.1f с" % entry.get("length", 0.0),
                                                  origin))
        self.v_donations.set(bool(self.profile.get(_check.CHECK_DONATIONS)))
        self.v_away.set(bool(self.profile.get(_check.CHECK_AWAY)))
        self._redraw_hits()

    def _redraw_hits(self):
        self.hits.delete(*self.hits.get_children())
        for item in self.found:
            if item.get("new"):
                what = "новый звук? говорит робот, знакомого звука нет"
            else:
                what = "донат %s (похожесть %.2f)" % (item.get("label") or "без подписи",
                                                     item.get("score") or 0.0)
            self.hits.insert("", "end", values=(_tc.format_hms(item["start"]), what))

    def _source(self):
        path = self.studio.source.get().strip()
        if path and os.path.isfile(path):
            return path
        messagebox.showinfo("Источник", "Сначала выберите стрим в главном окне.",
                            parent=self)
        return None

    def _context(self, source, run):
        return _check.Context(source, self.profile, log=run.log,
                              progress=run.progress, cancel=lambda: run.cancelled)

    def _save(self):
        _pro.save(self.profile)

    def _save_flags(self):
        if not self.profile:
            return
        self.profile[_check.CHECK_DONATIONS] = bool(self.v_donations.get())
        self.profile[_check.CHECK_AWAY] = bool(self.v_away.get())
        self._save()

    def _build_voice(self):
        source = self._source()
        if not source or not self.profile:
            return
        profile = self.profile

        def work(run):
            run.stage("Слушаю голоса…")
            ref, share = _check.build_voice(self._context(source, run))
            _don.set_voice_ref(profile, ref, _don.VOICE_KEY)
            _pro.save(profile)
            return share

        def done(kind, share):
            if kind == "done":
                self.studio._say("голос стримера записан: он говорит в %d%% речи стрима"
                                 % int(100 * share))
            self.refresh()

        self.studio._go(work, done)

    def _range(self):
        try:
            start = _tc.require_time(self.v_start.get())
            end = _tc.require_time(self.v_end.get())
        except ValueError as error:
            messagebox.showerror("Время", str(error), parent=self)
            return None
        if end <= start or end - start > 15.0:
            messagebox.showerror("Время", "Звук доната — от доли секунды до 15 с, "
                                          "конец позже начала.", parent=self)
            return None
        return start, end

    def _listen_range(self):
        source = self._source()
        bounds = self._range()
        if not source or not bounds:
            return
        try:
            play(cut_wav(source, bounds[0], bounds[1]))
        except RuntimeError as error:
            messagebox.showerror("Звук", str(error), parent=self)

    def _add_sound(self, bounds=None, label=None):
        source = self._source()
        bounds = bounds or self._range()
        if not source or not bounds or not self.profile:
            return
        try:
            samples = samples_of(source, bounds[0], bounds[1])
        except RuntimeError as error:
            messagebox.showerror("Звук", str(error), parent=self)
            return
        _don.add_sound(self.profile, samples,
                       label=self.v_label.get() if label is None else label,
                       source=source, start=bounds[0], end=bounds[1])
        self._save()
        self.v_start.set("")
        self.v_end.set("")
        self.v_label.set("")
        self.studio._say("звук доната добавлен в профиль")
        self.refresh()

    def _picked_sound(self):
        chosen = self.sounds.selection()
        entries = _don.sounds(self.profile) if self.profile else []
        if not chosen:
            return None
        index = self.sounds.index(chosen[0])
        return entries[index] if index < len(entries) else None

    def _listen_sound(self):
        entry = self._picked_sound()
        if entry:
            play(entry["path"])

    def _remove_sound(self):
        entry = self._picked_sound()
        if not entry:
            return
        if not messagebox.askyesno("Удалить", "Удалить звук «%s»?"
                                   % (entry.get("label") or entry["file"]), parent=self):
            return
        _don.remove_sound(self.profile, entry["file"])
        self._save()
        self.refresh()

    def _scan(self):
        source = self._source()
        if not source or not self.profile:
            return
        if not _don.sounds(self.profile):
            messagebox.showinfo("Звуки", "Сначала добавьте хотя бы один звук доната.",
                                parent=self)
            return
        profile = self.profile

        def work(run):
            context = self._context(source, run)
            run.stage("Ищу звуки донатов…")
            events = _check.find_donations(context)
            robot = _check.learn_robot(context, events)
            if robot is not None:
                _don.set_voice_ref(profile, robot, _don.ROBOT_KEY)
                _pro.save(profile)
                run.log("голос робота запомнен по первому донату")
            run.stage("Ищу донаты с незнакомым звуком…")
            fresh = _check.suggest_sounds(context, events)
            return events, fresh

        def done(kind, payload):
            if kind != "done":
                return
            events, fresh = payload
            self.found = sorted(list(events) + [dict(item, new=True) for item in fresh],
                                key=lambda item: item["start"])
            self.studio._say("донатов по знакомым звукам: %d, похожих на новый звук: %d"
                             % (len(events), len(fresh)))
            self.refresh()

        self.studio._go(work, done)

    def _picked_hit(self):
        chosen = self.hits.selection()
        if not chosen:
            return None, None
        index = self.hits.index(chosen[0])
        return (index, self.found[index]) if index < len(self.found) else (None, None)

    def _listen_hit(self):
        source = self._source()
        _index, item = self._picked_hit()
        if not source or not item:
            return
        end = item.get("robot_end") or item["end"] + LISTEN_AFTER
        try:
            play(cut_wav(source, item["start"] - LISTEN_PAD, end))
        except RuntimeError as error:
            messagebox.showerror("Звук", str(error), parent=self)

    def _accept_hit(self):
        index, item = self._picked_hit()
        if item is None:
            return
        if not item.get("new"):
            messagebox.showinfo("Звук", "Этот звук уже в профиле.", parent=self)
            return
        label = simpledialog.askstring(
            "Новый звук доната", "Подпись (например, сумма). Звук взят за %.0f с до "
            "голоса робота — его можно поправить в полях ниже и добавить вручную."
            % (item["end"] - item["start"]), parent=self)
        if label is None:
            return
        self._add_sound((item["start"], item["end"]), label=label)
        del self.found[index]
        self._redraw_hits()

    def _reject_hit(self):
        index, _item = self._picked_hit()
        if index is None:
            return
        del self.found[index]
        self._redraw_hits()

    def _leave(self):
        stop_playing()
        self.destroy()


def open_window(studio):
    window = Window(studio)
    return window
