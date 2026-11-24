#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Окошко для clipmaker: выбрал стрим, накидал отрезки, нажал 'Сделать'."""

import os
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import clipmaker

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Нарезка роликов")
        self.geometry("880x680")
        self.minsize(760, 600)
        self.log_q = queue.Queue()
        self.worker = None
        self.stop_flag = False

        self.v = {k: tk.StringVar() for k in (
            "input", "outro", "music", "outdir", "fade", "blur", "dark",
            "mvol", "crf")}
        self.gpu = tk.BooleanVar(value=False)
        self.v["fade"].set("1.5")
        self.v["blur"].set("20")
        self.v["dark"].set("0")
        self.v["mvol"].set("1.0")
        self.v["crf"].set("20")

        self._build()
        self._load_settings()
        self.after(120, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------- интерфейс
    def _build(self):
        pad = dict(padx=6, pady=4)
        top = ttk.LabelFrame(self, text="Файлы")
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)

        self._file_row(top, 0, "Стрим", "input", self._pick_input)
        self._file_row(top, 1, "Концовка", "outro",
                       lambda: self._pick("outro", "Видео"))
        self._file_row(top, 2, "Музыка", "music",
                       lambda: self._pick("music", "Аудио"))
        self._file_row(top, 3, "Папка вывода", "outdir", self._pick_dir)

        opts = ttk.LabelFrame(self, text="Переход и качество")
        opts.pack(fill="x", **pad)
        self._num(opts, 0, "Затемнение, сек", "fade")
        self._num(opts, 1, "Размытие", "blur")
        self._num(opts, 2, "Затемнить концовку 0..1", "dark")
        self._num(opts, 3, "Громкость музыки", "mvol")
        self._num(opts, 4, "CRF", "crf")
        ttk.Checkbutton(opts, text="NVIDIA (nvenc)", variable=self.gpu).grid(
            row=0, column=10, padx=8)

        seg = ttk.LabelFrame(self, text="Отрезки (начало / конец / имя файла)")
        seg.pack(fill="both", expand=True, **pad)

        row = ttk.Frame(seg)
        row.pack(fill="x", padx=6, pady=4)
        self.e_start = ttk.Entry(row, width=14)
        self.e_end = ttk.Entry(row, width=14)
        self.e_name = ttk.Entry(row, width=28)
        ttk.Label(row, text="начало").pack(side="left")
        self.e_start.pack(side="left", padx=4)
        ttk.Label(row, text="конец (или +7:25)").pack(side="left")
        self.e_end.pack(side="left", padx=4)
        ttk.Label(row, text="имя").pack(side="left")
        self.e_name.pack(side="left", padx=4)
        ttk.Button(row, text="Добавить", command=self.add_seg).pack(side="left", padx=4)
        ttk.Button(row, text="Удалить", command=self.del_seg).pack(side="left")
        self.e_start.bind("<Return>", lambda e: self.e_end.focus())
        self.e_end.bind("<Return>", lambda e: self.add_seg())
        self.e_name.bind("<Return>", lambda e: self.add_seg())

        self.tree = ttk.Treeview(seg, columns=("s", "e", "n"), show="headings",
                                 height=7)
        for c, t, w in (("s", "начало", 120), ("e", "конец", 120), ("n", "файл", 400)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=6, pady=4)

        bar = ttk.Frame(self)
        bar.pack(fill="x", **pad)
        self.btn = ttk.Button(bar, text="Сделать ролики", command=self.run)
        self.btn.pack(side="left")
        ttk.Button(bar, text="Стоп", command=self.stop).pack(side="left", padx=6)
        self.status = ttk.Label(bar, text="")
        self.status.pack(side="left", padx=10)

        self.log = tk.Text(self, height=9, wrap="none", bg="#111", fg="#ddd")
        self.log.pack(fill="both", expand=False, padx=6, pady=6)

    def _file_row(self, parent, r, label, key, cmd):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(parent, textvariable=self.v[key]).grid(
            row=r, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text="...", width=4, command=cmd).grid(
            row=r, column=2, padx=6)

    def _num(self, parent, col, label, key):
        f = ttk.Frame(parent)
        f.grid(row=0, column=col, padx=6, pady=6, sticky="w")
        ttk.Label(f, text=label).pack(side="left")
        ttk.Entry(f, textvariable=self.v[key], width=6).pack(side="left", padx=3)

    # ---------------------------------------------------------- действия
    def _pick(self, key, kind):
        types = {"Видео": [("Видео", "*.mp4 *.mkv *.mov *.ts *.flv *.webm *.avi"),
                           ("Все", "*.*")],
                 "Аудио": [("Аудио", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"),
                           ("Все", "*.*")]}[kind]
        p = filedialog.askopenfilename(filetypes=types)
        if p:
            self.v[key].set(p)

    def _pick_input(self):
        self._pick("input", "Видео")
        p = self.v["input"].get()
        if p and not self.v["outdir"].get():
            self.v["outdir"].set(os.path.dirname(p))
        if p:
            try:
                info = clipmaker.probe(p)
                self._log("Стрим: %s, %sx%s, %.3f fps"
                          % (clipmaker.fmt_time(info["duration"] or 0),
                             info["width"], info["height"], info["fps"] or 0))
            except RuntimeError as e:
                self._log(str(e))

    def _pick_dir(self):
        p = filedialog.askdirectory()
        if p:
            self.v["outdir"].set(p)

    def add_seg(self):
        s, e = self.e_start.get().strip(), self.e_end.get().strip()
        if not s or not e:
            return
        try:
            clipmaker.parse_time(s)
            clipmaker.parse_time(e)
        except ValueError as err:
            messagebox.showerror("Время", str(err))
            return
        n = self.e_name.get().strip() or "clip_%02d" % (len(self.tree.get_children()) + 1)
        if not os.path.splitext(n)[1]:
            n += ".mp4"
        self.tree.insert("", "end", values=(s, e, n))
        for w in (self.e_start, self.e_end, self.e_name):
            w.delete(0, "end")
        self.e_start.focus()

    def del_seg(self):
        for i in self.tree.selection():
            self.tree.delete(i)

    def run(self):
        if self.worker and self.worker.is_alive():
            return
        segs = [self.tree.item(i)["values"] for i in self.tree.get_children()]
        if not segs:
            messagebox.showinfo("Пусто", "Добавь хотя бы один отрезок.")
            return
        if not os.path.isfile(self.v["input"].get()):
            messagebox.showerror("Стрим", "Не выбран исходный файл.")
            return
        outdir = self.v["outdir"].get() or os.path.dirname(self.v["input"].get())
        os.makedirs(outdir, exist_ok=True)
        self._save_settings()
        self.stop_flag = False
        self.btn.state(["disabled"])
        self.worker = threading.Thread(target=self._work, args=(segs, outdir),
                                       daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_flag = True
        self._log("Остановлю после текущего ролика...")

    def _work(self, segs, outdir):
        def f(key, default):
            try:
                return float(self.v[key].get())
            except ValueError:
                return default
        base = {
            "input": self.v["input"].get(),
            "outro": self.v["outro"].get() or None,
            "music": self.v["music"].get() or None,
            "fade": f("fade", 1.5),
            "blur": f("blur", 20),
            "dark": f("dark", 0.0),
            "music_volume": f("mvol", 1.0),
            "crf": int(f("crf", 20)),
            "preset": "veryfast",
            "gpu": self.gpu.get(),
        }
        if base["music"] and not base["outro"]:
            base["music"] = None
            self._log("Музыка без концовки - пропускаю музыку.")
        ok = 0
        for i, (s, e, n) in enumerate(segs, 1):
            if self.stop_flag:
                break
            opt = dict(base, start=str(s), end=str(e),
                       output=os.path.join(outdir, str(n)))
            self._log("\n=== [%d/%d] %s  %s -> %s" % (i, len(segs), n, s, e))
            self.log_q.put(("status", "Делаю %d из %d..." % (i, len(segs))))
            try:
                rc = clipmaker.make_clip(opt, on_line=self._log)
            except (ValueError, RuntimeError) as err:
                self._log("Ошибка: %s" % err)
                continue
            if rc == 0:
                ok += 1
                self._log("Готово: %s" % opt["output"])
            else:
                self._log("Не получилось (код %d)" % rc)
        self.log_q.put(("status", "Сделано %d из %d" % (ok, len(segs))))
        self.log_q.put(("done", None))

    # ---------------------------------------------------------- лог/настройки
    def _log(self, line):
        self.log_q.put(("log", line))

    def _drain_log(self):
        try:
            while True:
                kind, val = self.log_q.get_nowait()
                if kind == "log":
                    self.log.insert("end", val + "\n")
                    self.log.see("end")
                    if int(self.log.index("end-1c").split(".")[0]) > 500:
                        self.log.delete("1.0", "200.0")
                elif kind == "status":
                    self.status.config(text=val)
                elif kind == "done":
                    self.btn.state(["!disabled"])
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _save_settings(self):
        data = {k: var.get() for k, var in self.v.items()}
        data["gpu"] = self.gpu.get()
        try:
            with open(CONFIG, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_settings(self):
        try:
            with open(CONFIG, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        for k, var in self.v.items():
            if k in data and k != "input":
                var.set(data[k])
        self.gpu.set(bool(data.get("gpu")))

    def _on_close(self):
        self._save_settings()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
