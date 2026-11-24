from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from core import autopilot as _ap
from core import ffmpeg as _ff
from core import upload as _up

from . import theme
from .runner import Runner

TICK_MS = 60000
SHOWN = (_ap.READY, _ap.APPROVED, _ap.FAILED)


class AutopilotPanel(tk.Frame):
    def __init__(self, master, studio):
        super().__init__(master, background=theme.BG)
        self.studio = studio
        self.runner = Runner(self)
        self.runner.listen(self._on_event)
        self.rows = []
        self.vars = {}
        self._build()
        self.load()
        self.after(TICK_MS, self._tick)

    def _profile(self):
        from core import profiles as _pro

        return _pro.active()

    def _card(self, master, title):
        outer = ttk.Frame(master, style="Panel.TFrame")
        outer.pack(fill="x", pady=(0, 10))
        inner = ttk.Frame(outer, style="Panel.TFrame")
        inner.pack(fill="x", padx=14, pady=12)
        ttk.Label(inner, text=title, style="PanelDim.TLabel").pack(anchor="w", pady=(0, 8))
        return inner

    def _row(self, master):
        row = ttk.Frame(master, style="Panel.TFrame")
        row.pack(fill="x", pady=2)
        return row

    def _field(self, row, label, key, width=10, kind=tk.StringVar):
        ttk.Label(row, text=label, style="PanelDim.TLabel").pack(side="left")
        var = kind()
        self.vars[key] = var
        ttk.Entry(row, textvariable=var, width=width).pack(side="left", padx=(6, 14))
        return var

    def _flag(self, row, label, key):
        var = tk.BooleanVar()
        self.vars[key] = var
        ttk.Checkbutton(row, text=label, variable=var).pack(side="left", padx=(0, 14))
        return var

    def _build(self):
        scroll = theme.Scrolled(self)
        scroll.pack(fill="both", expand=True)
        page = scroll.body

        card = self._card(page, "ОТКУДА — новые записи стримов")
        row = self._row(card)
        ttk.Label(row, text="канал твича", style="PanelDim.TLabel").pack(side="left")
        self.vars["twitch"] = tk.StringVar()
        ttk.Entry(row, textvariable=self.vars["twitch"], width=30).pack(side="left",
                                                                        padx=(6, 14))
        ttk.Label(row, text="имя или ссылка, например twitch.tv/имя_канала", style="PanelDim.TLabel").pack(
            side="left")
        row = self._row(card)
        ttk.Label(row, text="телеграм", style="PanelDim.TLabel").pack(side="left")
        self.vars["telegram"] = tk.StringVar()
        ttk.Entry(row, textvariable=self.vars["telegram"], width=30).pack(side="left",
                                                                          padx=(6, 14))
        ttk.Label(row, text="канал с анонсами, например t.me/somestreamer — запасной сигнал и "
                            "эфиры на других площадках", style="PanelDim.TLabel").pack(side="left")
        row = self._row(card)
        self._flag(row, "следить за эфирами (служба)", "watch")
        self._flag(row, "нарезать сразу после эфира", "after_live")
        row = self._row(card)
        ttk.Label(row, text="папка стримов", style="PanelDim.TLabel").pack(side="left")
        self.vars["folder"] = tk.StringVar()
        ttk.Entry(row, textvariable=self.vars["folder"]).pack(side="left", fill="x",
                                                              expand=True, padx=(6, 8))
        theme.Button(row, "…", self._pick_folder, width=3).pack(side="left")
        row = self._row(card)
        self._field(row, "клипов со стрима", "per_stream", 5)
        self._field(row, "длина от, с", "min_len", 5)
        self._field(row, "до", "max_len", 5)
        self._field(row, "стримов за раз", "streams_per_run", 4)
        row = self._row(card)
        self._flag(row, "искать по смыслу (нейронка, дольше)", "sense")
        self._flag(row, "убирать донаты не по теме", "donations")
        self._flag(row, "убирать, где стример отошёл", "away")
        ttk.Label(card, style="PanelDim.TLabel", wraplength=860, justify="left", text=(
            "Если запись уже лежит в папке стримов (номер записи в имени файла), она не "
            "качается заново. Без «по смыслу» моменты ищутся по громкости — быстро.")).pack(
            anchor="w", pady=(6, 0))

        card = self._card(page, "ДИСК — чистка за собой")
        row = self._row(card)
        self._field(row, "записи стримов хранить, дн.", "keep_source_days", 4)
        self._field(row, "но не дольше", "max_source_days", 4)
        self._field(row, "звук эфира, дн.", "keep_live_days", 4)
        row = self._row(card)
        self._field(row, "отклонённое, дн.", "keep_rejected_days", 4)
        self._field(row, "выложенное, дн.", "keep_posted_days", 4)
        self._field(row, "свободно не меньше, ГБ", "min_free_gb", 5)
        theme.Button(row, "Почистить сейчас", self._clean_now).pack(side="left", padx=(8, 0))
        ttk.Label(card, style="PanelDim.TLabel", wraplength=860, justify="left", text=(
            "Удаляется только то, что служба скачала или записала сама: ваши файлы в папке "
            "стримов не трогаются. Запись стрима живёт, пока из неё не всё решено, но не "
            "дольше второго срока. Перед скачиванием проверяется место: если его мало, "
            "сначала чистка, потом запись ждёт и служба пишет об этом в трей.")).pack(anchor="w",
                                                                              pady=(6, 0))

        card = self._card(page, "СЛУЖБА — все стримеры без окна")
        row = self._row(card)
        self.btn_service = theme.Button(row, "Запустить службу", self._service_start)
        self.btn_service.pack(side="left")
        self.btn_autostart = theme.Button(row, "Запускать с Windows", self._autostart)
        self.btn_autostart.pack(side="left", padx=(8, 0))
        self.lbl_service = ttk.Label(card, text="", style="PanelDim.TLabel", wraplength=860,
                                     justify="left")
        self.lbl_service.pack(anchor="w", pady=(6, 0))
        ttk.Label(card, style="PanelDim.TLabel", wraplength=860, justify="left", text=(
            "Стример — это профиль. Служба следит за каждым профилем, где вписан твич "
            "или телеграм и стоит «следить за эфирами»: пишет звук эфира, после конца ждёт "
            "запись и режет её в очередь. Анонс в телеграме со ссылкой на другую площадку "
            "(YouTube, Kick, VK, GoodGame) — эфир пишется целиком, видео. Новый профиль "
            "подхватывается за минуту. Значок службы — в трее, рядом с часами.")).pack(
            anchor="w", pady=(6, 0))

        card = self._card(page, "РОЛИКИ — игры и реакции")
        row = self._row(card)
        self._flag(row, "делать ролики по играм", "roliks")
        self._field(row, "игра от, мин", "rolik_min", 5)
        row = self._row(card)
        self._field(row, "название", "rolik_title", 40)
        ttk.Label(row, text="{стример} и {игра} подставятся", style="PanelDim.TLabel").pack(
            side="left")
        row = self._row(card)
        self._flag(row, "делать реакции из Just Chatting", "reactions")
        self._field(row, "реакция от, мин", "reaction_min", 5)
        row = self._row(card)
        self._field(row, "название реакции", "reaction_title", 40)
        self._flag(row, "читать заголовок с экрана", "screen_titles")
        ttk.Label(row, text="{стример} и {видео} подставятся", style="PanelDim.TLabel").pack(
            side="left")
        row = self._row(card)
        self._flag(row, "вырезать донаты не по теме", "rolik_donations")
        self._flag(row, "вырезать, где стример отошёл", "rolik_away")
        ttk.Label(card, style="PanelDim.TLabel", wraplength=860, justify="left", text=(
            "Твич помнит, когда стример менял категорию. Каждая игра дольше порога — "
            "ролик; короткая вставка другой категории посреди игры его не рвёт, границы "
            "подтягиваются к тишине. Концовка, музыка и цензура — как в режиме «Ролики». "
            "Реакции: в Just Chatting ищется, где стример переключается на следующее видео "
            "(по картинке и голосам), модель по расшифровке уточняет место и называет видео. "
            "Нужна расшифровка всего стрима — это самая долгая часть. Сомнительные куски "
            "тоже попадают в очередь: лишнее отклоните.")).pack(
            anchor="w", pady=(6, 0))

        card = self._card(page, "КУДА — выгрузка одобренных")
        row = self._row(card)
        ttk.Label(row, text="площадка", style="PanelDim.TLabel").pack(side="left")
        self.vars["service"] = tk.StringVar()
        ttk.Combobox(row, textvariable=self.vars["service"], state="readonly", width=10,
                     values=_up.SERVICE_TITLES).pack(side="left", padx=(6, 14))
        self._field(row, "аккаунт", "account", 12)
        self._field(row, "канал", "channel", 22)
        ttk.Label(row, text="доступ", style="PanelDim.TLabel").pack(side="left")
        self.vars["visibility"] = tk.StringVar()
        ttk.Combobox(row, textvariable=self.vars["visibility"], state="readonly", width=12,
                     values=_up.VISIBILITY_TITLES).pack(side="left", padx=(6, 0))
        row = self._row(card)
        self._field(row, "название", "title", 40)
        ttk.Label(row, text="пусто — из клипа", style="PanelDim.TLabel").pack(side="left")
        row = self._row(card)
        self._flag(row, "выкладывать одобренные ролики на YouTube", "rolik_post")
        self._field(row, "ролик не чаще раз в, ч", "rolik_gap_hours", 5)
        ttk.Label(row, text="доступ", style="PanelDim.TLabel").pack(side="left")
        self.vars["rolik_visibility"] = tk.StringVar()
        ttk.Combobox(row, textvariable=self.vars["rolik_visibility"], state="readonly",
                     width=12, values=_up.VISIBILITY_TITLES).pack(side="left", padx=(6, 0))
        row = self._row(card)
        self._field(row, "клип не чаще чем раз в, ч", "gap_hours", 5)
        self._flag(row, "выкладывать, пока Студия открыта", "post_while_open")
        ttk.Label(card, style="PanelDim.TLabel", wraplength=860, justify="left", text=(
            "Выкладываются только клипы, которые вы одобрили, по одному. YouTube — с окном "
            "браузера: окно откроется само, его не нужно трогать. Если Google попросит "
            "подтвердить личность, выгрузка остановится и подождёт вас.")).pack(
            anchor="w", pady=(6, 0))
        row = self._row(card)
        theme.Button(row, "Сохранить", self.save, primary=True).pack(side="left")
        self.btn_run = theme.Button(row, "Проверить стримы и нарезать", self._run_now)
        self.btn_run.pack(side="left", padx=(8, 0))
        self.btn_post = theme.Button(row, "Выложить следующий сейчас", self._post_now)
        self.btn_post.pack(side="left", padx=(8, 0))
        self.btn_stop = theme.Button(row, "Остановить", self.runner.cancel)
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.btn_stop.set_enabled(False)
        row = self._row(card)
        theme.Button(row, "Запускать сам каждый час", self._task_on).pack(side="left")
        theme.Button(row, "Не запускать сам", self._task_off).pack(side="left", padx=(8, 0))
        self.lbl_task = ttk.Label(row, text="", style="PanelDim.TLabel")
        self.lbl_task.pack(side="left", padx=(12, 0))

        card = self._card(page, "ОЧЕРЕДЬ — что одобрить")
        box = ttk.Frame(card, style="Panel.TFrame")
        box.pack(fill="x")
        self.tree = ttk.Treeview(box, columns=("status", "stream", "clip", "len", "cuts"),
                                 show="headings", height=10, selectmode="extended")
        for name, title, width in (("status", "статус", 100), ("stream", "стрим", 220),
                                   ("clip", "клип", 360), ("len", "длина", 70),
                                   ("cuts", "вырезов", 70)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, anchor="w")
        bar = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="x", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self._open())
        row = self._row(card)
        theme.Button(row, "Одобрить", lambda: self._decide(_ap.APPROVED),
                     primary=True).pack(side="left")
        theme.Button(row, "Отклонить", lambda: self._decide(_ap.REJECTED)).pack(
            side="left", padx=(8, 0))
        theme.Button(row, "Вернуть в ожидание", lambda: self._decide(_ap.READY)).pack(
            side="left", padx=(8, 0))
        theme.Button(row, "Посмотреть", self._open).pack(side="left", padx=(8, 0))
        theme.Button(row, "Переименовать", self._rename).pack(side="left", padx=(8, 0))
        theme.Button(row, "Папка", self._folder).pack(side="left", padx=(8, 0))
        self.v_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="показывать выложенные и отклонённые", variable=self.v_all,
                        command=self.refresh).pack(side="left", padx=(14, 0))
        self.lbl_state = ttk.Label(card, text="", style="PanelDim.TLabel")
        self.lbl_state.pack(anchor="w", pady=(6, 0))

        card = self._card(page, "ЖУРНАЛ")
        self.lbl_stage = ttk.Label(card, text="готов", style="PanelDim.TLabel")
        self.lbl_stage.pack(anchor="w")
        self.progress = ttk.Progressbar(card, maximum=1000)
        self.progress.pack(fill="x", pady=(4, 6))
        self.log = tk.Text(card, height=10, background=theme.PANEL2, foreground=theme.DIM,
                           insertbackground=theme.TEXT, font=theme.MONO_FONT, relief="flat",
                           wrap="word", borderwidth=0, highlightthickness=0)
        self.log.pack(fill="x")
        self.log.configure(state="disabled")

    def load(self):
        profile = self._profile()
        if not profile:
            self.lbl_state.configure(text="нет профиля")
            return
        opts = _ap.settings(profile)
        for key, var in self.vars.items():
            value = opts.get(key)
            if key == "service":
                var.set(dict(_up.SERVICES).get(value, _up.SERVICE_TITLES[0]))
            elif key in ("visibility", "rolik_visibility"):
                var.set(dict(_up.VISIBILITY).get(value, _up.VISIBILITY_TITLES[0]))
            elif isinstance(var, tk.BooleanVar):
                var.set(bool(value))
            else:
                var.set("" if value is None else str(value))
        self.refresh()
        self._show_task()
        self._show_service()

    def collect(self) -> dict:
        data = {}
        for key, var in self.vars.items():
            value = var.get()
            if key == "service":
                value = _up.service_key(value)
            elif key in ("visibility", "rolik_visibility"):
                value = _up.visibility_key(value)
            elif key in ("per_stream", "streams_per_run"):
                value = max(1, int(float(str(value).replace(",", ".") or 1)))
            elif key in ("min_len", "max_len", "gap_hours", "rolik_min", "reaction_min",
                         "rolik_gap_hours", "keep_source_days", "max_source_days",
                         "keep_live_days", "keep_rejected_days", "keep_posted_days",
                         "min_free_gb"):
                value = max(0.0, float(str(value).replace(",", ".") or 0))
            elif isinstance(value, str):
                value = value.strip()
            data[key] = value
        if data["max_len"] <= data["min_len"]:
            raise ValueError("длина «до» должна быть больше «от»")
        return data

    def save(self):
        profile = self._profile()
        if not profile:
            return None
        try:
            data = self.collect()
        except ValueError as error:
            messagebox.showerror("Автопилот", str(error), parent=self)
            return None
        _ap.save_settings(profile, data)
        self._say("настройки автопилота сохранены")
        self._show_service()
        return profile

    def _pick_folder(self):
        chosen = filedialog.askdirectory(parent=self, title="Папка со стримами")
        if chosen:
            self.vars["folder"].set(os.path.normpath(chosen))

    def refresh(self):
        profile = self._profile()
        self.tree.delete(*self.tree.get_children())
        if not profile:
            return
        statuses = None if self.v_all.get() else SHOWN
        self.rows = _ap.queue(profile, statuses)
        for row in self.rows:
            length = (row["finish"] or 0) - (row["start"] or 0)
            self.tree.insert("", "end", iid=str(row["id"]), values=(
                row["status"], row.get("stream") or "",
                ("[ролик] " if row.get("kind") == _ap.ROLIK else "")
                + (row.get("title") or os.path.basename(row["file"] or "")),
                _ff.format_hms(length), row.get("cuts") or 0))
        counts = {}
        for row in _ap.queue(profile):
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        due = _ap.next_post_at(profile)
        when = "сразу" if due <= time.time() else time.strftime("%d.%m %H:%M",
                                                               time.localtime(due))
        self.lbl_state.configure(text="ждут: %d, одобрено: %d, выложено: %d; следующая "
                                      "выгрузка — %s" % (counts.get(_ap.READY, 0),
                                                         counts.get(_ap.APPROVED, 0),
                                                         counts.get(_ap.POSTED, 0), when))

    def picked(self) -> list:
        return [int(item) for item in self.tree.selection()]

    def _decide(self, status):
        ids = self.picked()
        if not ids:
            messagebox.showinfo("Очередь", "Выберите клипы в очереди.", parent=self)
            return
        changed = _ap.decide(self._profile(), ids, status)
        self._say("%s: %d" % (status, changed))
        self.refresh()

    def _clean_now(self):
        profile = self.save()
        if not profile:
            return

        def work(run):
            from core import cleanup as _clean

            done = _clean.sweep_all([profile], log=run.log)
            run.log("удалено файлов: %d, освобождено %.1f ГБ" % (
                done["files"], done["bytes"] / float(1 << 30)))
            return done

        self._go("чищу диск…", work)

    def _row_picked(self):
        ids = self.picked()
        return next((row for row in self.rows if ids and row["id"] == ids[0]), None)

    def _rename(self):
        row = self._row_picked()
        if not row:
            messagebox.showinfo("Очередь", "Выберите клип или ролик.", parent=self)
            return
        title = simpledialog.askstring("Название", "Новое название:", parent=self,
                                       initialvalue=row.get("title") or "")
        if title and title.strip():
            _ap.rename(self._profile(), row["id"], title.strip())
            self.refresh()

    def _folder(self):
        row = self._row_picked()
        if row and row.get("file") and os.path.exists(row["file"]):
            os.startfile(os.path.dirname(row["file"]))

    def _open(self):
        ids = self.picked()
        for row in self.rows:
            if ids and row["id"] == ids[0] and row["file"] and os.path.isfile(row["file"]):
                os.startfile(row["file"])
                return

    def _say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M ") + str(text).rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_event(self, kind, payload):
        if kind == "log":
            self._say(payload)
        elif kind == "stage":
            self.lbl_stage.configure(text=payload)
        elif kind == "progress":
            value, note = payload
            self.progress.configure(value=max(0.0, min(1.0, value)) * 1000)
            if note:
                self.lbl_stage.configure(text=note)

    def _go(self, stage, work):
        if self.runner.busy():
            messagebox.showinfo("Занято", "Автопилот уже работает.", parent=self)
            return False
        self.btn_stop.set_enabled(True)
        self.btn_run.set_enabled(False)
        self.btn_post.set_enabled(False)
        self.lbl_stage.configure(text=stage)

        def finish(kind, payload):
            self.btn_stop.set_enabled(False)
            self.btn_run.set_enabled(True)
            self.btn_post.set_enabled(True)
            self.lbl_stage.configure(text="готов")
            if kind == "error":
                self._say("ОШИБКА: %s" % payload)
            self.refresh()

        self.runner.start(work, finish)
        return True

    def _pilot(self, run, profile):
        return _ap.Pilot(profile, log=run.log, progress=run.progress,
                         cancel=lambda: run.cancelled, judge=_ap.judge_for(profile))

    def _run_now(self):
        profile = self.save()
        if not profile:
            return
        if not _ap.settings(profile)["twitch"] and not _ap.Pilot(profile).waiting_streams():
            messagebox.showinfo("Автопилот", "Впишите канал твича.", parent=self)
            return

        def work(run):
            report = self._pilot(run, profile).run(post=False)
            run.log("новых записей: %d, в очередь: %d%s" % (
                report["new"], report["queued"],
                ("; ошибки: " + "; ".join(report["errors"])) if report["errors"] else ""))
            return report

        self._go("проверяю стримы…", work)

    def _show_service(self):
        from core import service as _svc

        on = _svc.running()
        names = [p["name"] for p in _ap.streamers()]
        self.btn_service.set_enabled(not on)
        self.btn_autostart.configure(text="Не запускать с Windows" if _svc.autostart_on()
                                     else "Запускать с Windows")
        self.lbl_service.configure(text="%s; стримеры: %s" % (
            "служба работает" if on else "служба не запущена",
            ", ".join(names) if names else "нет — впишите твич или телеграм и сохраните"))

    def _service_start(self):
        from core import service as _svc

        if not self.save():
            return
        if not _svc.running():
            _svc.launch()
            self._say("служба запускается — значок появится в трее")
        self.after(3000, self._show_service)

    def _autostart(self):
        from core import service as _svc

        on = _svc.autostart(not _svc.autostart_on())
        self._say("служба будет запускаться с Windows" if on else
                  "служба больше не запускается с Windows")
        self._show_service()

    def after_live(self, profile, done):
        """Эфир кончился: дождаться записи твича и нарезать её, не трогая окно."""
        if self.runner.busy():
            self.after(TICK_MS, lambda: self.after_live(profile, done))
            return

        def work(run):
            report = self._pilot(run, profile).after_live(started=done.get("started") or 0.0)
            if report:
                run.log("после эфира: в очередь %d клипов%s" % (
                    report["queued"],
                    ("; ошибки: " + "; ".join(report["errors"])) if report["errors"] else ""))
            return report

        self._go("жду запись эфира на твиче…", work)

    def _post_now(self):
        profile = self.save()
        if not profile:
            return
        if not _ap.queue(profile, (_ap.APPROVED,)):
            messagebox.showinfo("Автопилот", "Нет одобренных клипов.", parent=self)
            return

        def work(run):
            return self._pilot(run, profile).post_next(force=True)

        self._go("выкладываю…", work)

    def _tick(self):
        try:
            profile = self._profile()
            if (profile and _ap.settings(profile)["post_while_open"]
                    and not self.runner.busy() and _ap.queue(profile, (_ap.APPROVED,))
                    and _ap.next_post_at(profile) <= time.time()):
                self._go("выкладываю по расписанию…",
                         lambda run: self._pilot(run, profile).post_next())
        finally:
            self.after(TICK_MS, self._tick)

    def _show_task(self):
        code, text = _ap.task_run(_ap.task_query_command())
        self.lbl_task.configure(text="запускается сам каждый час" if code == 0 else
                                "сам не запускается")

    def _task_on(self):
        if not self.save():
            return
        if not messagebox.askyesno(
                "Автопилот", "Создать задачу в планировщике Windows: каждый час проверять "
                "новые стримы, нарезать и выкладывать одобренные клипы? Компьютер должен "
                "быть включён, вход в Windows выполнен.", parent=self):
            return
        code, text = _ap.task_run(_ap.task_on_command(_ap.launcher()))
        self._say(text or ("готово" if code == 0 else "ошибка %d" % code))
        self._show_task()

    def _task_off(self):
        code, text = _ap.task_run(_ap.task_off_command())
        self._say(text or ("готово" if code == 0 else "ошибка %d" % code))
        self._show_task()
