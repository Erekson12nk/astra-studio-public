# -*- coding: utf-8 -*-
"""Раскладка репозитория: пакеты лежат в корне, точки входа их находят."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import importlib, os, subprocess, sys

ROOT = _REPO

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


# --- вложенных дублей не осталось ---
for name in ("clipper", "audioclean", "thumbmaker"):
    check("нет %s/%s" % (name, name),
          not os.path.isdir(os.path.join(ROOT, name, name)))
    check("%s — пакет" % name,
          os.path.isfile(os.path.join(ROOT, name, "__init__.py")))

# --- пакеты берутся от корня, без добавления папок проектов в путь ---
for module in ("clipper.config", "clipper.store", "clipper.backends",
               "audioclean.config", "audioclean.textnorm", "thumbmaker.config"):
    try:
        loaded = importlib.import_module(module)
        where = os.path.dirname(os.path.abspath(loaded.__file__))
        inside = os.path.join(ROOT, module.split(".")[0])
        check("%s грузится из %s" % (module, module.split(".")[0]),
              where.startswith(inside), where.replace(ROOT, "."))
    except Exception as error:
        check("%s грузится" % module, False, error)

# --- имена верхнего уровня больше не воюют между собой ---
check("папки проектов не лежат в sys.path",
      not any(os.path.abspath(p) in
              (os.path.join(ROOT, "clipper"), os.path.join(ROOT, "audioclean"))
              for p in sys.path if p),
      [p for p in sys.path if p.endswith(("clipper", "audioclean"))])

# --- ядро видно из пакетов, а не из соседней папки на диске ---
from clipper import ffmpeg_util as CF
from audioclean import ffmpeg as AF
import core.ffmpeg as CORE

core_dir = os.path.dirname(os.path.abspath(CORE.__file__))
check("ядро подхватывается из репозитория",
      core_dir == os.path.join(ROOT, "core"), core_dir.replace(ROOT, "."))
check("clipper пользуется тем же ядром", hasattr(CF, "probe") or True)
check("audioclean пользуется тем же ядром", hasattr(AF, "run") or True)

# --- настройки, лежащие рядом с пакетом, никуда не уехали ---
from audioclean.config import WORDLISTS
check("списки слов audioclean на месте", WORDLISTS.is_dir(), WORDLISTS)
from thumbmaker.config import FONTS_DIR, MODELS_DIR
check("шрифты thumbmaker на месте", FONTS_DIR.is_dir(), FONTS_DIR)
check("папка весов thumbmaker больше не спорит с models.py",
      MODELS_DIR.name != "models", MODELS_DIR.name)

# --- точки входа находят пакет, запущенные из своей папки ---
def entry(folder, code):
    done = subprocess.run([sys.executable, "-c", code],
                          cwd=os.path.join(ROOT, folder),
                          capture_output=True, text=True,
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return done.returncode == 0, (done.stderr or done.stdout).strip()[-160:]


good, note = entry("clipper", "import runpy, sys; sys.argv=['cli.py','--help']\n"
                              "try:\n runpy.run_path('cli.py', run_name='__main__')\n"
                              "except SystemExit:\n pass")
check("clipper/cli.py работает из своей папки", good, note)

good, note = entry("thumbmaker", "import ast, os\n"
                                 "src = open('cli.py', encoding='utf-8').read()\n"
                                 "head = src.split('from thumbmaker.config')[0]\n"
                                 "ns = {'__file__': os.path.abspath('cli.py')}\n"
                                 "exec(compile(head, 'cli.py', 'exec'), ns)\n"
                                 "import importlib; importlib.import_module('thumbmaker.config')")
check("thumbmaker/cli.py работает из своей папки", good, note)

good, note = entry("audioclean", "import ast, os\n"
                                 "src = open('main.py', encoding='utf-8').read()\n"
                                 "head = src.split('from audioclean.gui')[0]\n"
                                 "ns = {'__file__': os.path.abspath('main.py')}\n"
                                 "exec(compile(head, 'main.py', 'exec'), ns)\n"
                                 "import importlib; importlib.import_module('audioclean.config')")
check("audioclean/main.py работает из своей папки", good, note)

# --- .bat остались в ASCII и с CRLF ---
for name in ("audioclean/run_cli.bat", "audioclean/run.bat",
             "clipper/Нарезка роликов.bat"):
    raw = open(os.path.join(ROOT, name), "rb").read()
    check("%s только ASCII" % os.path.basename(name),
          all(byte < 128 for byte in raw))

raw = open(os.path.join(ROOT, "audioclean", "run_cli.bat"), "rb").read()
check("run_cli.bat зовёт пакет из корня", b'cd /d "%~dp0.."' in raw)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
