# -*- coding: utf-8 -*-
"""Журнал обучения, сброс накопленного и мелкие удобства окна.

Три вещи: колесо над предпросмотром меняет
рамку и не листает страницу; Ctrl+V в любой раскладке кладёт ссылку в поле
источника; накопленные оценки и сравнения можно посмотреть журналом и
стереть, чтобы учить модель вкуса с нуля.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os
import tempfile
import tkinter as tk
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="learnlog_")
os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(HOME, "settings.json")
os.environ["ASTRA_PROFILES"] = os.path.join(HOME, "profiles")
os.environ["CLIPPER_WORK"] = os.path.join(HOME, "work")

import tkinter.messagebox as _mb
_mb.showerror = lambda *a, **k: None
_mb.showinfo = lambda *a, **k: None
_mb.askyesno = lambda *a, **k: True

from PIL import Image

from core import learnlog as _ll
from core import profiles as _pro
from clipper.models import Candidate

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-56s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


print("\n--- журнал пишется по-человечески ---")
_ll.write(HOME, "первая")
_ll.write(HOME, "вторая")
raw = open(_ll.path(HOME), encoding="utf-8", newline="").read()
check("две записи, по строке на каждую", raw.count("\n") == 2, repr(raw[-12:]))
check("пустых строк между ними нет", "\n\n" not in raw)
check("обе записи читаются", "первая" in _ll.read(HOME) and "вторая" in _ll.read(HOME))
_ll.clear(HOME)
check("очистка убирает файл", not _ll.path(HOME).is_file())
check("чтение пустого журнала не падает", _ll.read(HOME) == "")

print("\n--- оценка: журнал и сброс ---")
photo = os.path.join(HOME, "face.png")
Image.new("RGB", (64, 64), "gray").save(photo)
_pro.create("тест", photo)
_pro.set_active("тест")

from clipper.gui import Rating
from core import rerank as _rr

root = tk.Tk()
root.withdraw()
rating = Rating(root, chrome=False)

video = rating.store.upsert_video("stream.mp4", 60.0, "ru")
made = [Candidate(start=0, end=5, title="раз", reason="", score=1.0),
        Candidate(start=5, end=9, title="два", reason="", score=1.0)]
rating.store.save_candidates(video, made, "тест")
ids = [row["id"] for row in rating.store.list_candidates(video)]

rating.current_pair = ({"id": ids[0]}, {"id": ids[1]})
rating._vote("a")
rating.store.add_rating(ids[0], 5)
rating._note("оценка клипа %d: 5" % ids[0])

stats = rating.store.training_stats()
check("сравнение записано", stats["comparisons"] == 1)
check("оценка записана", stats["ratings"] == 1)
journal = _ll.read(rating._learn_folder())
check("сравнение попало в журнал", "сравнение" in journal)
check("оценка попала в журнал", "оценка клипа" in journal)
check("журнал лежит у профиля",
      rating._learn_folder() == _pro.folder("тест"), rating._learn_folder())

check("модель вкуса лежит у профиля",
      Path(rating.cfg.rerank_path) == _pro.folder("тест") / _pro.RERANK_NAME,
      rating.cfg.rerank_path)
model = _rr.save({"features": [], "weights": [], "means": [], "spreads": []},
                 rating.cfg.rerank_path)
check("модель вкуса на месте", os.path.isfile(model))

rating.forget_training()
stats = rating.store.training_stats()
check("сравнения стёрты", stats["comparisons"] == 0)
check("оценки стёрты", stats["ratings"] == 0)
check("кандидаты остались", stats["candidates"] == 2, stats["candidates"])
check("модель вкуса удалена", not os.path.isfile(model))
check("в журнале осталась запись о сбросе",
      "обучение сброшено" in _ll.read(rating._learn_folder()))

rating.show_journal()
root.update()
tops = [w for w in rating.winfo_children() if isinstance(w, tk.Toplevel)]
check("окно журнала открылось", len(tops) == 1)
if tops:
    text = tops[0].winfo_children()[0].get("1.0", "end")
    check("в окне видно, где лежит журнал", "learn.log" in text)
    check("в окне видно, сколько накоплено", "клипов" in text)
    check("в окне видно, где модель вкуса", "модель вкуса" in text)
    tops[0].destroy()

rating.shutdown()
root.destroy()

print("\n--- у каждого профиля своя модель вкуса ---")
from clipper.config import Config, with_profile

_pro.create("другой", photo)
_pro.set_active("тест")
mine = with_profile().rerank_path
_pro.set_active("другой")
yours = with_profile().rerank_path
check("пути разные", mine != yours)
check("модель первого — у первого", Path(mine).parent == _pro.folder("тест"))
check("модель второго — у второго", Path(yours).parent == _pro.folder("другой"))
check("обученная модель первого второму не видна",
      os.path.isfile(mine) is False and os.path.isfile(yours) is False)
check("без профиля путь прежний, в рабочей папке",
      Path(Config().rerank_path).parent == Path(os.environ["CLIPPER_WORK"]))

legacy = Path(os.environ["CLIPPER_WORK"]) / _pro.RERANK_NAME
legacy.parent.mkdir(parents=True, exist_ok=True)
legacy.write_text("{}", encoding="utf-8")
check("старая общая модель переезжает к активному профилю",
      _pro.adopt_former_rerank() and not legacy.is_file()
      and _pro.rerank_path(_pro.load("другой")).is_file())
legacy.write_text("{}", encoding="utf-8")
check("второй раз не переносит — у профиля уже своя",
      not _pro.adopt_former_rerank() and legacy.is_file())
legacy.unlink()

_pro.set_active("тест")

print("\n--- колесо и Ctrl+V в Студии ---")
import media as _media

from studio.window import Studio

source = _media.build()["speech"]
app = Studio()
app.source.set(source)
app._refresh_source()
app._switch("clip")
app._switch_clip("edit")

import time

deadline = time.time() + 120
while time.time() < deadline:
    app.update()
    time.sleep(0.05)
    if app.editor is not None and app.editor.info is not None:
        break

editor = app.editor
check("монтаж загрузился", editor is not None and editor.info is not None)

if editor is not None and editor.info is not None:
    editor.on_view(1)
    app.update()
    editor.var_target.set("cam")
    editor.cam_box = (100, 100, 200, 200)
    canvas = app.editor_canvas
    canvas.event_generate("<Enter>", x=5, y=5)
    app.update()
    was_box = tuple(editor.cam_box)
    was_view = canvas.yview()[0]
    editor.canvas_src.event_generate("<MouseWheel>", delta=-120, x=20, y=20)
    app.update()
    check("колесо над кадром меняет рамку", tuple(editor.cam_box) != was_box,
          "%s -> %s" % (was_box, tuple(editor.cam_box)))
    check("и при этом страница не листается", canvas.yview()[0] == was_view)

    was_view = canvas.yview()[0]
    canvas.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    app.update()
    check("колесо мимо кадра по-прежнему листает", canvas.yview()[0] != was_view)

app.clipboard_clear()
app.clipboard_append("https://www.twitch.tv/videos/123456")
app.source.set("")
app.focus_set()
app._on_control_key(type("Клавиша", (), {"keycode": 86, "keysym": "cyrillic_em"})())
app.update()
check("Ctrl+V в русской раскладке вставил ссылку",
      app.source.get() == "https://www.twitch.tv/videos/123456", app.source.get())

app.source.set("")
app._on_control_key(type("Клавиша", (), {"keycode": 86, "keysym": "v"})())
app.update()
check("и в латинской тоже", app.source.get().startswith("https://"))

app._on_control_key(type("Клавиша", (), {"keycode": 65, "keysym": "a"})())
check("другие сочетания не трогаются",
      app._on_control_key(type("Клавиша", (), {"keycode": 67, "keysym": "c"})()) is None)

if editor is not None:
    editor.shutdown()
app.destroy()

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
raise SystemExit(1 if ok.count(False) else 0)
