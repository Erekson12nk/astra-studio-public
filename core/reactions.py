"""Реакции: где в разговорной части стрима стример смотрит одно видео, где — следующее.

Голос стримера и звук видео в записи смешаны, поэтому «кто говорит» реакцию от
болтовни не отличает. Отличает смена: другое видео выглядит и звучит по-другому.
Для каждой точки стрима сравнивается картинка и голоса за пару минут до и после —
на смене видео разница большая. Смена сцены внутри одного видео даёт такие же
всплески, поэтому граница ставится не на каждом всплеске: реакция не бывает
короче нескольких минут, и из всплесков выбирается набор с такими промежутками,
у которого сумма всплесков наибольшая. Каждый кусок затем читает модель по
расшифровке: реакция это или разговор с чатом, и как назвать ролик.

Кадры берутся через ffmpeg, только ключевые: OpenCV на длинных записях твича
ошибается в длине и дальше часа-другого кадров не отдаёт.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import threading

import numpy as np

from . import ffmpeg as _ff

THUMB = (160, 90)
SPAN = 600.0              # картинка и голоса сравниваются за столько секунд до и после
GRID = 10.0
MIN_LEN = 10 * 60.0       # реакция короче — не ролик и не граница
PENALTY = 2.0             # граница должна быть заметнее обычного шума кривой
EDGE = 120.0              # к началу и концу разговорной части границы не ставятся
PHRASE_BONUS = 1.0
PHRASE_REACH = 90.0
TRANSITION = re.compile(
    r"\b(давай(?:те)? (?:посмотрим|глянем|смотреть|включ\w*)|что (?:там |у нас )?дальше|"
    r"следующ\w+ (?:видео|ролик)|включаю|посмотрим (?:что|это|видос)|го смотреть|"
    r"новое видео|другое видео|так[, ]+ладно)\b", re.I)
VERSION = 1
PREFIX = "{стример} смотрит: {видео}"


def _cache(path, *extra) -> str:
    try:
        stamp = "%s|%d|%d|%s|%d" % (os.path.abspath(path), os.path.getsize(path),
                                    int(os.path.getmtime(path)), "|".join(map(str, extra)),
                                    VERSION)
    except OSError:
        return ""
    folder = os.path.join(tempfile.gettempdir(), "ave_cache")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "keys_%s.npz" % hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16])


def keyframes(path, size=THUMB, cache=True, progress=None) -> dict:
    """Серые миниатюры ключевых кадров всей записи: {"time", "frames"}."""
    store = _cache(path, *size) if cache else ""
    if store and os.path.isfile(store):
        saved = np.load(store)
        return {"time": saved["time"], "frames": saved["frames"]}
    width, height = size
    cmd = [_ff.find("ffmpeg"), "-v", "info", "-skip_frame", "nokey", "-i", str(path), "-an",
           "-vf", "scale=%d:%d,format=gray,showinfo" % (width, height),
           "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_ff._NOWIN)
    times = []

    def read_times():
        for line in process.stderr:
            found = re.search(rb"pts_time:\s*([\d.]+)", line)
            if found:
                times.append(float(found.group(1)))
                if progress and len(times) % 500 == 0:
                    progress(times[-1])

    reader = threading.Thread(target=read_times, daemon=True)
    reader.start()
    frames = []
    size_bytes = width * height
    while True:
        data = process.stdout.read(size_bytes)
        if len(data) < size_bytes:
            break
        frames.append(np.frombuffer(data, np.uint8).reshape(height, width))
    process.wait()
    reader.join(10)
    count = min(len(frames), len(times))
    out = {"time": np.asarray(times[:count], np.float32),
           "frames": np.stack(frames[:count]) if count else np.zeros((0, height, width), np.uint8)}
    if store and count:
        np.savez_compressed(store, **out)
    return out


def content_mask(shape, box=None, full=(1920, 1080)) -> np.ndarray:
    """Всё, кроме вебки: там видео, которое смотрит стример."""
    height, width = shape
    mask = np.ones(shape, bool)
    if box:
        sx, sy = width / float(full[0]), height / float(full[1])
        x, y, w, h = box                    # как отдаёт away.locate_box: угол и размеры
        top, left = max(0, int(y * sy)), max(0, int(x * sx))   # вебка бывает у самого края
        mask[top:max(top, int((y + h) * sy) + 1), left:max(left, int((x + w) * sx) + 1)] = False
    return mask


def look_features(frames, mask=None) -> np.ndarray:
    count, height, width = frames.shape
    mask = content_mask((height, width)) if mask is None else mask
    rows, cols = height // 5, width // 5
    out = []
    for frame in frames:
        frame = frame.astype(np.float32)
        small = frame[:rows * 5, :cols * 5].reshape(rows, 5, cols, 5).mean(axis=(1, 3)).ravel()
        hist = np.histogram(frame[mask], bins=16, range=(0, 255))[0].astype(np.float32)
        hist /= hist.sum() + 1e-6
        out.append(np.concatenate([small / 255.0, hist * 4.0]))
    return np.asarray(out, np.float32)


def change_curve(times, feats, grid, span=SPAN, use=None, cosine=False) -> np.ndarray:
    """Насколько отличается то, что было до точки, от того, что после."""
    times = np.asarray(times, np.float64)
    grid = np.asarray(grid, np.float64)
    use = np.ones(times.size, bool) if use is None else np.asarray(use, bool)
    weights = use.astype(np.float64)
    sums = np.vstack([np.zeros((1, feats.shape[1])),
                      np.cumsum(np.asarray(feats, np.float64) * weights[:, None], axis=0)])
    counts = np.concatenate([[0.0], np.cumsum(weights)])
    low = np.searchsorted(times, grid - span)
    middle = np.searchsorted(times, grid)
    high = np.searchsorted(times, grid + span)
    left_n = counts[middle] - counts[low]
    right_n = counts[high] - counts[middle]
    left = (sums[middle] - sums[low]) / np.maximum(left_n, 1.0)[:, None]
    right = (sums[high] - sums[middle]) / np.maximum(right_n, 1.0)[:, None]
    if cosine:
        left /= np.linalg.norm(left, axis=1, keepdims=True) + 1e-9
        right /= np.linalg.norm(right, axis=1, keepdims=True) + 1e-9
        out = 1.0 - (left * right).sum(axis=1)
    else:
        out = np.abs(left - right).mean(axis=1)
    out[(left_n < 5) | (right_n < 5)] = 0.0
    return out.astype(np.float32)


def normalize(curve) -> np.ndarray:
    curve = np.asarray(curve, np.float32)
    if not curve.size:
        return curve
    middle = float(np.median(curve))
    spread = float(np.percentile(curve, 90)) - middle
    return (curve - middle) / (spread + 1e-9)


def phrase_times(lines) -> list:
    """Где стример говорит «давайте посмотрим», «что дальше» и подобное."""
    return [float(start) for start, _end, text in lines or () if TRANSITION.search(text or "")]


def with_phrases(grid, score, phrases, bonus=PHRASE_BONUS, reach=PHRASE_REACH) -> np.ndarray:
    score = np.asarray(score, np.float32).copy()
    for at in phrases:
        near = np.abs(np.asarray(grid) - at) <= reach
        score[near] += bonus * (1.0 - np.abs(np.asarray(grid)[near] - at) / reach)
    return score


def peaks(grid, score) -> list:
    """Вершины кривой: (секунда, высота)."""
    out = []
    for index in range(1, len(score) - 1):
        if score[index] >= score[index - 1] and score[index] > score[index + 1]:
            out.append((float(grid[index]), float(score[index])))
    return out


def choose(found, start, end, min_len=MIN_LEN, penalty=PENALTY, edge=EDGE) -> list:
    """Границы из вершин: между соседними не меньше min_len, сумма (высота − штраф) наибольшая."""
    usable = sorted((at, height - penalty) for at, height in found
                    if start + max(edge, min_len) <= at <= end - max(edge, min_len)
                    and height > penalty)
    if not usable:
        return []
    best = []
    for index, (at, gain) in enumerate(usable):
        total, chain = gain, [index]
        for back in range(index - 1, -1, -1):
            if at - usable[back][0] >= min_len and best[back][0] + gain > total:
                total, chain = best[back][0] + gain, best[back][1] + [index]
        best.append((total, chain))
    winner = max(best, key=lambda item: item[0])[1]
    return [usable[index][0] for index in winner]


def pieces(start, end, cuts) -> list:
    edges = [start] + sorted(cuts) + [end]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def region_score(start, end, look, voice=None, box=None, span=SPAN, grid_step=GRID):
    """Кривая смены видео на разговорной части: (сетка секунд, высота)."""
    grid = np.arange(start + span, end - span, grid_step)
    if not grid.size:
        return grid, np.zeros(0, np.float32)
    frames = look["frames"]
    times = np.asarray(look["time"])
    inside = (times >= start - span) & (times <= end + span)
    mask = content_mask(frames.shape[1:], box)
    score = normalize(change_curve(times[inside], look_features(frames[inside], mask), grid,
                                   span))
    if voice is not None:
        vt = np.asarray(voice["time"])
        keep = (vt >= start - span) & (vt <= end + span)
        heard = change_curve(vt[keep], np.asarray(voice["vec"], np.float32)[keep], grid, span,
                             use=np.asarray(voice["voiced"])[keep], cosine=True)
        score = score + normalize(heard)
    return grid, score


def split_region(start, end, look, voice=None, phrases=(), box=None, span=SPAN, grid_step=GRID,
                 min_len=MIN_LEN, penalty=PENALTY, scored=None) -> list:
    """Разговорная часть стрима → куски по одному видео."""
    if end - start < min_len:
        return []
    grid, score = scored or region_score(start, end, look, voice, box, span, grid_step)
    if not grid.size:
        return [(start, end)]
    score = with_phrases(grid, score, [at for at in phrases if start <= at <= end])
    return pieces(start, end, choose(peaks(grid, score), start, end, min_len, penalty))


JUDGE_SYSTEM = (
    "Ты помогаешь нарезчику стримов. Даны четыре отрывка расшифровки из одного куска "
    "стрима — начало, четверть, середина, три четверти. Определи: стример в этом куске "
    "смотрит и комментирует чужое видео (реакция) или всё время просто общается с чатом. "
    "В реакции звучит чужая речь: ведущий видео, его гости, «всем привет», «подписывайтесь», "
    "рассказ на чужую тему, а стример вставляет комментарии; стример может ставить видео на "
    "паузу, отвлекаться на донаты и даже звонить — это всё равно реакция. Если стример сам "
    "рассказывает о себе, своём здоровье, планах, отвечает чату — это не реакция. Реакция — "
    "когда чужое видео слышно хотя бы в двух отрывках из четырёх. В любом случае придумай "
    "короткое название по главной теме куска, как его назвал бы автор видео на YouTube: "
    "3–9 слов, по-русски, без кавычек и эмодзи. Ответ строго JSON.")
JUDGE_SCHEMA = {"type": "object", "properties": {
    "reaction": {"type": "boolean"}, "title": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["reaction", "title"]}
JUDGE_PARTS = (0.0, 0.25, 0.5, 0.75)
JUDGE_PIECE = 150.0


SWITCH_SYSTEM = (
    "Ты помогаешь нарезчику стримов. Стример на стриме смотрит видео с YouTube подряд, "
    "одно за другим, и комментирует. Когда одно видео кончилось, он выбирает следующее: "
    "говорит что-то вроде «так», «ладно», «что у нас», «гоу глянем» и читает вслух "
    "название следующего видео. Сразу после этого звучит само новое видео: чужой голос, "
    "часто «всем привет», и новая тема.\n"
    "Пример куска:\n"
    "[7] 1:17:40 Это пиздец, мы ебнемся. Гоу, лучше глянем этот, про сыра.\n"
    "[8] 1:17:47 Это был позор, актер Майкла из GTA в Москве. О, погоди, он был в Москве?\n"
    "[9] 1:17:54 Всем привет, я не планировал снимать отдельный видос о мероприятии...\n"
    "Ответ: {\"switch\": true, \"line\": 8, \"title\": \"Это был позор, актер Майкла из GTA "
    "в Москве\"}\n"
    "Строки расшифровки пронумерованы. Если в куске стример начинает смотреть видео — "
    "следующее или первое за стрим, после болтовни с чатом, — верни номер строки, где он "
    "читает название (или, если не читает, где начинается само видео). Если в куске "
    "продолжается то же видео, стример болтает с чатом, благодарит за донаты или читает "
    "их — switch false. Ответ строго JSON.")
TITLE_SYSTEM = (
    "Ты помогаешь нарезчику стримов. Перед тобой расшифровка момента, где стример включает "
    "видео с YouTube: сначала он обычно читает вслух его название, потом звучит само видео. "
    "Верни только название видео — так, как его прочитал стример, без его слов вокруг "
    "(«так», «ладно», «гоу глянем этот») и без первых слов самого видео («всем привет»). "
    "Исправь очевидные ошибки распознавания (ВБ → WB). Если стример название не читал — "
    "придумай короткое название по теме видео, 3–8 слов. Ответ строго JSON.")
TITLE_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}},
                "required": ["title"]}
TITLE_BEFORE = 10.0
TITLE_AFTER = 50.0
SWITCH_SCHEMA = {"type": "object", "properties": {
    "switch": {"type": "boolean"}, "line": {"type": "integer"}},
    "required": ["switch", "line"]}
SWITCH_BEFORE = 180.0
SWITCH_AFTER = 150.0
LINE_LEN = 8.0             # огрызки расшифровки склеиваются в строки примерно такой длины


def numbered(lines, start, end, joined=LINE_LEN):
    picked = []
    for s, e, t in lines:
        if e <= start or s >= end or not (t or "").strip():
            continue
        if picked and s - picked[-1][0] < joined:
            picked[-1] = (picked[-1][0], picked[-1][1] + " " + " ".join(t.split()))
        else:
            picked.append((s, " ".join(t.split())))
    text = "\n".join("[%d] %s %s" % (number, _ff.format_hms(s), t)
                     for number, (s, t) in enumerate(picked, 1))
    return picked, text


def switch_with(backend, cfg):
    """Модель смотрит расшифровку вокруг кандидата: есть ли смена видео, где, как оно зовётся."""
    complete = getattr(backend, "complete", None)
    if not callable(complete):
        return None
    from . import detect as _detect

    def ask(lines, at, before=SWITCH_BEFORE, after=SWITCH_AFTER):
        picked, text = numbered(lines, at - before, at + after)
        if not picked:
            return None
        answer = _detect.extract_json(complete(SWITCH_SYSTEM, text[:9000] + "\n\nОтветь JSON.",
                                               cfg, SWITCH_SCHEMA))
        if not isinstance(answer, dict) or not answer.get("switch"):
            return None
        try:
            index = int(answer.get("line") or 0)
        except (TypeError, ValueError):
            return None
        if not 1 <= index <= len(picked):
            return None
        at = float(picked[index - 1][0])
        _seen, near = numbered(lines, at - TITLE_BEFORE, at + TITLE_AFTER, joined=0.0)
        named = _detect.extract_json(complete(TITLE_SYSTEM, near[:3000] + "\n\nОтветь JSON.",
                                              cfg, TITLE_SCHEMA))
        title = named.get("title") if isinstance(named, dict) else answer.get("title")
        return {"at": at, "title": " ".join(str(title or "").split()).strip(" .")[:90]}

    return ask


def excerpt(lines, start, end, limit=1600) -> str:
    text = " ".join(t for s, e, t in lines if e > start and s < end and t)
    return " ".join(text.split())[:limit]


def judge_with(backend, cfg):
    complete = getattr(backend, "complete", None)
    if not callable(complete):
        return None
    from . import detect as _detect

    def ask(lines, start, end):
        parts = []
        for number, share in enumerate(JUDGE_PARTS, 1):
            at = start + (end - start) * share
            parts.append("Отрывок %d (%s):\n%s" % (number, _ff.format_hms(at),
                                                    excerpt(lines, at, at + JUDGE_PIECE, 900)
                                                    or "(тишина)"))
        user = "\n\n".join(parts) + "\n\nОтветь JSON."
        answer = _detect.extract_json(complete(JUDGE_SYSTEM, user, cfg, JUDGE_SCHEMA))
        if not isinstance(answer, dict):
            return None
        return {"reaction": bool(answer.get("reaction")),
                "title": " ".join(str(answer.get("title") or "").split())[:90],
                "reason": str(answer.get("reason") or "").strip()}

    return ask


def title_for(template, streamer, video) -> str:
    text = (template or PREFIX).replace("{стример}", streamer or "").replace("{видео}", video or "")
    return " ".join(text.split()).strip(" :")


def chatting(found, length=0.0) -> list:
    """Разговорные главы твича (Just Chatting и подобные): [(начало, конец)]."""
    from . import roliks as _rol

    out = []
    for chapter in found:
        if _rol.is_game(chapter["game"]):
            continue
        if out and chapter["start"] - out[-1][1] <= 1.0:
            out[-1] = (out[-1][0], chapter["end"])
        else:
            out.append((chapter["start"], chapter["end"]))
    return out


REFINE_REACH = 200.0      # модель уточняет границу не дальше этого от найденной кривой


def refine(lines, cuts, switch, log=None) -> list:
    """Границы по кривой → [(секунда, название)]: модель сдвигает к строке перехода и называет.

    Отменить границу модель не может: небольшая модель пропускает настоящие переходы
    чаще, чем кривая ставит лишние, а лишний кусок болтовни потом отсеет оценка куска.
    """
    out = []
    for at in cuts:
        found = None
        try:
            found = switch(lines, at)
        except Exception as error:
            if log:
                log("модель не ответила про %s: %s" % (_ff.format_hms(at),
                                                       str(error).splitlines()[0][:100]))
        if found and abs(found["at"] - at) <= REFINE_REACH:
            out.append((found["at"], found["title"]))
        else:
            out.append((at, None))
    return sorted(out)


VISION = "qwen2.5vl:3b"
TITLE_CROPS = ((0.0, 0.85, 0.6, 0.12),    # под плеером YouTube в браузере: x, y, ширина, высота
               (0.0, 0.75, 1.0, 0.25))    # раскладка другая — вся нижняя четверть
SCREEN_AT = (40.0, 100.0, 160.0)          # через столько секунд после начала видео смотреть
SCREEN_PROMPT = (
    "Это нижняя часть кадра со стрима, на экране браузер со страницей YouTube. Прочитай "
    "заголовок видео — строку крупного текста под плеером, над названием канала — "
    "дословно, с теми же заглавными буквами. Не бери субтитры и надписи внутри самого "
    "видео. Если заголовка не видно — пустая строка. Ответ JSON: {\"title\": \"...\"}")


STRIP = 0.25                  # из кадра берётся нижняя четверть: в ней все полосы заголовка


def strips_at(path, times, reach=1.5) -> dict:
    """Нижние полосы кадров к нужным секундам — одним проходом по ключевым кадрам.

    Каждый запуск ffmpeg на записи в десятки гигабайт заново читает её оглавление —
    это десятки секунд; один проход по ключевым кадрам дешевле многих перемоток.
    Ответ: {секунда: полоса BGR во всю ширину}.
    """
    times = sorted(set(float(t) for t in times))
    if not times:
        return {}
    info = _ff.probe(path)
    width, height = info.width, int(info.height * STRIP) // 2 * 2
    if not width or not height:
        return {}
    picked = "+".join("between(t,%.2f,%.2f)" % (t - reach, t + reach) for t in times)
    cmd = [_ff.find("ffmpeg"), "-v", "info", "-skip_frame", "nokey", "-i", str(path), "-an",
           "-vf", "select='%s',crop=iw:%d:0:ih-%d,showinfo" % (picked, height, height),
           "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_ff._NOWIN)
    stamps = []

    def read_times():
        for line in process.stderr:
            found = re.search(rb"pts_time:\s*([\d.]+)", line)
            if found:
                stamps.append(float(found.group(1)))

    reader = threading.Thread(target=read_times, daemon=True)
    reader.start()
    frames = []
    size = width * height * 3
    while True:
        data = process.stdout.read(size)
        if len(data) < size:
            break
        frames.append(np.frombuffer(data, np.uint8).reshape(height, width, 3))
    process.wait()
    reader.join(10)
    got = list(zip(stamps, frames))
    out = {}
    for at in times:
        near = [(abs(stamp - at), frame) for stamp, frame in got if abs(stamp - at) <= reach]
        if near:
            out[at] = min(near, key=lambda pair: pair[0])[1]
    return out


def crop_png(strip, crop) -> bytes:
    """Полоса заголовка (в долях всего кадра) из нижней полосы → PNG для модели."""
    import cv2

    x, y, w, h = crop
    height, width = strip.shape[:2]
    full = height / STRIP
    top = max(0, int((y - (1.0 - STRIP)) * full))
    piece = strip[top:min(height, top + int(h * full)), int(x * width):int((x + w) * width)]
    if not piece.size:
        return b""
    ok, data = cv2.imencode(".png", piece)
    return data.tobytes() if ok else b""


def vision_with(model=VISION, host=None):
    """Модель, которая видит картинку: кадр → заголовок видео или ""."""
    import base64
    import json
    import urllib.request

    host = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    if not host.startswith("http"):
        host = "http://" + host

    def post(path, payload):
        request = urllib.request.Request(host + path, json.dumps(payload).encode("utf-8"),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=300) as answer:
            return json.loads(answer.read().decode("utf-8"))

    def ask(image, retry=True):
        if not image:
            return ""
        data = post("/api/chat", {
            "model": model, "stream": False, "format": "json", "keep_alive": "5m",
            "options": {"temperature": 0},
            "messages": [{"role": "user", "content": SCREEN_PROMPT,
                          "images": [base64.b64encode(image).decode("ascii")]}]})
        content = str((data.get("message") or {}).get("content") or "").strip()
        if not content and retry:
            # Ollama после борьбы за видеопамять отвечает совсем пустым — выгрузить и повторить
            post("/api/generate", {"model": model, "keep_alive": 0})
            return ask(image, retry=False)
        try:
            found = json.loads(content).get("title")
        except (ValueError, AttributeError):
            return ""
        return str(found or "")

    return ask


def tidy_title(text) -> str:
    """«Заголовок | Серия (фильм)» → «Заголовок»: хвост после черты нарезчики срезают."""
    text = " ".join(str(text or "").split()).strip(" \"'«»")
    head = text.split(" | ")[0].strip()
    return head if len(head) >= 8 else text


def vote(readings) -> str:
    """Заголовок, прочитанный хотя бы с двух кадров одинаково."""
    seen = {}
    for title in readings:
        title = tidy_title(title)
        if len(title) < 4:
            continue
        seen.setdefault(re.sub(r"\W+", "", title.lower()), []).append(title)
    best = max(seen.values(), key=len) if seen else []
    return best[0] if len(best) >= 2 else ""


def screen_titles(path, pieces, ask, offsets=SCREEN_AT, crops=TITLE_CROPS,
                  strips=None) -> list:
    """Заголовки для кусков [(начало, конец)] — по одному на кусок, "" если не прочитан."""
    wanted = {index: [left + offset for offset in offsets if left + offset < right]
              for index, (left, right) in enumerate(pieces)}
    shots = (strips or strips_at)(path, [at for ats in wanted.values() for at in ats])
    out = []
    for index in range(len(pieces)):
        title = ""
        for crop in crops:
            title = vote([ask(crop_png(shots[at], crop)) for at in wanted[index] if at in shots])
            if title:
                break
        out.append(title)
    return out


def screen_with(model=VISION, host=None, log=None):
    """Заголовки с экрана, если модель есть; нет её или не отвечает — пусто, не ошибка."""
    ask = vision_with(model, host)

    def read(path, pieces):
        try:
            return screen_titles(path, pieces, ask)
        except Exception as error:
            if log:
                log("заголовки с экрана не читаются (%s): %s" % (
                    model, str(error).splitlines()[0][:120]))
            return [""] * len(pieces)

    return read


def find(path, found, lines=(), voice=None, look=None, box=None, judge=None, switch=None,
         streamer="", template=PREFIX, min_len=MIN_LEN, span=SPAN, screen=None,
         log=None) -> list:
    """Реакции стрима: [{"start", "end", "title", "kind": "reaction"}].

    Границы ставит кривая смены видео. Модель по расшифровке уточняет их до строки,
    где стример включает видео, и называет его; кусок, на чьей границе модель
    перехода не увидела, она же оценивает целиком: реакция это или болтовня.
    """
    log = log or (lambda text: None)
    look = look if look is not None else keyframes(path)
    out, pieces = [], []
    for start, end in chatting(found):
        if end - start < min_len:
            continue
        cuts = [a for a, _b in split_region(start, end, look, voice, box=box, span=span,
                                            min_len=min_len)[1:]]
        edges = refine(lines, cuts, switch, log) if switch and lines else [
            (at, None) for at in cuts]
        edges = [(start, None)] + edges + [(end, None)]
        for (left, title), (right, _next) in zip(edges, edges[1:]):
            if right - left < min_len:
                continue
            if title is None and judge and lines:
                try:
                    verdict = judge(lines, left, right)
                except Exception as error:
                    verdict = None
                    log("модель не ответила про кусок %s: %s" % (
                        _ff.format_hms(left), str(error).splitlines()[0][:100]))
                if verdict is not None and not verdict["reaction"] and left <= start:
                    log("%s–%s: не реакция (%s)" % (_ff.format_hms(left), _ff.format_hms(right),
                                                     verdict.get("reason") or "разговор"))
                    continue
                if verdict is not None and not verdict["reaction"]:
                    log("%s–%s: похоже на болтовню, но оставляю на ваше решение" % (
                        _ff.format_hms(left), _ff.format_hms(right)))
                title = (verdict or {}).get("title") or ""
            pieces.append((float(left), float(right), title or ""))
    shown = screen(path, [(left, right) for left, right, _t in pieces]) if screen and pieces \
        else [""] * len(pieces)
    for (left, right, title), seen in zip(pieces, shown):
        if seen:
            log("%s: заголовок с экрана «%s»" % (_ff.format_hms(left), seen))
        video = seen or title or "реакция с %s" % _ff.format_hms(left)
        out.append({"start": left, "end": right, "kind": "reaction", "game": "",
                    "title": title_for(template, streamer, video)})
    return out
