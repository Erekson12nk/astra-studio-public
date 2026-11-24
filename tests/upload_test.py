# -*- coding: utf-8 -*-
"""Выгрузка готовых клипов на YouTube и TikTok через внешнюю sau.

Настоящая sau (social-auto-upload) живёт в своём окружении Python 3.12 и
открывает браузер. Здесь вместо неё подставной скрипт: печатает то же, что
настоящая, и запоминает, с какими аргументами его позвали. TikTok идёт через
нашу обёртку core/sau_bridge.py — под неё подложены поддельные sau_cli и
uploader.tk_uploader. Сеть и браузер не нужны.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import textwrap
import time

work = tempfile.mkdtemp(prefix="upload_test_")
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


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(textwrap.dedent(text))


os.environ[up.ENV] = os.path.join(work, "нет-такой-папки")
check("без sau — не найдена", not up.available())
try:
    up.command("youtube", "check", "main")
    check("без sau команда не строится", False)
except up.Missing as error:
    advice = str(error)
    check("без sau — совет с командой установки", "uv pip install -e ." in advice)
    check("в совете отдельный Python 3.12", "--python 3.12" in advice)
    check("в совете ссылка на проект", up.REPO in advice)
    check("в совете переменная для своей папки", up.ENV in advice)

kit = os.path.join(work, "sau-kit")
scripts = os.path.join(kit, ".venv", "Scripts")
calls = os.path.join(work, "calls.jsonl")
write(os.path.join(kit, "fake_sau.py"), r'''
    import json, os, sys, time
    with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as h:
        h.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
    mode = os.environ.get("FAKE_MODE", "")
    args = sys.argv[1:]
    log = lambda text: print("2026-10-22 12:00:00 | INFO: " + text, flush=True)
    if args[:2] == ["youtube", "check"]:
        print("invalid" if mode == "expired" else "valid")
        sys.exit(1 if mode == "expired" else 0)
    if args[:2] == ["youtube", "login"]:
        log("🔐 请在弹出的浏览器里登录 Google / YouTube 账号，登录后会自动保存")
        print("YouTube login flow completed: cookies/youtube_main.json")
        sys.exit(0)
    if args[:2] == ["youtube", "upload-video"]:
        if mode == "hang":
            log("📤 等待上传完成（传完才发布）…")
            time.sleep(60)
        if mode == "fail":
            print("YouTube cookie is missing or expired", file=sys.stderr)
            sys.exit(1)
        log("🎬 开始上传: clip.mp4")
        for share in (12, 57, 100):
            log("⏳ 上传中: Uploading %d%% ... 3 minutes left" % share)
        if mode == "nobutton":
            log("🤔 未找到发布按钮，可能上传未到可发布进度；请在窗口里手动发布")
        else:
            log("🥳 发布完成（unlisted） https://youtu.be/AbCdEf12345")
        print("YouTube video upload submitted: clip.mp4")
        sys.exit(0)
    sys.exit(2)
''')
os.makedirs(scripts, exist_ok=True)
fake = os.path.join(scripts, "sau.cmd")


def short(path):
    import ctypes
    room = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(str(path), room, 1024):
        return room.value
    return str(path)


with open(fake, "w", encoding="utf-8") as handle:
    handle.write('@chcp 65001 >nul\r\n@"%s" "%s" %%*\r\n'
                 % (short(sys.executable), short(os.path.join(kit, "fake_sau.py"))))

shim = os.path.join(work, "shim")
write(os.path.join(shim, "sau_cli.py"), r'''
    import os, runpy, sys

    def main(argv=None):
        sys.argv = ["sau"] + list(argv or [])
        try:
            runpy.run_path(os.environ["FAKE_SAU"], run_name="__main__")
        except SystemExit as stop:
            return int(stop.code or 0)
        return 0
''')
os.environ["FAKE_SAU"] = os.path.join(kit, "fake_sau.py")
write(os.path.join(shim, "uploader", "__init__.py"), "")
write(os.path.join(shim, "uploader", "tk_uploader", "__init__.py"), "")
write(os.path.join(shim, "uploader", "tk_uploader", "main_chrome.py"), r'''
    import json, os
    from playwright.async_api import async_playwright

    async def cookie_auth(account_file):
        return os.environ.get("FAKE_MODE") != "expired"

    class TiktokVideo:
        def __init__(self, title, file_path, tags, publish_date, account_file, thumbnail_path=None):
            self.title, self.file_path, self.tags = title, file_path, tags
            self.publish_date, self.account_file = publish_date, account_file
            self.headless = True

        async def main(self):
            with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as h:
                h.write(json.dumps(["tiktok-video", self.title, self.tags, self.publish_date,
                                    self.headless], ensure_ascii=False) + "\n")
            print("[+]Uploading-------clip.mp4", flush=True)
            print("  [-] video published success", flush=True)
            print("video_id: 7400000000000000001", flush=True)
''')
write(os.path.join(shim, "patchright", "__init__.py"), "")
write(os.path.join(shim, "patchright", "async_api.py"), "async_playwright = None\n")

os.environ[up.ENV] = kit
os.environ["FAKE_CALLS"] = calls
os.environ["PYTHONPATH"] = shim + os.pathsep + os.environ.get("PYTHONPATH", "")
real_python_of = up.python_of
up.python_of = lambda tool: sys.executable


def last_call():
    with open(calls, encoding="utf-8") as handle:
        return json.loads(handle.read().splitlines()[-1])


check("sau находится в папке из переменной", up.find() == fake)
check("python окружения ищется рядом с sau",
      real_python_of(os.path.join(work, "пусто", "sau.exe")) == "")

args = up.command("YouTube", "upload-video", "main", file="D:\\к\\клип.mp4",
                  title="Смешной момент", desc="о стриме", tags="#стрим, нарезка",
                  visibility="по ссылке", headed=False, tool="sau")
check("YouTube идёт через общую команду sau", args[:4] == ["sau", "youtube", "upload-video",
                                                         "--account"])
check("видимость передаётся ключом", args[args.index("--visibility") + 1] == "unlisted")
check("теги через запятую без решёток", args[args.index("--tags") + 1] == "стрим,нарезка")
check("скрытый браузер по выбору", args[-1] == "--headless")
login_args = up.command("youtube", "login", "main", tool="sau")
check("вход YouTube — sau с видимым окном", login_args == ["sau", "youtube", "login",
                                                          "--account", "main", "--headed"])
cdp_args = up.command("youtube", "login", "main", cdp_url="http://127.0.0.1:9222", tool="sau")
check("вход через обычный Chrome уходит в обёртку",
      cdp_args[1] == up.BRIDGE and cdp_args[-2:] == ["--cdp-url", "http://127.0.0.1:9222"])
tk_args = up.command("tiktok", "upload-video", "main", file="x.mp4", title="t", tool="sau")
up.set_browser("msedge")
picked = up.command("youtube", "check", "main", tool="sau")
check("с выбранным браузером YouTube идёт через обёртку",
      picked[1] == up.BRIDGE and picked[2:5] == ["--sau", "youtube", "check"])
check("выбор браузера уходит в окружение", up._environment().get(up.BROWSER_ENV) == "msedge")
up.set_browser("")
check("без выбора — как раньше", up.command("youtube", "check", "main", tool="sau")[0] == "sau")
check("встроенный Chromium всегда в списке",
      (up.BUILTIN, "встроенный Chromium") in up.installed_browsers())
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "core"))
import sau_bridge as _bridge

check("Edge подменяет просьбу загрузчика о Chrome",
      _bridge.browser_options("msedge", {"headless": True, "channel": "chrome"})
      == {"headless": True, "channel": "msedge"})
check("свой браузер — путём к exe, без channel",
      _bridge.browser_options("C:/y/browser.exe", {"channel": "chrome",
                                                   "executable_path": ""})
      == {"executable_path": "C:/y/browser.exe"})
uc = "UC" + "a1B2c3D4e5F6g7H8i9J0kL"
check("канал из ссылки /channel/UC…",
      up.channel_id("https://www.youtube.com/channel/%s/videos" % uc) == uc)
check("канал из голого UC…", up.channel_id(uc) == uc)
asked = []
page = '<link rel="canonical" href="https://www.youtube.com/channel/%s">' % uc
check("канал по @имени — со страницы канала",
      up.channel_id("https://youtube.com/@мойканал", fetch=lambda url: asked.append(url)
                    or page) == uc and asked == ["https://www.youtube.com/@мойканал"])
try:
    up.channel_id("@нет_такого", fetch=lambda url: "<html></html>")
    check("несуществующий @канал объясняется", False)
except ValueError as error:
    check("несуществующий @канал объясняется", "не найден" in str(error))
check("пустой канал — без указания", up.channel_id("  ") == "")
with_channel = up.command("youtube", "upload-video", "main", file="x.mp4", title="t",
                          tool="sau", channel=uc)
check("с каналом выгрузка идёт через обёртку с адресом канала",
      with_channel[1] == up.BRIDGE and with_channel[2:5] == ["--channel", uc, "--sau"])
check("для входа канал не нужен",
      "--channel" not in up.command("youtube", "login", "main", tool="sau", channel=uc))
check("встроенный Chromium — без channel и без пути",
      _bridge.browser_options("chromium", {"channel": "chrome",
                                           "executable_path": "C:/x.exe"}) == {})
check("TikTok всегда через обёртку", tk_args[1] == up.BRIDGE and "tiktok" in tk_args)

import asyncio

class FakePage:
    def __init__(self, text, gone_after=None):
        self.text, self.left = text, gone_after

    async def evaluate(self, script):
        if self.left is not None:
            self.left -= 1
            if self.left < 0:
                return "Сохранение… Информация"
        return self.text

    async def wait_for_timeout(self, ms):
        return None

    async def screenshot(self, **kw):
        raise OSError("без экрана")


_bridge.STATE["headless"] = True
try:
    asyncio.run(_bridge.identity_gate(FakePage("Подтверждение личности Далее")))
    check("скрыто: проверка личности останавливает выгрузку", False)
except RuntimeError as error:
    check("скрыто: проверка личности останавливает выгрузку", "показать браузер" in str(error))
_bridge.STATE["headless"] = False
check("видимо: ждёт, пока человек подтвердит, и продолжает",
      asyncio.run(_bridge.identity_gate(FakePage("Подтверждение личности",
                                                 gone_after=4))) is True)
check("чужие окна не принимаются за проверку личности",
      asyncio.run(_bridge.identity_gate(FakePage("Сохранение… Информация"))) is False)
_bridge.STATE["headless"] = True
try:
    up.command("youtube", "check", "  ", tool="sau")
    check("пустой аккаунт не пропускается", False)
except ValueError:
    check("пустой аккаунт не пропускается", True)
check("ссылка YouTube узнаётся",
      up.found_video("发布完成 https://youtu.be/AbCdEf12345")[1] == "AbCdEf12345")
check("shorts тоже узнаётся",
      up.found_video("https://www.youtube.com/shorts/Zz-9_xYw")[1] == "Zz-9_xYw")
check("номер ролика TikTok узнаётся",
      up.found_video("video_id: 7400000000000000001")[:2] == ("tiktok", "7400000000000000001"))
check("процент выгрузки разбирается", abs(up.share("上传中: Uploading 57% ...") - 0.57) < 1e-6)
check("чужие проценты не принимаются за выгрузку", up.share("громкость 80%") is None)
check("китайское сообщение пересказано по-русски",
      up.explain("2026 | INFO: ✍️ 填写标题").endswith("пишу название"))

check("без файла входа TikTok не вошёл", up.check("tiktok", "main") is False)
write(os.path.join(shim, "cookies", "tiktok_main.json"), '{"cookies": [], "origins": []}')
check("действующий вход YouTube", up.check("youtube", "main") is True)
os.environ["FAKE_MODE"] = "expired"
check("устаревший вход YouTube", up.check("youtube", "main") is False)
check("TikTok-вход устарел — видно через обёртку", up.check("tiktok", "main") is False)
os.environ["FAKE_MODE"] = ""
check("TikTok-вход действует", up.check("tiktok", "main") is True)
said = []
check("вход YouTube проходит", up.login("youtube", "main", log=said.append))
check("в журнале входа русская подсказка", any("войдите в Google" in line for line in said))

clips = os.path.join(work, "клипы")
os.makedirs(clips)
stream = os.path.join(work, "стрим.mp4")
open(stream, "wb").close()
clip = os.path.join(clips, "01 Смешной момент 0-12-30.mp4")
with open(clip, "wb") as handle:
    handle.write(b"\0" * 1024)
from core import handoff as _hand
_hand.write_origin(clip, stream, 750.0, 790.0)

steps = []
done = up.upload("youtube", "main", clip, "Смешной момент", tags="стрим",
                 visibility="unlisted", progress=lambda share, note: steps.append(share))
check("выгрузка отдала номер ролика", done["video_id"] == "AbCdEf12345")
check("и ссылку", done["url"] == "https://youtu.be/AbCdEf12345")
check("прогресс шёл по шагам", steps[:3] == [0.12, 0.57, 1.0])
check("в sau ушёл сам файл клипа", last_call()[last_call().index("--file") + 1] == clip)

profile = _pro.save({"name": "тест", "out_dir": clips})
_pro.set_active("тест")
clipper_db = _pro.db_path(profile)
link = sqlite3.connect(str(clipper_db))
link.executescript("""
    CREATE TABLE videos (id INTEGER PRIMARY KEY, path TEXT, duration REAL, language TEXT,
                         created_at REAL);
    CREATE TABLE candidates (id INTEGER PRIMARY KEY, video_id INTEGER, start REAL, end REAL,
                             title TEXT, hook TEXT, reason TEXT, model_score REAL,
                             features_json TEXT, model TEXT, created_at REAL);
