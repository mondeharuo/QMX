from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_files (
    id INTEGER PRIMARY KEY,
    source_relative_path TEXT NOT NULL UNIQUE,
    source_filename TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    source_mtime REAL NOT NULL,
    source_hash TEXT,
    output_relative_path TEXT,
    output_size INTEGER,
    status TEXT NOT NULL CHECK(status IN ('pending','processing','success','failed')),
    error_message TEXT,
    processed_time TEXT,
    processed_source_size INTEGER,
    processed_source_mtime REAL
);
CREATE INDEX IF NOT EXISTS idx_audio_files_status ON audio_files(status);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(audio_files)")}
            if "processed_source_size" not in columns:
                db.execute("ALTER TABLE audio_files ADD COLUMN processed_source_size INTEGER")
            if "processed_source_mtime" not in columns:
                db.execute("ALTER TABLE audio_files ADD COLUMN processed_source_mtime REAL")
            # Migrate earlier successful records. Failed attempts caused by an
            # existing-output collision had already produced a valid candidate;
            # their previous output mapping and size are retained in the DB.
            db.execute("""UPDATE audio_files SET
                processed_source_size=source_size, processed_source_mtime=source_mtime
                WHERE processed_source_size IS NULL AND status='success'
                  AND output_relative_path IS NOT NULL AND output_size IS NOT NULL""")
            db.execute("""UPDATE audio_files SET
                processed_source_size=source_size, processed_source_mtime=source_mtime
                WHERE processed_source_size IS NULL AND status='failed'
                  AND error_message LIKE 'Output already exists; refusing to overwrite:%'
                  AND output_relative_path IS NOT NULL AND output_size IS NOT NULL""")
            db.execute("UPDATE audio_files SET status='pending', error_message='Recovered interrupted processing' WHERE status='processing'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, relative_path: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM audio_files WHERE source_relative_path=?", (relative_path,)).fetchone()
            return dict(row) if row else None

    def upsert_source(self, relative_path: str, name: str, size: int, mtime: float, status: str = "pending"):
        with self.connect() as db:
            db.execute("""INSERT INTO audio_files
                (source_relative_path,source_filename,source_size,source_mtime,status)
                VALUES(?,?,?,?,?) ON CONFLICT(source_relative_path) DO UPDATE SET
                source_filename=excluded.source_filename,source_size=excluded.source_size,
                source_mtime=excluded.source_mtime,
                status=CASE WHEN audio_files.source_size!=excluded.source_size OR audio_files.source_mtime!=excluded.source_mtime
                            THEN 'pending' ELSE audio_files.status END""",
                (relative_path, name, size, mtime, status))

    def mark_processing(self, relative_path: str):
        with self.connect() as db:
            db.execute("UPDATE audio_files SET status='processing',error_message=NULL WHERE source_relative_path=?", (relative_path,))

    def mark_success(self, relative_path: str, output_relative: str, output_size: int):
        with self.connect() as db:
            db.execute("""UPDATE audio_files SET output_relative_path=?,output_size=?,status='success',
                error_message=NULL,processed_time=?,processed_source_size=source_size,
                processed_source_mtime=source_mtime WHERE source_relative_path=?""",
                       (output_relative, output_size, datetime.now().astimezone().isoformat(timespec="seconds"), relative_path))

    def mark_pending(self, relative_path: str, error: str | None = None):
        with self.connect() as db:
            db.execute("UPDATE audio_files SET status='pending',error_message=? WHERE source_relative_path=?",
                       (error, relative_path))

    def mark_failed(self, relative_path: str, error: str, source_hash: str | None = None):
        with self.connect() as db:
            db.execute("UPDATE audio_files SET status='failed',error_message=?,source_hash=COALESCE(?,source_hash),processed_time=? WHERE source_relative_path=?",
                       (error, source_hash, datetime.now().astimezone().isoformat(timespec="seconds"), relative_path))

    def pending_and_failed(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audio_files WHERE status IN ('pending','failed') ORDER BY source_relative_path")]
