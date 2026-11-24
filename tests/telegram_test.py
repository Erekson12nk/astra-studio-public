# -*- coding: utf-8 -*-
"""Телеграм-анонсы и запись эфиров не с твича.

Страница канала собрана по разметке t.me/s/, тексты постов выдуманы. Склейка
кусков проверяется настоящим ffmpeg на коротких синтетических кусках .ts.
streamlink и сеть не нужны.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _REPO)

import shutil
import subprocess
import tempfile
import time

work = tempfile.mkdtemp(prefix="telegram_test_")
_os.environ["ASTRA_PROFILES"] = _os.path.join(work, "profiles")

from core import autopilot as ap
from core import elsewhere as el
from core import ffmpeg as _ff
from core import profiles as _pro
from core import service as svc
from core import telegram as tg

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


def post(number, text, when, links=()):
    hrefs = "".join('<a href="%s" target="_blank">%s</a>' % (url, url) for url in links)
    return ('<div class="tgme_widget_message_wrap js-widget_message_wrap">'
            '<div class="tgme_widget_message text_not_supported_wrap js-widget_message" '
            'data-post="streamer/%d" data-view="x">'
            '<div class="tgme_widget_message_text js-message_text" dir="auto">%s%s</div>'
            '<a class="tgme_widget_message_date" href="https://t.me/streamer/%d">'
            '<time datetime="%s" class="time">12:00</time></a></div></div>'
            % (number, text, hrefs, number, when))


PAGE = "<html><body>" + "".join((
    post(10, "сегодня без стрима, лечу голос", "2026-11-12T10:00:00+00:00"),
    post(11, "погнали<br/>", "2026-11-13T10:53:50+00:00",
         ["https://www.twitch.tv/streamer", "https://twitch.tv/streamer"]),
    post(12, "новый трек, пресейв", "2026-11-13T11:00:00+00:00",
         ["http://music.example.com/x"]),
)) + "</body></html>"

print("ссылки")
check("канал телеграма из ссылок и имени",
      [tg.channel_of(x) for x in ("t.me/somestreamer", "https://t.me/s/somestreamer", "@somestreamer",
                                  "somestreamer", "https://t.me/somestreamer/8623", "")]
      == ["somestreamer"] * 5 + [""])
cases = {
    "https://www.twitch.tv/somestreamer": ("twitch", "somestreamer"),
    "https://www.twitch.tv/videos/123": None,
    "https://www.youtube.com/live/abcDEF123": ("youtube", ""),
    "https://youtu.be/abcDEF123": ("youtube", ""),
    "https://www.youtube.com/@somestreamer/live": ("youtube", ""),
    "https://kick.com/somestreamer": ("kick", "somestreamer"),
    "https://live.vkvideo.ru/somestreamer": ("vk", ""),
    "https://vkvideo.ru/video-123_456": ("vk", ""),
    "https://goodgame.ru/somestreamer": ("goodgame", ""),
    "https://closefans.store/collection/somestreamer": None,
}
wrong = {url: tg.platform_of(url) for url, want in cases.items() if tg.platform_of(url) != want}
check("площадки распознаются, мерч и записи — нет", not wrong, str(wrong))
check("одна трансляция дважды — одна ссылка",
      len(tg.stream_links(["https://www.twitch.tv/somestreamer", "https://twitch.tv/somestreamer"])) == 1)

print("страница канала")
posts = tg.parse(PAGE)
check("посты разобраны по порядку", [p["id"] for p in posts] == [10, 11, 12])
check("текст и время поста", posts[1]["text"].startswith("погнали")
      and posts[1]["at"] == 1794567230.0, str(posts[1]["at"]))
check("ссылки на трансляцию — только в анонсе",
      [len(p["links"]) for p in posts] == [0, 1, 0])

_pro.save({"name": "стрим", "created": time.time(), "face": "", "keep_days": 3})
profile = _pro.load("стрим")
pages = [PAGE]
fetcher = lambda channel: tg.parse(pages[0])
check("первый раз — только запомнить, старое не трогать",
      tg.news(profile, "streamer", fetcher=fetcher) == [] and
      tg.last_seen(profile, "t.me/streamer") == 12)
pages[0] = PAGE.replace("</body>", post(13, "я не подрубаю сегодня стрим",
                                        "2026-11-14T09:00:00+00:00") +
                        post(14, "сегодня на ютубе!", "2026-11-14T10:00:00+00:00",
                             ["https://www.youtube.com/live/abcDEF123"]) + "</body>")
fresh = tg.news(profile, "streamer", fetcher=fetcher, now=1794650400.0 + 600)
check("новый анонс со ссылкой — есть, «не подрубаю» без ссылки — нет",
      [p["id"] for p in fresh] == [14], str([p["id"] for p in fresh]))
check("второй раз тот же анонс не приходит",
      tg.news(profile, "streamer", fetcher=fetcher, now=1794650400.0 + 700) == [])
pages[0] = pages[0].replace("</body>", post(15, "стрим тут", "2026-11-10T10:00:00+00:00",
                                            ["https://kick.com/streamer"]) + "</body>")
check("старый анонс (эфир давно прошёл) пропускается",
      tg.news(profile, "streamer", fetcher=fetcher, now=1794650400.0 + 800) == [])

print("запись не с твича")
folder = _os.path.join(work, "streams")
calls = []


def fake_run(sizes):
    def run(url, path, wait, first, cancel):
        calls.append((wait, first))
        amount = sizes.pop(0) if sizes else 0
        if amount:
            with open(path, "wb") as handle:
                handle.write(b"x" * amount)
    return run


glued = []
path = el.record("https://kick.com/x", folder, "kick_x", run=fake_run([2 << 20, 3 << 20, 0]),
                 glue=lambda parts, target: glued.append(parts) or target)
check("обрыв — второй кусок, конец — склейка",
      len(glued[0]) == 2 and path.endswith("kick_x.mp4"), str(glued))
check("начала ждёт полчаса, обрыв — пять минут", [c[0] for c in calls]
      == [el.WAIT_START, el.GAP, el.GAP] and calls[0][1] and not calls[1][1], str(calls))
check("эфир не начался — пусто, мусора нет",
      el.record("https://kick.com/y", folder, "kick_y", run=fake_run([10])) == ""
      and not [n for n in _os.listdir(folder) if n.startswith("kick_y")])
cmd = el.command("https://kick.com/x", "a.ts", 1800, first=True, binary="SL")
check("streamlink ждёт эфир повторами и берёт начало",
      cmd[:5] == ["SL", "--retry-streams", "30", "--retry-max", "60"]
      and "--hls-live-restart" in cmd and "best" in cmd)

if _ff.have_ffmpeg():
    parts = []
    for number in (1, 2):
        part = _os.path.join(folder, "part_%d.ts" % number)
        subprocess.run([_ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                        "-b:v", "3M", "-f", "mpegts", part], check=True)
        parts.append(part)
    target = _os.path.join(folder, "joined.mp4")
    joined = el.join(parts, target)
    length = _ff.duration(joined) if joined else 0.0
    check("куски склеены в один .mp4, куски убраны", joined == target
          and abs(length - 6.0) < 0.6 and not any(_os.path.exists(p) for p in parts),
          "%.2f с" % length)
else:
    print("  нет ffmpeg — склейка не проверяется")

print("служба")
ap.save_settings(profile, {"twitch": "streamer", "telegram": "t.me/streamer"})
_pro.save({"name": "ютубер", "created": time.time(), "face": "", "keep_days": 3})
tuber = _pro.load("ютубер")
ap.save_settings(tuber, {"telegram": "@tuberchan"})
check("стример только с телеграмом — тоже стример",
      sorted(p["name"] for p in ap.streamers()) == sorted(["ютубер", "стрим"]))


class FakeWatcher:
    def __init__(self, profile, channel):
        self.channel, self.finishes, self.finished, self.current = channel, 0, None, None
        self.pokes = 0

    def start(self):
        pass

    def stop(self):
        pass

    def poke(self):
        self.pokes += 1


made, notes, recorded = {}, [], []
inbox = {"streamer": [], "tuberchan": []}


def news(profile, channel):
    return inbox.pop(tg.channel_of(channel), [])


def recorder(url, folder, base, cancel=None, log=None):
    recorded.append((url, base))
    target = _os.path.join(folder, base + ".mp4")
    with open(target, "wb") as handle:
        handle.write(b"0")
    return target


tock = [1794650400.0]
service = svc.Service(log=lambda text: None, notify=notes.append,
                      watcher=lambda p, c: made.setdefault(p["name"], FakeWatcher(p, c)),
                      pilot=lambda p: None, clock=lambda: tock[0], news=news,
                      recorder=recorder, threads=False)
service.refresh()
check("сторож твича — только у кого есть твич", sorted(made) == ["стрим"])
while not service.jobs.empty():
    service.jobs.get()
service.pending.clear()
tock[0] += svc.TG_POLL
inbox["streamer"] = [{"id": 20, "text": "погнали", "at": tock[0], "links": tg.stream_links(
    ["https://www.twitch.tv/streamer", "https://www.youtube.com/live/abc"])}]
service.refresh()
check("анонс со своим твичем — твич спрошен сразу, лишнего не пишется",
      made["стрим"].pokes == 1 and recorded == [])
tock[0] += svc.TG_POLL
inbox["tuberchan"] = [{"id": 5, "text": "сегодня на ютубе", "at": tock[0],
                       "links": tg.stream_links(["https://www.youtube.com/live/abc"])}]
service.refresh()
rows = ap.streams(_pro.load("ютубер"))
check("анонс другой площадки — эфир записан", recorded and
      recorded[0][0] == "https://www.youtube.com/live/abc", str(recorded))
check("запись встала в очередь нарезки как обычный стрим",
      len(rows) == 1 and rows[0]["status"] == ap.NEW and rows[0]["path"].endswith(".mp4")
      and rows[0]["title"] == "сегодня на ютубе")
check("служба поставила нарезку", ("run", "ютубер") in service.pending)
check("уведомления: анонс и конец записи",
      any("анонс эфира на youtube" in n for n in notes)
      and any("закончился — режу" in n for n in notes), str(notes))
pilot = ap.Pilot(_pro.load("ютубер"), log=lambda text: None, lister=lambda c, l: [])
check("без твича старые записи не пропускаются и ничего не ломается",
      pilot.skip_old() == 0 and [s["vod"] for s in pilot.waiting_streams()]
      == [rows[0]["vod"]])

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
