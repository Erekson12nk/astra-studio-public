# -*- coding: utf-8 -*-
"""Разбор кеша: что где лежит, какие перекладки осиротели, что удаляется."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os, shutil, sys, tempfile
from pathlib import Path

from core import cache as C

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


work = Path(tempfile.mkdtemp(prefix="cachedir_test_"))
folder = work / "cache"
folder.mkdir()
sources = work / "streams"
sources.mkdir()


def put(path, size=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


live = put(sources / "живой.mp4", 32)
put(folder / "живой.seekable.mp4", 4096)
put(folder / "мёртвый.seekable.mp4", 2048)
put(folder / "живой_01.srt", 64)
put(folder / "живой-ollama.proposals.json", 128)
put(folder / "чтото.log", 8)

items = C.scan(folder)
check("собраны все файлы кеша", len(items) == 5, len(items))

groups = C.by_kind(items)
check("перекладки узнаны по имени", groups[C.REMUX]["count"] == 2)
check("субтитры отделены", groups[C.SUBS]["count"] == 1)
check("ответы движка отделены", groups[C.ANSWERS]["count"] == 1)
check("остальное не разложено по полкам", groups[C.OTHER]["count"] == 1)
check("размер перекладок сложен верно", groups[C.REMUX]["size"] == 4096 + 2048,
      groups[C.REMUX]["size"])

# --- сирота: исходника больше нет на диске ---
listed = [str(live), str(sources / "мёртвый.mp4")]
stale = C.orphans(items, listed)
check("осиротела ровно одна перекладка", len(stale) == 1, len(stale))
check("и это та, чей исходник исчез",
      stale and stale[0]["path"].name == "мёртвый.seekable.mp4")

# --- удаление сироты не трогает остальное ---
gone, freed = C.remove(stale)
check("удалён один файл", gone == 1, gone)
check("освобождено сколько он весил", freed == 2048, freed)
check("живая перекладка на месте", (folder / "живой.seekable.mp4").is_file())
check("субтитры на месте", (folder / "живой_01.srt").is_file())
check("исходник не тронут", live.is_file())

# --- пропавший файл не роняет удаление ---
призрак = [{"path": folder / "нет-такого.seekable.mp4", "size": 10, "kind": C.REMUX}]
gone2, freed2 = C.remove(призрак)
check("удаление несуществующего проходит тихо", (gone2, freed2) == (0, 0))

# --- перекладки astra лежат рядом с исходником и зовутся хешем ---
beside_src = put(sources / "рядом.mp4", 64)
hashed = C.remux_beside(beside_src)
check("имя перекладки astra — хеш от исходника",
      hashed.parent == beside_src.parent and len(hashed.name) == 16 + len(C.REMUX_SUFFIX),
      hashed.name)
put(hashed, 8192)
found = C.beside([str(beside_src)])
check("перекладка рядом с исходником найдена", len(found) == 1, len(found))
check("и это она", found and found[0]["path"].name == hashed.name)
check("её размер учтён", found and found[0]["size"] == 8192)

# --- живой хеш не считается сиротой, хотя имя на исходник не похоже ---
check("хешевая перекладка привязана к своему исходнику",
      C.owner(hashed, [str(beside_src)]) == str(beside_src))
check("живая хешевая перекладка не сирота",
      C.orphans(found, [str(beside_src)]) == [])
check("без своего исходника она сирота",
      len(C.orphans(found, [str(live)])) == 1)

# --- имя пересчитывается от размера и времени: правка исходника даёт другое имя ---
was = C.remux_name(beside_src)
beside_src.write_bytes(b"y" * 128)
check("другой исходник — другое имя перекладки", C.remux_name(beside_src) != was)

# --- у пропавшего исходника имени нет, но это не падение ---
check("нет исходника — нет имени", C.remux_name(sources / "нет.mp4") == "")

# --- clipper зовёт перекладку по имени исходника, и это тоже узнаётся ---
check("перекладка clipper привязана по имени",
      C.owner(folder / "живой.seekable.mp4", [str(live)]) == str(live))
check("не перекладка ничьей и остаётся",
      C.owner(folder / "живой_01.srt", [str(live)]) is None)

# --- две папки не дают дублей, а пустая не мешает ---
same = C.scan(folder, folder, work / "нет-такой-папки")
check("один файл считается один раз", len(same) == 4, len(same))

check("размеры печатаются по-человечески",
      C.human(0) == "0 Б" and C.human(2048) == "2.0 КБ"
      and C.human(43 * 1024 ** 3).endswith("ГБ"),
      "%s / %s / %s" % (C.human(0), C.human(2048), C.human(43 * 1024 ** 3)))

check("ave_cache ищется во временной папке",
      C.ave_folder().name == "ave_cache"
      and str(C.ave_folder()).startswith(tempfile.gettempdir()))

shutil.rmtree(work, ignore_errors=True)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
