# -*- coding: utf-8 -*-
"""Скачивание стрима по ссылке прямо из Студии.

Раньше стрим качали руками в консоли через yt-dlp. Теперь ссылка вставляется
в поле источника, и Студия качает сама. Сеть здесь не нужна: команда
проверяется по составу, а весь разбор вывода — на поддельном yt-dlp,
который печатает то же, что настоящий.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os
import shutil
import sys
import tempfile
import time

os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(
    tempfile.mkdtemp(prefix="fetch_cfg_"), "settings.json")

import tkinter.messagebox as _mb
DIALOGS = []
_mb.showerror = lambda *a, **k: DIALOGS.append(a)
_mb.showinfo = lambda *a, **k: DIALOGS.append(a)

from core import fetch
from studio.window import Studio

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-56s %s" % (label, "ок" if value else "ПРОВАЛ"))


check("ссылка распознаётся", fetch.looks_like_link("https://twitch.tv/videos/1"))
check("http тоже ссылка", fetch.looks_like_link("http://x.ru/y"))
check("путь к файлу не ссылка", not fetch.looks_like_link(r"D:\x\стрим.mp4"))
check("пусто не ссылка", not fetch.looks_like_link("   "))
check("подпись без схемы не ссылка", not fetch.looks_like_link("twitch.tv/x"))

python_home = os.path.join(tempfile.gettempdir(), "astra_fetch_test")
os.makedirs(os.path.join(python_home, "Scripts"), exist_ok=True)
os.makedirs(os.path.join(python_home, "Lib", "site-packages", "certifi"), exist_ok=True)
with open(os.path.join(python_home, "Lib", "site-packages", "certifi", "cacert.pem"), "w") as f:
    f.write("x")
fake_tool = os.path.join(python_home, "Scripts", "yt-dlp.exe")
bundle = fetch.ca_bundle(fake_tool)
check("сертификаты для curl по пути без кириллицы",
      bundle == "" or (bundle.isascii() and os.path.isfile(bundle)))
check("латинский путь к сертификатам не трогается",
      fetch.ca_bundle(os.path.join(tempfile.gettempdir(), "nope", "Scripts", "y.exe")) == "")
check("кодировка для yt-dlp задана", fetch.tool_env()["PYTHONIOENCODING"] == "utf-8")
shutil.rmtree(python_home, ignore_errors=True)

args = fetch.command("https://x/y", r"D:\куда", "до 1080p")
check("ссылка идёт последней и отделена", args[-2] == "--" and args[-1] == "https://x/y")
check("высота ограничена выбором", "bv*[height<=1080]+ba/b[height<=1080]/b" in args)
check("склейка в mp4", "mp4" in args)
check("вывод в нужную папку",
      any(str(part).startswith(r"D:\куда") for part in args))
check("имя как у прежних файлов",
      any("%(title).150B [%(id)s].%(ext)s" in str(part) for part in args))
check("плейлист не тянем", "--no-playlist" in args)
check("построчный вывод", "--newline" in args)
check("прогресс не глушится тихим режимом --print", "--progress" in args)
check("недокачанное не выдаём за готовое", "--no-part" not in args)
check("оборванное докачивается", "--continue" in args)
check("лучшее качество по умолчанию", "bv*+ba/b" in fetch.command("https://x/y", "."))
sound = fetch.command("https://x/y", ".", "только звук")
check("для звука берётся m4a и ничего не склеивается",
      "ba[ext=m4a]/ba/b" in sound and "--merge-output-format" not in sound)

fast = "[download]  12.3% of ~  22.00GiB at    3.00MiB/s ETA 02:05:31"
check("скорость сказана человечески", "3.1 МБ/с" in fetch.note(fast))
check("остаток в часах и минутах", "осталось 2 ч 05 мин" in fetch.note(fast))
check("между ними разделитель", " · " in fetch.note(fast))
check("минуты без часов",
      fetch.note("[download] 99.9% of 1.5GiB at 12.00MiB/s ETA 00:43")
      .endswith("осталось меньше минуты"))
check("неизвестная скорость не выдумывается",
      not fetch.note("[download] 45.0% of 700.00MiB at Unknown B/s ETA Unknown"))
check("в конце остаётся одна скорость",
      fetch.note("[download] 100% of 22.00GiB in 02:16:03 at 2.80MiB/s")
      == "2.9 МБ/с")
check("размер видно заранее",
      abs(fetch.whole(fast) / 1000 ** 3 - 23.6) < 0.2)

check("доля разбирается", abs(fetch.share("[download]  12.3% of 1GiB") - 0.123) < 1e-6)
check("сотня не переполняет", fetch.share("[download] 100.0% of 1GiB") == 1.0)
check("посторонняя строка не доля", fetch.share("[info] что-то") is None)

work = tempfile.mkdtemp(prefix="fetch_")
target = os.path.join(work, "стрим [v1].mp4")
fake = os.path.join(work, "fake_dlp.py")
with open(fake, "w", encoding="utf-8") as handle:
    handle.write(
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "print('[info] беру поток')\n"
        "print('[download]   0.0% of 1.00GiB')\n"
        "print('[download]  50.0% of 1.00GiB at 4.00MiB/s ETA 00:30')\n"
        "print('[download] 100.0% of 1.00GiB')\n"
        "open(sys.argv[1], 'wb').write(b'0' * 16)\n"
        "print(sys.argv[1])\n")

real_command = fetch.command
fetch.command = lambda url, folder, quality=None, **kw: [
    sys.executable, fake, target]

steps, said = [], []
got = fetch.download("https://x/y", work, log=said.append,
                     progress=lambda part, note="": steps.append((part, note)))
check("вернулся путь к скачанному", got == target and os.path.isfile(target))
check("прогресс дошёл до конца",
      steps and steps[0][0] == 0.0 and steps[-1][0] == 1.0)
check("пояснение доходит вместе с долей",
      any("МБ/с" in note for _, note in steps))
check("путь не попал в журнал как строка", target not in said)
check("прочие строки в журнал попали", any("беру поток" in line for line in said))

mute = os.path.join(work, "mute_dlp.py")
with open(mute, "w", encoding="utf-8") as handle:
    handle.write(chr(10).join([
        "import sys",
        "open(sys.argv[1], %s).write(b%s * (2 << 20))" % (repr("wb"), repr("0")),
        "print(%s)" % repr("[download] 100.0% of 2MiB at 5.00MiB/s ETA 00:01"),
    ]))
quiet_target = os.path.join(work, "молчком [v2].mp4")
fetch.command = lambda url, folder, quality=None, **kw: [
    sys.executable, mute, quiet_target]
told = []
rescued = fetch.download("https://x/y", work, log=told.append)
check("файл найден, даже если путь не напечатан", rescued == quiet_target)
check("про запасной путь сказано", any("свежий файл" in line for line in told))

slow = os.path.join(work, "slow_dlp.py")
with open(slow, "w", encoding="utf-8") as handle:
    handle.write(chr(10).join([
        "import sys, time",
        "for step in range(1, 200):",
        "    print('[download] %.1f%% of 1.00GiB at 4.00MiB/s ETA 00:30' % (step / 2.0))",
        "    sys.stdout.flush()",
        "    time.sleep(0.05)",
    ]))
fetch.command = lambda url, folder, quality=None, **kw: [sys.executable, slow]
seen_steps = []
halted = time.time()
try:
    fetch.download("https://x/y", work,
                   progress=lambda part, note="": seen_steps.append(part),
                   cancel=lambda: len(seen_steps) >= 3)
    check("остановка посреди прогресса срабатывает", False)
except RuntimeError as error:
    check("остановка посреди прогресса срабатывает", "остановлено" in str(error))
check("остановились сразу, а не дожидались конца",
      time.time() - halted < 5 and len(seen_steps) < 10)

check("код ffmpeg 3753488571 — это оборванный поток",
      "оборвался" in fetch.explain("ERROR: ffmpeg exited with code 3753488571"))
check("прочие коды ffmpeg показаны со знаком",
      fetch.explain("ERROR: ffmpeg exited with code 4294967295")
      == "ffmpeg завершился с кодом -1")
check("чужая строка ошибки не портится", fetch.explain("ERROR: x") == "ERROR: x")
ssl = ("ERROR: unable to download video data: [SSL: UNEXPECTED_EOF_WHILE_READING] "
       "EOF occurred in violation of protocol (_ssl.c:1082)")
check("обрыв TLS считается обрывом связи", fetch.flaky([ssl]))
check("отказ сервера обрывом связи не считается",
      not fetch.flaky(["ERROR: [twitch:vod] 1: HTTP Error 404: Not Found"]))
check("повторы самого yt-dlp подняты",
      "--fragment-retries" in real_command("https://x/y", "."))
check("ffmpeg тоже перезапрашивает кусок",
      "ffmpeg_i:-seg_max_retry 10" in real_command("https://x/y", ".", engine="ffmpeg"))

fetch.PAUSE = 0.0
tally = os.path.join(work, "tally.txt")
shaky_target = os.path.join(work, "рваный [v3].mp4")
shaky = os.path.join(work, "shaky_dlp.py")
with open(shaky, "w", encoding="utf-8") as handle:
    handle.write(chr(10).join([
        "import os, sys",
        "tally, target = sys.argv[1], sys.argv[2]",
        "done = int(open(tally).read()) if os.path.exists(tally) else 0",
        "open(tally, 'w').write(str(done + 1))",
        "if done < 3:",
        "    print(%r)" % ssl,
        "    sys.exit(1)",
        "open(target, 'wb').write(b'0' * 16)",
        "print(target)",
    ]))
engines = []
fetch.command = lambda url, folder, quality=None, engine="native", **kw: (
    engines.append(engine) or [sys.executable, shaky, tally, shaky_target])
heard = []
got = fetch.download("https://x/y", work, log=heard.append)
check("после обрывов связи докачалось", got == shaky_target)
check("докачивали обычным загрузчиком, без ffmpeg",
      engines == ["native"] * 4)
check("про повтор сказано в журнале", any("попытка 2" in line for line in heard))

os.remove(tally)
fetch.ATTEMPTS = 2
engines.clear()
try:
    fetch.download("https://x/y", work)
    check("бесконечный обрыв заканчивается ошибкой", False)
except RuntimeError as error:
    check("бесконечный обрыв заканчивается ошибкой",
          "обрывается" in str(error) and "ещё раз" in str(error))
check("попыток не больше заданного", engines == ["native"] * 2)
fetch.ATTEMPTS = 12

both = os.path.join(work, "both_dlp.py")
with open(both, "w", encoding="utf-8") as handle:
    handle.write(chr(10).join([
        "import sys",
        "if sys.argv[1] == 'native':",
        "    print('ERROR: ' + %r)" % fetch.BROKEN_NATIVE,
        "else:",
        "    print('ERROR: ffmpeg exited with code 3753488571')",
        "sys.exit(1)",
    ]))
fetch.command = lambda url, folder, quality=None, engine="native", **kw: [
    sys.executable, both, engine]
real_length = fetch.length
fetch.length = lambda url: 0.0
try:
    fetch.download("https://x/y", work)
    check("видны обе причины", False)
except RuntimeError as error:
    text = str(error)
    check("видны обе причины",
          fetch.BROKEN_NATIVE in text and "оборвался" in text)
    check("вместо числа — человеческие слова", "3753488571" not in text)

if os.path.exists(tally):
    os.remove(tally)
vod_target = os.path.join(work, "твич [v4].mp4")
vod = os.path.join(work, "vod_dlp.py")
with open(vod, "w", encoding="utf-8") as handle:
    handle.write(chr(10).join([
        "import os, sys",
        "engine, tally, target = sys.argv[1:4]",
        "if engine == 'native':",
        "    print('ERROR: ' + %r)" % fetch.BROKEN_NATIVE,
        "    sys.exit(1)",
        "done = int(open(tally).read()) if os.path.exists(tally) else 0",
        "open(tally, 'w').write(str(done + 1))",
        "if done < 2:",
        "    print(%r)" % ssl,
        "    sys.exit(1)",
        "open(target, 'wb').write(b'0' * 16)",
        "print(target)",
    ]))
engines = []
fetch.command = lambda url, folder, quality=None, engine="native", **kw: (
    engines.append(engine) or [sys.executable, vod, engine, tally, vod_target])
got = fetch.download("https://x/y", work)
check("обрывы под ffmpeg тоже переживаются", got == vod_target)
check("сломанный плейлист обычному загрузчику не повторяем",
      engines == ["native", "ffmpeg", "ffmpeg", "ffmpeg"])
fetch.length = real_length

crumbs = tempfile.mkdtemp(prefix="crumbs_")
open(os.path.join(crumbs, "мелочь.txt"), "wb").write(b"0" * 10)
check("мелочь за скачанное не сойдёт", not fetch.newest_since(crumbs, 0))
check("пустая папка ничего не даёт",
      not fetch.newest_since(tempfile.mkdtemp(), 0))
check("старое не выдаётся за новое",
      not fetch.newest_since(work, time.time() + 60))

fetch.command = real_command
try:
    fetch.download("не ссылка", work)
    check("не-ссылку не берём", False)
except ValueError:
    check("не-ссылку не берём", True)

app = Studio()
app.update()
check("кнопка «Скачать» на месте", app.btn_grab.winfo_ismapped())
check("выбор качества на месте", app.v_quality.get() in fetch.QUALITY_TITLES)
DIALOGS.clear()
app.source.set(r"D:\это\файл.mp4")
app._grab_stream()
check("на файл вместо ссылки жалуется", len(DIALOGS) == 1)
fetch.command = lambda url, folder, quality=None, **kw: [sys.executable, slow]
fetch.available = lambda: True
app.cfg["dldir"] = work
app.source.set("https://x/y")
app._grab_stream()
app.update()
check("во время скачивания кнопка «Остановить» доступна", app.btn_stop.enabled)
check("кнопка «Скачать» на время работы погашена", not app.btn_grab.enabled)
DIALOGS.clear()
app.runner.cancel()
waited = time.time()
while (app.runner.busy() or app.btn_stop.enabled) and time.time() - waited < 5:
    app.update()
    time.sleep(0.05)
check("после остановки окно снова готово",
      not app.runner.busy() and app.lbl_stage["text"] == "готов")
check("кнопка «Скачать» снова доступна", app.btn_grab.enabled)
check("своя остановка — не ошибка, окна с ней нет", not DIALOGS)
fetch.command = real_command
app.clipboard_clear()
app.clipboard_append("https://www.twitch.tv/videos/2864061119")
app.update()
check("ссылка из буфера вставляется", app._paste_link()
      and app.source.get().endswith("/videos/2864061119"))

here = os.path.abspath(__file__)
app.clipboard_clear()
app.clipboard_append(here)
app.update()
check("путь к файлу из буфера тоже принимается",
      app._paste_link() and app.source.get() == here)

app.clipboard_clear()
app.clipboard_append("просто текст")
app.update()
before = app.source.get()
check("мусор из буфера не подставляется",
      not app._paste_link() and app.source.get() == before)

app.v_quality.set("до 720p")
app._remember()
check("качество запоминается", app.cfg.get("quality") == "до 720p")
app.destroy()

shutil.rmtree(work, ignore_errors=True)
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
