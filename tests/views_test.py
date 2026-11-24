# -*- coding: utf-8 -*-
"""Просмотры YouTube без API: чтение списка роликов в YouTube Studio.

Вместо Studio — локальная страница со строками ytcp-video-row, как на вкладке
«Контент», с переходом на вторую страницу. Её читает настоящая обёртка
core/sau_bridge.py в браузере окружения sau (если оно установлено). Разбор
строк, сопоставление с выгрузками и кнопка во вкладке «Выгрузка» проверяются
без сети и браузера.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

work = tempfile.mkdtemp(prefix="views_test_")
os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(work, "settings.json")
os.environ["ASTRA_PROFILES"] = os.path.join(work, "profiles")

import tkinter.messagebox as _mb
DIALOGS = []
_mb.showerror = lambda *a, **k: DIALOGS.append(a)
_mb.showinfo = lambda *a, **k: DIALOGS.append(a)

from core import learning as _learn
from core import profiles as _pro
from core import upload as up

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-60s %s" % (label, "ок" if value else "ПРОВАЛ"))


def day(text):
    return time.mktime(time.strptime(text + " 15:00", "%Y-%m-%d %H:%M"))


ROW = """
<ytcp-video-row>
  <div class="tablecell-video">
    <a id="thumbnail-anchor" href="https://youtube.com/shorts/{vid}"><img></a>
    <a id="video-title" href="/video/{vid}/edit">{title}</a>
  </div>
  <div class="tablecell-visibility">{access}</div>
  <div class="tablecell-restrictions">Нет</div>
  <div class="tablecell-date">{date}<br>Опубликовано</div>
  <div class="tablecell-views">{views}</div>
  <div class="tablecell-comments">{comments}</div>
  <div class="tablecell-likes">{likes}</div>
</ytcp-video-row>"""
FIRST = [("AbCdEf12345", "Смешной момент", "Открытый доступ", "21 окт. 2026 г.", "1&nbsp;234",
          "5", "97,0 %<br>40 отметок «Нравится»"),
         ("QwErTy67890", "Смешной момент", "Открытый доступ", "2 окт. 2026 г.", "88", "0", "–"),
         ("ZxCvBn13579", "Чужой ролик", "Ограниченный доступ", "20 окт. 2026 г.", "3,4 тыс.",
          "12", "")]
SECOND = [("PoIuYt24680", "Старый клип про босса", "Открытый доступ", "1 сент. 2026 г.",
           "15", "1", "")]


def rows_html(rows):
    return "".join(ROW.format(vid=v, title=t, access=a, date=d, views=n, comments=c, likes=l)
                   for v, t, a, d, n, c, l in rows)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Контент канала</title></head>
<body><ytcp-video-section-content id="list">%s</ytcp-video-section-content>
<ytcp-table-footer><button id="navigate-after" aria-label="Следующая страница">›</button>
</ytcp-table-footer>
<script>
document.getElementById('navigate-after').addEventListener('click', function () {
  var button = this;
  setTimeout(function () {
    document.getElementById('list').innerHTML = %s;
    button.setAttribute('disabled', '');
  }, 300);
});
</script></body></html>"""

import json

page = Path(work) / "studio.html"
page.write_text(PAGE % (rows_html(FIRST), json.dumps(rows_html(SECOND))), encoding="utf-8")

raw = {"title": "Смешной момент", "tab": "short",
       "links": ["https://youtube.com/shorts/AbCdEf12345",
                 "https://studio.youtube.com/video/AbCdEf12345/edit"],
       "visibility": "Открытый доступ", "date": "21 окт. 2026 г.\nОпубликовано",
       "views": "1\xa0234", "comments": "5", "likes": "97,0 %\n40 отметок «Нравится»"}
row = up.studio_row(raw)
check("номер ролика из ссылки в строке", row["video_id"] == "AbCdEf12345")
check("ссылка на Shorts", row["url"] == "https://youtube.com/shorts/AbCdEf12345")
check("просмотры с неразрывным пробелом", row["views"] == 1234)
check("лайки — число, а не проценты", row["likes"] == 40)
check("дата по-русски", time.strftime("%Y-%m-%d", time.localtime(row["day"])) == "2026-10-21")
check("дата по-английски",
      time.strftime("%m-%d", time.localtime(up.day_of("Oct 2, 2026\nPublished"))) == "10-02")
