# -*- coding: utf-8 -*-
"""Мут твича: где заглушено и как положить на это место свой звук.

Звук синтетический: ролик — тон с тишиной на месте мута, своя запись — другой тон.
Твич подставной, сеть не нужна.
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

import numpy as np

from core import ffmpeg as _ff
from core import mute

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


work = tempfile.mkdtemp(prefix="mute_test_")

print("откуда знать про мут")
check("номер записи из имени файла",
      mute.vod_id(r"D:\x\еще не выздоровил ｜ !тг [v2918345272].mp4") == "2918345272"
      and mute.vod_id("стрим.mp4") == "")
answer = {"data": {"video": {"muteInfo": {"mutedSegmentConnection": {"nodes": [
    {"offset": 1260, "duration": 180}, {"offset": 0, "duration": 180},
    {"offset": 1440, "duration": 180}]}}}}}
asked = []
check("куски твича по порядку, соседние слиты",
      mute.twitch_ranges("1", ask=lambda body: asked.append(body) or answer)
      == [(0.0, 180.0), (1260.0, 1620.0)] and '"1"' in asked[0]["query"])
check("запись без мута", mute.twitch_ranges("1", ask=lambda body: {
    "data": {"video": {"muteInfo": None}}}) == [])


print("куда класть звук")
found = [{"start": 1100, "end": 1280, "from": 100, "rate": 1.0, "clip": "c", "gain": 2.0}]
spots = mute.placements([(1000, 150), (1200, 300)], found)
check("кусок мута режется вырезами ролика", [(round(a, 2), round(b, 2), round(c, 2))
                                            for a, b, _, c, *_ in spots]
      == [(100, 50, 100), (150, 80, 200)], str(spots))
check("вне мута ничего", mute.placements([(0, 500)], found) == [])
text, clips = mute.graph(spots)
check("граф: свой звук глушится, сверху свой звук с громкостью",
      "between(t,100.000,150.000)" in text and "volume=2.0000" in text
      and "amix=inputs=3:normalize=0" in text and clips == ["c"])

print("на настоящем файле")
ffmpeg = _ff.binary("ffmpeg")
rolik = _os.path.join(work, "ролик.mp4")
clip = _os.path.join(work, "запись.m4a")
subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "color=c=gray:s=320x180:r=25:d=30", "-f", "lavfi", "-i",
                "sine=f=440:d=30:sample_rate=48000", "-af",
                "volume=0:enable='between(t,10,20)'", "-c:v", "libx264", "-c:a", "aac",
                "-shortest", rolik], check=True, **_ff._NOWIN)
subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "sine=f=880:d=60:sample_rate=44100", "-c:a", "aac", clip], check=True,
               **_ff._NOWIN)
before = mute.rms(mute.read_audio(rolik, 2, 5))
moment = {"start": 500.0, "end": 530.0, "drops": []}
put = mute.apply(rolik, moment, [{"start": 510, "end": 520, "from": 40, "rate": 1.0,
                                  "clip": clip, "gain": 1.0}])
inside = mute.read_audio(rolik, 12, 6)
check("на месте мута теперь звук записи", put == 1 and mute.rms(inside) > 0.05,
      "%.3f" % mute.rms(inside))
spectrum = np.abs(np.fft.rfft(inside))
check("это именно его звук (880 Гц)", abs(np.argmax(spectrum) / 6.0 - 880) < 20)
check("остальное как было", abs(mute.rms(mute.read_audio(rolik, 2, 5)) - before) < 0.02
      and abs(_ff.duration(rolik) - 30) < 0.5 and not _os.path.exists(
          _os.path.join(work, "ролик.mute.mp4")))

print("без номера записи — по тишине")
quiet = _os.path.join(work, "стрим.mp4")
shutil.copy(rolik, quiet)
spans = mute.muted_ranges(quiet)
check("тишина длиннее 20 с не ищется в 30-секундном файле", spans == [])
check("ответ кешируется рядом", _os.path.isfile(mute.cache_path(quiet)))
subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "sine=f=440:d=60:sample_rate=48000", "-af",
                "volume=0:enable='between(t,10,40)'", "-c:a", "aac", quiet + ".m4a"],
               check=True, **_ff._NOWIN)
found = mute.silent_ranges(quiet + ".m4a")
check("полная тишина находится", len(found) == 1 and abs(found[0][0] - 10) < 1
      and abs(found[0][1] - 40) < 1, str(found))

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
