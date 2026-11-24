from __future__ import annotations

import os
import re
import time
import tkinter as tk
from tkinter import messagebox, ttk

from core import upload as _up

from . import theme
from .runner import Runner

CLIP_EXT = (".mp4", ".mov", ".mkv", ".webm")
LISTED = 300
_STAMP = re.compile(r"[\s_\-\[(]*(?<!\d)\d{1,2}([-.:])\d{2}\1\d{2}(?!\d)[\s_\-\])]*")


def nice_title(path) -> str:
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    stem = _STAMP.sub(" ", stem).replace("_", " ")
    stem = re.sub(r"^\s*(?:0\d\s+|\d{1,3}\s*[-.)]\s*)", "", stem)
    return re.sub(r"\s+", " ", stem).strip()[:100]


def ready_clips(folders, limit=LISTED) -> list:
    found = {}
    for folder in folders or ():
        for top, _dirs, names in os.walk(str(folder)):
            for name in names:
                if not name.lower().endswith(CLIP_EXT):
                    continue
                path = os.path.join(top, name)
                try:
                    found[os.path.normcase(os.path.abspath(path))] = (
                        os.path.getmtime(path), os.path.abspath(path))
                except OSError:
                    continue
    ordered = sorted(found.values(), reverse=True)
    return [path for _when, path in ordered[:limit]]


