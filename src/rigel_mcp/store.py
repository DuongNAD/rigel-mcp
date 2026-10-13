"""SQLite storage: documents, chunks, an FTS5 index over folded text, and a tiny key/value state."""

from __future__ import annotations

import json
import random
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from .text import fold, fts_match

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS docs (
    id          INTEGER PRIMARY KEY,
    course      TEXT NOT NULL,
    lesson      TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'library',
    n_chunks    INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    size        INTEGER,
    mtime       REAL,
    abs_path    TEXT,
    UNIQUE (course, lesson, rel_path)
);
CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY,
    doc_id  INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    locator TEXT NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    text    TEXT NOT NULL,
    UNIQUE (doc_id, seq)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(heading, body, tokenize = 'porter unicode61');
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added in 0.2.0 (change detection and citations). Databases made by 0.1.0 get them via ALTER TABLE.
_DOC_COLUMNS_V2 = {"size": "INTEGER", "mtime": "REAL", "abs_path": "TEXT"}

REF_RE = re.compile(r"^\s*D(\d+)#(\d+)\s*$", re.IGNORECASE)

_COLS = "c.doc_id, c.seq, c.locator, c.heading, c.text, d.course, d.lesson, d.rel_path, d.kind, d.abs_path"
_JOIN = "FROM chunks AS c JOIN docs AS d ON d.id = c.doc_id"
_SCOPE = "(:course IS NULL OR d.course = :course) AND (:lesson IS NULL OR d.lesson = :lesson)"

SEARCH_SQL = f"""
SELECT c.id AS chunk_id, {_COLS}, bm25(chunks_fts, 4.0, 1.0) AS raw_score
FROM chunks_fts
JOIN chunks AS c ON c.id = chunks_fts.rowid
JOIN docs AS d ON d.id = c.doc_id
WHERE chunks_fts MATCH :match AND {_SCOPE}
ORDER BY raw_score
LIMIT :limit
"""


def make_ref(doc_id: int, seq: int) -> str:
    return f"D{doc_id}#{seq}"


