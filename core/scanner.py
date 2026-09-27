from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import tempfile

from .database import Database
from .verifier import verify_audio


SUPPORTED = {".mflac", ".mgg", ".qmc0", ".qmc2", ".qmc3", ".qmcflac", ".qmcogg"}
OUTPUT_EXTENSIONS = (".flac", ".ogg", ".mp3", ".m4a", ".wav", ".bin")


@dataclass
class FileEntry:
    source: Path
    relative: str
    filename: str
    extension: str
    size: int
    mtime: float
    status: str
    output_relative: str = ""
    error: str = ""


@dataclass
class ScanResult:
    entries: list[FileEntry]
    total_files: int
    supported_count: int
    new_count: int
    done_count: int
    skipped_count: int
    failed_count: int
    unsupported_count: int
    lrc_copied: int


def sync_lrcs(source: Path, output: Path, files: list[Path], keep_structure: bool) -> int:
    copied = 0
    for src in files:
        try:
            rel = src.relative_to(source)
            dst = output / rel if keep_structure else output / rel.name
            if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.read_bytes() == src.read_bytes():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".lrc-", dir=dst.parent)
            os.close(fd)
            tmp = Path(temp_name)
            try:
                shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
            finally:
                tmp.unlink(missing_ok=True)
            copied += 1
        except OSError:
            continue
    return copied


def _output_candidates(relative: Path, keep_structure: bool, recorded: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if recorded:
        stored = Path(recorded)
        if not stored.is_absolute() and ".." not in stored.parts:
            candidates.append(stored)
    for ext in OUTPUT_EXTENSIONS:
        candidate = relative.with_name(relative.stem + ext) if keep_structure else Path(relative.stem + ext)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _inspect_outputs(output: Path, candidates: list[Path], ffprobe: str) -> tuple[Path | None, tuple[Path, str] | None]:
    first_invalid: tuple[Path, str] | None = None
    root = output.resolve()
    for relative in candidates:
        candidate = output / relative
        try:
            resolved = candidate.resolve()
            if root not in resolved.parents or not candidate.is_file():
                continue
        except OSError:
            continue
        valid, reason = verify_audio(ffprobe, candidate)
        if valid:
            return relative, None
        if first_invalid is None:
            first_invalid = (relative, reason)
    return None, first_invalid


def scan(source: Path, output: Path, db: Database, ffprobe: str, copy_lrc: bool = True,
         keep_structure: bool = True) -> ScanResult:
    all_files = [p for p in source.rglob("*") if p.is_file()]
    audio_files = sorted((p for p in all_files if p.suffix.lower() in SUPPORTED), key=lambda p: str(p).casefold())
    lrc_files = [p for p in all_files if p.suffix.lower() == ".lrc"]
    lrc_copied = sync_lrcs(source, output, lrc_files, keep_structure) if copy_lrc else 0
    entries: list[FileEntry] = []
    counts = {"pending": 0, "success": 0, "skipped": 0, "failed": 0}

    for src in audio_files:
        rel_path = src.relative_to(source)
        rel = rel_path.as_posix()
        stat = src.stat()
        record = db.get(rel)
        was_untracked = record is None
        same_scanned_source = bool(record and record["source_size"] == stat.st_size and record["source_mtime"] == stat.st_mtime)
        same_processed_source = bool(record and record.get("processed_source_size") == stat.st_size
                                     and record.get("processed_source_mtime") == stat.st_mtime)
        if record is None or not same_scanned_source:
            db.upsert_source(rel, src.name, stat.st_size, stat.st_mtime)
            record = db.get(rel)

        # Reconcile the database with disk on every scan. A prior failure can
        # still have a valid output (for example, retry refused to overwrite it).
        # Only trust outputs for the same source version or a recorded successful
        # source fingerprint; changed sources must not inherit a stale FLAC.
        source_conflict = bool(record and record["status"] == "failed"
                               and (record["error_message"] or "").startswith(
                                   "Source changed since the recorded output was produced;"))
        source_version_known = was_untracked or same_processed_source or (same_scanned_source and not source_conflict)
        candidates = _output_candidates(rel_path, keep_structure,
                                        record["output_relative_path"] if record else None)
        output_rel, invalid_output = (None, None)
        if source_version_known:
            output_rel, invalid_output = _inspect_outputs(output, candidates, ffprobe)

        status, error = "pending", ""
        if output_rel is not None:
            status = "success"
            output_size = (output / output_rel).stat().st_size
            if (not record or record["status"] != "success"
                    or record["output_relative_path"] != output_rel.as_posix()
                    or record["output_size"] != output_size):
                db.mark_success(rel, output_rel.as_posix(), output_size)
        elif invalid_output is not None:
            invalid_rel, why = invalid_output
            status = "failed"
            error = f"Existing output is invalid and was left untouched: {invalid_rel} ({why})"
            db.mark_failed(rel, error)
        elif record and not source_version_known:
            # A valid file with the same name may belong to an older source
            # version. Preserve it and make the collision explicit.
            existing = next((candidate for candidate in candidates if (output / candidate).is_file()), None)
            if existing is not None:
                status = "failed"
                error = ("Source changed since the recorded output was produced; "
                         f"existing output was left untouched: {existing}")
                db.mark_failed(rel, error)
            else:
                db.mark_pending(rel)
        elif record and record["status"] == "failed":
            status = "failed"
            error = record["error_message"] or "Previous attempt failed"
        else:
            status = "pending"
            if record and record["status"] != "pending":
                db.mark_pending(rel, "Previously recorded output is missing; it can be regenerated.")

        entries.append(FileEntry(src, rel, src.name, src.suffix[1:].upper(), stat.st_size, stat.st_mtime, status, output_rel, error))
        counts[status] += 1

    unsupported_count = sum(1 for p in all_files if p.suffix.lower() not in SUPPORTED and p.suffix.lower() != ".lrc")
    unsupported = [FileEntry(p, p.relative_to(source).as_posix(), p.name, p.suffix[1:].upper() or "FILE",
                             p.stat().st_size, p.stat().st_mtime, "unsupported")
                   for p in all_files if p.suffix.lower() not in SUPPORTED and p.suffix.lower() != ".lrc"]
    entries.extend(unsupported)
    return ScanResult(entries, len(all_files), len(audio_files), counts["pending"], counts["success"],
                      counts["skipped"], counts["failed"], unsupported_count, lrc_copied)