""")
link.execute("INSERT INTO videos VALUES (1, ?, 3600, 'ru', 0)", (stream,))
link.execute("INSERT INTO candidates (id, video_id, start, end, created_at)"
             " VALUES (5, 1, 748, 792, 0)")
link.commit()
link.close()

db = up.db_for(_pro.active())
check("база выгрузок лежит в папке профиля", str(db).startswith(profile["folder"]))
up.record(db, done)
past = up.history(db)
check("выгрузка записана в журнал", len(past) == 1 and past[0]["service"] == "youtube")
check("записаны исходник и время клипа",
      past[0]["source"] == os.path.abspath(stream) and past[0]["start"] == 750.0
      and past[0]["finish"] == 790.0)
report = _learn.link_databases(clipper_db, db)
check("learning снова сопоставляет выложенное с кандидатом", report["matched"] == 1)
check("и пишет исход", report["written"] == 1)
check("файл отмечен как выложенный",
      up.posted_files(db).get(os.path.normcase(clip)) == {"youtube"})
bare = os.path.join(clips, "без пометки 1-02-03.mp4")
check("без .origin.json начало берётся из имени", up.origin_of(bare)["start"] == 3723.0)

os.environ["FAKE_MODE"] = "nobutton"
try:
    up.upload("youtube", "main", clip, "т")
    check("ненажатая «Опубликовать» — ошибка, а не успех", False)
except RuntimeError as error:
    check("ненажатая «Опубликовать» — ошибка, а не успех", "Опубликовать" in str(error))
os.environ["FAKE_MODE"] = "fail"
try:
    up.upload("youtube", "main", clip, "т")
    check("отказ sau доходит до человека", False)
except RuntimeError as error:
    check("отказ sau доходит до человека", "expired" in str(error))
check("в журнал попала только удачная выгрузка", len(up.history(db)) == 1)
try:
    up.upload("youtube", "main", clip, "х" * 101)
    check("длинное название YouTube не уходит", False)
except ValueError:
    check("длинное название YouTube не уходит", True)

os.environ["FAKE_MODE"] = "hang"
began = time.time()
try:
    up.upload("youtube", "main", clip, "т", cancel=lambda: time.time() - began > 1.5)
    check("остановка прерывает выгрузку", False)
except RuntimeError as error:
    check("остановка прерывает выгрузку", "остановлена" in str(error))
check("и не ждёт, пока sau закончит сам", time.time() - began < 20)
os.environ["FAKE_MODE"] = ""

tk_done = up.upload("TikTok", "main", clip, "Смешной момент", desc="лучшее",
                    tags="стрим,нарезка", headed=True)
call = last_call()
check("TikTok выложен через tk_uploader", call[0] == "tiktok-video")
check("подпись TikTok — название и описание", call[1] == "Смешной момент лучшее")
check("теги TikTok списком", call[2] == ["стрим", "нарезка"])
check("TikTok сразу, без расписания", call[3] == 0)
check("окно браузера по выбору", call[4] is False)
check("номер ролика TikTok получен", tk_done["video_id"] == "7400000000000000001")
up.record(db, tk_done)
check("файл выложен на обе площадки",
      up.posted_files(db).get(os.path.normcase(clip)) == {"youtube", "tiktok"})

from studio.window import Studio

app = Studio()
app.update()
app._switch("clip")
app._switch_clip("upload")
app.update()
panel = app.uploader
check("вкладка «Выгрузка» строится", panel is not None and panel.winfo_ismapped())
check("в списке готовый клип из папки профиля", clip in panel.files)
check("общий журнал Студии спрятан на этой вкладке", not app.log_wrap.winfo_ismapped())
check("в журнале выгрузок обе записи", len(panel.past.get_children()) == 2)
index = panel.files.index(clip)
panel.tree.selection_set(str(index))
app.update()
check("название подставлено из имени файла", panel.v_title.get() == "Смешной момент")
check("пометка «выложен» в списке",
      "YouTube" in panel.tree.item(str(index), "values")[2])
panel.v_service.set("TikTok")
panel._service_changed()
check("у TikTok видимость не выбирается", str(panel.box_visibility.cget("state")) == "disabled")
panel.v_service.set("YouTube")
panel._service_changed()
check("у YouTube подсказка про обычный Chrome", "remote-debugging-port" in panel.lbl_hint["text"])
check("у YouTube галочка «показывать браузер» недоступна",
      str(panel.box_headed.cget("state")) == "disabled"
      and "только с окном" in panel.lbl_hint["text"])
panel.v_service.set("TikTok")
panel._service_changed()
check("у TikTok галочка снова доступна", str(panel.box_headed.cget("state")) == "normal")
panel.v_service.set("YouTube")
panel._service_changed()

panel.v_title.set("Свежий момент")
own = os.path.join(work, "мои клипы", "день 1")
os.makedirs(own, exist_ok=True)
for number in range(25):
    with open(os.path.join(own, "клип_%02d.mp4" % number), "wb") as handle:
        handle.write(b"0")
panel.v_folder.set(os.path.dirname(own))
panel.refresh()
app.update()
check("своя папка: клипы берутся из неё и подпапок",
      len(panel.files) == 25 and all("мои клипы" in path for path in panel.files))
check("своя папка: подпись говорит, сколько клипов", "25" in panel.lbl_where.cget("text"))


class Wheel:
    def __init__(self, widget):
        self.widget, self.delta = widget, -120


page = panel.winfo_children()[0]
check("прокрутка над длинным списком крутит список, а не окно",
      page._inner_scroller(Wheel(panel.tree)) is panel.tree)
before = panel.tree.yview()[0]
page._wheel(Wheel(panel.tree))
app.update()
check("список действительно сдвинулся", panel.tree.yview()[0] > before)
panel.v_folder.set(os.path.join(work, "нет такой"))
panel.refresh()
check("несуществующая папка объясняется", "нет такой папки" in panel.lbl_where.cget("text"))
panel._studio_folders()
check("«Папки Студии» возвращает обычный поиск", panel.v_folder.get() == "" and clip in panel.files)
panel.tree.selection_set(str(panel.files.index(clip)))
app.update()
check("вкладка: несколько клипов выбираются", str(panel.tree.cget("selectmode")) == "extended")
panel._send()
deadline = time.time() + 30
while panel.runner.busy() and time.time() < deadline:
    app.update()
    time.sleep(0.05)
for _ in range(20):
    app.update()
    time.sleep(0.05)
check("«Выложить» из окна дошло до конца", len(up.history(db)) == 3)
check("в журнале окна есть ссылка", "youtu.be/AbCdEf12345" in panel.log.get("1.0", "end"))

panel.v_cdp.set("http://127.0.0.1:9222")
panel.v_account.set("канал")
app._remember()
check("площадка и аккаунт запоминаются",
      app.cfg.get("upload_accounts", {}).get("youtube") == "канал"
      and app.cfg.get("upload_cdp") == "http://127.0.0.1:9222")
app.destroy()

shutil.rmtree(work, ignore_errors=True)
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