check("«3,4 тыс.» — 3400", up.count_of("3,4 тыс.") == 3400)
check("«1.2K» — 1200", up.count_of("1.2K") == 1200)
check("прочерк — нет числа", up.count_of("–") is None)
check("строки без номера ролика пропускаются",
      up.studio_rows(["ROW " + json.dumps({"title": "черновик", "links": []}),
                      "ROW " + json.dumps(raw), "ROW " + json.dumps(raw), "шум"])
      == [row])
check("длинное название, обрезанное многоточием, узнаётся",
      up.same_title("Очень длинное название клипа про стрим и босса",
                    "Очень длинное название клипа про…"))
check("разные названия не путаются", not up.same_title("Смешной момент", "Смешной"))

profile = _pro.save({"name": "тест"})
_pro.set_active("тест")
db = up.db_for(_pro.active())
clip = os.path.join(work, "01 Смешной момент.mp4")
stream = os.path.join(work, "стрим.mp4")
base = {"service": "youtube", "account": "main", "video_id": "", "url": "",
        "file": clip, "title": "Смешной момент", "visibility": "public"}
origin = {"source": stream, "start": 750.0, "finish": 790.0}
fresh = up.record(db, base, origin=origin)
older = up.record(db, dict(base, file=clip + ".2.mp4"), origin={"source": stream, "start": 100.0, "finish": 130.0})
boss = up.record(db, dict(base, title="Старый клип про босса",
                          file=clip + ".3.mp4"),
                 origin={"source": stream, "start": 2000.0, "finish": 2030.0})
lost = up.record(db, dict(base, title="Не дошёл до YouTube",
                          file=clip + ".4.mp4"),
                 origin={"source": stream, "start": 3000.0, "finish": 3030.0})
link = sqlite3.connect(str(db))
for post_id, when in ((fresh, "2026-10-21"), (older, "2026-10-02"), (boss, "2026-09-01"),
                      (lost, "2026-10-21")):
    link.execute("UPDATE posts SET posted_at = ? WHERE id = ?", (day(when), post_id))
link.commit()
link.close()

clipper_db = _pro.db_path(profile)
link = sqlite3.connect(str(clipper_db))
link.executescript("""
    CREATE TABLE videos (id INTEGER PRIMARY KEY, path TEXT, duration REAL, language TEXT,
                         created_at REAL);
    CREATE TABLE candidates (id INTEGER PRIMARY KEY, video_id INTEGER, start REAL, end REAL,
                             title TEXT, hook TEXT, reason TEXT, model_score REAL,
                             features_json TEXT, model TEXT, created_at REAL);
    CREATE TABLE ratings (id INTEGER PRIMARY KEY, candidate_id INTEGER, rating REAL);
""")
link.execute("INSERT INTO videos VALUES (1, ?, 3600, 'ru', 0)", (os.path.abspath(stream),))
link.execute("INSERT INTO candidates (id, video_id, start, end, created_at)"
             " VALUES (5, 1, 748, 792, 0)")
link.commit()
link.close()

sau_root = os.path.join(_REPO, "tools", up.FOLDER)
sau_python = os.path.join(sau_root, ".venv", "Scripts", "python.exe")
live = []
if os.path.isfile(sau_python):
    os.environ[up.ENV] = sau_root
    up.set_browser(up.BUILTIN)
    said = []
    try:
        live = up.read_views("views_test", page=page.as_uri(), headless=True, log=said.append)
    except Exception as error:
        said.append("ОШИБКА %s" % error)
    up.set_browser("")
    check("подставная Studio прочитана браузером, обе страницы", len(live) == 4)
    check("со страницы взяты номер, просмотры и дата",
          any(item["video_id"] == "AbCdEf12345" and item["views"] == 1234
              and item["likes"] == 40 and item["day"] for item in live))
    check("вторая страница списка тоже прочитана",
          any(item["video_id"] == "PoIuYt24680" for item in live))
    check("сырые строки не сыплются в журнал", not any(line.startswith("ROW ")
                                                      for line in said))
    if len(live) != 4:
        print("\n".join(said[-15:]))
    check("файл входа для проверки не появился",
          not os.path.exists(os.path.join(sau_root, "cookies", "youtube_views_test.json")))
