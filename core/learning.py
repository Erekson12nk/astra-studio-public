from __future__ import annotations

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS outcomes (
    id           INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL,
    service      TEXT,
    video_id     TEXT,
    url          TEXT,
    views        INTEGER,
    likes        INTEGER,
    posted_at    REAL,
    matched_by   TEXT,
    overlap      REAL,
    updated_at   REAL NOT NULL,
    UNIQUE (candidate_id, service, video_id)
);
CREATE INDEX IF NOT EXISTS idx_outcomes_candidate ON outcomes(candidate_id);
"""

MIN_OVERLAP = 0.5


def norm_path(path) -> str:
    if not path:
        return ""
    text = os.path.abspath(str(path))
    if os.name == "nt":
        text = text.lower()
    return text


def overlap(a_start, a_end, b_start, b_end) -> float:
    inner = min(a_end, b_end) - max(a_start, b_start)
    if inner <= 0:
        return 0.0
    outer = max(a_end, b_end) - min(a_start, b_start)
    return inner / outer if outer > 0 else 0.0


def _connect(path):
    link = sqlite3.connect(str(path))
    link.row_factory = sqlite3.Row
    return link


def read_candidates(clipper_db) -> list:
    link = _connect(clipper_db)
    try:
        rows = link.execute(
            "SELECT c.id, c.video_id, c.start, c.end, c.title, c.model_score,"
            "       c.model, v.path"
            " FROM candidates c JOIN videos v ON v.id = c.video_id").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        link.close()
    return [dict(row) for row in rows]


def read_published(astra_db) -> list:
    link = _connect(astra_db)
    try:
        rows = link.execute("""
            SELECT p.video_id, p.service, p.url, p.posted_at,
                   c.source, c.start, c.finish, c.file,
                   (SELECT views FROM stats s WHERE s.video_id = p.video_id
                    ORDER BY s.at DESC LIMIT 1) AS views,
                   (SELECT likes FROM stats s WHERE s.video_id = p.video_id
                    ORDER BY s.at DESC LIMIT 1) AS likes
            FROM posts p JOIN clips c ON c.id = p.clip_id
            WHERE p.dry = 0""").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        link.close()
    return [dict(row) for row in rows]


def read_rendered(astra_db) -> list:
    link = _connect(astra_db)
    try:
        rows = link.execute(
            "SELECT source, start, finish, file FROM clips"
            " WHERE file IS NOT NULL AND file <> ''").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        link.close()
    return [dict(row) for row in rows]


def _match(candidates, source, start, finish, min_overlap):
    key = norm_path(source)
    best = None
    best_score = 0.0
    for item in candidates:
        if norm_path(item["path"]) != key:
            continue
        score = overlap(item["start"], item["end"], start, finish)
        if score > best_score:
            best, best_score = item, score
    if best is None or best_score < min_overlap:
        return None, 0.0
    return best, best_score


def link_databases(clipper_db, astra_db, min_overlap: float = MIN_OVERLAP) -> dict:
    report = {"candidates": 0, "published": 0, "matched": 0, "written": 0,
              "unmatched": [], "no_stats": 0, "rendered_matched": 0}
    if not os.path.isfile(str(clipper_db)):
        report["error"] = "нет базы clipper: %s" % clipper_db
        return report
    if not os.path.isfile(str(astra_db)):
        report["error"] = "нет базы astra: %s" % astra_db
        return report

    candidates = read_candidates(clipper_db)
    published = read_published(astra_db)
    report["candidates"] = len(candidates)
    report["published"] = len(published)
    if not candidates:
        report["error"] = "в базе clipper нет кандидатов"
        return report

    link = _connect(clipper_db)
    link.executescript(SCHEMA)
    now = time.time()
    written = 0
    posted_ids = set()
    for row in published:
        found, score = _match(candidates, row.get("source"), row.get("start") or 0.0,
                              row.get("finish") or 0.0, min_overlap)
        if found is None:
            report["unmatched"].append(
                {"file": os.path.basename(str(row.get("file") or "")),
                 "source": os.path.basename(str(row.get("source") or "")),
                 "start": row.get("start"), "end": row.get("finish")})
            continue
        report["matched"] += 1
        posted_ids.add(found["id"])
        if row.get("views") is None:
            report["no_stats"] += 1
        link.execute(
            "INSERT INTO outcomes (candidate_id, service, video_id, url, views,"
            " likes, posted_at, matched_by, overlap, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (candidate_id, service, video_id) DO UPDATE SET"
            " views = excluded.views, likes = excluded.likes,"
            " url = excluded.url, overlap = excluded.overlap,"
            " updated_at = excluded.updated_at",
            (found["id"], row.get("service"), row.get("video_id"), row.get("url"),
             row.get("views"), row.get("likes"), row.get("posted_at"),
             "время", score, now))
        written += 1

    only_rendered = set()
    for row in read_rendered(astra_db):
        found, _score = _match(candidates, row.get("source"), row.get("start") or 0.0,
                               row.get("finish") or 0.0, min_overlap)
        if found is not None and found["id"] not in posted_ids:
            only_rendered.add(found["id"])
    report["rendered_matched"] = len(only_rendered)

    link.commit()
    link.close()
    report["written"] = written
    return report


def training_signal(clipper_db, limit: int = 0) -> list:
    if not os.path.isfile(str(clipper_db)):
        return []
    link = _connect(clipper_db)
    try:
        link.executescript(SCHEMA)
        query = ("SELECT c.id, c.title, c.model_score, c.model,"
                 "       c.end - c.start AS length, c.features_json,"
                 "       o.views, o.likes, o.service, o.posted_at,"
                 "       (SELECT AVG(rating) FROM ratings r"
                 "        WHERE r.candidate_id = c.id) AS rating"
                 " FROM candidates c"
                 " LEFT JOIN outcomes o ON o.candidate_id = c.id"
                 " ORDER BY o.views DESC NULLS LAST, c.model_score DESC")
        if limit:
            query += " LIMIT %d" % int(limit)
        rows = link.execute(query).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        link.close()
    return [dict(row) for row in rows]


def summary(clipper_db) -> dict:
    out = {"candidates": 0, "rated": 0, "compared": 0, "with_outcome": 0,
           "with_views": 0, "total_views": 0}
    if not os.path.isfile(str(clipper_db)):
        return out
    link = _connect(clipper_db)
    try:
        link.executescript(SCHEMA)
        one = link.execute("SELECT COUNT(*) FROM candidates").fetchone()
        out["candidates"] = one[0] if one else 0
        out["rated"] = link.execute(
            "SELECT COUNT(DISTINCT candidate_id) FROM ratings").fetchone()[0]
        out["compared"] = link.execute("SELECT COUNT(*) FROM comparisons").fetchone()[0]
        out["with_outcome"] = link.execute(
            "SELECT COUNT(DISTINCT candidate_id) FROM outcomes").fetchone()[0]
        out["with_views"] = link.execute(
            "SELECT COUNT(DISTINCT candidate_id) FROM outcomes"
            " WHERE views IS NOT NULL").fetchone()[0]
        total = link.execute("SELECT SUM(views) FROM outcomes").fetchone()[0]
        out["total_views"] = int(total or 0)
    except sqlite3.OperationalError:
        pass
    finally:
        link.close()
    return out
