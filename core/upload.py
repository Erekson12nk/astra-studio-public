from __future__ import annotations

import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import ffmpeg as _ff
from . import fetch as _fetch
from . import handoff as _hand

TOOL = "sau"
ENV = "ASTRA_SAU"
REPO = "https://github.com/dreammis/social-auto-upload.git"
FOLDER = "social-auto-upload"
BROWSERS = "browsers"
BROWSER_ENV = "ASTRA_SAU_BROWSER"
BUILTIN = "chromium"
_PROGRAMS = (os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", ""))
KNOWN_BROWSERS = (
    ("msedge", "Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe", True),
    ("chrome", "Google Chrome", r"Google\Chrome\Application\chrome.exe", True),
    ("yandex", "Яндекс Браузер", r"Yandex\YandexBrowser\Application\browser.exe", False),
    ("brave", "Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe", False),
    ("vivaldi", "Vivaldi", r"Vivaldi\Application\vivaldi.exe", False),
)
_chosen = {"browser": ""}
CHANNEL_ID = re.compile(r"UC[\w-]{22}")
HANDLE = re.compile(r"@([\w.\-]{3,30})")
CHANNEL_IN_PAGE = (
    re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'),
    re.compile(r'"externalId":"(UC[\w-]{22})"'),
    re.compile(r'"channelId":"(UC[\w-]{22})"'),
)
BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sau_bridge.py")
DB_NAME = "uploads.db"
SERVICES = (("youtube", "YouTube"), ("tiktok", "TikTok"))
SERVICE_TITLES = [title for _, title in SERVICES]
VISIBILITY = (("public", "для всех"), ("unlisted", "по ссылке"), ("private", "только мне"))
VISIBILITY_TITLES = [title for _, title in VISIBILITY]
CDP_DEFAULT = "http://127.0.0.1:9222"
CDP_HINT = (
    "YouTube выкладывается только с окном браузера: без окна YouTube Studio его "
    "не принимает и просит подтвердить личность. Окно можно не трогать.\n"
    "Google может не пустить во встроенный браузер («небезопасный браузер»). "
    "Тогда закройте Chrome, запустите его с портом отладки:\n"
    "  chrome.exe --remote-debugging-port=9222 --user-data-dir=%%LOCALAPPDATA%%\\sau-chrome\n"
    "войдите там в Google и укажите адрес %s в поле «Chrome»." % CDP_DEFAULT)
_SHARE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_UPLOADING = ("上传中", "Uploading", "uploading", "Загрузка", "Загружено")
_YOUTUBE = re.compile(
    r"https?://(?:youtu\.be/|(?:www\.|m\.)?youtube\.com/(?:watch\?v=|shorts/))([\w-]{6,})")
_TIKTOK = re.compile(r"video_id:\s*(\d{6,})")
_NO_BUTTON = "未找到发布按钮"
_SAID = (
    ("未找到发布按钮", "YouTube не нашёл кнопку «Опубликовать»: видео не дозагрузилось"),
    ("等待上传完成", "жду, пока видео догрузится"),
    ("等上传超时", "загрузка идёт больше 30 минут, всё равно пробую опубликовать"),
    ("开始上传", "начинаю выгрузку"),
    ("上传完成", "видео загружено"),
    ("上传中", "загружается"),
    ("填写标题", "пишу название"),
    ("填写简介", "пишу описание"),
    ("设置可见性", "ставлю видимость"),
    ("发布完成", "опубликовано"),
    ("登录态已保存", "вход сохранён"),
    ("登录态不存在或失效", "входа нет или он устарел"),
    ("等待登录超时", "вход не завершён вовремя"),
    ("请在弹出的浏览器里登录", "войдите в Google в открывшемся окне браузера"),
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, origin TEXT, start REAL, finish REAL,
    score REAL, reason TEXT, file TEXT, status TEXT,
    made_at REAL, ready_at REAL);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER, service TEXT, video_id TEXT, url TEXT,
    privacy TEXT, posted_at REAL, dry INTEGER DEFAULT 0,
    account TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT, at REAL, views INTEGER, likes INTEGER,
    comments INTEGER);
