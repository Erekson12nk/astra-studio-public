# -*- coding: utf-8 -*-
"""Монтаж astra внутри Студии: вкладка, источник, моменты, автономное окно."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)
import tempfile as _tempfile
_os.environ.setdefault("ASTRA_STUDIO_SETTINGS", _os.path.join(
    _tempfile.gettempdir(), "astra_studio_tests", "settings.json"))

from media import build as _build

_MEDIA = _build()

import os, sys, time

ROOT = _REPO
sys.path.insert(0, ROOT)

import tkinter.messagebox as _mb
DIALOGS = []
_mb.showerror = lambda *a, **k: DIALOGS.append(("error",) + a)
_mb.showinfo = lambda *a, **k: DIALOGS.append(("info",) + a)
_mb.showwarning = lambda *a, **k: DIALOGS.append(("warning",) + a)

from studio.window import Studio, _load_astra_gui

ok = []


def pump(app, ready, limit=30.0):
    edge = time.time() + limit
    while time.time() < edge:
        app.update()
        if ready():
            return True
        time.sleep(0.05)
    return False


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


G = _load_astra_gui()
check("модуль astra грузится по пути, а не по имени",
      hasattr(G, "Editor") and hasattr(G, "App"), G.__name__)
check("имя не отобрано у clipper", G.__name__ == "astra_gui")

app = Studio()
app.source.set(_MEDIA["speech"])
app.update()

# --- вкладка монтажа появляется только по требованию ---
check("монтаж не строится, пока его не открыли", app.editor is None)
app._switch("clip")
app.update()
check("подвкладки на месте",
      app.tab_search.winfo_exists() and app.tab_edit.winfo_exists())
check("сначала показан поиск", app.clip_page == "search")
check("журнал Студии виден в поиске", app.log_wrap.winfo_ismapped())

app._switch_clip("edit")
app.update()
check("редактор построен", app.editor is not None)
check("это панель, а не отдельное окно", isinstance(app.editor, G.Editor))
check("шапка astra во встроенном виде убрана", app.editor.bar is None)
check("журнал Студии в монтаже спрятан", not app.log_wrap.winfo_ismapped())
check("три шага astra на месте",
      [t for _i, t in app.editor.steps.items] == ["Нарезать", "Кадр", "Надписи"])

# --- источник из общей шапки доходит до редактора ---
pump(app, lambda: app.editor.source == _MEDIA["speech"])
check("источник подхвачен из шапки", app.editor.source == _MEDIA["speech"],
      app.editor.source)
check("редактор помнит, что уже открыл", app.editor_source == _MEDIA["speech"])

# --- моменты уезжают в монтаж без файла на диске руками ---
app._switch_clip("search")
app.update()
check("журнал Студии вернулся", app.log_wrap.winfo_ismapped())
app.clip_moments = [{"start": 1.0, "end": 4.0, "title": "первый"},
                    {"start": 6.0, "end": 9.0, "title": "второй"}]
app._to_editor()
pump(app, lambda: len(app.editor.candidates) == 2)
check("«Передать моменты» открывает монтаж", app.clip_page == "edit")
check("моменты доехали до списка astra", len(app.editor.candidates) == 2,
      len(app.editor.candidates))
check("названия сохранились",
      [c["title"] for c in app.editor.candidates] == ["первый", "второй"])

# --- монтаж прокручивается, иначе низ карточек недостижим ---
check("у монтажа есть прокрутка", app.editor_canvas is not None)
check("редактор выше окна, прокрутка не зря",
      app.editor.winfo_reqheight() > 700, app.editor.winfo_reqheight())

# --- клавиши astra не стреляют, когда монтаж не показан ---
app._switch("rolik")
app.update()
check("в режиме «Ролики» панель монтажа скрыта", not app.editor.winfo_ismapped())

# --- готовые клипы находятся там, куда их положил встроенный монтаж ---
app._switch("clip")
app._switch_clip("edit")
app.update()

рядом = os.path.join(os.path.dirname(_MEDIA["speech"]), "клипы_проверка")
os.makedirs(рядом, exist_ok=True)
app.editor.batch_folder = рядом
folders = app._clip_folders()
check("папка пакетной нарезки попадает в поиск клипов",
      os.path.abspath(рядом) in folders, folders)

app.editor.batch_folder = None
app.editor.var_output.set(os.path.join(рядом, "один_клип.mp4"))
folders = app._clip_folders()
check("одиночный клип ищется в своей папке",
      os.path.abspath(рядом) in folders, folders)

app.editor.var_output.set(os.path.join(рядом, "нет", "такой", "клип.mp4"))
app.editor.batch_folder = None
check("несуществующая папка в поиск не идёт",
      os.path.abspath(рядом) not in app._clip_folders())


# --- вкладка оценки: та же панель clipper, но без «Нарезки» ---
check("оценка не строится, пока её не открыли", app.rating is None)
app._switch_clip("rate")
app.update()
check("панель оценок построена", app.rating is not None)
if app.rating is not None:
    nb = app.rating.nb
    вкладки = [nb.tab(i, "text").strip() for i in range(nb.index("end"))]
    check("во встроенном виде остались оценка и список",
          вкладки == ["Оценка", "Все клипы"], вкладки)
    check("нарезка убрана: она соседней вкладкой Студии",
          "Нарезка" not in вкладки)
    check("панель умеет перечитать базу", hasattr(app.rating, "reload"))
    check("и умеет закрыться без окна", hasattr(app.rating, "shutdown"))
    check("монтаж при этом никуда не делся", app.editor is not None)


app._close()

# --- автономное окно astra живо ---
window = G.App()
window.withdraw()
window.update()
check("отдельное окно astra собирается", window.editor is not None)
check("в нём шапка на месте", window.editor.bar is not None)
check("и своя карточка источника показана",
      window.editor.chrome is True)
window.editor.shutdown()
window.destroy()

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
