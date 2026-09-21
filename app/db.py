"""SQLite repository layer — the storage swap point (SQLite now, Supabase later).

Callers use `Repo`; they never touch SQL. Swapping to Supabase means a new class
with the same methods.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS folder (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES folder(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS doc (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    folder_id INTEGER REFERENCES folder(id) ON DELETE SET NULL,
    font_style TEXT NOT NULL DEFAULT 'handwriting',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS block (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES doc(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    type TEXT NOT NULL,          -- text | image | todo
    content TEXT NOT NULL DEFAULT '',
    image_path TEXT
);
CREATE TABLE IF NOT EXISTS tag (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS doc_tag (
    doc_id INTEGER NOT NULL REFERENCES doc(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
    PRIMARY KEY (doc_id, tag_id)
);
CREATE TABLE IF NOT EXISTS todo_list (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER REFERENCES doc(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    ord INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS todo (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER REFERENCES doc(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    date TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES doc(id) ON DELETE CASCADE,
    original_path TEXT NOT NULL,
    cropped_path TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)
        # migration: add manual-ordering column to todo if missing
        cols = [r["name"] for r in c.execute("PRAGMA table_info(todo)").fetchall()]
        if "ord" not in cols:
            c.execute("ALTER TABLE todo ADD COLUMN ord INTEGER DEFAULT 0")
            c.execute("UPDATE todo SET ord = id")  # stable initial order
        if "details" not in cols:
            c.execute("ALTER TABLE todo ADD COLUMN details TEXT NOT NULL DEFAULT ''")
        if "list_id" not in cols:
            # groups a todo into a checklist (todo_list); NULL = standalone todo
            c.execute("ALTER TABLE todo ADD COLUMN list_id INTEGER")


class Repo:
    """Thin CRUD over the tables. Phase 2 adds search."""

    # --- users ---
    def get_user(self, username: str):
        with _conn() as c:
            return c.execute(
                "SELECT * FROM user WHERE username = ?", (username,)
            ).fetchone()

    def create_user(self, username: str, password_hash: str) -> None:
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO user (username, password_hash) VALUES (?, ?)",
                (username, password_hash),
            )

    # --- folders ---
    def list_folders(self):
        with _conn() as c:
            return [
                dict(r)
                for r in c.execute("SELECT * FROM folder ORDER BY name").fetchall()
            ]

    def create_folder(self, name: str, parent_id: int | None = None) -> int:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO folder (name, parent_id) VALUES (?, ?)", (name, parent_id)
            )
            return cur.lastrowid

    # --- docs ---
    def create_doc(
        self, title: str, folder_id: int | None, blocks: list[dict], tags: list[str]
    ) -> int:
        now = _now()
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO doc (title, folder_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (title, folder_id, now, now),
            )
            doc_id = cur.lastrowid
            for i, b in enumerate(blocks):
                c.execute(
                    "INSERT INTO block (doc_id, ord, type, content, image_path) VALUES (?, ?, ?, ?, ?)",
                    (
                        doc_id,
                        i,
                        b.get("type", "text"),
                        b.get("content", ""),
                        b.get("image_path"),
                    ),
                )
            self._attach_tags(c, doc_id, tags)
            return doc_id

    def get_doc(self, doc_id: int) -> dict | None:
        with _conn() as c:
            d = c.execute("SELECT * FROM doc WHERE id = ?", (doc_id,)).fetchone()
            if not d:
                return None
            blocks = c.execute(
                "SELECT * FROM block WHERE doc_id = ? ORDER BY ord", (doc_id,)
            ).fetchall()
            tags = c.execute(
                "SELECT t.name FROM tag t JOIN doc_tag dt ON dt.tag_id = t.id WHERE dt.doc_id = ?",
                (doc_id,),
            ).fetchall()
            media = c.execute(
                "SELECT original_path FROM media WHERE doc_id = ? ORDER BY id", (doc_id,)
            ).fetchall()
            out = dict(d)
            out["blocks"] = [dict(b) for b in blocks]
            out["tags"] = [r["name"] for r in tags]
            out["originals"] = [r["original_path"] for r in media]
            return out

    def list_docs(
        self, q: str | None = None, folder_id: int | None = None, tag: str | None = None
    ):
        # include a short preview snippet (first non-empty text block) for app-style cards
        preview = (
            "(SELECT content FROM block WHERE doc_id = d.id AND type = 'text' "
            "AND content != '' ORDER BY ord LIMIT 1) AS preview"
        )
        sql = f"SELECT DISTINCT d.*, {preview} FROM doc d"
        args: list = []
        where = []
        if q:
            sql += (
                " LEFT JOIN block b ON b.doc_id = d.id"
                " LEFT JOIN doc_tag dt ON dt.doc_id = d.id"
                " LEFT JOIN tag t ON t.id = dt.tag_id"
            )
            where.append("(d.title LIKE ? OR b.content LIKE ? OR t.name LIKE ?)")
            args += [f"%{q}%", f"%{q}%", f"%{q}%"]
        if folder_id is not None:
            where.append("d.folder_id = ?")
            args.append(folder_id)
        if tag:
            sql += " JOIN doc_tag dt2 ON dt2.doc_id = d.id JOIN tag t2 ON t2.id = dt2.tag_id"
            where.append("t2.name = ?")
            args.append(tag)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY d.updated_at DESC"
        with _conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def update_doc(self, doc_id, title, folder_id, font_style, blocks, tags):
        with _conn() as c:
            c.execute(
                "UPDATE doc SET title = ?, folder_id = ?, font_style = ?, updated_at = ? WHERE id = ?",
                (title, folder_id, font_style, _now(), doc_id),
            )
            c.execute("DELETE FROM block WHERE doc_id = ?", (doc_id,))
            for i, b in enumerate(blocks):
                c.execute(
                    "INSERT INTO block (doc_id, ord, type, content, image_path) VALUES (?, ?, ?, ?, ?)",
                    (
                        doc_id,
                        i,
                        b.get("type", "text"),
                        b.get("content", ""),
                        b.get("image_path"),
                    ),
                )
            c.execute("DELETE FROM doc_tag WHERE doc_id = ?", (doc_id,))
            self._attach_tags(c, doc_id, tags)
            self._prune_orphan_tags(c)

    def set_doc_folder(self, doc_id, folder_id):
        with _conn() as c:
            c.execute(
                "UPDATE doc SET folder_id = ?, updated_at = ? WHERE id = ?",
                (folder_id, _now(), doc_id),
            )

    def delete_doc(self, doc_id):
        # blocks/doc_tag/todos/media cascade via FK ON DELETE CASCADE;
        # the tag rows themselves don't, so prune any left with no note.
        with _conn() as c:
            c.execute("DELETE FROM doc WHERE id = ?", (doc_id,))
            self._prune_orphan_tags(c)

    def _prune_orphan_tags(self, c) -> None:
        """Remove tags no note references any more (keeps the sidebar clean)."""
        c.execute("DELETE FROM tag WHERE id NOT IN (SELECT tag_id FROM doc_tag)")

    def delete_folder(self, folder_id):
        # notes in it keep existing (folder_id -> NULL via FK); subfolders cascade
        with _conn() as c:
            c.execute("DELETE FROM folder WHERE id = ?", (folder_id,))

    def list_tags(self):
        with _conn() as c:
            return [
                r["name"]
                for r in c.execute("SELECT name FROM tag ORDER BY name").fetchall()
            ]

    def _attach_tags(self, c, doc_id: int, tags: list[str]) -> None:
        for name in tags:
            c.execute("INSERT OR IGNORE INTO tag (name) VALUES (?)", (name,))
            tag_id = c.execute("SELECT id FROM tag WHERE name = ?", (name,)).fetchone()[
                "id"
            ]
            c.execute(
                "INSERT OR IGNORE INTO doc_tag (doc_id, tag_id) VALUES (?, ?)",
                (doc_id, tag_id),
            )

    # --- todos ---
    _NEXT_ORD = "(SELECT COALESCE(MAX(ord), 0) + 1 FROM todo)"

    def add_todos(self, doc_id: int, todos: list[dict]) -> None:
        with _conn() as c:
            for t in todos:
                c.execute(
                    f"INSERT INTO todo (doc_id, text, date, ord) VALUES (?, ?, ?, {self._NEXT_ORD})",
                    (doc_id, t["text"], t["date"]),
                )

    def list_todos(self):
        with _conn() as c:
            # standalone todos only (checklist items live under their list); manual
            # order via drag-to-reorder, newest additions default to the end
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM todo WHERE list_id IS NULL ORDER BY ord"
                ).fetchall()
            ]

    def set_todo_done(self, todo_id: int, done: bool) -> None:
        with _conn() as c:
            c.execute(
                "UPDATE todo SET done = ? WHERE id = ?", (1 if done else 0, todo_id)
            )

    def update_todo_text(self, todo_id: int, text: str) -> None:
        with _conn() as c:
            c.execute("UPDATE todo SET text = ? WHERE id = ?", (text, todo_id))

    def update_todo_details(self, todo_id: int, details: str) -> None:
        with _conn() as c:
            c.execute("UPDATE todo SET details = ? WHERE id = ?", (details, todo_id))

    def delete_todo(self, todo_id: int) -> None:
        with _conn() as c:
            c.execute("DELETE FROM todo WHERE id = ?", (todo_id,))

    def clear_todos(self, done=None) -> None:
        # only standalone todos; checklists are cleared via their own delete
        with _conn() as c:
            if done is None:
                c.execute("DELETE FROM todo WHERE list_id IS NULL")
            else:
                c.execute(
                    "DELETE FROM todo WHERE list_id IS NULL AND done = ?",
                    (1 if done else 0,),
                )

    def create_todo(self, text: str, date: str, doc_id=None) -> int:
        with _conn() as c:
            cur = c.execute(
                f"INSERT INTO todo (doc_id, text, date, ord) VALUES (?, ?, ?, {self._NEXT_ORD})",
                (doc_id, text, date),
            )
            return cur.lastrowid

    # --- checklists (todo_list + its items) ---
    def create_todo_list(self, name: str, doc_id, date: str, items: list[str]) -> int:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO todo_list (doc_id, name, date, ord) VALUES (?, ?, ?, "
                "(SELECT COALESCE(MAX(ord), 0) + 1 FROM todo_list))",
                (doc_id, name, date),
            )
            lid = cur.lastrowid
            for t in items:
                c.execute(
                    f"INSERT INTO todo (list_id, text, date, ord) VALUES (?, ?, ?, {self._NEXT_ORD})",
                    (lid, t, date),
                )
            return lid

    def list_todo_lists(self):
        with _conn() as c:
            lists = [
                dict(r)
                for r in c.execute("SELECT * FROM todo_list ORDER BY ord").fetchall()
            ]
            for lst in lists:
                lst["items"] = [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM todo WHERE list_id = ? ORDER BY ord", (lst["id"],)
                    ).fetchall()
                ]
            return lists

    def rename_todo_list(self, list_id: int, name: str) -> None:
        with _conn() as c:
            c.execute("UPDATE todo_list SET name = ? WHERE id = ?", (name, list_id))

    def delete_todo_list(self, list_id: int) -> None:
        with _conn() as c:
            c.execute("DELETE FROM todo WHERE list_id = ?", (list_id,))
            c.execute("DELETE FROM todo_list WHERE id = ?", (list_id,))

    def add_list_item(self, list_id: int, text: str, date: str) -> int:
        with _conn() as c:
            cur = c.execute(
                f"INSERT INTO todo (list_id, text, date, ord) VALUES (?, ?, ?, {self._NEXT_ORD})",
                (list_id, text, date),
            )
            return cur.lastrowid

    def reorder_todos(self, ids: list[int]) -> None:
        with _conn() as c:
            for i, tid in enumerate(ids):
                c.execute("UPDATE todo SET ord = ? WHERE id = ?", (i, tid))

    # --- media ---
    def add_media(
        self, doc_id: int, original_path: str, cropped_path: str | None = None
    ) -> None:
        with _conn() as c:
            c.execute(
                "INSERT INTO media (doc_id, original_path, cropped_path) VALUES (?, ?, ?)",
                (doc_id, original_path, cropped_path),
            )
