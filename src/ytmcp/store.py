"""The transcript history: one SQLite table plus one .md file per transcript."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ytmcp.youtube import Fetched

FILE_NAME = re.compile(r"^[A-Za-z0-9_-]{11}\.[a-z]{2,3}\.md$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    language TEXT NOT NULL,
    is_original INTEGER NOT NULL,
    track_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    duration_seconds INTEGER,
    published TEXT,
    file_name TEXT NOT NULL,
    chars INTEGER NOT NULL,
    words INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (video_id, language)
);
"""

_COLUMNS = ("video_id, language, is_original, track_kind, title, channel, duration_seconds, "
            "published, file_name, chars, words, created_at")


@dataclass(frozen=True)
class TranscriptRecord:
    video_id: str
    language: str
    is_original: bool
    track_kind: str
    title: str
    channel: str
    duration_seconds: int | None
    published: str | None
    file_name: str
    chars: int
    words: int
    created_at: str


def _record(row: tuple) -> TranscriptRecord:
    values = list(row)
    values[2] = bool(values[2])
    return TranscriptRecord(*values)


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.files = Path(data_dir) / "transcripts"
        self.files.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(data_dir) / "transcripts.db"
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _one(self, where: str, params: tuple) -> TranscriptRecord | None:
        """The newest matching record whose file still exists. A row whose file was
        deleted by hand is dropped here, so the video can be transcribed again."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, {_COLUMNS} FROM transcripts WHERE {where} ORDER BY created_at DESC, id DESC",
                params).fetchall()
            for row_id, *values in rows:
                record = _record(tuple(values))
                if (self.files / record.file_name).is_file():
                    return record
                conn.execute("DELETE FROM transcripts WHERE id = ?", (row_id,))
        return None

    def find(self, video_id: str, language: str | None) -> TranscriptRecord | None:
        if language is None:
            return self._one("video_id = ? AND is_original = 1", (video_id,))
        return self._one("video_id = ? AND language = ?", (video_id, language))

    def latest(self, video_id: str) -> TranscriptRecord | None:
        return self._one("video_id = ?", (video_id,))

    def by_file_name(self, file_name: str) -> TranscriptRecord | None:
        if not FILE_NAME.fullmatch(file_name):
            return None
        return self._one("file_name = ?", (file_name,))

    def save(self, *, fetched: Fetched, markdown: str, is_original: bool, now: datetime) -> TranscriptRecord:
        language = fetched.track.language
        file_name = f"{fetched.video_id}.{language}.md"
        if not FILE_NAME.fullmatch(file_name):
            raise ValueError(f"unsafe file name: {file_name!r}")
        self._write_atomic(self.files / file_name, markdown)
        values = (fetched.video_id, language, int(is_original), fetched.track.kind, fetched.title,
                  fetched.channel, fetched.duration_seconds, fetched.published, file_name,
                  len(markdown), len(markdown.split()), now.isoformat())
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO transcripts ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (video_id, language) DO UPDATE SET "
                "is_original = MAX(transcripts.is_original, excluded.is_original), "
                "track_kind = excluded.track_kind, title = excluded.title, channel = excluded.channel, "
                "duration_seconds = excluded.duration_seconds, published = excluded.published, "
                "file_name = excluded.file_name, chars = excluded.chars, words = excluded.words, "
                "created_at = excluded.created_at",
                values)
        found = self.find(fetched.video_id, language)
        assert found is not None
        return found

    def list(self, search: str | None = None, limit: int = 20) -> list[TranscriptRecord]:
        """Newest first. Search is a case-insensitive substring of title or channel,
        done in Python so non-ASCII titles match too (SQLite's lower() is ASCII-only)."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM transcripts ORDER BY created_at DESC, id DESC").fetchall()
        records = [_record(r) for r in rows]
        if search and search.strip():
            needle = search.strip().casefold()
            records = [r for r in records if needle in r.title.casefold() or needle in r.channel.casefold()]
        return records[:max(limit, 0)]

    def read_markdown(self, record: TranscriptRecord) -> str:
        return (self.files / record.file_name).read_text(encoding="utf-8")

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