CREATE INDEX IF NOT EXISTS posts_clip ON posts(clip_id);
CREATE INDEX IF NOT EXISTS stats_video ON stats(video_id);
"""


class Missing(RuntimeError):
    pass


def service_key(title) -> str:
    for key, shown in SERVICES:
        if title in (key, shown):
            return key
    return SERVICES[0][0]


def visibility_key(title) -> str:
    for key, shown in VISIBILITY:
        if title in (key, shown):
            return key
    return VISIBILITY[0][0]


def install_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(local) / FOLDER


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(__file__).resolve().parent.parent


def _exe(name) -> str:
    return name + ".exe" if os.name == "nt" else name


def _names(name) -> tuple:
    if os.name == "nt":
        return (name + ".exe", name + ".cmd", name + ".bat")
    return (name,)


def _in_folder(folder) -> str:
    folder = Path(folder)
    for place in (folder / ".venv" / "Scripts", folder / ".venv" / "bin",
                  folder / "Scripts", folder / "bin", folder):
        for name in _names(TOOL):
            candidate = place / name
            if candidate.is_file():
                return str(candidate)
    return ""


def find() -> str:
    given = os.environ.get(ENV, "").strip()
    if given:
        if os.path.isfile(given):
            return given
        if os.path.isdir(given):
            return _in_folder(given)
        return ""
    for folder in (_app_root() / "tools" / FOLDER, install_dir(),
                   Path(os.path.expanduser("~")) / FOLDER):
        found = _in_folder(folder)
        if found:
            return found
    return shutil.which(TOOL) or ""


def available() -> bool:
    return bool(find())


def python_of(tool) -> str:
    folder = os.path.dirname(os.path.abspath(str(tool)))
    for name in (_exe("python"), _exe("python3")):
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate):
            return candidate
    return ""


def install_advice() -> str:
    place = install_dir()
    return (
        "Не найдена программа выгрузки sau (social-auto-upload).\n"
        "Ей нужен отдельный Python 3.10–3.12, поэтому внутрь Студии она не ставится.\n"
        "Установка в PowerShell (нужен uv: https://docs.astral.sh/uv/):\n"
        "  git clone %s \"%s\"\n"
        "  cd \"%s\"\n"
        "  uv venv --python 3.12\n"
        "  uv pip install -e .\n"
        "  copy conf.example.py conf.py\n"
        "  .venv\\Scripts\\patchright install chromium\n"
        "Если поставили в другое место, укажите папку в переменной %s.\n"
        "После установки нажмите «Проверить» ещё раз." % (REPO, place, place, ENV))


def _need():
    tool = find()
    if not tool:
        raise Missing(install_advice())
    return tool


def _bridge(tool) -> list:
    python = python_of(tool)
    if not python:
        raise Missing("Рядом с %s нет python.exe окружения sau.\n\n%s"
                      % (tool, install_advice()))
    return [python, BRIDGE]


def command(service, action, account, file="", title="", desc="", tags="",
            visibility="public", headed=True, cdp_url="", tool=None, channel="") -> list:
    service = service_key(service)
    account = str(account or "").strip()
    if not account:
        raise ValueError("укажите имя аккаунта")
    tool = tool or _need()
    cdp_url = str(cdp_url or "").strip()
    if service == "tiktok" or (action == "login" and cdp_url):
        args = _bridge(tool) + [service, action, "--account", account]
        if cdp_url and action == "login":
            args += ["--cdp-url", cdp_url]
        if action == "upload-video":
            args += ["--file", str(file), "--title", str(title), "--desc", str(desc or ""),
                     "--tags", tag_text(tags)]
            if headed:
                args.append("--headed")
        return args
    args = [tool, service, action, "--account", account]
    channel = str(channel or "").strip() if action == "upload-video" else ""
    if browser() or channel:
        args = _bridge(tool) + (["--channel", channel] if channel else []) + ["--sau"] + args[1:]
    if action == "login":
        return args + ["--headed"]
    if action == "check":
        return args
    args += ["--file", str(file), "--title", str(title), "--desc", str(desc or ""),
             "--tags", tag_text(tags), "--visibility", visibility_key(visibility)]
    return args + (["--headed"] if headed else ["--headless"])


def tag_text(tags) -> str:
    if isinstance(tags, (list, tuple)):
        parts = tags
    else:
        parts = re.split(r"[,\s]+", str(tags or ""))
    return ",".join(part.strip().lstrip("#") for part in parts if part.strip().lstrip("#"))


def share(line):
    line = str(line)
    if not any(mark in line for mark in _UPLOADING):
        return None
    found = _SHARE.search(line)
    if not found:
        return None
    return min(1.0, float(found.group(1)) / 100.0)


def found_video(line):
    line = str(line)
    hit = _YOUTUBE.search(line)
    if hit:
        return "youtube", hit.group(1), hit.group(0)
    hit = _TIKTOK.search(line)
    if hit:
        return "tiktok", hit.group(1), ""
    return None


def explain(line) -> str:
    text = str(line)
    for mark, said in _SAID:
        if mark in text:
            tail = text.split(mark, 1)[1].strip(" :：()（）")
            return said + (": " + tail if tail else "")
    return text


def browsers_dir(tool=None):
    tool = tool or find()
    if not tool:
        return ""
    for folder in Path(tool).resolve().parents[:4]:
        candidate = folder / BROWSERS
        if candidate.is_dir():
            return str(candidate)
    return ""


def channel_id(text, fetch=None) -> str:
    from urllib.parse import unquote

    text = unquote(str(text or "").strip())
    if not text:
        return ""
    found = CHANNEL_ID.search(text)
    if found:
        return found.group(0)
    handle = HANDLE.search(text)
    if not handle:
        raise ValueError("не понял канал: вставьте ссылку на канал, @имя или UC…")
    page = (fetch or _fetch_page)("https://www.youtube.com/@%s" % handle.group(1))
    for pattern in CHANNEL_IN_PAGE:
        hit = pattern.search(page or "")
        if hit:
            return hit.group(1)
    raise ValueError("канал @%s не найден на YouTube" % handle.group(1))


def _fetch_page(url) -> str:
    import urllib.request
    from urllib.parse import quote

    request = urllib.request.Request(quote(url, safe=":/@?=&"), headers={"User-Agent": "Mozilla/5.0",
                                                   "Accept-Language": "ru"})
    with urllib.request.urlopen(request, timeout=20) as answer:
        return answer.read().decode("utf-8", "replace")


def channel_upload_url(channel) -> str:
    return "https://studio.youtube.com/channel/%s/videos/upload?d=ud" % channel


def installed_browsers() -> list:
    found = []
    for key, title, tail, channel in KNOWN_BROWSERS:
        for base in _PROGRAMS:
            if base and os.path.isfile(os.path.join(base, tail)):
                found.append((key if channel else os.path.join(base, tail), title))
                break
    found.append((BUILTIN, "встроенный Chromium"))
    return found


def default_browser() -> str:
    found = installed_browsers()
    return found[0][0] if found else BUILTIN


def set_browser(value) -> None:
    _chosen["browser"] = str(value or "").strip()


def browser() -> str:
    return _chosen["browser"]


def _environment():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               NO_COLOR="1", LOGURU_COLORIZE="0")
    browsers = browsers_dir()
    if browsers:
        env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
    if browser():
        env[BROWSER_ENV] = browser()
    return env


def run(args, log=None, progress=None, cancel=None, stopped="выгрузка остановлена"):
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                               errors="replace", bufsize=1, env=_environment(),
                               **_ff._NOWIN)
    box = queue.Queue()

    def pump():
        try:
            for line in process.stdout:
                box.put(line)
        finally:
            box.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    lines = []
    try:
        while True:
            if cancel and cancel():
                _fetch.stop(process)
                raise RuntimeError(stopped)
            try:
                line = box.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            line = line.rstrip()
            if not line:
                continue
            lines.append(line)
            step = share(line)
            if step is not None and progress:
                progress(step, "загружено %d%%" % int(step * 100))
            if log:
                log(explain(line))
    finally:
        process.wait()
        if process.stdout:
            process.stdout.close()
    return process.returncode, lines


def _trouble(lines, code) -> str:
    for line in reversed(lines):
        if "ERROR" in line.upper() or "Error" in line or "error" in line:
            return explain(line)
    return explain(lines[-1]) if lines else "sau завершился с кодом %s" % code


def login(service, account, cdp_url="", log=None, cancel=None) -> bool:
    args = command(service, "login", account, cdp_url=cdp_url)
    code, lines = run(args, log=log, cancel=cancel, stopped="вход остановлен")
    if code != 0:
        raise RuntimeError("Вход не удался: %s" % _trouble(lines, code))
    return True


def check(service, account, log=None, cancel=None) -> bool:
    args = command(service, "check", account)
    code, lines = run(args, log=log, cancel=cancel, stopped="проверка остановлена")
    said = [line.strip().lower() for line in lines]
    if "invalid" in said:
        return False
    if code == 0 and "valid" in said:
        return True
    raise RuntimeError(_trouble(lines, code))


def upload(service, account, file, title, desc="", tags="", visibility="public",
           headed=True, log=None, progress=None, cancel=None, channel="") -> dict:
    service = service_key(service)
    if not os.path.isfile(str(file)):
        raise ValueError("нет файла клипа: %s" % file)
    title = str(title or "").strip()
    if not title:
        raise ValueError("нужно название")
    if service == "youtube" and len(title) > 100:
        raise ValueError("название для YouTube длиннее 100 знаков")
    if service == "youtube" and not headed:
        headed = True
        if log:
            log("YouTube не принимает браузер без окна, выкладываю с окном")
    channel = channel_id(channel) if service == "youtube" and channel else ""
    if channel and log:
        log("канал: %s" % channel)
    args = command(service, "upload-video", account, file=file, title=title, desc=desc,
                   tags=tags, visibility=visibility, headed=headed, channel=channel)
    code, lines = run(args, log=log, progress=progress, cancel=cancel)
    if code != 0:
        raise RuntimeError("Выгрузка не удалась: %s" % _trouble(lines, code))
    if any(_NO_BUTTON in line for line in lines):
        raise RuntimeError(explain(_NO_BUTTON) + ". Попробуйте ещё раз.")
    video_id, url = "", ""
    for line in lines:
        hit = found_video(line)
        if hit and hit[0] == service:
            video_id, url = hit[1], hit[2]
    if service == "youtube" and video_id and not url:
        url = "https://youtu.be/%s" % video_id
    if progress:
        progress(1.0, "готово")
    return {"service": service, "account": str(account).strip(), "video_id": video_id,
            "url": url, "file": os.path.abspath(str(file)), "title": title,
            "visibility": visibility_key(visibility) if service == "youtube" else ""}


def origin_of(file) -> dict:
    path = _hand.origin_path(file)
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle) or {}
        except (OSError, ValueError):
            data = {}
    start = data.get("start")
    finish = data.get("end")
    if start is None:
        start = _hand.start_from_name(file)
    try:
        start = float(start) if start is not None else None
        finish = float(finish) if finish is not None else None
    except (TypeError, ValueError):
        start, finish = None, None
    return {"source": str(data.get("source") or ""), "start": start, "finish": finish}


def db_for(profile=None) -> Path:
    if profile and profile.get("folder"):
        return Path(profile["folder"]) / DB_NAME
    return _app_root() / "astra" / "clips.db"


def _connect(db):
    os.makedirs(os.path.dirname(os.path.abspath(str(db))), exist_ok=True)
    link = sqlite3.connect(str(db))
    link.row_factory = sqlite3.Row
    link.executescript(SCHEMA)
    have = {row[1] for row in link.execute("PRAGMA table_info(posts)")}
    for name in ("account", "title"):
        if name not in have:
            link.execute("ALTER TABLE posts ADD COLUMN %s TEXT" % name)
    return link


def record(db, done, origin=None) -> int:
    origin = origin if origin is not None else origin_of(done["file"])
    now = time.time()
    link = _connect(db)
    try:
        row = link.execute("SELECT id FROM clips WHERE file = ? ORDER BY id DESC LIMIT 1",
                           (done["file"],)).fetchone()
        if row:
            clip_id = row["id"]
            link.execute("UPDATE clips SET source = COALESCE(NULLIF(?, ''), source),"
                         " start = COALESCE(?, start), finish = COALESCE(?, finish),"
                         " status = 'posted' WHERE id = ?",
                         (origin.get("source") or "", origin.get("start"),
                          origin.get("finish"), clip_id))
        else:
            clip_id = link.execute(
                "INSERT INTO clips (source, origin, start, finish, file, status, made_at,"
                " ready_at) VALUES (?, 'studio', ?, ?, ?, 'posted', ?, ?)",
                (origin.get("source") or "", origin.get("start"), origin.get("finish"),
                 done["file"], now, now)).lastrowid
        post_id = link.execute(
            "INSERT INTO posts (clip_id, service, video_id, url, privacy, posted_at, dry,"
            " account, title) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (clip_id, done["service"], done.get("video_id") or "", done.get("url") or "",
             done.get("visibility") or "", now, done.get("account") or "",
             done.get("title") or "")).lastrowid
        link.commit()
        return post_id
    finally:
        link.close()


_ROW = "ROW "
_VIDEO_IN_LINK = (re.compile(r"/video/([\w-]{11})(?:/|$)"),
                  re.compile(r"youtu\.be/([\w-]{11})"),
                  re.compile(r"/shorts/([\w-]{11})"),
                  re.compile(r"[?&]v=([\w-]{11})"))
_MONTHS = (("янв", "jan"), ("фев", "feb"), ("мар", "mar"), ("апр", "apr"),
           ("мая", "may"), ("июн", "jun"), ("июл", "jul"), ("авг", "aug"),
           ("сен", "sep"), ("окт", "oct"), ("ноя", "nov"), ("дек", "dec"))
_SCALES = (("тыс", 1e3), ("млн", 1e6), ("млрд", 1e9), ("k", 1e3), ("m", 1e6), ("b", 1e9))
MATCH_DAYS = 3


def views_command(account, channel="", page="", headless=False, tool=None) -> list:
    account = str(account or "").strip()
    if not account:
        raise ValueError("укажите имя аккаунта")
    args = _bridge(tool or _need()) + ["youtube", "views", "--account", account]
    if channel:
        args += ["--channel", channel]
    if page:
        args += ["--page", page]
        if headless:
            args.append("--headless")
    return args


def count_of(text):
    text = str(text or "").strip().lower().replace("\xa0", " ").replace(" ", " ")
    if not text:
        return None
    text = text.splitlines()[0].strip()
    hit = re.match(r"^(\d[\d\s.,]*)\s*([a-zа-я]*)", text)
    if not hit:
        return None
    digits, word = hit.group(1).strip(), hit.group(2)
    scale = next((size for mark, size in _SCALES if word.startswith(mark)), 1)
    if scale == 1:
        return int(re.sub(r"\D", "", digits) or 0)
    try:
        return int(round(float(digits.replace(" ", "").replace(",", ".")) * scale))
    except ValueError:
        return None


def likes_of(text):
    for line in str(text or "").splitlines():
        if "%" not in line:
            value = count_of(line)
            if value is not None:
                return value
    return None


def day_of(text):
    text = str(text or "").lower().replace("\xa0", " ")
    hit = re.search(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)", text)
    if hit:
        day, month, year = int(hit.group(1)), int(hit.group(2)), int(hit.group(3))
    else:
        month = 0
        for number, marks in enumerate(_MONTHS, 1):
            if any(re.search(r"(?<![a-zа-я])" + mark, text) for mark in marks):
                month = number
                break
        year_hit = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
        day_hit = re.search(r"(?<!\d)(\d{1,2})(?!\d)", text)
        if not (month and year_hit and day_hit):
            return None
        day, year = int(day_hit.group(1)), int(year_hit.group(1))
    try:
        return time.mktime((year, month, day, 12, 0, 0, 0, 0, -1))
    except (OverflowError, ValueError):
        return None


def video_of(links) -> str:
    for pattern in _VIDEO_IN_LINK:
        for link in links or ():
            hit = pattern.search(str(link))
            if hit:
                return hit.group(1)
    return ""


def studio_row(raw) -> dict:
    links = raw.get("links") or ()
    video_id = video_of(links)
    short = raw.get("tab") == "short" or any("/shorts/" in str(link) for link in links)
    url = ""
    if video_id:
        url = ("https://youtube.com/shorts/%s" if short else "https://youtu.be/%s") % video_id
    shown = str(raw.get("visibility") or "").strip()
    return {"title": str(raw.get("title") or "").strip(), "video_id": video_id, "url": url,
            "day": day_of(raw.get("date")),
            "visibility": shown.splitlines()[0].strip() if shown else "",
            "views": count_of(raw.get("views")), "comments": count_of(raw.get("comments")),
            "likes": likes_of(raw.get("likes"))}


def studio_rows(lines) -> list:
    rows, seen = [], set()
    for line in lines:
        line = str(line)
        if not line.startswith(_ROW):
            continue
        try:
            row = studio_row(json.loads(line[len(_ROW):]))
        except (ValueError, AttributeError):
            continue
        if not row["video_id"] or row["video_id"] in seen:
            continue
        seen.add(row["video_id"])
        rows.append(row)
    return rows


def read_views(account, channel="", log=None, cancel=None, page="", headless=False) -> list:
    channel = channel_id(channel) if channel else ""
    args = views_command(account, channel=channel, page=page, headless=headless)
    quiet = None
    if log:
        def quiet(line):
            if not str(line).startswith(_ROW):
                log(line)
    code, lines = run(args, log=quiet, cancel=cancel, stopped="чтение просмотров остановлено")
    if code != 0:
        raise RuntimeError("Просмотры не прочитаны: %s" % _trouble(lines, code))
    return studio_rows(lines)


def same_title(post_title, row_title) -> bool:
    left = re.sub(r"\s+", " ", str(post_title or "")).strip().casefold()
    right = re.sub(r"\s+", " ", str(row_title or "")).strip().casefold()
    if not left or not right:
        return False
    if left == right:
        return True
    cut = right.rstrip("…").rstrip(".").strip()
    return cut != right and len(cut) >= 20 and left.startswith(cut)


def match_views(db, rows, account="", now=None) -> dict:
    now = time.time() if now is None else now
    report = {"rows": len(rows), "matched": 0, "linked": 0, "stats": 0, "unmatched": []}
    by_id = {row["video_id"]: row for row in rows if row.get("video_id")}
    link = _connect(db)
    try:
        posts = [dict(row) for row in link.execute(
            "SELECT id, video_id, url, title, posted_at, account FROM posts"
            " WHERE service = 'youtube' AND dry = 0 ORDER BY posted_at, id")]
        account = str(account or "").strip()
        if account:
            posts = [post for post in posts if not post["account"] or post["account"] == account]
        taken = {post["video_id"] for post in posts if post["video_id"]}
        hits = {}
        for post in posts:
            if post["video_id"] and post["video_id"] in by_id:
                hits[post["id"]] = by_id[post["video_id"]]
        for post in posts:
            if post["video_id"]:
                continue
            best, gap = None, None
            for row in rows:
                if row["video_id"] in taken or not same_title(post["title"], row["title"]):
                    continue
                if row.get("day") is None:
                    distance = MATCH_DAYS * 86400.0
                else:
                    distance = abs(row["day"] - float(post["posted_at"] or 0))
                    if distance > (MATCH_DAYS + 0.5) * 86400:
                        continue
                if gap is None or distance < gap:
                    best, gap = row, distance
            if best is None:
                report["unmatched"].append(post["title"] or "")
                continue
            taken.add(best["video_id"])
            hits[post["id"]] = best
            link.execute("UPDATE posts SET video_id = ?, url = ? WHERE id = ?",
                         (best["video_id"], best["url"], post["id"]))
            report["linked"] += 1
        written = set()
        for row in hits.values():
            if row["video_id"] in written or row.get("views") is None:
                continue
            written.add(row["video_id"])
            link.execute("INSERT INTO stats (video_id, at, views, likes, comments)"
                         " VALUES (?, ?, ?, ?, ?)",
                         (row["video_id"], now, row["views"], row.get("likes"),
                          row.get("comments")))
        report["matched"] = len(hits)
        report["stats"] = len(written)
        link.commit()
    finally:
        link.close()
    return report

def history(db, limit=50) -> list:
    if not os.path.isfile(str(db)):
        return []
    link = _connect(db)
    try:
        rows = link.execute(
            "SELECT p.id, p.service, p.account, p.video_id, p.url, p.privacy, p.posted_at,"
            " p.title, c.file, c.source, c.start, c.finish,"
            " (SELECT s.views FROM stats s WHERE s.video_id = p.video_id AND p.video_id <> ''"
            "  ORDER BY s.at DESC, s.id DESC LIMIT 1) AS views"
            " FROM posts p LEFT JOIN clips c ON c.id = p.clip_id"
            " WHERE p.dry = 0 ORDER BY p.posted_at DESC, p.id DESC LIMIT ?",
            (int(limit),)).fetchall()
    finally:
        link.close()
    return [dict(row) for row in rows]


def posted_files(db) -> dict:
    marks = {}
    for row in reversed(history(db, limit=100000)):
        key = os.path.normcase(os.path.abspath(str(row.get("file") or "")))
        marks.setdefault(key, set()).add(row["service"])
    return marks
