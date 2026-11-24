"""Хранилище: SQLite.

Задача этого модуля — не «сохранить результат», а **собрать датасет**.
Фаза 1 обучения не содержит, но всё, что понадобится фазам 2-4, пишется
с самого начала: кандидаты с признаками, абсолютные оценки и — главное —
попарные сравнения.

Попарные сравнения важнее звёздочек: абсолютная оценка у человека плывёт
со временем, относительная — устойчива. Именно на парах обучается модель
Брэдли-Терри, которая даст шкалу предпочтений.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .models import Candidate, Sentence, Transcript

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    duration    REAL,
    language    TEXT,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sentences (
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (video_id, idx)
);

CREATE TABLE IF NOT EXISTS candidates (
    id            INTEGER PRIMARY KEY,
    video_id      INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start         REAL NOT NULL,
    end           REAL NOT NULL,
    title         TEXT,
    hook          TEXT,
    reason        TEXT,
    model_score   REAL,
    features_json TEXT,
    model         TEXT,
    created_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_video ON candidates(video_id);

CREATE TABLE IF NOT EXISTS renders (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    created_at   REAL NOT NULL
);

-- Абсолютная оценка: просто и привычно, но сигнал шумный.
CREATE TABLE IF NOT EXISTS ratings (
    id           INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    rating       INTEGER NOT NULL,
    created_at   REAL NOT NULL
);

-- Попарное сравнение: основной обучающий сигнал для фазы 3-4.
CREATE TABLE IF NOT EXISTS comparisons (
    id         INTEGER PRIMARY KEY,
    left_id    INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    right_id   INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    winner_id  INTEGER,          -- NULL = ничья
    created_at REAL NOT NULL
);

-- Референсные ролики: положительные примеры для контрастного обучения.
CREATE TABLE IF NOT EXISTS references_ (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL,
    note       TEXT,
    created_at REAL NOT NULL
);
"""


