"""clipper — графический интерфейс.

Запускается двойным кликом по «Нарезка роликов.bat» или через `py gui.py`.
Зависимостей нет: tkinter входит в состав Python.

Главная ценность интерфейса — не кнопка «запустить», а вкладка «Оценка».
Датасет копится только тогда, когда сравнивать клипы не лень, а набирать
в консоли `py cli.py compare 12 15 --winner a` лень становится на третий раз.
Здесь это два клика: посмотрел два клипа — нажал, какой лучше.
"""

from __future__ import annotations

import io
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipper.config import Config, with_profile
from clipper.store import Store
from core import learnlog as _learnlog
from core import profiles as _pro

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv"}


class QueueWriter(io.TextIOBase):
    """Перехватывает print() из конвейера и передаёт в очередь для GUI.

    Конвейер работает в рабочем потоке, а трогать виджеты можно только из
    главного — поэтому строки идут через очередь, а не напрямую в текстовое поле.
    """

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s: str) -> int:
        if s:
            self.q.put(("log", s))
        return len(s)

    def flush(self) -> None:
        pass


class Rating(tk.Frame):
    def __init__(self, master, chrome=True, background=None):
        super().__init__(master)
        if background:
            self.configure(background=background)
        self.chrome = chrome

        self.cfg = with_profile()
        self.cfg.ensure_dirs()
        _pro.adopt_former_rerank()
        self.store = Store(self.cfg.db_path)

        self.sources: list[Path] = []
        self.msg_q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.pair_queue: list = []
        self.current_pair = None

        self._build()
        self.after(100, self._drain)
        self._refresh_stats()

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.nb = nb

        self.tab_run = ttk.Frame(nb)
        self.tab_rate = ttk.Frame(nb)
        self.tab_list = ttk.Frame(nb)
        # Во встроенном виде нарезка не нужна: она и так стоит соседней
        # вкладкой Студии. Собрать её всё равно надо — на её виджеты
        # ссылается очередь сообщений.
        if self.chrome:
            nb.add(self.tab_run, text="  Нарезка  ")
        nb.add(self.tab_rate, text="  Оценка  ")
        nb.add(self.tab_list, text="  Все клипы  ")

        self._build_run(self.tab_run)
        self._build_rate(self.tab_rate)
        self._build_list(self.tab_list)

        self.status = tk.StringVar(value="готов")
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        ttk.Separator(bar).pack(fill="x")
        ttk.Label(bar, textvariable=self.status, anchor="w",
                  padding=(10, 4)).pack(fill="x")

    # --- вкладка «Нарезка» ---

    def _build_run(self, root: ttk.Frame) -> None:
        left = ttk.Frame(root)
        left.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)

        ttk.Label(left, text="Исходные видео").pack(anchor="w")
        box = ttk.Frame(left)
        box.pack(fill="both", expand=True, pady=(2, 6))
        self.lst_src = tk.Listbox(box, height=7, selectmode="extended")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.lst_src.yview)
        self.lst_src.configure(yscrollcommand=sb.set)
        self.lst_src.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Button(row, text="Добавить файлы…", command=self._add_files).pack(side="left")
        ttk.Button(row, text="Добавить папку…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(row, text="Убрать", command=self._remove_selected).pack(side="left")
        ttk.Button(row, text="Очистить", command=self._clear_sources).pack(side="left", padx=4)

        ttk.Label(left, text="Ход работы").pack(anchor="w", pady=(10, 2))
        logbox = ttk.Frame(left)
        logbox.pack(fill="both", expand=True)
        self.txt_log = tk.Text(logbox, height=14, wrap="word", state="disabled",
                               font=("Consolas", 9))
        lsb = ttk.Scrollbar(logbox, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=lsb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        # --- правая колонка: настройки ---
        right = ttk.Frame(root)
        right.pack(side="right", fill="y", padx=(4, 8), pady=8)

        g = ttk.LabelFrame(right, text="Движок", padding=8)
        g.pack(fill="x")
        self.var_backend = tk.StringVar(value=self.cfg.backend)
        for name, label in (
            ("heuristic", "heuristic — без модели, мгновенно"),
            ("ollama", "ollama — локальная модель"),
            ("openai-compat", "LM Studio / llama.cpp"),
            ("claude", "Claude API — платно"),
        ):
            ttk.Radiobutton(g, text=label, value=name,
                            variable=self.var_backend).pack(anchor="w")
        ttk.Button(g, text="Проверить доступность",
                   command=self._check_backends).pack(fill="x", pady=(6, 0))

        m = ttk.LabelFrame(right, text="Локальная модель", padding=8)
        m.pack(fill="x", pady=(8, 0))
        self.var_local = tk.StringVar(value=self.cfg.local_model)
        ttk.Entry(m, textvariable=self.var_local).pack(fill="x")

        o = ttk.LabelFrame(right, text="Параметры", padding=8)
        o.pack(fill="x", pady=(8, 0))

        self.var_top = tk.IntVar(value=self.cfg.top_n)
        self.var_min = tk.DoubleVar(value=self.cfg.min_clip_s)
        self.var_max = tk.DoubleVar(value=self.cfg.max_clip_s)
        for label, var in (("Клипов на видео", self.var_top),
                           ("Мин. длина, сек", self.var_min),
                           ("Макс. длина, сек", self.var_max)):
            r = ttk.Frame(o)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=label, width=17).pack(side="left")
            ttk.Spinbox(r, from_=1, to=600, textvariable=var, width=7).pack(side="right")

        self.var_vertical = tk.BooleanVar(value=False)
        self.var_subs = tk.BooleanVar(value=False)
        self.var_norender = tk.BooleanVar(value=False)
        ttk.Checkbutton(o, text="Вертикальный 9:16",
                        variable=self.var_vertical).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(o, text="Вжечь субтитры", variable=self.var_subs).pack(anchor="w")
        ttk.Checkbutton(o, text="Не резать, только найти",
                        variable=self.var_norender).pack(anchor="w")

        self.btn_run = ttk.Button(right, text="Запустить", command=self._start)
        self.btn_run.pack(fill="x", pady=(12, 0), ipady=4)
        self.btn_stop = ttk.Button(right, text="Остановить", command=self._stop,
                                   state="disabled")
        self.btn_stop.pack(fill="x", pady=(4, 0))
        ttk.Button(right, text="Открыть папку с клипами",
                   command=self._open_out).pack(fill="x", pady=(12, 0))

        self.prog = ttk.Progressbar(right, mode="indeterminate")
        self.prog.pack(fill="x", pady=(8, 0))

    # --- вкладка «Оценка» ---

    def _build_rate(self, root: ttk.Frame) -> None:
        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")
        ttk.Label(
            top,
            text="Какой клип лучше? Сравнение надёжнее оценок по пятибалльной шкале:\n"
                 "абсолютная оценка со временем плывёт, относительная — нет.",
            justify="left",
        ).pack(anchor="w")
        tools = ttk.Frame(top)
        tools.pack(anchor="w", pady=(8, 0))
        ttk.Button(tools, text="Обновить список пар",
                   command=self._load_pairs).pack(side="left")
        ttk.Button(tools, text="Журнал обучения",
                   command=self.show_journal).pack(side="left", padx=(8, 0))
        ttk.Button(tools, text="Начать обучение заново",
                   command=self.forget_training).pack(side="left", padx=(8, 0))

        body = ttk.Frame(root, padding=10)
        body.pack(fill="both", expand=True)

        self.pair_info = tk.StringVar(value="Нажмите «Обновить список пар»")
        ttk.Label(body, textvariable=self.pair_info, font=("", 10, "bold")).pack(anchor="w")

        cards = ttk.Frame(body)
        cards.pack(fill="both", expand=True, pady=10)

        self.card_a = self._make_card(cards, "Вариант A", "a")
        self.card_a["frame"].pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.card_b = self._make_card(cards, "Вариант B", "b")
        self.card_b["frame"].pack(side="left", fill="both", expand=True, padx=(6, 0))

        btns = ttk.Frame(body)
        btns.pack(fill="x")
        self.btn_a = ttk.Button(btns, text="◀  A лучше",
                                command=lambda: self._vote("a"), state="disabled")
        self.btn_a.pack(side="left", expand=True, fill="x", padx=2, ipady=6)
        self.btn_tie = ttk.Button(btns, text="Одинаково",
                                  command=lambda: self._vote("tie"), state="disabled")
        self.btn_tie.pack(side="left", expand=True, fill="x", padx=2, ipady=6)
        self.btn_b = ttk.Button(btns, text="B лучше  ▶",
                                command=lambda: self._vote("b"), state="disabled")
        self.btn_b.pack(side="left", expand=True, fill="x", padx=2, ipady=6)
        ttk.Button(btns, text="Пропустить", command=self._next_pair).pack(
            side="left", expand=True, fill="x", padx=2, ipady=6)

    def _make_card(self, parent, title: str, key: str) -> dict:
        f = ttk.LabelFrame(parent, text=title, padding=10)
        title_var = tk.StringVar(value="—")
        meta_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=title_var, wraplength=330,
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(f, textvariable=meta_var, foreground="#666").pack(anchor="w", pady=(4, 8))
        btn = ttk.Button(f, text="▶  Посмотреть",
                         command=lambda: self._play(key), state="disabled")
        btn.pack(fill="x", ipady=4)
        drop = ttk.Button(f, text="🗑  Удалить — плохой клип",
                          command=lambda: self._reject(key), state="disabled")
        drop.pack(fill="x", pady=(4, 0), ipady=2)
        return {"frame": f, "title": title_var, "meta": meta_var,
                "btn": btn, "drop": drop}

    # --- вкладка «Все клипы» ---

    def _build_list(self, root: ttk.Frame) -> None:
        bar = ttk.Frame(root, padding=(10, 10, 10, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Обновить", command=self._load_list).pack(side="left")
        ttk.Button(bar, text="Посмотреть", command=self._play_selected).pack(side="left", padx=4)
        ttk.Label(bar, text="  Оценка:").pack(side="left", padx=(12, 2))
        for n in (1, 2, 3, 4, 5):
            ttk.Button(bar, text=str(n), width=3,
                       command=lambda n=n: self._rate_selected(n)).pack(side="left", padx=1)
        ttk.Button(bar, text="🗑 Удалить", command=self._reject_selected).pack(
            side="left", padx=(12, 0))

        filt = ttk.Frame(root, padding=(10, 6, 10, 0))
        filt.pack(fill="x")
        ttk.Label(filt, text="Показывать:").pack(side="left")
        self.var_filter = tk.StringVar(value="все")
        for label in ("все", "только с файлом", "только оценённые", "удалённые"):
            ttk.Radiobutton(filt, text=label, value=label, variable=self.var_filter,
                            command=self._load_list).pack(side="left", padx=(8, 0))

        wrap = ttk.Frame(root, padding=10)
        wrap.pack(fill="both", expand=True)
        cols = ("id", "score", "rated", "status", "start", "dur", "title")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=20)
        # Числовые колонки сортируются как числа, а не как строки:
        # иначе «10» встаёт перед «9».
        self._sort_numeric = {"id", "score", "rated", "start", "dur"}
        self._sort_state: tuple[str, bool] = ("score", True)
        for c, txt, w in (("id", "id", 50), ("score", "оценка", 68),
                          ("rated", "ваша", 55), ("status", "статус", 90),
                          ("start", "начало", 80), ("dur", "длит", 60),
                          ("title", "название", 400)):
            self.tree.heading(c, text=txt, command=lambda c=c: self._sort_by(c))
            self.tree.column(c, width=w, anchor="w" if c == "title" else "center")
        # Удалённые приглушены, ещё не резавшиеся — серые.
        self.tree.tag_configure("удалён", foreground="#b00020")
        self.tree.tag_configure("нет файла", foreground="#999999")
        self.tree.tag_configure("пропал", foreground="#c07000")
        tsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self._play_selected())

    # -------------------------------------------------------------- actions

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Выберите видео",
            filetypes=[("Видео", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.flv"),
                       ("Все файлы", "*.*")],
        )
        for p in paths:
            self._add_source(Path(p))

    def _add_folder(self) -> None:
        d = filedialog.askdirectory(title="Выберите папку с видео")
        if not d:
            return
        found = sorted(f for f in Path(d).iterdir() if f.suffix.lower() in VIDEO_EXT)
        if not found:
            messagebox.showinfo("Пусто", "В этой папке нет видеофайлов.")
            return
        for f in found:
            self._add_source(f)

    def _add_source(self, p: Path) -> None:
        if p not in self.sources:
            self.sources.append(p)
            self.lst_src.insert("end", str(p))

    def _remove_selected(self) -> None:
        for i in reversed(self.lst_src.curselection()):
            self.lst_src.delete(i)
            del self.sources[i]

    def _clear_sources(self) -> None:
        self.lst_src.delete(0, "end")
        self.sources.clear()

    def _open_out(self) -> None:
        try:
            os.startfile(self.cfg.out_dir)
        except OSError as exc:
            messagebox.showerror("Не открылось", str(exc))

    def _check_backends(self) -> None:
        from clipper.backends import AVAILABLE, get_backend

        self._log("\n--- проверка движков ---\n")
        for name, desc in AVAILABLE.items():
            probe = Config()
            probe.backend = name
            probe.local_model = self.var_local.get().strip() or probe.local_model
            try:
                b = get_backend(probe)
                check = getattr(b, "check", None)
                if callable(check):
                    check()
                self._log(f"  [готов] {name} — {desc}\n")
            except Exception as exc:  # noqa: BLE001
                self._log(f"  [ -- ] {name}: {str(exc).splitlines()[0]}\n")

    # --- запуск конвейера ---

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.sources:
            messagebox.showwarning("Нечего обрабатывать", "Добавьте хотя бы одно видео.")
            return

        cfg = with_profile()
        cfg.ensure_dirs()
        cfg.backend = self.var_backend.get()
        cfg.local_model = self.var_local.get().strip() or cfg.local_model
        cfg.top_n = max(1, self.var_top.get())
        cfg.min_clip_s = self.var_min.get()
        cfg.max_clip_s = self.var_max.get()
        cfg.vertical = self.var_vertical.get()
        cfg.burn_subs = self.var_subs.get()

        if cfg.min_clip_s >= cfg.max_clip_s:
            messagebox.showerror("Проверьте длину",
                                 "Минимальная длина должна быть меньше максимальной.")
            return

        self.stop_flag.clear()
        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.prog.start(12)
        self.status.set("работаю…")

        sources = list(self.sources)
        no_render = self.var_norender.get()

        def job():
            # У рабочего потока своё соединение с базой: sqlite3 не разрешает
            # делить одно подключение между потоками.
            store = Store(cfg.db_path)
            old = sys.stdout
            sys.stdout = QueueWriter(self.msg_q)
            try:
                from clipper.pipeline import process_many
                results = process_many(
                    sources, cfg, store,
                    do_render=not no_render,
                    cancel=self.stop_flag.is_set,
                )
                print("\n" + "=" * 50)
                for r in results:
                    print(f"{r.task.source.name}: {len(r.candidates)} кандидатов, "
                          f"{len(r.rendered)} отрендерено, {r.seconds:.0f} сек")
            except Exception:  # noqa: BLE001
                print("\nОШИБКА:\n" + traceback.format_exc())
            finally:
                sys.stdout = old
                store.close()
                self.msg_q.put(("done", None))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.stop_flag.set()
        self.status.set("останавливаюсь — дождитесь текущего шага…")
        self.btn_stop.configure(state="disabled")

    # --- сравнение пар ---

    def _load_pairs(self) -> None:
        self.pair_queue = self.store.suggest_pairs(limit=40)
        if not self.pair_queue:
            self.pair_info.set(
                "Пар пока нет. Нарежьте видео с рендером — сравнивать можно "
                "только готовые клипы."
            )
            self._set_pair_buttons("disabled")
            return
        self._next_pair()

    def _next_pair(self) -> None:
        if not self.pair_queue:
            self.pair_info.set("Пары закончились. Нажмите «Обновить список пар».")
            self.current_pair = None
            self._set_pair_buttons("disabled")
            for card in (self.card_a, self.card_b):
                card["title"].set("—")
                card["meta"].set("")
                card["btn"].configure(state="disabled")
                card["drop"].configure(state="disabled")
            return

        gap, a, b = self.pair_queue.pop(0)
        self.current_pair = (a, b)
        self.pair_info.set(f"Осталось пар: {len(self.pair_queue) + 1}   "
                           f"(движок развёл их всего на {gap:.2f})")
        for card, row in ((self.card_a, a), (self.card_b, b)):
            mm, ss = divmod(int(row["start"]), 60)
            card["title"].set(row["title"] or "без названия")
            card["meta"].set(f"оценка движка {row['model_score']:.1f}   "
                             f"с {mm}:{ss:02d}   {row['end'] - row['start']:.0f} сек")
            card["btn"].configure(state="normal" if row["render_path"] else "disabled")
            card["drop"].configure(state="normal")
        self._set_pair_buttons("normal")

    def _set_pair_buttons(self, state: str) -> None:
        for b in (self.btn_a, self.btn_b, self.btn_tie):
            b.configure(state=state)

    def _play(self, key: str) -> None:
        if not self.current_pair:
            return
        row = self.current_pair[0 if key == "a" else 1]
        p = row["render_path"]
        if not p or not Path(p).exists():
            messagebox.showinfo("Файла нет", "Клип ещё не отрендерен.")
            return
        try:
            os.startfile(p)
        except OSError as exc:
            messagebox.showerror("Не открылось", str(exc))

    def _reject(self, key: str) -> None:
        """Удаление клипа = сильная отрицательная оценка.

        Запись о кандидате остаётся в базе с оценкой 0 — отрицательные
        примеры для обучения нужны не меньше положительных. Удаляется
        только видеофайл.
        """
        if not self.current_pair:
            return
        row = self.current_pair[0 if key == "a" else 1]
        title = row["title"] or f"клип {row['id']}"
        if not messagebox.askokcancel(
            "Удалить клип",
            f"«{title}»\n\n"
            "Видеофайл будет удалён с диска, клипу проставится оценка 0.\n"
            "Сам фрагмент останется в базе как отрицательный пример "
            "для обучения — это полезные данные, а не мусор.\n\n"
            "Удалить?",
        ):
            return

        removed = self.store.reject(row["id"], rating=0)
        # Убираем из очереди все пары, где участвует удалённый клип:
        # сравнивать с несуществующим файлом больше нечего.
        self.pair_queue = [
            (g, x, y) for g, x, y in self.pair_queue
            if x["id"] != row["id"] and y["id"] != row["id"]
        ]
        self.status.set(f"клип {row['id']} удалён"
                        + (f" ({Path(removed).name})" if removed else ""))
        self._next_pair()
        self._load_list()

    def _vote(self, which: str) -> None:
        if not self.current_pair:
            return
        a, b = self.current_pair
        winner = {"a": a["id"], "b": b["id"], "tie": None}[which]
        self.store.add_comparison(a["id"], b["id"], winner)
        self._note("сравнение %d и %d: %s" % (
            a["id"], b["id"],
            "ничья" if winner is None else "лучше %d" % winner))
        self._refresh_stats()
        self._next_pair()

    # --- список клипов ---

    def _status_of(self, cid: int, renders: dict, ratings: dict, fresh_after: float) -> str:
        """Состояние клипа одним словом.

        «нет файла» — это НЕ сбой рендера. Движок находит сотни кандидатов,
        а режутся только лучшие (top_n выше порога оценки). Остальные живут
        в базе как данные и файла никогда не имели.
        """
        if ratings.get(cid) == 0:
            return "удалён"
        info = renders.get(cid)
        if info is None:
            return "нет файла"
        if not info["exists"]:
            return "пропал"
        return "новый" if info["ts"] >= fresh_after else "старый"

    def _load_list(self) -> None:
        for i in self.tree.get_children():
            self.tree.delete(i)

        renders = self.store.render_index()
        ratings = self.store.rating_index()
        # Всё, что отрендерено в пределах пяти минут от последнего рендера,
        # считаем одной пачкой — это «новые».
        fresh_after = self.store.last_render_batch() - 300

        mode = self.var_filter.get()
        shown = 0
        for r in self.store.list_candidates():
            cid = r["id"]
            status = self._status_of(cid, renders, ratings, fresh_after)
            rating = ratings.get(cid)

            if mode == "только с файлом" and status not in ("новый", "старый"):
                continue
            if mode == "только оценённые" and rating is None:
                continue
            if mode == "удалённые" and status != "удалён":
                continue

            mm, ss = divmod(int(r["start"]), 60)
            hh, mm = divmod(mm, 60)
            self.tree.insert("", "end", tags=(status,), values=(
                cid,
                f"{r['model_score']:.1f}",
                "—" if rating is None else rating,
                status,
                f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}",
                f"{r['end'] - r['start']:.0f}с",
                r["title"] or "—",
            ))
            shown += 1

        col, desc = self._sort_state
        self._apply_sort(col, desc)
        self._refresh_stats()
        self.tree.heading("title", text=f"название  ({shown} строк)")

    def _sort_by(self, col: str) -> None:
        prev_col, prev_desc = self._sort_state
        # Повторный клик по той же колонке переворачивает порядок.
        desc = not prev_desc if col == prev_col else True
        self._sort_state = (col, desc)
        self._apply_sort(col, desc)

    def _apply_sort(self, col: str, desc: bool) -> None:
        idx = self.tree["columns"].index(col)

        def key(item):
            v = self.tree.item(item, "values")[idx]
            if col in self._sort_numeric:
                if v in ("—", ""):
                    # Неоценённые всегда в конце, в любом направлении.
                    return float("-inf") if desc else float("inf")
                try:
                    return float(str(v).rstrip("сc").replace(":", ""))
                except ValueError:
                    return float("-inf")
            return str(v).lower()

        items = sorted(self.tree.get_children(""), key=key, reverse=desc)
        for pos, item in enumerate(items):
            self.tree.move(item, "", pos)

        arrow = " ▼" if desc else " ▲"
        titles = {"id": "id", "score": "оценка", "rated": "ваша", "status": "статус",
                  "start": "начало", "dur": "длит"}
        for c, t in titles.items():
            self.tree.heading(c, text=t + (arrow if c == col else ""))

    def _selected_id(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Ничего не выбрано", "Выберите клип в списке.")
            return None
        return int(self.tree.item(sel[0], "values")[0])

    def _play_selected(self) -> None:
        cid = self._selected_id()
        if cid is None:
            return
        p = self.store.render_path(cid)
        if not p:
            messagebox.showinfo("Файла нет", "Этот клип ещё не отрендерен.")
            return
        try:
            os.startfile(p)
        except OSError as exc:
            messagebox.showerror("Не открылось", str(exc))

    def _reject_selected(self) -> None:
        cid = self._selected_id()
        if cid is None:
            return
        if not messagebox.askokcancel(
            "Удалить клип",
            f"Клип {cid}: видеофайл будет удалён, проставится оценка 0.\n"
            "Фрагмент останется в базе как отрицательный пример.\n\nУдалить?",
        ):
            return
        self.store.reject(cid, rating=0)
        self._load_list()
        self.status.set(f"клип {cid} удалён (оценка 0)")

    def _rate_selected(self, n: int) -> None:
        cid = self._selected_id()
        if cid is None:
            return
        self.store.add_rating(cid, n)
        self._note("оценка клипа %d: %d" % (cid, n))
        self._load_list()
        self.status.set(f"клип {cid} оценён на {n}")

    def _learn_folder(self) -> Path:
        return Path(self.cfg.db_path).parent

    def _note(self, text: str) -> None:
        _learnlog.write(self._learn_folder(), text)

    def show_journal(self) -> None:
        text = _learnlog.read(self._learn_folder())
        s = self.store.training_stats()
        model = Path(self.cfg.rerank_path)
        head = ("клипов %d, оценок %d, сравнений %d%s"
                "журнал: %s%s"
                "модель вкуса: %s%s"
                % (s["candidates"], s["ratings"], s["comparisons"], os.linesep,
                   _learnlog.path(self._learn_folder()), os.linesep,
                   model if model.is_file() else "%s — ещё не обучена" % model,
                   os.linesep * 2))
        win = tk.Toplevel(self)
        win.title("Журнал обучения")
        win.geometry("760x520")
        box = tk.Text(win, wrap="word")
        box.pack(fill="both", expand=True, padx=8, pady=8)
        box.insert("1.0", head + (text or "Записей пока нет."))
        box.configure(state="disabled")
        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="Открыть папку",
                   command=self._open_learn_folder).pack(side="left")
        ttk.Button(bar, text="Закрыть", command=win.destroy).pack(side="right")

    def _open_learn_folder(self) -> None:
        folder = self._learn_folder()
        try:
            os.startfile(str(folder))
        except (OSError, AttributeError):
            subprocess.Popen(["explorer", str(folder)])

    def forget_training(self) -> None:
        s = self.store.training_stats()
        if not messagebox.askyesno(
                "Начать обучение заново",
                "Удалить все оценки (%d) и сравнения (%d)?\n\n"
                "Сами клипы и кандидаты останутся — уйдёт только то, "
                "на чём училась модель вкуса. Отменить будет нельзя."
                % (s["ratings"], s["comparisons"]), parent=self):
            return
        gone_c, gone_r = self.store.forget_training()
        model = Path(self.cfg.rerank_path)
        dropped = False
        try:
            model.unlink()
            dropped = True
        except OSError:
            pass
        _learnlog.clear(self._learn_folder())
        self._note("обучение сброшено: сравнений %d, оценок %d%s"
                   % (gone_c, gone_r, ", модель удалена" if dropped else ""))
        self.reload()
        self.status.set("обучение сброшено: сравнений %d, оценок %d" % (gone_c, gone_r))

    # ------------------------------------------------------------ служебное

    def _log(self, text: str) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text)
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _drain(self) -> None:
        """Переносит сообщения рабочего потока в виджеты. Только главный поток."""
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    self.prog.stop()
                    self.btn_run.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self.status.set("готово")
                    self._load_list()
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _refresh_stats(self) -> None:
        s = self.store.training_stats()
        self.status.set(
            f"клипов: {s['candidates']}   сравнений: {s['comparisons']}   "
            f"оценок: {s['ratings']}   "
            + ("данных на реранкер хватает" if s["comparisons"] >= 300
               else f"до обучения реранкера ещё ~{300 - s['comparisons']} сравнений")
        )

    def busy(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def shutdown(self) -> None:
        self.stop_flag.set()
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass

    def reload(self) -> None:
        self._load_pairs()
        self._load_list()
        self._refresh_stats()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("clipper — нарезка роликов")
        self.geometry("980x680")
        self.minsize(820, 560)

        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass

        self.rating = Rating(self)
        self.rating.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        if self.rating.busy():
            if not messagebox.askokcancel("Работа не закончена",
                                          "Обработка идёт. Всё равно закрыть?"):
                return
        self.rating.shutdown()
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
