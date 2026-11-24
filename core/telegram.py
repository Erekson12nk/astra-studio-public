"""Телеграм-канал стримера: анонсы эфиров со ссылками.

Публичный канал читается без бота и без входа — страница t.me/s/канал отдаёт
последние два десятка постов. Анонсом считается только пост со ссылкой на
трансляцию: «я не подрубаю сегодня стрим» — тоже пост про стрим, а эфира нет.
Ссылка на свой твич будит сторожа твича, ссылка на другую площадку —
запись этой трансляции streamlink-ом.
"""

from __future__ import annotations

import calendar
import html
import json
import os
import re
import time
import urllib.request
from pathlib import Path

PAGE = "https://t.me/s/%s"
SEEN_NAME = "telegram_seen.json"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstraStudio"
_POST = re.compile(r'data-post="([A-Za-z0-9_]+)/(\d+)"')
_TEXT = re.compile(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', re.S)
_HREF = re.compile(r'href="(https?://[^"]+)"')
_WHEN = re.compile(r'datetime="([^"]+)"')
_BARE = re.compile(r"https?://[^\s<>\"']+")
PLATFORMS = (
    ("twitch", re.compile(r"^https?://(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{2,25})/?(?:\?.*)?$",
                          re.I)),
    ("youtube", re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:live/|watch\?v=|@[^/]+/"
                           r"live|channel/[^/]+/live)|youtu\.be/)", re.I)),
    ("kick", re.compile(r"^https?://(?:www\.)?kick\.com/([A-Za-z0-9_-]{2,30})/?(?:\?.*)?$", re.I)),
    ("vk", re.compile(r"^https?://(?:www\.)?(?:live\.vkvideo\.ru|live\.vkplay\.ru|vkplay\.live)/"
                      r"[A-Za-z0-9_.-]+/?$", re.I)),
    ("vk", re.compile(r"^https?://(?:www\.|m\.)?(?:vk\.com|vkvideo\.ru)/video(?:-?\d+_\d+|/@)",
                      re.I)),
    ("goodgame", re.compile(r"^https?://(?:www\.)?goodgame\.ru/", re.I)),
)
NOT_CHANNELS = ("videos", "directory", "settings", "p", "downloads", "jobs")


def channel_of(text) -> str:
    """t.me/somestreamer, https://t.me/s/somestreamer, @somestreamer, somestreamer → somestreamer."""
    text = str(text or "").strip().rstrip("/")
    found = re.search(r"(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{4,32})", text)
    if found:
        return found.group(1)
    text = text.lstrip("@")
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", text) else ""


def _epoch(stamp) -> float:
    try:
        return float(calendar.timegm(time.strptime(str(stamp)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0.0


def platform_of(url):
    """Ссылка на трансляцию: (площадка, канал) или None."""
    for name, pattern in PLATFORMS:
        found = pattern.match(url)
        if not found:
            continue
        channel = found.group(1).lower() if found.groups() else ""
        if name == "twitch" and channel in NOT_CHANNELS:
            return None
        return name, channel
    return None


def stream_links(text_or_links) -> list:
    """Ссылки на трансляции без повторов: [{"url", "platform", "channel"}]."""
    links = text_or_links if isinstance(text_or_links, (list, tuple)) else \
        _BARE.findall(str(text_or_links or ""))
    out, seen = [], set()
    for url in links:
        url = html.unescape(url).rstrip(".,;)!")
        hit = platform_of(url)
        if not hit:
            continue
        key = (hit[0], hit[1] or url.split("?")[0].lower().replace("www.", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({"url": url, "platform": hit[0], "channel": hit[1]})
    return out


def parse(page) -> list:
    """Посты со страницы t.me/s/: [{"id", "text", "at", "links"}], старые первыми."""
    posts = []
    for block in re.split(r'(?=<div class="tgme_widget_message_wrap)', page or "")[1:]:
        post = _POST.search(block)
        if not post:
            continue
        found = _TEXT.search(block)
        text = html.unescape(re.sub(r"<br\s*/?>", "\n", found.group(1))) if found else ""
        text = re.sub(r"<[^>]+>", " ", text)
        when = _WHEN.search(block)
        links = [url for url in _HREF.findall(block) if "t.me/" not in url]
        posts.append({"id": int(post.group(2)), "text": " ".join(text.split()),
                      "at": _epoch(when.group(1)) if when else 0.0,
                      "links": stream_links(links + _BARE.findall(text))})
    posts.sort(key=lambda item: item["id"])
    return posts


def _curl(url) -> str:
    """Запасной путь: соединение питона с t.me провайдер иногда рвёт, curl Windows проходит."""
    import shutil
    import subprocess

    from . import ffmpeg as _ff

    binary = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
    binary = binary if os.path.isfile(binary) else shutil.which("curl")
    if not binary:
        raise RuntimeError("t.me не открылся, а curl не найден")
    done = subprocess.run([binary, "-s", "-f", "--max-time", "20", "-A", AGENT, url],
                          capture_output=True, **_ff._NOWIN)
    if done.returncode != 0 or not done.stdout:
        raise RuntimeError("t.me не открылся (curl %d)" % done.returncode)
    return done.stdout.decode("utf-8", errors="replace")


def fetch(channel, opener=None, tries=3, pause=2.0) -> list:
    url = PAGE % channel_of(channel)
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    for attempt in range(tries):
        try:
            with (opener or urllib.request.urlopen)(request, timeout=20) as answer:
                return parse(answer.read().decode("utf-8", errors="replace"))
        except OSError:
            if attempt + 1 < tries:
                time.sleep(pause)
    return parse(_curl(url))


def seen_path(profile) -> Path:
    return Path(profile["folder"]) / SEEN_NAME


def last_seen(profile, channel) -> int:
    try:
        data = json.loads(seen_path(profile).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return -1
    return int(data.get(channel_of(channel), -1))


def mark_seen(profile, channel, post_id):
    path = seen_path(profile)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[channel_of(channel)] = int(post_id)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def news(profile, channel, fetcher=None, now=None, fresh=3 * 3600.0) -> list:
    """Новые анонсы канала с прошлого раза.

    Первый раз ничего не отдаёт, только запоминает последний пост: старые анонсы
    давно отыграли. Анонс старше fresh тоже пропускается — эфир уже кончился.
    """
    posts = (fetcher or fetch)(channel)
    if not posts:
        return []
    before = last_seen(profile, channel)
    mark_seen(profile, channel, posts[-1]["id"])
    if before < 0:
        return []
    now = time.time() if now is None else now
    return [post for post in posts if post["id"] > before and post["links"]
            and (not post["at"] or now - post["at"] <= fresh)]