HAND = "рука"
PICKS = "выбор"
MODEL = "модель"


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self._add_source_column()
        self._add_power_column()
        self.db.commit()

    def _add_source_column(self) -> None:
        for table in ("ratings", "comparisons"):
            have = {r["name"] for r in self.db.execute(
                "PRAGMA table_info(%s)" % table)}
            if "source" not in have:
                self.db.execute(
                    "ALTER TABLE %s ADD COLUMN source TEXT NOT NULL "
                    "DEFAULT '%s'" % (table, HAND))

    def _add_power_column(self) -> None:
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(candidates)")}
        if "power" not in have:
            self.db.execute("ALTER TABLE candidates ADD COLUMN power REAL")

    def save_power(self, power: dict) -> None:
        if not power:
            return
        self.db.executemany(
            "UPDATE candidates SET power = ? WHERE id = ?",
            [(float(value), int(cid)) for cid, value in power.items()])
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # --- видео и расшифровка ---------------------------------------------

    def upsert_video(self, path: Path, duration: float, language: str) -> int:
        cur = self.db.execute("SELECT id FROM videos WHERE path = ?", (str(path),))
        row = cur.fetchone()
        if row:
            self.db.execute(
                "UPDATE videos SET duration = ?, language = ? WHERE id = ?",
                (duration, language, row["id"]),
            )
            self.db.commit()
            return row["id"]
        cur = self.db.execute(
            "INSERT INTO videos (path, duration, language, created_at) VALUES (?, ?, ?, ?)",
            (str(path), duration, language, time.time()),
        )
        self.db.commit()
        return cur.lastrowid

    def save_transcript(self, video_id: int, transcript: Transcript) -> None:
        self.db.execute("DELETE FROM sentences WHERE video_id = ?", (video_id,))
        self.db.executemany(
            "INSERT INTO sentences (video_id, idx, start, end, text) VALUES (?, ?, ?, ?, ?)",
            [(video_id, s.idx, s.start, s.end, s.text) for s in transcript.sentences],
        )
        self.db.commit()

    def load_sentences(self, video_id: int) -> list[Sentence]:
        rows = self.db.execute(
            "SELECT idx, start, end, text FROM sentences WHERE video_id = ? ORDER BY idx",
            (video_id,),
        ).fetchall()
        return [Sentence(idx=r["idx"], start=r["start"], end=r["end"], text=r["text"]) for r in rows]

    # --- кандидаты --------------------------------------------------------

    def save_candidates(self, video_id: int, cands: list[Candidate], model: str) -> list[int]:
        """Записывает кандидатов видео и возвращает присвоенные id.

        Старые кандидаты вычищаются, НО не те, к которым уже привязаны данные
        пользователя — оценки, сравнения или отрендеренный файл. Раньше здесь
        стоял безусловный DELETE, и повторный прогон того же видео каскадом
        сносил всю накопленную разметку. Терять её нельзя: ради неё всё и
        затевалось, а получить заново её нельзя ничем, кроме времени человека.

        Побочный эффект — возможные почти-дубли между старым и новым набором.
        Это дешёвая плата: лишняя строка в таблице против потерянной работы.
        """
        self.db.execute(
            """DELETE FROM candidates
               WHERE video_id = ?
                 AND id NOT IN (SELECT candidate_id FROM ratings)
                 AND id NOT IN (SELECT candidate_id FROM renders)
                 AND id NOT IN (SELECT left_id FROM comparisons)
                 AND id NOT IN (SELECT right_id FROM comparisons)""",
            (video_id,),
        )
        ids: list[int] = []
        now = time.time()
        for c in cands:
            cur = self.db.execute(
                """INSERT INTO candidates
                   (video_id, start, end, title, hook, reason, model_score,
                    features_json, model, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video_id, c.start, c.end, c.title, c.hook, c.reason, c.score,
                 json.dumps(c.features, ensure_ascii=False), model, now),
            )
            ids.append(cur.lastrowid)
        self.db.commit()
        return ids

    def list_candidates(self, video_id: int | None = None) -> list[sqlite3.Row]:
        if video_id is None:
            return self.db.execute(
                """SELECT c.*, v.path AS video_path, v.duration AS video_duration
                   FROM candidates c JOIN videos v ON v.id = c.video_id
                   ORDER BY c.model_score DESC"""
            ).fetchall()
        return self.db.execute(
            """SELECT c.*, v.path AS video_path, v.duration AS video_duration
               FROM candidates c JOIN videos v ON v.id = c.video_id
               WHERE c.video_id = ? ORDER BY c.model_score DESC""",
            (video_id,),
        ).fetchall()

    def add_missed(self, video_id: int, start: float, end: float, title: str,
                   score: float, features: dict) -> int:
        cur = self.db.execute(
            """INSERT INTO candidates
               (video_id, start, end, title, hook, reason, model_score,
                features_json, model, created_at)
               VALUES (?, ?, ?, ?, '', '', ?, ?, ?, ?)""",
            (video_id, start, end, title, score,
             json.dumps(features, ensure_ascii=False), HAND, time.time()),
        )
        self.db.commit()
        return cur.lastrowid

    def drop_unused_missed(self) -> int:
        cur = self.db.execute(
            """DELETE FROM candidates
               WHERE model = ?
                 AND id NOT IN (SELECT candidate_id FROM ratings)
                 AND id NOT IN (SELECT candidate_id FROM renders)
                 AND id NOT IN (SELECT left_id FROM comparisons)
                 AND id NOT IN (SELECT right_id FROM comparisons)""",
            (HAND,),
        )
        self.db.commit()
        return cur.rowcount

    def forget_source(self, source: str) -> tuple[int, int]:
        cur = self.db.execute("DELETE FROM comparisons WHERE source = ?", (source,))
        gone_c = cur.rowcount
        cur = self.db.execute("DELETE FROM ratings WHERE source = ?", (source,))
        gone_r = cur.rowcount
        self.db.commit()
        return gone_c, gone_r

    def forget_training(self) -> tuple[int, int]:
        cur = self.db.execute("DELETE FROM comparisons")
        gone_c = cur.rowcount
        cur = self.db.execute("DELETE FROM ratings")
        gone_r = cur.rowcount
        self.db.commit()
        return gone_c, gone_r

    def count_source(self, source: str) -> tuple[int, int]:
        c = self.db.execute("SELECT COUNT(*) FROM comparisons WHERE source = ?",
                            (source,)).fetchone()[0]
        r = self.db.execute("SELECT COUNT(*) FROM ratings WHERE source = ?",
                            (source,)).fetchone()[0]
        return c, r

    def latest_video(self) -> sqlite3.Row | None:
        return self.db.execute(
            """SELECT v.* FROM videos v
               WHERE EXISTS (SELECT 1 FROM candidates c WHERE c.video_id = v.id)
               ORDER BY v.id DESC LIMIT 1"""
        ).fetchone()

    def videos_with_candidates(self) -> int:
        return self.db.execute(
            "SELECT COUNT(DISTINCT video_id) FROM candidates"
        ).fetchone()[0]

    def record_render(self, candidate_id: int, path: Path) -> None:
        # Один кандидат — одна запись. Пере-рендер и adopt добавляли вторую,
        # и тогда JOIN на renders размножал строки: клип попадал в подбор пар
        # дважды и мог встать сам против себя.
        self.db.execute("DELETE FROM renders WHERE candidate_id = ?", (candidate_id,))
        self.db.execute(
            "INSERT INTO renders (candidate_id, path, created_at) VALUES (?, ?, ?)",
            (candidate_id, str(path), time.time()),
        )
        self.db.commit()

    def drop_duplicate_renders(self) -> int:
        before = self.db.execute("SELECT COUNT(*) FROM renders").fetchone()[0]
        self.db.execute(
            """DELETE FROM renders WHERE rowid NOT IN
               (SELECT MAX(rowid) FROM renders GROUP BY candidate_id)"""
        )
        self.db.commit()
        return before - self.db.execute("SELECT COUNT(*) FROM renders").fetchone()[0]

    # --- обучающий сигнал -------------------------------------------------

    def add_rating(self, candidate_id: int, rating: int) -> None:
        self.db.execute(
            "INSERT INTO ratings (candidate_id, rating, created_at) VALUES (?, ?, ?)",
            (candidate_id, rating, time.time()),
        )
        self.db.commit()

    def reject(self, candidate_id: int, *, rating: int = 0, delete_file: bool = True) -> Path | None:
        """Отклоняет клип: ставит оценку 0 и убирает файл.

        Сама запись о кандидате в базе ОСТАЁТСЯ — и это принципиально.
        «Плохо» такой же обучающий сигнал, как «хорошо»: без отрицательных
        примеров модель предпочтений не обучается вообще. Удаляется только
        видеофайл, чтобы отклонённый клип не мешался в списке и не занимал место.

        Возвращает путь удалённого файла или None.
        """
        self.db.execute(
            "INSERT INTO ratings (candidate_id, rating, created_at) VALUES (?, ?, ?)",
            (candidate_id, rating, time.time()),
        )
        removed: Path | None = None
        if delete_file:
            for row in self.db.execute(
                "SELECT path FROM renders WHERE candidate_id = ?", (candidate_id,)
            ).fetchall():
                p = Path(row["path"])
                try:
                    if p.exists():
                        p.unlink()
                        removed = p
                except OSError:
                    pass
            self.db.execute("DELETE FROM renders WHERE candidate_id = ?", (candidate_id,))
        self.db.commit()
        return removed

    def add_comparison(self, left_id: int, right_id: int, winner_id: int | None,
                       source: str = HAND) -> None:
        self.db.execute(
            "INSERT INTO comparisons (left_id, right_id, winner_id, created_at,"
            " source) VALUES (?, ?, ?, ?, ?)",
            (left_id, right_id, winner_id, time.time(), source),
        )
        self.db.commit()

    def add_reference(self, path: Path, note: str = "") -> None:
        self.db.execute(
            "INSERT INTO references_ (path, note, created_at) VALUES (?, ?, ?)",
            (str(path), note, time.time()),
        )
        self.db.commit()

    def render_path(self, candidate_id: int) -> Path | None:
        row = self.db.execute(
            "SELECT path FROM renders WHERE candidate_id = ? ORDER BY created_at DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        if not row:
            return None
        p = Path(row["path"])
        return p if p.exists() else None

    def render_index(self) -> dict[int, dict]:
        """candidate_id → сведения о последнем рендере.

        Один запрос вместо обращения к базе на каждую строку списка: на двух
        сотнях кандидатов разница уже заметна на глаз.
        """
        out: dict[int, dict] = {}
        for r in self.db.execute(
            "SELECT candidate_id, path, created_at FROM renders ORDER BY created_at"
        ):
            p = Path(r["path"])
            out[r["candidate_id"]] = {
                "path": p, "ts": r["created_at"], "exists": p.exists(),
            }
        return out

    def rating_index(self) -> dict[int, int]:
        """candidate_id → последняя выставленная оценка (0 = удалён)."""
        return {
            r["candidate_id"]: r["rating"]
            for r in self.db.execute(
                """SELECT candidate_id, rating FROM ratings
                   WHERE id IN (SELECT MAX(id) FROM ratings GROUP BY candidate_id)"""
            )
        }

    def last_render_batch(self, video_id: int | None = None) -> float:
        """Время последнего рендера — граница, отделяющая «новые» от «старых»."""
        if video_id is None:
            row = self.db.execute("SELECT MAX(created_at) FROM renders").fetchone()
        else:
            row = self.db.execute(
                """SELECT MAX(r.created_at) FROM renders r
                   JOIN candidates c ON c.id = r.candidate_id
                   WHERE c.video_id = ?""",
                (video_id,),
            ).fetchone()
        return row[0] or 0.0

    def suggest_pairs(self, limit: int = 20, *, rendered_only: bool = True) -> list[tuple]:
        """Пары кандидатов, которые стоит сравнить.

        Порядок важнее, чем кажется. Движок насыщает оценку — на живой базе
        77 клипов из 79 получили ровно 9.0, — поэтому «близкие оценки» дают
        сплошные нули, и стабильная сортировка каждый раз возвращала одни и
        те же самые старые пары: свежесмонтированные клипы не показывались
        никогда. Поэтому первым делом смотрим, кого ещё мало сравнивали,
        и только потом на разницу оценок и свежесть.
        """
        sql = """SELECT c.id, c.video_id, c.title, c.model_score, c.start, c.end,
                        r.path AS render_path, r.created_at AS render_at
                 FROM candidates c
                 LEFT JOIN renders r ON r.candidate_id = c.id"""
        if rendered_only:
            sql += " WHERE r.path IS NOT NULL"
        rows = {}
        for row in self.db.execute(sql):
            kept = rows.get(row["id"])
            if kept is None or (row["render_at"] or 0) >= (kept["render_at"] or 0):
                rows[row["id"]] = row
        rows = list(rows.values())

        seen = set()
        met = {}
        for r in self.db.execute("SELECT left_id, right_id FROM comparisons"):
            seen.add(tuple(sorted((r["left_id"], r["right_id"]))))
            met[r["left_id"]] = met.get(r["left_id"], 0) + 1
            met[r["right_id"]] = met.get(r["right_id"], 0) + 1

        pairs = []
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                if a["video_id"] != b["video_id"]:
                    continue
                if tuple(sorted((a["id"], b["id"]))) in seen:
                    continue
                rest = met.get(a["id"], 0) + met.get(b["id"], 0)
                gap = abs(a["model_score"] - b["model_score"])
                fresh = -max(a["render_at"] or 0, b["render_at"] or 0)
                pairs.append((rest, gap, fresh, a, b))
        pairs.sort(key=lambda p: p[:3])

        # Без ограничения на повторы весь список вырождается в «самый свежий
        # клип против всех остальных»: у новых клипов одинаково нулевой счёт
        # сравнений, и разнимает их только свежесть.
        picked = []
        shown = {}
        for allowance in (2, 4, None):
            for item in pairs:
                if len(picked) >= limit:
                    break
                if item in picked:
                    continue
                left, right = item[3]["id"], item[4]["id"]
                if allowance is not None and (shown.get(left, 0) >= allowance
                                              or shown.get(right, 0) >= allowance):
                    continue
                picked.append(item)
                shown[left] = shown.get(left, 0) + 1
                shown[right] = shown.get(right, 0) + 1
            if len(picked) >= limit:
                break
        return [(p[1], p[3], p[4]) for p in picked[:limit]]

    def training_stats(self) -> dict:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return {
            "videos": q("SELECT COUNT(*) FROM videos"),
            "candidates": q("SELECT COUNT(*) FROM candidates"),
            "ratings": q("SELECT COUNT(*) FROM ratings"),
            "comparisons": q("SELECT COUNT(*) FROM comparisons"),
            "references": q("SELECT COUNT(*) FROM references_"),
        }
