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
            # Add durable library/history fields without replacing the existing
            # processing table, preserving all earlier records in place.
            additions = {
                "song_id": "TEXT",
                "title": "TEXT",
                "artist": "TEXT",
                "album": "TEXT",
                "duration": "REAL",
                "source_path": "TEXT",
                "output_path": "TEXT",
                "output_filename": "TEXT",
                "output_mtime_ns": "INTEGER",
                "first_seen_time": "TEXT",
                "last_seen_time": "TEXT",
                "last_attempt_time": "TEXT",
                "exported_time": "TEXT",
                "source_exists": "INTEGER NOT NULL DEFAULT 1",
                "output_exists": "INTEGER NOT NULL DEFAULT 0",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE audio_files ADD COLUMN {name} {declaration}")
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            db.execute("UPDATE audio_files SET first_seen_time=COALESCE(first_seen_time, ?), last_seen_time=COALESCE(last_seen_time, ?)", (now, now))
            db.execute("UPDATE audio_files SET output_exists=CASE WHEN output_relative_path IS NOT NULL THEN 1 ELSE 0 END WHERE output_exists=0")
            db.execute("UPDATE audio_files SET source_path=source_relative_path WHERE source_path IS NULL")
            # Older builds used processed_time for both successes and failures.
            # Preserve failed-attempt timestamps separately during migration.
            db.execute("UPDATE audio_files SET last_attempt_time=COALESCE(last_attempt_time,processed_time), processed_time=NULL WHERE status='failed' AND processed_time IS NOT NULL")
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

    def upsert_source(self, relative_path: str, name: str, size: int, mtime: float,
                      status: str = "pending", absolute_path: str | None = None):
        self.upsert_sources([(relative_path, name, size, mtime, absolute_path, status)])

    def upsert_sources(self, sources: list[tuple[str, str, int, float, str | None, str]]):
        if not sources:
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        prepared = []
        for relative_path, name, size, mtime, absolute_path, status in sources:
            stem = Path(name).stem
            if " - " in stem:
                artist, title = stem.split(" - ", 1)
            else:
                artist, title = None, stem
            prepared.append((relative_path, name, size, mtime, status, absolute_path, now, now, title, artist))
        with self.connect() as db:
            db.executemany("""INSERT INTO audio_files
                (source_relative_path,source_filename,source_size,source_mtime,status,source_path,first_seen_time,last_seen_time,source_exists,title,artist)
                VALUES(?,?,?,?,?,?,?,?,1,?,?) ON CONFLICT(source_relative_path) DO UPDATE SET
                source_filename=excluded.source_filename,source_size=excluded.source_size,
                source_path=COALESCE(excluded.source_path,audio_files.source_path),
                last_seen_time=excluded.last_seen_time,source_exists=1,
                title=COALESCE(audio_files.title,excluded.title),artist=COALESCE(audio_files.artist,excluded.artist),
                source_mtime=excluded.source_mtime,
                status=CASE WHEN audio_files.source_size!=excluded.source_size OR audio_files.source_mtime!=excluded.source_mtime
                            THEN 'pending' ELSE audio_files.status END""",
                prepared)

    def mark_processing(self, relative_path: str):
        with self.connect() as db:
            db.execute("UPDATE audio_files SET status='processing',error_message=NULL,last_attempt_time=?,retry_count=retry_count+1 WHERE source_relative_path=?",
                       (datetime.now().astimezone().isoformat(timespec="seconds"), relative_path))

    def mark_success(self, relative_path: str, output_relative: str, output_size: int,
                     output_path: str | None = None, duration: float | None = None):
        self.mark_success_many([(relative_path, output_relative, output_size, output_path, duration)])

    def mark_success_many(self, outputs: list[tuple[str, str, int, str | None, float | None]]):
        if not outputs:
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        prepared = []
        for relative_path, output_relative, output_size, output_path, duration in outputs:
            output_mtime_ns = None
            if output_path:
                try:
                    output_mtime_ns = Path(output_path).stat().st_mtime_ns
                except OSError:
                    pass
            prepared.append((output_relative, output_size, output_path, Path(output_relative).name,
                             output_mtime_ns, duration, now, relative_path))
        with self.connect() as db:
            db.executemany("""UPDATE audio_files SET output_relative_path=?,output_size=?,status='success',
                output_path=COALESCE(?,output_path),output_filename=?,output_mtime_ns=COALESCE(?,output_mtime_ns),output_exists=1,
                duration=COALESCE(?,duration),error_message=NULL,processed_time=?,processed_source_size=source_size,
                processed_source_mtime=source_mtime WHERE source_relative_path=?""",
                       prepared)

    def mark_pending(self, relative_path: str, error: str | None = None):
        with self.connect() as db:
            db.execute("UPDATE audio_files SET status='pending',error_message=? WHERE source_relative_path=?",
                       (error, relative_path))

    def mark_failed(self, relative_path: str, error: str, source_hash: str | None = None):
        with self.connect() as db:
            db.execute("UPDATE audio_files SET status='failed',error_message=?,source_hash=COALESCE(?,source_hash),last_attempt_time=COALESCE(last_attempt_time,?) WHERE source_relative_path=?",
                       (error, source_hash, datetime.now().astimezone().isoformat(timespec="seconds"), relative_path))

    def all_records(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audio_files ORDER BY source_relative_path")]

    def reconcile_presence(self, seen_paths: set[str], output_root: Path):
        """Update current presence flags while retaining historical status."""
        with self.connect() as db:
            records = db.execute("SELECT source_relative_path,output_relative_path FROM audio_files").fetchall()
            for row in records:
                rel = row["source_relative_path"]
                output_rel = row["output_relative_path"]
                output_exists = bool(output_rel and (output_root / output_rel).is_file())
                db.execute("UPDATE audio_files SET source_exists=?,output_exists=? WHERE source_relative_path=?",
                           (int(rel in seen_paths), int(output_exists), rel))

    def set_presence(self, relative_path: str, *, source_exists: bool | None = None,
                     output_exists: bool | None = None):
        fields = []
        values = []
        if source_exists is not None:
            fields.append("source_exists=?")
            values.append(int(source_exists))
        if output_exists is not None:
            fields.append("output_exists=?")
            values.append(int(output_exists))
        if not fields:
            return
        values.append(relative_path)
        with self.connect() as db:
            db.execute(f"UPDATE audio_files SET {', '.join(fields)} WHERE source_relative_path=?", values)

    def pending_and_failed(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audio_files WHERE status IN ('pending','failed') ORDER BY source_relative_path")]
