"""Local history: analyzed documents saved in a small SQLite file on this computer.

File: ~/.paperwork_pilot/history.db
The user can turn saving off in Settings and can delete everything with one click.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .config import data_dir


def _db_path():
    return data_dir() / "history.db"


@contextmanager
def _conn():
    path = _db_path()
    fresh = not path.exists()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if fresh:
        try:
            os.chmod(path, 0o600)  # document text is private to this user
        except OSError:
            pass
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                created REAL NOT NULL,
                filename TEXT,
                title TEXT,
                doc_type TEXT,
                text TEXT NOT NULL,
                result TEXT NOT NULL
            )"""
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def save(filename: str, text: str, result: dict) -> str:
    doc_id = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            "INSERT INTO documents (id, created, filename, title, doc_type, text, result) VALUES (?,?,?,?,?,?,?)",
            (doc_id, time.time(), filename, result.get("title", ""), result.get("document_type", ""), text, json.dumps(result)),
        )
    return doc_id


def get(doc_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "created": row["created"],
        "filename": row["filename"],
        "title": row["title"],
        "doc_type": row["doc_type"],
        "text": row["text"],
        "result": json.loads(row["result"]),
    }


def update(doc_id: str, result: dict) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE documents SET result = ?, title = ?, doc_type = ? WHERE id = ?",
            (json.dumps(result), result.get("title", ""), result.get("document_type", ""), doc_id),
        )
        return cur.rowcount > 0


def list_recent(limit: int = 30) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, created, filename, title, doc_type FROM documents ORDER BY created DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete(doc_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return cur.rowcount > 0


def delete_all() -> int:
    with _conn() as c:
        cur = c.execute("DELETE FROM documents")
        return cur.rowcount
