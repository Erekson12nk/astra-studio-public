"""Чистка диска: запись стрима весит 30–50 ГБ, звук эфира и кеши — ещё гигабайты.

Удаляется только то, что программа сделала сама:
- записи стримов, которые скачала или записала служба (помечены в базе), — когда
  всё нарезанное из них решено и прошло keep_source_days, либо в любом случае
  через max_source_days; ваши собственные файлы в папке стримов не трогаются;
- звук эфира в папке «_эфир» — старше keep_live_days;
- готовые клипы и ролики в папке автопилота: отклонённые — через
  keep_rejected_days, выложенные — через keep_posted_days;
- кеши разбора (звук, голоса, кадры) — старше недели.
Записи в базе не удаляются: обучающий сигнал и история остаются.
"""

from __future__ import annotations

import glob
import os
import tempfile
import time
from pathlib import Path

from . import autopilot as _ap

DAY = 86400.0
CACHE_DAYS = 7.0
LIVE_EXT = (".m4a", ".ts", ".json", ".part")


def _remove(path, remove, report) -> int:
    try:
        size = os.path.getsize(path)
        remove(path)
    except OSError:
        return 0
    report["files"] += 1
    report["bytes"] += size
    return size


def _inside(path, folder) -> bool:
    try:
        return Path(path).resolve().is_relative_to(Path(folder).resolve())
    except (OSError, ValueError):
        return False


def sweep(profile, now=None, log=None, remove=os.remove) -> dict:
    """Почистить одного стримера. Ответ: {"files", "bytes"}."""
    now = time.time() if now is None else now
    log = log or (lambda text: None)
    opts = _ap.settings(profile)
    report = {"files": 0, "bytes": 0}
    link = _ap.connect(profile)
    try:
        streams = [dict(row) for row in link.execute(
            "SELECT * FROM streams WHERE ours = 1 AND path IS NOT NULL AND path != ''"
            " AND removed_at IS NULL AND status IN (?, ?)", (_ap.DONE, _ap.BROKEN))]
        for stream in streams:
            waiting = link.execute("SELECT COUNT(*) FROM queue WHERE stream_id = ? AND status IN"
                                   " (?, ?)", (stream["id"], _ap.READY, _ap.APPROVED)
                                   ).fetchone()[0]
            age = now - float(stream.get("done_at") or stream.get("found_at") or now)
            if age < float(opts["keep_source_days"]) * DAY:
                continue
            if waiting and age < float(opts["max_source_days"]) * DAY:
                continue
            if os.path.isfile(stream["path"]):
                size = _remove(stream["path"], remove, report)
                log("удалена запись стрима: %s (%.1f ГБ)" % (os.path.basename(stream["path"]),
                                                             size / float(1 << 30)))
                for extra in glob.glob(glob.escape(stream["path"]) + ".*"):
                    _remove(extra, remove, report)
            link.execute("UPDATE streams SET removed_at = ? WHERE id = ?", (now, stream["id"]))
        own = Path(profile["folder"]) / _ap.FOLDER
        for status, days in ((_ap.REJECTED, opts["keep_rejected_days"]),
                             (_ap.POSTED, opts["keep_posted_days"])):
            for row in link.execute("SELECT file, decided_at, posted_at FROM queue WHERE"
                                    " status = ?", (status,)).fetchall():
                when = float(row["posted_at"] or row["decided_at"] or now)
                if (row["file"] and now - when >= float(days) * DAY
                        and os.path.isfile(row["file"]) and _inside(row["file"], own)):
                    _remove(row["file"], remove, report)
        link.commit()
    finally:
        link.close()
    from . import live as _live

    live = _live.folder_of(_ap.Pilot(profile, lister=lambda c, l: []).streams_folder())
    if os.path.isdir(live):
        for name in os.listdir(live):
            path = os.path.join(live, name)
            if (name.lower().endswith(LIVE_EXT) and os.path.isfile(path)
                    and now - os.path.getmtime(path) >= float(opts["keep_live_days"]) * DAY):
                _remove(path, remove, report)
    return report


def sweep_caches(now=None, remove=os.remove, folder=None, days=CACHE_DAYS) -> dict:
    """Кеши разбора: звук стрима целиком, голоса, кадры — по сотне мегабайт на стрим."""
    now = time.time() if now is None else now
    folder = folder or os.path.join(tempfile.gettempdir(), "ave_cache")
    report = {"files": 0, "bytes": 0}
    if not os.path.isdir(folder):
        return report
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if name.endswith(".onnx") or not os.path.isfile(path):
            continue                                  # модель голосов качается один раз
        if now - os.path.getmtime(path) >= days * DAY:
            _remove(path, remove, report)
    return report


def sweep_all(profiles=None, log=None, now=None) -> dict:
    total = {"files": 0, "bytes": 0}
    for profile in (profiles if profiles is not None else _ap.streamers()):
        for key, value in sweep(profile, now=now, log=log).items():
            total[key] += value
    for key, value in sweep_caches(now=now).items():
        total[key] += value
    if total["files"] and log:
        log("чистка диска: удалено %d файлов, %.1f ГБ" % (total["files"],
                                                         total["bytes"] / float(1 << 30)))
    return total
