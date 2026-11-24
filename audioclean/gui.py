from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core import asr as _asr
from core import ui

from . import ffmpeg as FF
from . import pipeline as P
from .config import Settings, WORDLISTS, load_saved, save_current

VIDEO_TYPES = [("Видео и звук", "*.mp4 *.mkv *.mov *.avi *.ts *.webm *.m4v *.mp3 *.wav *.m4a"),
               ("Все файлы", "*.*")]


class Cleaner(tk.Frame):
    def __init__(self, master, chrome=True, background=None):
        super().__init__(master, background=background or ui.BG)
        self.chrome = chrome

        self.s = load_saved()
        self.result: Optional[P.Result] = None
        self._q: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._busy = False

        self._build_vars()
        self._build_ui()
        self.alive = True
        self.pump_job = self.after(100, self._pump)
        if not FF.have_ffmpeg():
            self.log("ВНИМАНИЕ: не найден ffmpeg. Поставьте его и добавьте в PATH.")

    # ------------------------------------------------------------------ vars
    def _build_vars(self) -> None:
        s = self.s
        self.v_video = tk.StringVar(value="")
        self.v_out = tk.StringVar(value="")
        self.v_ru = tk.BooleanVar(value=s.use_ru)
        self.v_en = tk.BooleanVar(value=s.use_en)
        self.v_plat = tk.BooleanVar(value=s.use_platform)
        self.v_extra = tk.StringVar(value=" ".join(s.extra_words))
        self.v_mode = tk.StringVar(value=s.mode)
        self.v_pad = tk.DoubleVar(value=s.pad_before)
        self.v_keep = tk.BooleanVar(value=s.keep_edges)
        self.v_edge = tk.DoubleVar(value=s.keep_head)
        self.v_hz = tk.IntVar(value=s.beep_hz)
        self.v_model = tk.StringVar(value=s.model)
        self.v_lang = tk.StringVar(value=s.language)
        self.v_device = tk.StringVar(value=s.device)
        self.v_status = tk.StringVar(value="Готов к работе")
        self.v_total = tk.StringVar(value="")

    def collect(self) -> Settings:
        s = self.s
        s.video = self.v_video.get().strip()
        s.output = self.v_out.get().strip()
        s.use_ru = self.v_ru.get()
        s.use_en = self.v_en.get()
        s.use_platform = self.v_plat.get()
        s.extra_words = [w for w in self.v_extra.get().split() if w]
        s.mode = self.v_mode.get()
        s.pad_before = float(self.v_pad.get())
        s.pad_after = float(self.v_pad.get()) + 0.03
        s.keep_edges = self.v_keep.get()
        s.keep_head = s.keep_tail = float(self.v_edge.get())
        s.beep_hz = int(self.v_hz.get())
        s.model = self.v_model.get()
        s.language = self.v_lang.get().strip()
        s.device = self.v_device.get()
        return s

    # -------------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        files = ttk.LabelFrame(self, text="ФАЙЛ", style="Panel.TLabelframe")
        files.pack(fill="x", pady=(0, 10))
        files.columnconfigure(1, weight=1)
        ttk.Label(files, style="PanelDim.TLabel", text="Видео или звук:").grid(row=0, column=0, sticky="w")
        ttk.Entry(files, textvariable=self.v_video).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(files, text="Обзор", width=8,
                   command=self.pick_video).grid(row=0, column=2)
        ttk.Label(files, style="PanelDim.TLabel", text="Куда сохранить:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(files, textvariable=self.v_out).grid(row=1, column=1, sticky="ew",
                                                       padx=4, pady=(4, 0))
        ttk.Button(files, text="Обзор", width=8,
                   command=self.pick_output).grid(row=1, column=2, pady=(4, 0))

        opts = ttk.Frame(self)
        opts.pack(fill="x")
        for i in range(3):
            opts.columnconfigure(i, weight=1)

        what = ttk.LabelFrame(opts, text="ЧТО ГЛУШИТЬ", style="Panel.TLabelframe")
        what.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ttk.Checkbutton(what, text="Русский мат", variable=self.v_ru).pack(anchor="w")
        ttk.Checkbutton(what, text="Английская брань", variable=self.v_en).pack(anchor="w")
        ttk.Checkbutton(what, text="Слова, за которые режут показы",
                        variable=self.v_plat).pack(anchor="w")
        ttk.Label(what, text="(TikTok, YouTube Shorts)",
                  style="PanelDim.TLabel").pack(anchor="w", padx=(20, 0))
        ttk.Label(what, style="PanelDim.TLabel", text="Свои слова (через пробел):").pack(anchor="w", pady=(4, 0))
        ttk.Entry(what, textvariable=self.v_extra).pack(fill="x")
        ttk.Button(what, text="Открыть словари",
                   command=self.open_wordlists).pack(anchor="w", pady=(4, 0))

        how = ttk.LabelFrame(opts, text="КАК ГЛУШИТЬ", style="Panel.TLabelframe")
        how.grid(row=0, column=1, sticky="nsew", padx=5)
        ttk.Radiobutton(how, text="Тишиной", value="silence",
                        variable=self.v_mode).pack(anchor="w")
        ttk.Radiobutton(how, text="Писком", value="beep",
                        variable=self.v_mode).pack(anchor="w")
        row = ttk.Frame(how, style="Panel.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, style="PanelDim.TLabel", text="частота писка, Гц:").pack(side="left")
        ttk.Spinbox(row, from_=400, to=3000, increment=100, width=6,
                    textvariable=self.v_hz).pack(side="left", padx=4)
        ttk.Separator(how, orient="horizontal").pack(fill="x", pady=6)
        ttk.Checkbutton(how, text="Слышно начало и конец слова",
                        variable=self.v_keep,
                        command=self._sync_edges).pack(anchor="w")
        row = ttk.Frame(how, style="Panel.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, style="PanelDim.TLabel", text="слышно по краям, с:").pack(side="left")
        self.sp_edge = ttk.Spinbox(row, from_=0.0, to=0.30, increment=0.01, width=6,
                                   textvariable=self.v_edge)
        self.sp_edge.pack(side="left", padx=4)
        row = ttk.Frame(how, style="Panel.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, style="PanelDim.TLabel", text="запас вокруг слова, с:").pack(side="left")
        self.sp_pad = ttk.Spinbox(row, from_=0.0, to=0.6, increment=0.02, width=6,
                                  textvariable=self.v_pad)
        self.sp_pad.pack(side="left", padx=4)
        ttk.Label(how, text="Видео не пережимается,\nменяется только звук.",
                  style="PanelOk.TLabel").pack(anchor="w", pady=(6, 0))
        self._sync_edges()

        rec = ttk.LabelFrame(opts, text="РАСПОЗНАВАНИЕ РЕЧИ", style="Panel.TLabelframe")
        rec.grid(row=0, column=2, sticky="nsew", padx=(5, 0))
        row = ttk.Frame(rec, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, style="PanelDim.TLabel", text="модель:").pack(side="left")
        ttk.Combobox(row, textvariable=self.v_model, width=14, state="readonly",
                     values=list(_asr.MODELS)).pack(side="left", padx=4)
        row = ttk.Frame(rec, style="Panel.TFrame")
        row.pack(fill="x", pady=4)
        ttk.Label(row, style="PanelDim.TLabel", text="язык:").pack(side="left")
        ttk.Combobox(row, textvariable=self.v_lang, width=6,
                     values=["ru", "en", ""]).pack(side="left", padx=4)
        ttk.Label(row, style="PanelDim.TLabel", text="считать на:").pack(side="left")
        ttk.Combobox(row, textvariable=self.v_device, width=7, state="readonly",
                     values=["auto", "cpu", "cuda"]).pack(side="left", padx=4)
        ttk.Label(rec, text="Крупнее модель — точнее и медленнее.\n"
                            "Распознанный текст сохраняется рядом\n"
                            "с файлом, второй раз считать не надо.",
                  style="PanelDim.TLabel").pack(anchor="w")

        run = ttk.Frame(self)
        run.pack(fill="x", pady=10)
        self.b_find = ttk.Button(run, text="1. Найти слова", command=self.on_find)
        self.b_find.pack(side="left")
        self.b_again = ttk.Button(run, text="Пересчитать по словарям",
                                  command=self.on_rescan, state="disabled")
        self.b_again.pack(side="left", padx=4)
        self.b_clean = ttk.Button(run, text="2. Заглушить и сохранить",
                                  command=self.on_clean, state="disabled")
        self.b_clean.pack(side="left")
        self.b_stop = ttk.Button(run, text="Стоп", command=self.on_stop, state="disabled")
        self.b_stop.pack(side="left", padx=4)
        self.pbar = ttk.Progressbar(run, mode="determinate", maximum=1000)
        self.pbar.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(run, textvariable=self.v_status, width=30, style="Dim.TLabel").pack(side="left")

        logf = ttk.LabelFrame(self, text="ЖУРНАЛ", style="Panel.TLabelframe")
        logf.pack(side="bottom", fill="x", pady=(10, 0))
        self.txt = tk.Text(logf, height=5, wrap="word", background=ui.PANEL2,
                           foreground=ui.TEXT, insertbackground=ui.TEXT,
                           relief="flat", borderwidth=6, highlightthickness=0)
        sb2 = ttk.Scrollbar(logf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)

        table = ttk.LabelFrame(self, text="НАЙДЕННЫЕ СЛОВА", style="Panel.TLabelframe")
        table.pack(fill="both", expand=True)
        bar = ttk.Frame(table, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 4))
        ttk.Button(bar, text="Вкл/выкл (пробел)", width=18,
                   command=self.toggle).pack(side="left")
        ttk.Button(bar, text="Послушать", width=12,
                   command=self.play).pack(side="left", padx=4)
        ttk.Button(bar, text="Выключить все такие же", width=24,
                   command=self.disable_rule).pack(side="left")
        ttk.Button(bar, text="Включить все", width=14,
                   command=lambda: self.set_all(True)).pack(side="left", padx=4)
        ttk.Button(bar, text="Выключить все", width=14,
                   command=lambda: self.set_all(False)).pack(side="left")
        ttk.Label(bar, textvariable=self.v_total,
                  style="PanelOk.TLabel").pack(side="right", padx=6)

        cols = ("on", "time", "word", "rule", "prob", "context")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=10)
        for c, t, w, anchor in (("on", "вкл", 44, "center"), ("time", "время", 90, "center"),
                                ("word", "слово", 160, "w"), ("rule", "правило", 130, "w"),
                                ("prob", "точн.", 56, "center"),
                                ("context", "в контексте", 500, "w")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "context"))
        sb = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("off", foreground=ui.DIM)
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<Double-1>", lambda e: self.play())
        self.tree.bind("<space>", lambda e: self.toggle())

    def _sync_edges(self) -> None:
        keep = self.v_keep.get()
        self.sp_edge.configure(state="normal" if keep else "disabled")
        self.sp_pad.configure(state="disabled" if keep else "normal")
        if self.result:
            self.update_total()

    # ----------------------------------------------------------------- файлы
    def pick_video(self) -> None:
        p = filedialog.askopenfilename(title="Выберите видео", filetypes=VIDEO_TYPES)
        if p:
            self.set_source(p)

    def set_source(self, path: str) -> None:
        self.v_video.set(path)
        self.v_out.set(P.default_output(path))
        self.result = None
        self.b_clean.configure(state="disabled")
        self.b_again.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.v_total.set("")
        try:
            info = FF.probe(path)
            self.log("Открыт %s — %s, звук: %s"
                     % (Path(path).name, P.hms(info.duration),
                        "есть" if info.has_audio else "НЕТ"))
            if not info.has_audio:
                messagebox.showwarning("Нет звука", "В этом файле нет звуковой дорожки.")
        except Exception as e:
            messagebox.showerror("Не читается", str(e))

    def pick_output(self) -> None:
        ext = Path(self.v_video.get().strip()).suffix or ".mp4"
        p = filedialog.asksaveasfilename(title="Куда сохранить", defaultextension=ext,
                                         filetypes=VIDEO_TYPES)
        if p:
            self.v_out.set(p)

    def open_wordlists(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(WORDLISTS))
            else:
                self.log("Словари лежат в " + str(WORDLISTS))
        except Exception as e:
            messagebox.showinfo("Словари", "Папка со словарями:\n%s\n\n%s" % (WORDLISTS, e))

    # --------------------------------------------------------------- таблица
    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.result:
            self.v_total.set("")
            return
        for i, f in enumerate(self.result.found):
            self.tree.insert("", "end", iid=str(i), tags=() if f.enabled else ("off",),
                             values=("V" if f.enabled else "-", P.hms(f.start),
                                     f.text, f.rule,
                                     "≈" if f.prob <= 0 else "%.2f" % f.prob,
                                     f.context))
        self.update_total()

    def update_total(self) -> None:
        if not self.result:
            self.v_total.set("")
            return
        s = self.collect()
        spans = P.spans_of(self.result.found, s, self.result.duration)
        on = sum(1 for f in self.result.found if f.enabled)
        self.v_total.set("включено %d из %d, заглушим %d участков (%.1f с)"
                         % (on, len(self.result.found), len(spans),
                            sum(b - a for a, b in spans)))

    def _selected(self) -> List[int]:
        return [int(i) for i in self.tree.selection()]

    def _click(self, e) -> None:
        if self.tree.identify_region(e.x, e.y) == "cell" \
                and self.tree.identify_column(e.x) == "#1":
            iid = self.tree.identify_row(e.y)
            if iid and self.result:
                f = self.result.found[int(iid)]
                f.enabled = not f.enabled
                self.refresh()

    def toggle(self) -> None:
        if not self.result:
            return
        for i in self._selected():
            self.result.found[i].enabled = not self.result.found[i].enabled
        self.refresh()

    def set_all(self, value: bool) -> None:
        if not self.result:
            return
        for f in self.result.found:
            f.enabled = value
        self.refresh()

    def disable_rule(self) -> None:
        if not self.result:
            return
        rules = {self.result.found[i].rule for i in self._selected()}
        if not rules:
            messagebox.showinfo("Не выбрано", "Выберите в таблице строку с лишним словом.")
            return
        n = 0
        for f in self.result.found:
            if f.rule in rules and f.enabled:
                f.enabled = False
                n += 1
        self.log("Выключено по правилам %s: %d шт." % (", ".join(sorted(rules)), n))
        self.refresh()

    def play(self) -> None:
        sel = self._selected()
        video = self.v_video.get().strip()
        if not sel or not self.result or not video or not Path(video).exists():
            return
        f = self.result.found[sel[0]]
        if FF.play_range(video, max(0.0, f.start - 1.5), (f.end - f.start) + 3.0) is None:
            self.log("Не найден ffplay — послушать не получится.")

    # ---------------------------------------------------------------- работа
    def log(self, msg: str) -> None:
        self.txt.insert("end", str(msg).rstrip() + "\n")
        self.txt.see("end")

    def _busy_on(self, busy: bool) -> None:
        self._busy = busy
        self.b_find.configure(state="disabled" if busy else "normal")
        has = self.result is not None and not busy
        self.b_clean.configure(state="normal" if has else "disabled")
        self.b_again.configure(state="normal" if has else "disabled")
        self.b_stop.configure(state="normal" if busy else "disabled")

    def busy(self) -> bool:
        return self._busy

    def shutdown(self) -> None:
        self.alive = False
        self._cancel.set()
        if self.pump_job is not None:
            try:
                self.after_cancel(self.pump_job)
            except tk.TclError:
                pass
            self.pump_job = None

    def _pump(self) -> None:
        if not self.alive or not self.winfo_exists():
            return
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    p, note = payload
                    self.pbar["value"] = p * 1000
                    self.v_status.set(note[:36])
                elif kind == "result":
                    self.result = payload
                    self.refresh()
                elif kind == "done":
                    self._busy_on(False)
                    self.v_status.set(payload or "Готово")
                elif kind == "error":
                    self._busy_on(False)
                    self.v_status.set("Ошибка")
                    self.log(payload)
                    messagebox.showerror("Ошибка", payload.strip().split("\n")[-1][:500])
        except queue.Empty:
            pass
        self.pump_job = self.after(100, self._pump)

    def _run(self, fn) -> None:
        if self._busy:
            return
        self._cancel.clear()
        self._busy_on(True)
        self.pbar["value"] = 0

        def worker():
            try:
                fn()
            except Exception:
                self._q.put(("error", traceback.format_exc()))
        threading.Thread(target=worker, daemon=True).start()

    def on_stop(self) -> None:
        self._cancel.set()
        self.v_status.set("Останавливаю…")

    def on_find(self) -> None:
        s = self.collect()
        if not s.video or not Path(s.video).exists():
            messagebox.showwarning("Нет файла", "Сначала выберите видео.")
            return
        if not s.output:
            self.v_out.set(P.default_output(s.video))
            s.output = self.v_out.get()
        save_current(s)

        def job():
            res = P.analyze(s, progress=lambda p, n: self._q.put(("progress", (p, n))),
                            log=lambda m: self._q.put(("log", m)),
                            cancel=self._cancel.is_set)
            self._q.put(("result", res))
            self._q.put(("done", "Найдено слов: %d" % len(res.found)))
        self.log("--- Ищу слова ---")
        self._run(job)

    def on_rescan(self) -> None:
        if not self.result:
            return
        s = self.collect()
        save_current(s)
        self.result.found = P.find_words(self.result, s)
        self.refresh()
        self.log("Пересчитано по словарям: найдено %d" % len(self.result.found))

    def on_clean(self) -> None:
        if not self.result:
            return
        s = self.collect()
        out = s.output or P.default_output(s.video)
        if Path(out).exists() and not messagebox.askyesno(
                "Файл существует", "Перезаписать %s ?" % Path(out).name):
            return
        save_current(s)
        res = self.result

        def job():
            path = P.clean(s, res, out,
                           progress=lambda p, n: self._q.put(("progress", (p, n))),
                           log=lambda m: self._q.put(("log", m)),
                           cancel=self._cancel.is_set)
            self._q.put(("log", "Готово: " + path))
            self._q.put(("done", "Сохранено"))
        self.log("--- Пишу очищенный файл ---")
        self._run(job)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AudioClean — глушилка мата и запрещённых слов")
        self.geometry("1080x780")
        self.minsize(900, 640)
        ui.apply(self)
        self.cleaner = Cleaner(self)
        self.cleaner.pack(fill="both", expand=True, padx=8, pady=8)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        if self.cleaner.busy():
            if not messagebox.askokcancel("Работа не закончена",
                                          "Обработка идёт. Всё равно закрыть?"):
                return
        self.cleaner.shutdown()
        self.destroy()


def main() -> int:
    app = App()
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        app.cleaner.v_video.set(sys.argv[1])
        app.cleaner.v_out.set(P.default_output(sys.argv[1]))
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
