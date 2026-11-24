# -*- coding: utf-8 -*-
"""Выбор кодировщика: объяснение по-русски и запасной путь на процессор."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import sys

from core import ffmpeg as F

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


NVENC_OLD = ("[h264_nvenc @ 0000] Driver does not support the required nvenc API "
             "version. Required: 13.1 Found: 13.0 [h264_nvenc @ 0000] The minimum "
             "required Nvidia driver for nvenc is 610.00 or newer")

# --- старый драйвер объясняется словами, а не выводом ffmpeg ---
note = F.explain_encoder("h264_nvenc", NVENC_OLD)
check("сказано, что дело в драйвере", "драйвер" in note, note)
check("названы обе версии API", "13.1" in note and "13.0" in note)
check("сказано, какой драйвер нужен", "610.00" in note)
check("сказано, чем это грозит", "на процессоре" in note)
check("сырой вывод ffmpeg не пролезает", "Driver does not support" not in note)

# --- отсутствующее железо: без пугающих DLL ---
qsv = F.explain_encoder("h264_qsv", "[h264_qsv @ 0] Error creating a MFX session: -9.")
check("Intel: сказано про отсутствие железа", "Intel" in qsv, qsv)
amf = F.explain_encoder("h264_amf", "[AMF @ 0] DLL amfrt64.dll failed to open")
check("AMD: сказано про отсутствие железа", "AMD" in amf, amf)

# --- незнакомая причина отдаётся как есть, а не теряется ---
other = F.explain_encoder("h264_nvenc", "что-то совсем другое")
check("незнакомая причина не проглатывается", "что-то совсем другое" in other, other)

# --- явно заказанный кодировщик не проверяется и не подменяется ---
calls = []
real_works = F.encoder_works
F.encoder_works = lambda name: (calls.append(name), (False, "нет"))[1]
try:
    check("заказанный libx264 берётся как есть",
          F.pick_encoder("libx264") == "libx264")
    check("на заказанный кодировщик проб не тратится", calls == [], calls)

    # --- когда ничего не работает, остаётся процессор ---
    said = []
    picked = F.pick_encoder("auto", report=said.append)
    check("без рабочего ускорителя берётся libx264", picked == "libx264", picked)
    check("о каждой неудаче сказано", len(said) >= 1, len(said))
finally:
    F.encoder_works = real_works

# --- список кодировщиков спрашивается у ffmpeg один раз ---
F._ENCODER_LISTING.clear()
runs = []
real_run = F.run
F.run = lambda *a, **k: (runs.append(1), real_run(*a, **k))[1]
try:
    F.encoder_available("h264_nvenc")
    F.encoder_available("h264_qsv")
    F.encoder_available("h264_amf")
    check("ffmpeg -encoders запускается один раз на все проверки",
          len(runs) == 1, len(runs))
finally:
    F.run = real_run

# --- то, что реально доступно на этой машине ---
here = F.pick_encoder("auto")
check("выбранный кодировщик существует",
      here == "libx264" or F.encoder_available(here), here)
print("      на этой машине выбран: %s" % here)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