def parse_ref(ref: str) -> tuple[int, int] | None:
    m = REF_RE.match(ref or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _hit(row: sqlite3.Row) -> dict:
    return {
        "ref": make_ref(row["doc_id"], row["seq"]),
        "file": row["rel_path"],
        "locator": row["locator"],
        "heading": row["heading"],
        "course": row["course"],
        "lesson": row["lesson"],
        "text": row["text"],
        "kind": row["kind"],
        "path": row["abs_path"],
    }


def _migrate(conn: sqlite3.Connection) -> None:
    """Add the 0.2.0 columns to a `docs` table created by an older version."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(docs)")}
    for column, sql_type in _DOC_COLUMNS_V2.items():
        if column not in have:
            try:
                conn.execute(f"ALTER TABLE docs ADD COLUMN {column} {sql_type}")
            except sqlite3.OperationalError:  # another process added it first
                if column not in {r[1] for r in conn.execute("PRAGMA table_info(docs)")}:
                    raise


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            _migrate(conn)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per operation: handlers may run on worker threads.
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    # ---- documents -------------------------------------------------------------------------

    def doc_sha(self, course: str, lesson: str, rel_path: str) -> tuple[int, str] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, sha256 FROM docs WHERE course=? AND lesson=? AND rel_path=?",
                (course, lesson, rel_path),
            ).fetchone()
        return (row["id"], row["sha256"]) if row else None

    @staticmethod
    def _drop_chunks(conn: sqlite3.Connection, doc_id: int) -> None:
        conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

    def upsert_doc(
        self,
        *,
        course: str,
        lesson: str,
        rel_path: str,
        kind: str,
        sha256: str,
        origin: str,
        chunks: Sequence,
        size: int | None = None,
        mtime: float | None = None,
        abs_path: str | None = None,
    ) -> tuple[int, bool]:
        """Insert or replace one document and its chunks. The doc id (and so every ref) stays stable."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM docs WHERE course=? AND lesson=? AND rel_path=?",
                (course, lesson, rel_path),
            ).fetchone()
            if row:
                doc_id, created = row["id"], False
                self._drop_chunks(conn, doc_id)
                conn.execute(
                    "UPDATE docs SET kind=?, sha256=?, origin=?, n_chunks=?, ingested_at=?, size=?, mtime=?, abs_path=?"
                    " WHERE id=?",
                    (kind, sha256, origin, len(chunks), now, size, mtime, abs_path, doc_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO docs (course, lesson, rel_path, kind, sha256, origin, n_chunks, ingested_at,"
                    " size, mtime, abs_path) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (course, lesson, rel_path, kind, sha256, origin, len(chunks), now, size, mtime, abs_path),
                )
                doc_id, created = cur.lastrowid, True
            for seq, ch in enumerate(chunks, start=1):
                cur = conn.execute(
                    "INSERT INTO chunks (doc_id, seq, locator, heading, text) VALUES (?,?,?,?,?)",
                    (doc_id, seq, ch.locator, ch.heading, ch.text),
                )
                conn.execute(
                    "INSERT INTO chunks_fts (rowid, heading, body) VALUES (?,?,?)",
                    (cur.lastrowid, fold(ch.heading), fold(ch.text)),
                )
        return doc_id, created

    def list_docs(self, origin: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM docs WHERE origin=?", (origin,)).fetchall()

    def docs_index(self) -> dict[tuple[str, str, str], dict]:
        """Every doc keyed by (course, lesson, rel_path), for change detection during sync."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, course, lesson, rel_path, sha256, origin, size, mtime, abs_path FROM docs"
            ).fetchall()
        return {(r["course"], r["lesson"], r["rel_path"]): dict(r) for r in rows}

    def touch_doc(self, doc_id: int, *, size: int, mtime: float, abs_path: str, origin: str) -> None:
        """Record new size/mtime/location of a doc whose content (sha256) did not change. No re-ingest."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE docs SET size=?, mtime=?, abs_path=?, origin=? WHERE id=?",
                (size, mtime, abs_path, origin, doc_id),
            )

    def origin_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {r["origin"]: r["n"] for r in conn.execute("SELECT origin, COUNT(*) AS n FROM docs GROUP BY origin")}

    def docs_with_path(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT id, course, lesson, abs_path FROM docs WHERE abs_path IS NOT NULL").fetchall()

    def delete_doc(self, doc_id: int) -> None:
        with self.connect() as conn:
            self._drop_chunks(conn, doc_id)
            conn.execute("DELETE FROM docs WHERE id=?", (doc_id,))

    def summary(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT course, lesson, COUNT(*) AS docs, SUM(n_chunks) AS chunks"
                " FROM docs GROUP BY course, lesson ORDER BY course, lesson"
            ).fetchall()
        return [dict(r) for r in rows]

    def courses(self) -> list[str]:
        with self.connect() as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT course FROM docs ORDER BY course")]

    def lessons(self, course: str) -> list[str]:
        with self.connect() as conn:
            return [
                r[0]
                for r in conn.execute("SELECT DISTINCT lesson FROM docs WHERE course=? ORDER BY lesson", (course,))
            ]

    # ---- scope -----------------------------------------------------------------------------

    def get_scope(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key='scope'").fetchone()
        return json.loads(row["value"]) if row else None

    def set_scope(self, course: str, lesson: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO state (key, value) VALUES ('scope', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps({"course": course, "lesson": lesson}, ensure_ascii=False),),
            )

    def clear_scope(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM state WHERE key='scope'")

    # ---- reading ---------------------------------------------------------------------------

    @staticmethod
    def _chunks_with_term(conn: sqlite3.Connection, term: str, ids: list[int]) -> set[int]:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN ({marks})", [f'"{term}"', *ids]
        )
        return {r[0] for r in cur}

    def search(self, terms: list[str], course: str | None, lesson: str | None, limit: int) -> list[dict]:
        """BM25 search. Each hit carries `coverage`: the share of query terms the chunk really contains,
        decided by the index itself (same tokenizer and stemming), so it never disagrees with retrieval."""
        match = fts_match(terms)
        if not match:
            return []
        params = {"match": match, "course": course, "lesson": lesson, "limit": limit}
        try:
            with self.connect() as conn:
                rows = conn.execute(SEARCH_SQL, params).fetchall()
                ids = [r["chunk_id"] for r in rows]
                has = {t: self._chunks_with_term(conn, t, ids) for t in terms} if ids else {}
        except sqlite3.OperationalError:
            return []
        out = []
        for r in rows:
            matched = [t for t in terms if r["chunk_id"] in has[t]]
            h = _hit(r)
            h.update(
                chunk_id=r["chunk_id"],
                score=-float(r["raw_score"]),  # bm25() is lower-is-better; flip it
                coverage=round(len(matched) / len(terms), 2),
                matched=matched,
            )
            out.append(h)
        return out

    def window(self, doc_id: int, lo: int, hi: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} {_JOIN} WHERE c.doc_id=? AND c.seq BETWEEN ? AND ? ORDER BY c.seq",
                (doc_id, lo, hi),
            ).fetchall()
        return [_hit(r) for r in rows]

    def resolve_refs(self, refs: Sequence[str]) -> tuple[dict[str, dict], list[str]]:
        found: dict[str, dict] = {}
        missing: list[str] = []
        for ref in refs:
            parsed = parse_ref(ref)
            hits = self.window(parsed[0], parsed[1], parsed[1]) if parsed else []
            if hits:
                found[make_ref(*parsed)] = hits[0]
            else:
                missing.append(ref)
        return found, missing

    def outline(self, course: str | None, lesson: str | None) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT c.doc_id, c.seq, c.locator, c.heading, d.course, d.lesson, d.rel_path {_JOIN}"
                f" WHERE {_SCOPE} ORDER BY d.course, d.lesson, d.rel_path, c.seq",
                {"course": course, "lesson": lesson},
            ).fetchall()
        return [dict(r) for r in rows]

    def sample(self, course: str | None, lesson: str | None, n: int, seed: int | None = None) -> list[dict]:
        with self.connect() as conn:
            ids = [
                r["id"]
                for r in conn.execute(
                    f"SELECT c.id {_JOIN} WHERE {_SCOPE} AND length(c.text) >= 80",
                    {"course": course, "lesson": lesson},
                )
            ]
            if not ids:
                return []
            picked = random.Random(seed).sample(ids, min(n, len(ids)))
            marks = ",".join("?" * len(picked))
            rows = conn.execute(f"SELECT c.id, {_COLS} {_JOIN} WHERE c.id IN ({marks})", picked).fetchall()
        order = {cid: i for i, cid in enumerate(picked)}
        rows.sort(key=lambda r: order[r["id"]])
        return [_hit(r) for r in rows]
