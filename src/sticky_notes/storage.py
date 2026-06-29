from __future__ import annotations

import os
import re
import sqlite3
from html import unescape
from datetime import datetime, timezone
from pathlib import Path

from sticky_notes.models import Note


APP_NAME = "StickyNotes"
DEFAULT_COLORS = ["#fff4a3", "#ffd4dc", "#cfe8ff", "#d8f6d0", "#ffe1b8", "#f0d9ff"]
DEFAULT_REMINDER_MINUTES = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_db_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_NAME / "notes.db"


def html_to_text(value: str) -> str:
    text = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", " ", value or "", flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [" ".join(line.split()) for line in unescape(text).splitlines()]
    return "\n".join(line for line in lines if line)


class NoteStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body_html TEXT NOT NULL,
                color TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                is_pinned INTEGER NOT NULL DEFAULT 0,
                is_priority INTEGER NOT NULL DEFAULT 0,
                reminder_minutes INTEGER NOT NULL DEFAULT 5,
                reminder_start_at TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(notes)").fetchall()
        }
        if "is_priority" not in columns:
            self.connection.execute("ALTER TABLE notes ADD COLUMN is_priority INTEGER NOT NULL DEFAULT 0")
        if "reminder_minutes" not in columns:
            self.connection.execute("ALTER TABLE notes ADD COLUMN reminder_minutes INTEGER NOT NULL DEFAULT 5")
        if "reminder_start_at" not in columns:
            self.connection.execute("ALTER TABLE notes ADD COLUMN reminder_start_at TEXT NOT NULL DEFAULT ''")
        if "deleted_at" not in columns:
            self.connection.execute("ALTER TABLE notes ADD COLUMN deleted_at TEXT NOT NULL DEFAULT ''")
        self.connection.commit()

    def list_notes(self, query: str = "") -> list[Note]:
        query = query.strip().lower()
        rows = self.connection.execute(
            """
            SELECT id, title, body_html, color, tags, is_pinned, is_priority, reminder_minutes,
                   reminder_start_at, sort_order, created_at, updated_at, deleted_at
            FROM notes
            WHERE deleted_at = ''
            ORDER BY is_priority DESC, is_pinned DESC, sort_order ASC, updated_at DESC
            """
        ).fetchall()
        notes = [self._row_to_note(row) for row in rows]
        if not query:
            return notes
        return [
            note
            for note in notes
            if query in note.title.lower()
            or query in html_to_text(note.body_html).lower()
            or query in note.tags.lower()
        ]

    def list_trashed_notes(self) -> list[Note]:
        rows = self.connection.execute(
            """
            SELECT id, title, body_html, color, tags, is_pinned, is_priority, reminder_minutes,
                   reminder_start_at, sort_order, created_at, updated_at, deleted_at
            FROM notes
            WHERE deleted_at <> ''
            ORDER BY deleted_at DESC
            """
        ).fetchall()
        return [self._row_to_note(row) for row in rows]

    def create_note(self) -> Note:
        timestamp = now_iso()
        max_sort = self.connection.execute("SELECT COALESCE(MAX(sort_order), -1) FROM notes").fetchone()[0]
        cursor = self.connection.execute(
            """
            INSERT INTO notes (
                title, body_html, color, tags, is_pinned, is_priority, reminder_minutes, reminder_start_at,
                sort_order, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Untitled note",
                "",
                DEFAULT_COLORS[0],
                "",
                0,
                0,
                DEFAULT_REMINDER_MINUTES,
                "",
                max_sort + 1,
                timestamp,
                timestamp,
            ),
        )
        self.connection.commit()
        return self.get_note(int(cursor.lastrowid))

    def get_note(self, note_id: int) -> Note:
        row = self.connection.execute(
            """
            SELECT id, title, body_html, color, tags, is_pinned, is_priority, reminder_minutes,
                   reminder_start_at, sort_order, created_at, updated_at, deleted_at
            FROM notes
            WHERE id = ?
            """,
            (note_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Note {note_id} was not found")
        return self._row_to_note(row)

    def save_note(self, note: Note) -> Note:
        if note.id is None:
            raise ValueError("Cannot save a note without an id")
        timestamp = now_iso()
        self.connection.execute(
            """
            UPDATE notes
            SET title = ?, body_html = ?, color = ?, tags = ?, is_pinned = ?, is_priority = ?,
                reminder_minutes = ?, reminder_start_at = ?, sort_order = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                note.title.strip() or "Untitled note",
                note.body_html,
                note.color,
                note.tags.strip(),
                int(note.is_pinned),
                int(note.is_priority),
                note.reminder_minutes,
                note.reminder_start_at,
                note.sort_order,
                timestamp,
                note.id,
            ),
        )
        self.connection.commit()
        return self.get_note(note.id)

    def delete_note(self, note_id: int) -> None:
        timestamp = now_iso()
        self.connection.execute("UPDATE notes SET deleted_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, note_id))
        self.connection.commit()

    def restore_note(self, note_id: int) -> None:
        timestamp = now_iso()
        self.connection.execute("UPDATE notes SET deleted_at = '', updated_at = ? WHERE id = ?", (timestamp, note_id))
        self.connection.commit()

    def permanently_delete_note(self, note_id: int) -> None:
        self.connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.connection.commit()

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> Note:
        return Note(
            id=int(row["id"]),
            title=row["title"],
            body_html=row["body_html"],
            color=row["color"],
            tags=row["tags"],
            is_pinned=bool(row["is_pinned"]),
            is_priority=bool(row["is_priority"]),
            reminder_minutes=int(row["reminder_minutes"] or DEFAULT_REMINDER_MINUTES),
            reminder_start_at=row["reminder_start_at"] or "",
            sort_order=int(row["sort_order"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"] or "",
        )
