# -*- coding: utf-8 -*-
"""Запись звука эфира и звук из неё на местах мута твича.

Звук синтетический: «стрим» — шум с тишиной на месте мута, «запись эфира» — тот же
шум целиком, тише и сдвинутый во времени. Твич и streamlink подставные, сеть не нужна.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _REPO)

import json
import shutil
import subprocess
import tempfile

import numpy as np

from core import ffmpeg as _ff
from core import live
from core import mute

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


work = tempfile.mkdtemp(prefix="live_test_")
RATE = 16000


def write_audio(path, samples, fmt):
    data = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
    cmd = [_ff.binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-"] + fmt + [path]
    subprocess.run(cmd, input=data, check=True, **_ff._NOWIN)


print("канал и твич")
check("канал из ссылки на эфир", live.channel_of("https://www.twitch.tv/SomeStreamer") == "somestreamer")
check("канал просто именем", live.channel_of(" somestreamer ") == "somestreamer")
check("ссылка на запись — не канал", live.channel_of("https://www.twitch.tv/videos/29") == ""
      and live.channel_of("что-то не то") == "")
answer = {"data": {"user": {"stream": {"id": "77", "createdAt": "2026-11-05T15:00:00Z"}}}}
asked = []
now = live.live_now("somestreamer", ask=lambda body: asked.append(body) or answer)
check("эфир идёт: номер и начало", now == {"id": "77", "started": 1793890800.0}
      and '"somestreamer"' in asked[0]["query"], str(now))
check("эфира нет", live.live_now("x", ask=lambda body: {"data": {"user": {"stream": None}}})
      is None)
info = live.vod_info("5", ask=lambda body: {"data": {"video": {
    "createdAt": "2026-11-05T15:00:10Z", "lengthSeconds": 300, "owner": {"login": "Somestreamer"}}}})
check("запись твича: начало, длина, канал",
      info == {"started": 1793890810.0, "length": 300.0, "channel": "somestreamer"}, str(info))

print("сверка по звуку")
rng = np.random.default_rng(7)
noise = rng.standard_normal(RATE * 60).astype(np.float32) * 0.2
probe = noise[RATE * 20:RATE * 30]
shift, score = live.match(probe, noise[RATE * 5:RATE * 50] * 0.4)
check("кусок найден на своём месте, громкость не мешает",
      shift == RATE * 15 and score > 0.99, "%d %.2f" % (shift, score))
shift, score = live.match(probe, rng.standard_normal(RATE * 30).astype(np.float32))
check("чужой звук не похож", score < live.LEAST_SCORE, "%.2f" % score)

print("звук мута из записи эфира")
LENGTH, MUTE_A, MUTE_B, LAG = 300, 120.0, 180.0, 40.0
full = rng.standard_normal(RATE * LENGTH).astype(np.float32) * 0.2
stream_audio = full.copy()
stream_audio[int(MUTE_A * RATE):int(MUTE_B * RATE)] = 0.0
stream = _os.path.join(work, "стрим [v5000001].m4a")
write_audio(stream, stream_audio, ["-c:a", "aac", "-b:a", "192k"])
with open(mute.cache_path(stream), "w", encoding="utf-8") as handle:
    json.dump({"muted": [[MUTE_A, MUTE_B]], "source": "твич"}, handle)
folder = _os.path.join(work, live.FOLDER)
_os.makedirs(folder)
record = _os.path.join(folder, "somestreamer_2026-11-05_15-00-50.ts")
write_audio(record, full[int(LAG * RATE):] * 0.5, ["-c:a", "aac", "-b:a", "160k", "-f", "mpegts"])
CREATED = 1793890810.0
live.write_meta(record, {"channel": "somestreamer", "started": CREATED + LAG + live.LATENCY + 3.0})
twitch = lambda body: {"data": {"video": {"createdAt": "2026-11-05T15:00:10Z",
                                          "lengthSeconds": LENGTH,
                                          "owner": {"login": "somestreamer"}}}}
said = []
patches = live.patches_for(stream, folder, ask=twitch, log=said.append)
check("одно место мута закрыто целиком", len(patches) == 1
      and abs(patches[0]["start"] - MUTE_A) < 0.01 and abs(patches[0]["end"] - MUTE_B) < 0.01,
      str([(p["start"], p["end"]) for p in patches]))
if patches:
    patch = patches[0]
    check("громкость подогнана под стрим (запись вдвое тише)",
          1.8 < patch["gain"] < 2.2, "%.2f" % patch["gain"])
    got = mute.read_audio(patch["clip"], patch["from"] + 10.0, 10.0)
    want = full[int((MUTE_A + 10) * RATE):int((MUTE_A + 20) * RATE)]
    _, score = live.match(want, np.concatenate([np.zeros(800, np.float32), got,
                                                np.zeros(800, np.float32)]))
    lag_at, _ = live.match(got[:RATE * 5], full[int((MUTE_A + 9) * RATE):
                                                 int((MUTE_A + 22) * RATE)])
    check("в заплатке тот самый звук стрима, сдвиг меньше 50 мс",
          score > 0.8 and abs(lag_at - RATE) < RATE * 0.05,
          "сходство %.2f, сдвиг %.0f мс" % (score, (lag_at - RATE) * 1000.0 / RATE))
check("ответ запомнен рядом со стримом", _os.path.isfile(live.live_cache(stream)))
again = live.patches_for(stream, folder, ask=lambda body: 1 / 0)
check("второй раз — из запомненного, без твича", again == patches)

print("запись эфира рвалась внутри мута")
gap_folder = _os.path.join(work, "gap", live.FOLDER)
_os.makedirs(gap_folder)
gap_record = _os.path.join(gap_folder, "somestreamer_gap.ts")
torn = np.concatenate([full[int(LAG * RATE):int(150 * RATE)], full[int(162 * RATE):]])
write_audio(gap_record, torn * 0.5, ["-c:a", "aac", "-b:a", "160k", "-f", "mpegts"])
live.write_meta(gap_record, {"channel": "somestreamer", "started": CREATED + LAG + live.LATENCY})
gap_stream = _os.path.join(work, "gap", "стрим [v5000001].m4a")
shutil.copy(stream, gap_stream)
shutil.copy(mute.cache_path(stream), mute.cache_path(gap_stream))
said = []
parts = live.patches_for(gap_stream, gap_folder, ask=twitch, log=said.append)
check("мут закрыт двумя кусками, каждый со своим сдвигом",
      len(parts) == 2 and abs(parts[0]["end"] - parts[1]["start"]) < 0.01
      and any("пропуск 12 с" in line for line in said),
      str([(round(p["start"]), round(p["end"])) for p in parts]))

print("реклама вырезана из записи эфира (кусочки MP4, как у твича), в метках дыра")
ad_folder = _os.path.join(work, "ad", live.FOLDER)
_os.makedirs(ad_folder)
ad_record = _os.path.join(ad_folder, "somestreamer_ad.m4a")
cut = (np.arange(len(full)) / RATE >= LAG)
write_audio(ad_record, full[cut] * 0.5,
            ["-af", "aselect='not(between(t,%g,%g))'" % (150 - LAG, 162 - LAG), "-c:a", "aac",
             "-b:a", "160k", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
             "-frag_duration", "2000000", "-f", "mp4"])
live.write_meta(ad_record, {"channel": "somestreamer", "started": CREATED + LAG + live.LATENCY})
check("длина записи — по меткам, вместе с дырой",
      abs(live.length_of(ad_record) - (LENGTH - LAG)) < 1.0, "%.1f" % live.length_of(ad_record))
ad_stream = _os.path.join(work, "ad", "стрим [v5000001].m4a")
shutil.copy(stream, ad_stream)
shutil.copy(mute.cache_path(stream), mute.cache_path(ad_stream))
said = []
ads = live.patches_for(ad_stream, ad_folder, ask=twitch, log=said.append)
check("сдвиг с двух сторон один — мут закрыт одним куском",
      len(ads) == 1 and abs(ads[0]["start"] - MUTE_A) < 0.01, str(said[-2:]))
if ads:
    got = mute.read_audio(ads[0]["clip"], ads[0]["from"], MUTE_B - MUTE_A)
    gap = got[int((152 - MUTE_A) * RATE):int((160 - MUTE_A) * RATE)]
    after = got[int((165 - MUTE_A) * RATE):int((175 - MUTE_A) * RATE)]
    _, score = live.match(full[int(166 * RATE):int(172 * RATE)], after)
    check("на месте рекламы тишина, после неё звук на своём месте",
          mute.rms(gap) < 1e-3 and score > 0.8, "%.4f %.2f" % (mute.rms(gap), score))

print("две записи перекрываются (звук с начала и запись эфира)")
both = _os.path.join(work, "both", live.FOLDER)
_os.makedirs(both)
early = _os.path.join(both, "somestreamer_1_начало.m4a")
write_audio(early, full[:int(160 * RATE)] * 0.5, ["-c:a", "aac", "-b:a", "160k"])
live.write_meta(early, {"channel": "somestreamer", "started": CREATED + live.LATENCY})
shutil.copy(record, _os.path.join(both, "somestreamer_2.ts"))
shutil.copy(live.meta_path(record), live.meta_path(_os.path.join(both, "somestreamer_2.ts")))
both_stream = _os.path.join(work, "both", "стрим [v5000001].m4a")
shutil.copy(stream, both_stream)
shutil.copy(mute.cache_path(stream), mute.cache_path(both_stream))
twice = live.patches_for(both_stream, both, ask=twitch)
spans = sorted((round(p["start"], 1), round(p["end"], 1)) for p in twice)
check("каждое место мута закрыто один раз", spans == [(120.0, 160.0), (160.0, 180.0)]
      or spans == [(120.0, 180.0)], str(spans))

print("чужие и лишние записи")
other = _os.path.join(work, "other", live.FOLDER)
_os.makedirs(other)
alien = _os.path.join(other, "kto-to_1.ts")
shutil.copy(record, alien)
live.write_meta(alien, {"channel": "kto-to", "started": CREATED + LAG + live.LATENCY})
other_stream = _os.path.join(work, "other", "стрим [v5000001].m4a")
shutil.copy(stream, other_stream)
shutil.copy(mute.cache_path(stream), mute.cache_path(other_stream))
check("запись другого канала не берётся", live.patches_for(other_stream, other, ask=twitch) == [])
check("нет записей — нет заплаток",
      live.patches_for(stream, _os.path.join(work, "пусто"), ask=twitch) == [])

print("нарезчик закрывает только то, чего нет в записи эфира")
cutter = [{"start": 100.0, "end": 200.0, "from": 10.0, "rate": 1.0, "clip": "c", "gain": 1}]
left = live.without(cutter, [{"start": 120.0, "end": 180.0}])
check("дыра вырезана, «откуда» сдвинуто",
      [(p["start"], p["end"], p["from"]) for p in left] == [(100.0, 120.0, 10.0),
                                                            (180.0, 200.0, 90.0)], str(left))
check("закрытое целиком пропадает",
      live.without(cutter, [{"start": 90.0, "end": 210.0}]) == [])

print("сторож эфира")


class FakeProc:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code

    def terminate(self):
        self.code = 1

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = 1


procs, cmds = [], []


def popen(cmd, **kw):
    cmds.append(cmd)
    procs.append(FakeProc())
    return procs[-1]


rec_folder = _os.path.join(work, "rec")
clock = [1000.0]
rec = live.Recorder("somestreamer", rec_folder, stream={"id": "77", "started": 900.0},
                    binary="streamlink", popen=popen, clock=lambda: clock[0])
check("streamlink пишет только звук канала в .ts",
      cmds[0][0] == "streamlink" and "https://www.twitch.tv/somestreamer" in cmds[0]
      and live.QUALITY in cmds[0] and cmds[0][cmds[0].index("-o") + 1] == rec.path
      and rec.path.endswith(live.EXT))
check(".json рядом: номер эфира, начало ещё неизвестно",
      live.read_meta(rec.path)["stream"] == "77" and live.read_meta(rec.path)["started"] is None)
with open(rec.path, "wb") as handle:
    handle.write(b"x" * 100)
clock[0] = 1004.0
rec.tick()
check("начало куска — когда пошли первые байты", live.read_meta(rec.path)["started"] == 1004.0)
empty = live.Recorder("somestreamer", rec_folder, popen=popen, clock=lambda: 2000.0)
empty.stop()
check("пустой кусок за собой убирается", not _os.path.exists(empty.path)
      and not _os.path.exists(live.meta_path(empty.path)))

states, made = [], []
on_air = [False]


def fake_recorder(channel, folder, stream=None):
    made.append(stream)
    return live.Recorder(channel, folder, stream=stream, binary="streamlink", popen=popen)


watcher = live.Watcher("somestreamer", rec_folder, on_state=states.append,
                       ask=lambda body: answer if on_air[0] else
                       {"data": {"user": {"stream": None}}}, recorder=fake_recorder)
pause = watcher.step()
check("не в эфире — ждёт минуту", pause == live.POLL and "не в эфире" in states[-1])
on_air[0] = True
pause = watcher.step()
check("эфир начался — запись пошла", len(made) == 1 and watcher.current is not None
      and pause == live.TICK and "начался" in states[-1])
watcher.step()
check("пока пишет — показывает время и объём", "идёт запись" in states[-1])
procs[-1].code = 1
watcher.step()
check("streamlink вышел, эфир идёт — новый кусок", len(made) == 2 and watcher.parts == 2)
procs[-1].code = 1
on_air[0] = False
watcher.step()
check("эфир кончился — запись не начинается", len(made) == 2 and watcher.current is None)
watcher.ask = lambda body: 1 / 0
watcher.step()
check("твич не ответил — сторож не падает", "не ответил" in states[-1])

print("конец эфира: сторож говорит, сколько записано")
air = [True]
tock = [5000.0]


class WroteRecorder:
    """Кусок, в котором уже что-то записано: streamlink здесь не нужен."""

    def __init__(self, channel, folder, stream=None):
        _os.makedirs(folder, exist_ok=True)
        self.path = _os.path.join(folder, "wrote_%d.m4a" % tock[0])
        with open(self.path, "wb") as handle:
            handle.write(b"x" * (3 << 20))
        self.meta = {"started": tock[0], "stream_started": (stream or {}).get("started")}
        self.dead = False

    def size(self):
        return _os.path.getsize(self.path)

    def alive(self):
        return not self.dead

    def tick(self):
        pass

    def stop(self):
        self.dead = True


ends = live.Watcher("somestreamer", _os.path.join(work, "ends"), on_state=states.append,
                    ask=lambda body: answer if air[0] else {"data": {"user": {"stream": None}}},
                    recorder=lambda *a, **k: WroteRecorder(*a, **k), catcher=None,
                    clock=lambda: tock[0])
ends.step()
check("эфир пошёл — пишется кусок", ends.current is not None and ends.finishes == 0)
ends.current.dead = True
air[0] = False
tock[0] = 5000.0 + 3725
ends.step()
done = ends.finished
check("конец эфира замечен один раз", ends.finishes == 1 and ends.current is None)
check("посчитано, сколько записано",
      done and done["parts"] == 1 and done["bytes"] == (3 << 20)
      and abs(done["seconds"] - 3725) < 1, str(done))
check("запомнено, когда эфир начался по твичу — по нему ищется запись",
      done["started"] == live.live_now("somestreamer", ask=lambda body: answer)["started"],
      str(done.get("started")))
text = live.finished_text(done)
check("уведомление о конце: время, объём и папка",
      "закончился" in text and "1:02:05" in text and "3 МБ" in text
      and _os.path.join(work, "ends") in text, text)
ends.step()
check("второй раз конец не объявляется", ends.finishes == 1)
air[0] = True
ends.step()
ends.current.dead = True
air[0] = False
ends.step()
check("следующий эфир — новое уведомление", ends.finishes == 2)

print("начало эфира докачивается, если сторож включён посреди эфира")
vods = {"data": {"user": {"videos": {"edges": [
    {"node": {"id": "900", "status": "RECORDED", "createdAt": "2026-11-04T10:00:00Z"}},
    {"node": {"id": "901", "status": "RECORDING", "createdAt": "2026-11-05T15:00:04Z"}}]}}}}
check("запись твича этого эфира найдена",
      live.recording_vod("somestreamer", {"id": "77", "started": 1793890800.0},
                         ask=lambda body: vods) == {"id": "901", "started": 1793890804.0})
check("старая запись за эфир не принимается",
      live.recording_vod("somestreamer", {"id": "77", "started": 1793990800.0},
                         ask=lambda body: vods) is None)
PLAYLIST = ("#EXTM3U\n#EXT-X-MAP:URI=\"init-0.mp4\"\n#EXTINF:10.000,\n0.mp4\n"
            "#EXTINF:10.000,\n1-muted.mp4\n#EXTINF:10.000,\n2.mp4\n")
files = {"http://x/v/index-dvr.m3u8": PLAYLIST.encode(), "http://x/v/init-0.mp4": b"I",
         "http://x/v/0.mp4": b"A", "http://x/v/1-muted.mp4": b"B", "http://x/v/2.mp4": b"C"}
fetched, steps, said = [], [], []


def fake_get(url):
    fetched.append(url)
    return files[url]


catch_dir = _os.path.join(work, "catch")
got = live.fetch_start("somestreamer", catch_dir, {"id": "77", "started": 1793890800.0},
                       ask=lambda body: vods, playlist=lambda vod: "http://x/v/index-dvr.m3u8",
                       get=fake_get, log=said.append,
                       progress=lambda done, total: steps.append((done, total)))
check("все куски по порядку, перед ними заголовок",
      got and open(got, "rb").read() == b"IABC" and steps[-1] == (3, 3), str(fetched))
meta = live.read_meta(got) if got else {}
check("файл «начало» привязан к записи твича",
      got.endswith("somestreamer_77_начало" + live.EXT) and meta.get("vod") == "901"
      and meta.get("started") == 1793890804.0 + live.LATENCY and meta.get("start"))
check("сказано, что часть уже заглушена", "1 уже заглушены" in said[-1], said[-1])
stopped = live.fetch_start("somestreamer", catch_dir, {"id": "78", "started": 1793890800.0},
                           ask=lambda body: vods, playlist=lambda vod: "http://x/v/index-dvr.m3u8",
                           get=fake_get, cancel=lambda: True)
check("остановили — недокачанное убрано", stopped == "" and not any(
    name.startswith("somestreamer_78") for name in _os.listdir(catch_dir)))
asked_gone = []


def renamed_get(url):
    asked_gone.append(url)
    if url.endswith("/2.mp4"):
        raise live.Gone("2.mp4")                 # после эфира твич переименовал в 2-muted
    return files[url]


said.clear()
got = live.fetch_start("somestreamer", catch_dir, {"id": "79", "started": 1793890800.0},
                       ask=lambda body: vods, playlist=lambda vod: "http://x/v/index-dvr.m3u8",
                       get=renamed_get, log=said.append)
check("переименованный твичем кусок пропущен сразу, остальное на месте",
      got and open(got, "rb").read() == b"IAB" and asked_gone.count("http://x/v/2.mp4") == 1
      and "2 уже заглушены" in said[-1], str(said))


def broken_get(url):
    if url.endswith("/1-muted.mp4"):
        raise RuntimeError("сеть упала")
    return files[url]


try:
    live.fetch_start("somestreamer", catch_dir, {"id": "80", "started": 1793890800.0},
                     ask=lambda body: vods, playlist=lambda vod: "http://x/v/index-dvr.m3u8",
                     get=broken_get)
    fell = False
except RuntimeError:
    fell = True
check("докачка упала — недокачанный .part не остаётся", fell and not any(
    name.startswith("somestreamer_80") for name in _os.listdir(catch_dir)))


import urllib.error
import urllib.request as _request

real_open = _request.urlopen
tries = []


def refuse(url, timeout=None):
    tries.append(url)
    raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)


_request.urlopen = refuse
try:
    live._download("http://x/v/5.mp4")
    gone = False
except live.Gone:
    gone = True
_request.urlopen = real_open
check("403 — кусок переименован, без 30 повторов", gone and len(tries) == 1)

catches = []
clock2 = [1793890800.0 + 1800]
late = live.Watcher("somestreamer", catch_dir, ask=lambda body: answer, threads=False,
                    recorder=lambda channel, folder, stream=None: FakeRecorder(),
                    catcher=lambda *a, **k: catches.append(a[2]["id"]) or "путь",
                    clock=lambda: clock2[0])


class FakeRecorder:
    meta = {"started": None}

    def alive(self):
        return False

    def stop(self):
        pass


late.step()
check("включён через полчаса после начала — докачивает начало", catches == ["77"])
late.step()
check("для того же эфира второй раз не качает", catches == ["77"])
early = live.Watcher("somestreamer", catch_dir, ask=lambda body: answer, threads=False,
                     recorder=lambda channel, folder, stream=None: FakeRecorder(),
                     catcher=lambda *a, **k: catches.append("рано") or "путь",
                     clock=lambda: 1793890800.0 + 30)
early.step()
check("включён в первые минуты — докачивать нечего", "рано" not in catches)
broken = live.Watcher("somestreamer", catch_dir, ask=lambda body: answer, threads=False,
                      recorder=lambda channel, folder, stream=None: FakeRecorder(),
                      catcher=lambda *a, **k: 1 / 0, clock=lambda: clock2[0])
broken.step()
check("докачка сломалась — сторож пишет дальше и попробует снова",
      "не докачалось" in broken.catch_text and "77" not in broken.caught)

shutil.rmtree(work, ignore_errors=True)
print("\nитог: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