else:
    print("  (окружения sau нет — браузерная часть пропущена)")
    live = up.studio_rows("ROW " + json.dumps({
        "title": t, "tab": "short", "links": ["https://youtube.com/shorts/" + v],
        "date": d, "views": n.replace("&nbsp;", "\xa0"), "comments": c,
        "likes": l.replace("<br>", "\n")}) for v, t, a, d, n, c, l in FIRST + SECOND)

report = up.match_views(db, live, account="main", now=day("2026-10-22"))
past = {item["id"]: item for item in up.history(db)}
check("два ролика с одним названием разведены по дате",
      past[fresh]["video_id"] == "AbCdEf12345" and past[older]["video_id"] == "QwErTy67890")
check("ссылка записана в выгрузку", past[fresh]["url"] == "https://youtube.com/shorts/AbCdEf12345")
check("старый клип со второй страницы узнан", past[boss]["video_id"] == "PoIuYt24680")
check("чужой ролик канала не приписан выгрузке",
      "ZxCvBn13579" not in {item["video_id"] for item in past.values()})
check("невыложенный остался без номера и попал в отчёт",
      past[lost]["video_id"] == "" and report["unmatched"] == ["Не дошёл до YouTube"])
check("просмотры записаны в stats", past[fresh]["views"] == 1234 and report["stats"] == 3)
check("отчёт: узнано три, новых ссылок три", report["matched"] == 3 and report["linked"] == 3)

again = [dict(item, views=(item["views"] or 0) + 100) for item in live]
second = up.match_views(db, again, account="main", now=day("2026-10-22") + 3600)
check("повторное чтение узнаёт по номеру, без новых ссылок",
      second["matched"] == 3 and second["linked"] == 0)
check("свежие просмотры поверх старых",
      {item["id"]: item for item in up.history(db)}[fresh]["views"] == 1334)
check("другой аккаунт не трогает выгрузки main",
      up.match_views(db, live, account="другой")["matched"] == 0)

learned = _learn.link_databases(clipper_db, db)
check("learning получил просмотры", learned["matched"] == 1 and learned["no_stats"] == 0)
signal = [item for item in _learn.training_signal(clipper_db) if item["id"] == 5]
check("у кандидата теперь есть просмотры для реранкера",
      signal and signal[0]["views"] == 1334)

from studio.window import Studio

app = Studio()
app.update()
app._switch("clip")
app._switch_clip("upload")
app.update()
panel = app.uploader
check("кнопка «Обновить просмотры» на вкладке", panel.btn_views.winfo_ismapped())
check("в журнале выгрузок колонка просмотров",
      "views" in panel.past.cget("columns")
      and any(1334 == int(panel.past.item(item, "values")[4] or 0)
              for item in panel.past.get_children()))

asked = []
real_read = up.read_views


def fake_read(account, channel="", log=None, cancel=None, page="", headless=False):
    asked.append((account, channel))
    if log:
        log("канал: UCtest")
    return [dict(item, views=(item["views"] or 0) + 1000) for item in live]


up.read_views = fake_read
up.available = lambda: True
panel.v_service.set("YouTube")
panel._service_changed()
panel.v_account.set("main")
panel.v_channel.set("")
panel._views()
deadline = time.time() + 20
while panel.runner.busy() and time.time() < deadline:
    app.update()
    time.sleep(0.05)
for _ in range(10):
    app.update()
    time.sleep(0.05)
text = panel.log.get("1.0", "end")
check("кнопка читает аккаунт из поля", asked == [("main", "")])
check("в журнале окна итог", "узнано выгрузок: 3" in text and "Не дошёл до YouTube" in text)
check("в таблице свежие просмотры",
      any(int(panel.past.item(item, "values")[4] or 0) == 2234
          for item in panel.past.get_children()))
check("модели вкуса сказано про исходы", "Модель вкуса" in text)
up.read_views = real_read
app.destroy()

shutil.rmtree(work, ignore_errors=True)
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