class UploadPanel(tk.Frame):
    def __init__(self, master, studio):
        super().__init__(master, background=theme.BG)
        self.studio = studio
        cfg = studio.cfg
        self.accounts = dict(cfg.get("upload_accounts") or {})
        self.v_service = tk.StringVar(
            value=dict(_up.SERVICES).get(cfg.get("upload_service", "youtube"),
                                         _up.SERVICE_TITLES[0]))
        self.v_account = tk.StringVar(
            value=self.accounts.get(_up.service_key(self.v_service.get()), "main"))
        self.v_cdp = tk.StringVar(value=cfg.get("upload_cdp", ""))
        self.channels = dict(cfg.get("upload_channels") or {})
        self.v_channel = tk.StringVar(value=self.channels.get(self.v_account.get(), ""))
        self.browsers = _up.installed_browsers()
        keys = [key for key, _title in self.browsers]
        chosen = cfg.get("upload_browser") or _up.default_browser()
        if chosen not in keys:
            chosen = _up.default_browser()
        self.v_browser = tk.StringVar(value=dict(self.browsers).get(chosen, ""))
        _up.set_browser(chosen)
        self.v_visibility = tk.StringVar(
            value=dict(_up.VISIBILITY).get(cfg.get("upload_visibility", "public"),
                                           _up.VISIBILITY_TITLES[0]))
        self.v_headed = tk.BooleanVar(value=bool(cfg.get("upload_headed", True)))
        self.v_title = tk.StringVar()
        self.v_desc = tk.StringVar(value=cfg.get("upload_desc", ""))
        self.v_tags = tk.StringVar(value=cfg.get("upload_tags", ""))
        self.v_folder = tk.StringVar(value=cfg.get("upload_folder", ""))
        self.auto_title = ""
        self.files = []
        self.runner = Runner(self)
        self.runner.listen(self._on_event)
        self.slot = self.v_service.get()
        self._build()
        self._service_changed()

    def _card(self, master, title):
        outer = ttk.Frame(master, style="Panel.TFrame")
        outer.pack(fill="x", pady=(0, 10))
        inner = ttk.Frame(outer, style="Panel.TFrame")
        inner.pack(fill="x", padx=14, pady=12)
        ttk.Label(inner, text=title, style="PanelDim.TLabel",
                  font=theme.SMALL_FONT).pack(anchor="w", pady=(0, 8))
        return inner

    def _build(self):
        scroll = theme.Scrolled(self)
        scroll.pack(fill="both", expand=True)
        page = scroll.body

        card = self._card(page, "ПЛОЩАДКА И ВХОД")
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="площадка", style="PanelDim.TLabel").pack(side="left")
        box = ttk.Combobox(row, textvariable=self.v_service, width=10, state="readonly",
                           values=_up.SERVICE_TITLES)
        box.pack(side="left", padx=(6, 14))
        box.bind("<<ComboboxSelected>>", lambda _e: self._service_changed())
        ttk.Label(row, text="аккаунт", style="PanelDim.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.v_account, width=16).pack(side="left",
                                                                   padx=(6, 14))
        self.btn_login = theme.Button(row, "Войти", self._login, primary=True)
        self.btn_login.pack(side="left")
        self.btn_check = theme.Button(row, "Проверить", self._check)
        self.btn_check.pack(side="left", padx=(8, 0))
        self.lbl_state = ttk.Label(row, text="вход не проверен", style="PanelDim.TLabel")
        self.lbl_state.pack(side="left", padx=(12, 0))

        row0 = ttk.Frame(card, style="Panel.TFrame")
        row0.pack(fill="x", pady=(8, 0))
        ttk.Label(row0, text="Канал", style="PanelDim.TLabel").pack(side="left")
        self.ent_channel = ttk.Entry(row0, textvariable=self.v_channel, width=36)
        self.ent_channel.pack(side="left", padx=(6, 10))
        ttk.Label(row0, style="PanelDim.TLabel", text=(
            "ссылка на канал или @имя; пусто — канал, открытый при входе")).pack(side="left")

        row1 = ttk.Frame(card, style="Panel.TFrame")
        row1.pack(fill="x", pady=(8, 0))
        ttk.Label(row1, text="Браузер", style="PanelDim.TLabel").pack(side="left")
        pick = ttk.Combobox(row1, textvariable=self.v_browser, state="readonly", width=24,
                            values=[title for _key, title in self.browsers])
        pick.pack(side="left", padx=(6, 10))
        pick.bind("<<ComboboxSelected>>", lambda _e: self._browser_changed())
        ttk.Label(row1, style="PanelDim.TLabel", text=(
            "любой на движке Chromium; Firefox не подходит")).pack(side="left")

        row2 = ttk.Frame(card, style="Panel.TFrame")
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="Chrome", style="PanelDim.TLabel").pack(side="left")
        ttk.Entry(row2, textvariable=self.v_cdp, width=26).pack(side="left", padx=(6, 10))
        ttk.Label(row2, style="PanelDim.TLabel", text=(
            "пусто — браузер выше; адрес вида %s — уже открытый браузер" % _up.CDP_DEFAULT)
        ).pack(side="left")
        self.lbl_hint = ttk.Label(card, style="PanelDim.TLabel", justify="left",
                                  wraplength=860, text="")
        self.lbl_hint.pack(anchor="w", pady=(8, 0))

        card2 = self._card(page, "ГОТОВЫЕ КЛИПЫ")
        pick = ttk.Frame(card2, style="Panel.TFrame")
        pick.pack(fill="x", pady=(0, 8))
        ttk.Label(pick, text="папка", style="PanelDim.TLabel").pack(side="left")
        ttk.Entry(pick, textvariable=self.v_folder).pack(side="left", fill="x", expand=True,
                                                        padx=(6, 8))
        theme.Button(pick, "Выбрать…", self._pick_folder).pack(side="left")
        theme.Button(pick, "Папки Студии", self._studio_folders).pack(side="left",
                                                                     padx=(8, 0))
        box = ttk.Frame(card2, style="Panel.TFrame")
        box.pack(fill="x")
        self.tree = ttk.Treeview(box, columns=("when", "name", "done"),
                                 show="headings", height=10, selectmode="extended")
        bar = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        for name, title, width in (("when", "смонтирован", 130), ("name", "файл", 520),
                                   ("done", "выложен", 140)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, anchor="w")
        self.tree.pack(side="left", fill="x", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._picked())
        row3 = ttk.Frame(card2, style="Panel.TFrame")
        row3.pack(fill="x", pady=(8, 0))
        theme.Button(row3, "Обновить список", self.refresh).pack(side="left")
        self.lbl_where = ttk.Label(row3, text="", style="PanelDim.TLabel")
        self.lbl_where.pack(side="left", padx=(12, 0))

        form = ttk.Frame(card2, style="Panel.TFrame")
        form.pack(fill="x", pady=(10, 0))
        for index, (label, var) in enumerate((("название", self.v_title),
                                              ("описание", self.v_desc),
                                              ("теги", self.v_tags))):
            ttk.Label(form, text=label, style="PanelDim.TLabel", width=12).grid(
                row=index, column=0, sticky="w", pady=2)
            ttk.Entry(form, textvariable=var).grid(row=index, column=1, sticky="ew", pady=2)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="через запятую: стрим, нарезка", style="PanelDim.TLabel").grid(
            row=2, column=2, sticky="w", padx=(8, 0))

        row4 = ttk.Frame(card2, style="Panel.TFrame")
        row4.pack(fill="x", pady=(10, 0))
        ttk.Label(row4, text="видимость", style="PanelDim.TLabel").pack(side="left")
        self.box_visibility = ttk.Combobox(row4, textvariable=self.v_visibility, width=12,
                                           state="readonly", values=_up.VISIBILITY_TITLES)
        self.box_visibility.pack(side="left", padx=(6, 14))
        self.box_headed = ttk.Checkbutton(row4, text="показывать браузер",
                                          variable=self.v_headed)
        self.box_headed.pack(side="left")
        self.btn_send = theme.Button(row4, "Выложить выбранные", self._send, primary=True)
        self.btn_send.pack(side="left", padx=(14, 0))
        self.btn_stop = theme.Button(row4, "Остановить", self.runner.cancel)
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.btn_stop.set_enabled(False)

        ttk.Label(card2, style="PanelDim.TLabel", wraplength=860, justify="left", text=(
            "Несколько клипов — Ctrl или Shift в списке, выкладываются по очереди. "
            "Название одно на все; пусто — у каждого из имени файла.")).pack(
            anchor="w", pady=(6, 0))

        card3 = self._card(page, "ЖУРНАЛ")
        self.lbl_stage = ttk.Label(card3, text="готов", style="PanelDim.TLabel")
        self.lbl_stage.pack(anchor="w")
        self.progress = ttk.Progressbar(card3, maximum=1000)
        self.progress.pack(fill="x", pady=(4, 6))
        self.log = tk.Text(card3, height=8, background=theme.PANEL2,
                           foreground=theme.DIM, insertbackground=theme.TEXT,
                           font=theme.MONO_FONT, relief="flat", wrap="word",
                           borderwidth=0, highlightthickness=0)
        self.log.pack(fill="x")
        self.log.configure(state="disabled")
        row5 = ttk.Frame(card3, style="Panel.TFrame")
        row5.pack(fill="x", pady=(8, 0))
        self.btn_views = theme.Button(row5, "Обновить просмотры", self._views)
        self.btn_views.pack(side="left")
        self.btn_taste = theme.Button(row5, "Правила вкуса", self._taste)
        self.btn_taste.pack(side="left", padx=(8, 0))
        self.lbl_views = ttk.Label(row5, style="PanelDim.TLabel", text=(
            "YouTube: откроется окно браузера с тем же входом, программа только читает "
            "список роликов в YouTube Studio"))
        self.lbl_views.pack(side="left", padx=(12, 0))
        self.past = ttk.Treeview(card3, columns=("when", "service", "name", "link", "views"),
                                 show="headings", height=5)
        for name, title, width in (("when", "когда", 130), ("service", "куда", 90),
                                   ("name", "клип", 330), ("link", "ссылка", 240),
                                   ("views", "просмотры", 90)):
            self.past.heading(name, text=title)
            self.past.column(name, width=width, anchor="w")
        self.past.pack(fill="x", pady=(8, 0))

    def _profile(self):
        try:
            from core import profiles as _pro
            return _pro.active()
        except Exception:
            return None

    def db(self):
        return _up.db_for(self._profile())

    def _service_changed(self):
        service = _up.service_key(self.v_service.get())
        before = _up.service_key(self.slot)
        if before != service:
            self.accounts[before] = self.v_account.get().strip()
            self.v_account.set(self.accounts.get(service, "") or "main")
        self.slot = self.v_service.get()
        self.lbl_state.configure(text="вход не проверен")
        if service == "youtube":
            self.box_visibility.configure(state="readonly")
            self.ent_channel.configure(state="normal")
            self.box_headed.configure(state="disabled")
            self.lbl_hint.configure(text=_up.CDP_HINT)
        else:
            self.box_visibility.configure(state="disabled")
            self.ent_channel.configure(state="disabled")
            self.box_headed.configure(state="normal")
            self.lbl_hint.configure(text=(
                "TikTok: войдите в открывшемся окне любым способом, вход сохранится сам. "
                "Видимость TikTok задаётся на сайте, отсюда — как в настройках аккаунта."))

    def _pick_folder(self):
        from tkinter import filedialog

        chosen = filedialog.askdirectory(parent=self, title="Папка с готовыми клипами",
                                         initialdir=self.v_folder.get() or None)
        if chosen:
            self.v_folder.set(os.path.normpath(chosen))
            self.refresh()

    def _studio_folders(self):
        self.v_folder.set("")
        self.refresh()

    def folders(self):
        own = self.v_folder.get().strip()
        if own:
            return [own] if os.path.isdir(own) else []
        return self.studio._clip_folders()

    def refresh(self):
        folders = self.folders()
        try:
            marks = _up.posted_files(self.db())
        except Exception:
            marks = {}
        keep = self.selected()
        self.tree.delete(*self.tree.get_children())
        self.files = ready_clips(folders)
        for index, path in enumerate(self.files):
            try:
                when = time.strftime("%d.%m %H:%M", time.localtime(os.path.getmtime(path)))
            except OSError:
                when = ""
            done = marks.get(os.path.normcase(path), ())
            shown = ", ".join(dict(_up.SERVICES).get(item, item) for item in sorted(done))
            self.tree.insert("", "end", iid=str(index),
                             values=(when, os.path.basename(path), shown))
            if keep and os.path.normcase(keep) == os.path.normcase(path):
                self.tree.selection_set(str(index))
        if self.v_folder.get().strip() and not folders:
            where = "нет такой папки: %s" % self.v_folder.get().strip()
        elif folders:
            where = "из %s — клипов: %d" % (", ".join(folders), len(self.files))
        else:
            where = "клипов не видно: выберите папку или смонтируйте клипы на «Монтаж 9:16»"
        self.lbl_where.configure(text=where)
        self._redraw_past()

    def _redraw_past(self):
        self.past.delete(*self.past.get_children())
        try:
            rows = _up.history(self.db(), limit=30)
        except Exception as error:
            self._say("Журнал выгрузок не прочитался: %s" % error)
            return
        for row in rows:
            when = time.strftime("%d.%m %H:%M", time.localtime(row.get("posted_at") or 0))
            self.past.insert("", "end", values=(
                when, dict(_up.SERVICES).get(row["service"], row["service"]),
                os.path.basename(str(row.get("file") or "")),
                row.get("url") or row.get("video_id") or "—",
                "" if row.get("views") is None else row["views"]))

    def selected(self):
        chosen = self.tree.selection()
        if not chosen:
            return ""
        try:
            return self.files[int(chosen[0])]
        except (ValueError, IndexError):
            return ""

    def selected_all(self):
        out = []
        for item in self.tree.selection():
            try:
                out.append(self.files[int(item)])
            except (ValueError, IndexError):
                continue
        return out

    def _picked(self):
        path = self.selected()
        if not path:
            return
        if not self.v_title.get().strip() or self.v_title.get() == self.auto_title:
            self.auto_title = nice_title(path)
            self.v_title.set(self.auto_title)

    def _say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text).rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _busy(self, value):
        for button in (self.btn_login, self.btn_check, self.btn_send, self.btn_views):
            button.set_enabled(not value)
        self.btn_stop.set_enabled(value)

    def _go(self, stage, work, done):
        if self.runner.busy():
            messagebox.showinfo("Занято", "Дождитесь окончания выгрузки.", parent=self)
            return False
        if not _up.available():
            self._say(_up.install_advice())
            messagebox.showinfo("Нет программы выгрузки", _up.install_advice(), parent=self)
            return False
        self._busy(True)
        self.progress.configure(value=0)
        self.lbl_stage.configure(text=stage)

        def finish(kind, payload):
            self._busy(False)
            self.lbl_stage.configure(text="готов")
            if kind == "error":
                self._say("ОШИБКА: %s" % payload)
                messagebox.showerror("Выгрузка", str(payload), parent=self)
            done(kind, payload)

        self.runner.start(work, finish)
        return True

    def _on_event(self, kind, payload):
        if kind == "log":
            self._say(payload)
        elif kind == "progress":
            value, note = payload
            self.progress.configure(value=max(0.0, min(1.0, value)) * 1000)
            if note:
                self.lbl_stage.configure(text=note)

    def _who(self):
        service = _up.service_key(self.v_service.get())
        account = self.v_account.get().strip()
        if not account:
            messagebox.showinfo("Аккаунт", "Впишите любое имя аккаунта, например main.",
                                parent=self)
            return None, None
        self.accounts[service] = account
        return service, account

    def _login(self):
        service, account = self._who()
        if not service:
            return
        cdp = self.v_cdp.get().strip()
        self._say("Вход в %s (%s): откроется браузер, войдите сами — пароль программа "
                  "не вводит." % (dict(_up.SERVICES)[service], account))

        def work(run):
            return _up.login(service, account, cdp_url=cdp, log=run.log,
                             cancel=lambda: run.cancelled)

        def done(kind, _payload):
            if kind == "done":
                self.lbl_state.configure(text="вход сохранён")
                self._say("Вход сохранён.")
            elif service == "youtube" and not cdp:
                self._say(_up.CDP_HINT)

        self._go("вход…", work, done)

    def _check(self):
        service, account = self._who()
        if not service:
            return

        def work(run):
            return _up.check(service, account, log=run.log, cancel=lambda: run.cancelled)

        def done(kind, payload):
            if kind != "done":
                return
            self.lbl_state.configure(text="вход действует" if payload
                                     else "входа нет — нажмите «Войти»")
            self._say("Вход %s." % ("действует" if payload else "не действует"))

        self._go("проверяю вход…", work, done)

    def _send(self):
        paths = self.selected_all()
        if not paths:
            messagebox.showinfo("Клип", "Выберите готовые клипы в списке.", parent=self)
            return
        service, account = self._who()
        if not service:
            return
        title = self.v_title.get().strip()
        if len(paths) > 1 and title == self.auto_title:
            title = ""
        if not title and len(paths) == 1:
            messagebox.showinfo("Название", "Впишите название клипа.", parent=self)
            return
        desc = self.v_desc.get().strip()
        tags = self.v_tags.get().strip()
        visibility = _up.visibility_key(self.v_visibility.get())
        headed = bool(self.v_headed.get()) or service == "youtube"
        channel = self.v_channel.get().strip() if service == "youtube" else ""
        if service == "youtube":
            self.channels[account] = channel
        db = self.db()
        profile = self._profile()
        self._say("Выкладываю %d в %s (%s)…" % (len(paths), dict(_up.SERVICES)[service],
                                                account))

        def work(run):
            made = []
            for number, path in enumerate(paths, 1):
                if run.cancelled:
                    run.log("Остановлено, выложено %d из %d" % (len(made), len(paths)))
                    break
                name = title or nice_title(path)
                run.log("[%d/%d] %s" % (number, len(paths), os.path.basename(path)))

                def part(value, note="", _number=number):
                    run.progress(((_number - 1) + max(0.0, min(1.0, value))) / len(paths), note)

                try:
                    done = _up.upload(service, account, path, name, desc=desc, tags=tags,
                                      visibility=visibility, headed=headed, log=run.log,
                                      progress=part, cancel=lambda: run.cancelled,
                                      channel=channel)
                except Exception as error:
                    if not made:
                        raise
                    run.log("ОШИБКА на %s: %s" % (os.path.basename(path), error))
                    run.log("Остальные не выкладываю: выложено %d из %d"
                            % (len(made), len(paths)))
                    break
                _up.record(db, done)
                made.append(done)
            if profile and made:
                try:
                    from core import learning as _learn
                    from core import profiles as _pro
                    _learn.link_databases(_pro.db_path(profile), db)
                except Exception as error:
                    run.log("Связь с кандидатами не обновилась: %s" % error)
            return made

        def done(kind, payload):
            if kind != "done":
                return
            for item in payload or ():
                where = item.get("url") or item.get("video_id") or "ссылку сайт не показал"
                self._say("Выложено: %s — %s (%s)" % (item.get("title"), where,
                                                    item.get("visibility") or ""))
            self.refresh()

        self._go("выкладываю…", work, done)

    def _views(self):
        if _up.service_key(self.v_service.get()) == "youtube":
            account = self.v_account.get().strip()
            channel = self.v_channel.get().strip()
        else:
            account = str(self.accounts.get("youtube") or "main").strip()
            channel = self.channels.get(account, "")
        if not account:
            messagebox.showinfo("Аккаунт", "Впишите имя аккаунта YouTube, например main.",
                                parent=self)
            return
        db = self.db()
        profile = self._profile()
        self._say("Читаю просмотры YouTube (%s): откроется окно браузера, ничего не "
                  "нажимайте и не закрывайте его." % account)
        taste_ready = self._taste_engine()

        def work(run):
            rows = _up.read_views(account, channel=channel, log=run.log,
                                  cancel=lambda: run.cancelled)
            report = _up.match_views(db, rows, account=account)
            if profile and taste_ready:
                try:
                    report["taste"] = self._build_taste(run, profile, db, taste_ready)
                except Exception as error:
                    run.log("Правила вкуса не обновились: %s" % str(error).splitlines()[0])
            if profile:
                try:
                    from core import learning as _learn
                    from core import profiles as _pro
                    report["learn"] = _learn.link_databases(_pro.db_path(profile), db)
                except Exception as error:
                    run.log("Связь с кандидатами не обновилась: %s" % error)
            return report

        def done(kind, report):
            if kind != "done":
                return
            self._say("В Studio роликов: %d; узнано выгрузок: %d (новых ссылок %d), "
                      "просмотры записаны у %d." % (report["rows"], report["matched"],
                                                    report["linked"], report["stats"]))
            if report["unmatched"]:
                self._say("Не нашлись в Studio: %s" % "; ".join(
                    title or "без названия" for title in report["unmatched"][:10]))
            learn = report.get("learn") or {}
            if learn.get("written"):
                self._say("Модель вкуса получит исходы %d клипов." % learn["written"])
            if report.get("taste"):
                self._show_taste(report["taste"])
            self.lbl_views.configure(text="просмотры обновлены %s"
                                     % time.strftime("%d.%m %H:%M"))
            self._redraw_past()

        self._go("читаю просмотры…", work, done)

    def _taste_engine(self):
        try:
            from clipper.backends import get_backend

            cfg = self.studio._clipper_config()
            backend = get_backend(cfg)
        except Exception:
            return None
        if not callable(getattr(backend, "complete", None)):
            return None
        return backend, cfg, self.studio.model.get(), self.studio.language.get().strip()

    def _build_taste(self, run, profile, db, engine):
        from core import taste as _taste

        backend, cfg, model, language = engine
        run.log("Ищу, чем отличаются сильные клипы от слабых…")
        return _taste.build(profile, db, backend, cfg,
                            transcribe=_taste.whisper_reader(model, language),
                            log=run.log)

    def _show_taste(self, data):
        rules = data.get("rules") or []
        self._say("Правила вкуса по %d клипам%s:" % (
            data.get("based_on", 0), (" — " + data["note"]) if data.get("note") else ""))
        for rule in rules:
            self._say("  • " + rule)
        if not rules:
            self._say("  модель не нашла устойчивой разницы")
        self._say("Поиск «По смыслу» теперь учитывает эти правила.")

    def _taste(self):
        profile = self._profile()
        if not profile:
            messagebox.showinfo("Профиль", "Сначала выберите профиль стримера.", parent=self)
            return
        engine = self._taste_engine()
        if engine is None:
            messagebox.showinfo(
                "Правила вкуса", "Нужен движок с языковой моделью: на вкладке «Клипы» "
                "выберите openai-compat (LM Studio), ollama или claude.", parent=self)
            return
        from core import taste as _taste

        current = _taste.load(profile)
        if current.get("rules"):
            self._say("Сейчас действуют правила от %s:" % time.strftime(
                "%d.%m %H:%M", time.localtime(current.get("made_at") or 0)))
            for rule in current["rules"]:
                self._say("  • " + rule)
        db = self.db()

        def work(run):
            return self._build_taste(run, profile, db, engine)

        def done(kind, data):
            if kind == "done" and data:
                self._show_taste(data)

        self._go("собираю правила вкуса…", work, done)

    def remember(self, cfg):
        self.accounts[_up.service_key(self.v_service.get())] = self.v_account.get().strip()
        if _up.service_key(self.v_service.get()) == "youtube" and self.v_account.get().strip():
            self.channels[self.v_account.get().strip()] = self.v_channel.get().strip()
        cfg.update({
            "upload_service": _up.service_key(self.v_service.get()),
            "upload_accounts": dict(self.accounts),
            "upload_cdp": self.v_cdp.get().strip(),
            "upload_browser": self._browser_key(),
            "upload_channels": dict(self.channels),
            "upload_folder": self.v_folder.get().strip(),
            "upload_visibility": _up.visibility_key(self.v_visibility.get()),
            "upload_headed": bool(self.v_headed.get()),
            "upload_desc": self.v_desc.get().strip(),
            "upload_tags": self.v_tags.get().strip(),
        })

    def _browser_key(self):
        title = self.v_browser.get()
        for key, name in self.browsers:
            if name == title:
                return key
        return _up.default_browser()

    def _browser_changed(self):
        _up.set_browser(self._browser_key())
        self._say("браузер для выгрузки: %s" % self.v_browser.get())

    def stop(self):
        self.runner.cancel()
