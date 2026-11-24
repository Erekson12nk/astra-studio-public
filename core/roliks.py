"""Ролики: сборка длинных роликов и разметка стрима на игры по главам твича.

Твич сам хранит в записи главы — когда стример менял категорию. Игровая глава —
готовый ролик «<стример> играет в …». Короткая вставка другой категории посреди игры
(«Games + Demos» на минуту) ролик не рвёт. Категорию часто меняют с опозданием —
граница подтягивается к самому тихому месту рядом. Разговорные категории
(Just Chatting и подобные) здесь не режутся: там реакции, это отдельная разметка.

Сборка: clipmaker режет кусок с затемнением и концовкой, на местах мута твича
звук подставляется из своей записи эфира, audioclean глушит мат отдельной копией.
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROUGH_DIR = "с матом"
CLEAN_DIR = "без мата"
MIN_GAME = 15 * 60.0          # игра короче — не ролик
MERGE_GAP = 10 * 60.0         # та же игра после перерыва короче — один ролик
EDGE_REACH = 60.0             # граница игры ищется в тишине на столько вокруг главы
QUIET_SHARE = 0.5             # тишина — вдвое тише обычного звука вокруг
TITLE = "{стример} играет в {игра}"
NOT_GAMES = {
    "just chatting", "music", "art", "talk shows & podcasts", "special events", "irl",
    "asmr", "pools, hot tubs, and beaches", "travel & outdoors", "food & drink",
    "science & technology", "software and game development", "makers & crafting",
    "sports", "fitness & health", "politics", "animals, aquariums, and zoos",
    "beauty & body art", "co-working & studying", "lotteries", "games + demos",
}
CHAPTERS_QUERY = (
    '{ video(id: "%s") { lengthSeconds game { displayName } '
    'moments(momentRequestType: VIDEO_CHAPTER_MARKERS) { edges { node { '
    'positionMilliseconds durationMilliseconds type description '
    'details { ... on GameChangeMomentDetails { game { displayName } } } } } } } }')


def chapters(vod, ask=None) -> list:
    """Главы записи твича: [{"start", "end", "game"}]. Одна игра — одна глава на всё."""
    from . import live as _live

    data = (ask or _live._ask)({"query": CHAPTERS_QUERY % vod})
    video = (data.get("data") or {}).get("video")
    if not video:
        return []
    length = float(video.get("lengthSeconds") or 0)
    out = []
    for edge in ((video.get("moments") or {}).get("edges") or ()):
        node = edge.get("node") or {}
        if node.get("type") not in (None, "GAME_CHANGE"):
            continue
        game = (((node.get("details") or {}).get("game") or {}).get("displayName")
                or node.get("description") or "")
        start = float(node.get("positionMilliseconds") or 0) / 1000.0
        out.append({"start": start,
                    "end": start + float(node.get("durationMilliseconds") or 0) / 1000.0,
                    "game": str(game).strip()})
    if not out and length:
        out.append({"start": 0.0, "end": length,
                    "game": ((video.get("game") or {}).get("displayName") or "").strip()})
    out.sort(key=lambda item: item["start"])
    return out


def is_game(name) -> bool:
    return bool(name) and name.strip().lower() not in NOT_GAMES


def game_parts(found, min_len=MIN_GAME, merge_gap=MERGE_GAP) -> list:
    """Игровые куски стрима: та же игра через короткую вставку — один кусок."""
    parts = []
    for chapter in found:
        if not is_game(chapter["game"]):
            continue
        last = parts[-1] if parts else None
        if (last and last["game"].lower() == chapter["game"].lower()
                and chapter["start"] - last["end"] <= merge_gap):
            last["end"] = max(last["end"], chapter["end"])
            continue
        parts.append({"start": chapter["start"], "end": chapter["end"],
                      "game": chapter["game"], "kind": "game"})
    return [part for part in parts if part["end"] - part["start"] >= min_len]


def quiet_near(level, window, at, reach=EDGE_REACH) -> float:
    """Самое тихое место около секунды at — там стример и переключался."""
    if level is None or not len(level):
        return at
    low = max(0, int((at - reach) / window))
    high = min(len(level), int((at + reach) / window) + 1)
    if high - low < 3:
        return at
    piece = np.convolve(np.asarray(level[low:high], np.float32), np.ones(3) / 3.0, "same")
    if piece.min() >= QUIET_SHARE * float(np.median(piece)):
        return at                                  # заметной тишины нет — верим главе
    return (low + int(np.argmin(piece))) * window + window / 2.0


def snap(parts, levels, length=0.0, reach=EDGE_REACH) -> list:
    level = (levels or {}).get("level")
    window = float((levels or {}).get("window") or 1.0)
    out = []
    for part in parts:
        start = 0.0 if part["start"] <= reach else quiet_near(level, window, part["start"], reach)
        end = part["end"] if length and part["end"] >= length - reach else \
            quiet_near(level, window, part["end"], reach)
        out.append(dict(part, start=start, end=max(end, start + 1.0)))
    return out


def title_for(template, streamer, game) -> str:
    text = (template or TITLE).replace("{стример}", streamer or "").replace("{игра}", game or "")
    return " ".join(text.split())


def streamer_name(profile) -> str:
    name = str((profile or {}).get("display") or (profile or {}).get("name") or "").strip()
    return name[:1].upper() + name[1:]


def plan_games(vod, profile, source=None, ask=None, levels=None, min_len=MIN_GAME,
               template=TITLE) -> list:
    """Моменты-ролики по играм записи: [{"start", "end", "title", "game", "kind"}]."""
    found = chapters(vod, ask=ask)
    parts = game_parts(found, min_len=min_len)
    if not parts:
        return []
    if levels is None and source:
        from . import loudness as _loud

        levels = _loud.audio_levels(source)
    length = max(item["end"] for item in found) if found else 0.0
    parts = snap(parts, levels, length)
    who = streamer_name(profile)
    return [dict(part, title=title_for(template, who, part["game"])) for part in parts]


def options_from_studio() -> dict:
    """Концовка, музыка и прочее для сборки — как настроено в окне Студии."""
    try:
        from studio.window import load_settings

        saved = load_settings()
    except Exception:
        saved = {}

    def number(key, fallback):
        try:
            return float(str(saved.get(key, fallback)).replace(",", "."))
        except (TypeError, ValueError):
            return fallback

    return {
        "options": {
            "outro": saved.get("outro") or None, "music": saved.get("music") or None,
            "music_volume": number("music_volume", 0.25),
            "music_start": number("music_start", 0.0), "fade": number("fade", 1.5),
            "blur": number("blur", 20.0), "crf": int(number("crf", 20)),
            "gpu": bool(saved.get("gpu")),
        },
        "censor": bool(saved.get("censor")),
        "model": saved.get("model") or "large-v3-turbo",
        "language": saved.get("language") or "ru",
    }


def safe_name(text) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    keep = [char if (char.isalnum() or char in " -_") else "_" for char in text]
    return "_".join("".join(keep).split())[:60]


def unique(path) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    index = 2
    while os.path.exists("%s_%d%s" % (base, index, ext)):
        index += 1
    return "%s_%d%s" % (base, index, ext)


def same_file(path, target) -> str:
    """Копия ролика без мата: жёсткая ссылка, если можно, иначе обычная копия."""
    try:
        os.link(path, target)
    except OSError:
        import shutil

        shutil.copy2(path, target)
    return target


def _clipmaker():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder = os.path.join(root, "clipmaker")
    if os.path.isdir(folder) and folder not in sys.path:
        sys.path.append(folder)
    import clipmaker

    return clipmaker


def make(source, moments, outdir, setup, log=None, stage=None, progress=None, cancel=None,
         live=None, fix=None) -> list:
    """Собирает ролики и чистые версии. Ответ: [{"video", "clean", "title", "moment"}].

    Ролики ложатся в «с матом», чистые версии — в «без мата» под тем же именем.
    live — заплатки мута из своей записи эфира; fix(item) правит ролик до цензуры.
    """
    log = log or (lambda text: None)
    stage = stage or log
    progress = progress or (lambda share: None)
    cancel = cancel or (lambda: False)
    clipmaker = _clipmaker()
    rough_dir = os.path.join(outdir, ROUGH_DIR)
    os.makedirs(rough_dir, exist_ok=True)
    made = []
    total = len(moments)
    for index, item in enumerate(moments, 1):
        if cancel():
            log("Остановлено.")
            break
        name = safe_name(item.get("title")) or ("rolik_%02d" % index)
        target = unique(os.path.join(rough_dir, name + ".mp4"))
        stage("Ролик %d из %d: %s" % (index, total, os.path.basename(target)))
        progress((index - 1) / max(1, total))
        opt = dict(setup["options"])
        opt.update({"input": source, "start": "%.3f" % item["start"],
                    "end": "+%.3f" % (item["end"] - item["start"]),
                    "output": target, "drops": item.get("drops")})
        code = clipmaker.make_clip(opt, on_line=log)
        if code != 0:
            log("ffmpeg вернул %d, ролик пропущен" % code)
            continue
        if live:
            from . import mute as _mute

            _mute.apply(target, item, live, log=log)
        made.append({"video": target, "clean": None, "title": item.get("title") or "",
                     "moment": item})
        if fix:
            fix(made[-1])
        log("готово: %s" % target)

    if setup.get("censor") and made and not cancel():
        from audioclean import pipeline as clean
        from audioclean.config import Settings

        clean_dir = os.path.join(outdir, CLEAN_DIR)
        os.makedirs(clean_dir, exist_ok=True)
        for index, item in enumerate(made, 1):
            if cancel():
                break
            path = item["video"]
            target = unique(os.path.join(clean_dir, os.path.basename(path)))
            stage("Глушу мат %d из %d" % (index, len(made)))
            settings = Settings(video=path, model=setup["model"], language=setup["language"],
                                device="auto")
            result = clean.analyze(settings, log=log)
            if not result.found:
                log("нечего глушить: %s — кладу как есть" % os.path.basename(path))
                item["clean"] = same_file(path, target)
                continue
            item["clean"] = clean.clean(settings, result, target, log=log)
            log("очищено: %s (%d слов)" % (item["clean"], len(result.found)))
    return made
