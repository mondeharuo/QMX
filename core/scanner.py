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
    source_exists: bool = True
    output_exists: bool = False
    history_status: str = ""
    processed_time: str = ""
    last_attempt_time: str = ""
    first_seen_time: str = ""
    last_seen_time: str = ""


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


def _inspect_outputs(output: Path, candidates: list[Path], ffprobe: str,
                     record: dict | None = None) -> tuple[Path | None, tuple[Path, str] | None]:
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
        # Trust an output only when this exact file was already successfully
        # verified and its size plus nanosecond timestamp are unchanged.
        # Any mutation or missing cache fingerprint falls back to ffprobe.
        if (record and record.get("status") == "success"
                and record.get("output_relative_path") == relative.as_posix()):
            try:
                stat = candidate.stat()
                if (stat.st_size == record.get("output_size")
                        and stat.st_mtime_ns == record.get("output_mtime_ns")):
                    return relative, None
            except OSError:
                pass
        valid, reason = verify_audio(ffprobe, candidate)
        if valid:
            return relative, None
        if first_invalid is None:
            first_invalid = (relative, reason)
    return None, first_invalid


def scan(source: Path, output: Path, db: Database, ffprobe: str, copy_lrc: bool = True,
         keep_structure: bool = True) -> ScanResult:
    # scandir avoids repeatedly resolving Path objects and does not follow
    # directory symlinks, preventing cycles in user libraries.
    all_files: list[Path] = []
    pending_dirs = [source]
    while pending_dirs:
        current_dir = pending_dirs.pop()
        try:
            with os.scandir(current_dir) as children:
                for child in children:
                    try:
                        if child.is_dir(follow_symlinks=False):
                            pending_dirs.append(Path(child.path))
                        elif child.is_file(follow_symlinks=False):
                            all_files.append(Path(child.path))
                    except OSError:
                        continue
        except OSError:
            continue
    all_files.sort(key=lambda p: str(p).casefold())
    audio_files = sorted((p for p in all_files if p.suffix.lower() in SUPPORTED), key=lambda p: str(p).casefold())
    lrc_files = [p for p in all_files if p.suffix.lower() == ".lrc"]
    lrc_copied = sync_lrcs(source, output, lrc_files, keep_structure) if copy_lrc else 0
    entries: list[FileEntry] = []
    success_updates = []
    counts = {"pending": 0, "success": 0, "skipped": 0, "failed": 0, "output_missing": 0}

    records_by_path = {record["source_relative_path"]: record for record in db.all_records()}
    original_paths = set(records_by_path)
    observations = []
    changed_paths = set()
    source_stats = {}
    for src in audio_files:
        rel = src.relative_to(source).as_posix()
        stat = src.stat()
        source_stats[rel] = stat
        record = records_by_path.get(rel)
        changed = bool(record and (record["source_size"] != stat.st_size
                                   or record["source_mtime"] != stat.st_mtime))
        source_path_needs_refresh = bool(record and not Path(record.get("source_path") or "").is_absolute())
        if record is None or changed or source_path_needs_refresh:
            observations.append((rel, src.name, stat.st_size, stat.st_mtime, str(src), "pending"))
        if changed:
            changed_paths.add(rel)
    if observations:
        db.upsert_sources(observations)
        records_by_path = {record["source_relative_path"]: record for record in db.all_records()}

    for src in audio_files:
        rel_path = src.relative_to(source)
        rel = rel_path.as_posix()
        stat = source_stats[rel]
        record = records_by_path.get(rel)
        was_untracked = rel not in original_paths
        same_scanned_source = bool(record and record["source_size"] == stat.st_size and record["source_mtime"] == stat.st_mtime)
        same_processed_source = bool(record and record.get("processed_source_size") == stat.st_size
                                     and record.get("processed_source_mtime") == stat.st_mtime)
        # Reconcile the database with disk on every scan. A prior failure can
        # still have a valid output (for example, retry refused to overwrite it).
        # Only trust outputs for the same source version or a recorded successful
        # source fingerprint; changed sources must not inherit a stale FLAC.
        source_conflict = bool(record and record["status"] == "failed"
                               and (record["error_message"] or "").startswith(
                                   "Source changed since the recorded output was produced;"))
        source_version_known = was_untracked or same_processed_source or (
            same_scanned_source and rel not in changed_paths and not source_conflict)
        candidates = _output_candidates(rel_path, keep_structure,
                                        record["output_relative_path"] if record else None)
        output_rel, invalid_output = (None, None)
        if source_version_known:
            output_rel, invalid_output = _inspect_outputs(output, candidates, ffprobe, record)

        status, error = "pending", ""
        if output_rel is not None:
            status = "success"
            output_size = (output / output_rel).stat().st_size
            if (not record or record["status"] != "success"
                    or record["output_relative_path"] != output_rel.as_posix()
                    or record["output_size"] != output_size
                    or record.get("output_mtime_ns") != (output / output_rel).stat().st_mtime_ns
                    or record.get("output_path") != str(output / output_rel)):
                success_updates.append((rel, output_rel.as_posix(), output_size,
                                        str(output / output_rel), None))
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
        elif record and record["status"] == "success":
            # A successful historical run stays successful in the database.
            # Missing output is a current file-presence condition, not loss of
            # the processing history and not an automatic retry request.
            status = "output_missing"
            error = "Previously processed output is missing"
        else:
            status = "pending"
            if record and record["status"] != "pending":
                db.mark_pending(rel, "Previously recorded output is missing; it can be regenerated.")

        output_exists = bool(output_rel and (output / output_rel).is_file())
        entries.append(FileEntry(src, rel, src.name, src.suffix[1:].upper(), stat.st_size,
                                 stat.st_mtime, status, output_rel.as_posix() if output_rel else ((record or {}).get("output_relative_path") or ""),
                                 error, True, output_exists,
                                 (record or {}).get("status", ""),
                                 (record or {}).get("processed_time") or "",
                                 (record or {}).get("last_attempt_time") or "",
                                 (record or {}).get("first_seen_time") or "",
                                 (record or {}).get("last_seen_time") or ""))
        counts[status] += 1

    seen_paths = {p.relative_to(source).as_posix() for p in audio_files}
    db.mark_success_many(success_updates)
    db.reconcile_presence(seen_paths, output)
    # Keep database-only records visible after source removal. Their last
    # processing status and timestamps remain intact; filesystem presence is
    # represented separately in FileEntry.
    for record in db.all_records():
        rel = record["source_relative_path"]
        if rel in seen_paths:
            continue
        source_path = Path(record.get("source_path") or (source / Path(rel)))
        output_rel = record.get("output_relative_path") or ""
        output_exists = bool(output_rel and (output / output_rel).is_file())
        status = "history_source_missing"
        if record["status"] == "success" and not output_exists:
            status = "history_files_missing"
        entries.append(FileEntry(source_path, rel, record.get("source_filename") or Path(rel).name,
                                 Path(rel).suffix[1:].upper(), int(record.get("source_size") or 0),
                                 float(record.get("source_mtime") or 0), status, output_rel,
                                 record.get("error_message") or "", False, output_exists,
                                 record.get("status") or "", record.get("processed_time") or "",
                                 record.get("last_attempt_time") or "",
                                 record.get("first_seen_time") or "",
                                 record.get("last_seen_time") or ""))

    unsupported_count = sum(1 for p in all_files if p.suffix.lower() not in SUPPORTED and p.suffix.lower() != ".lrc")
    unsupported = [FileEntry(p, p.relative_to(source).as_posix(), p.name, p.suffix[1:].upper() or "FILE",
                             p.stat().st_size, p.stat().st_mtime, "unsupported")
                   for p in all_files if p.suffix.lower() not in SUPPORTED and p.suffix.lower() != ".lrc"]
    entries.extend(unsupported)
    return ScanResult(entries, len(all_files), len(audio_files), counts["pending"], counts["success"],
                      counts["skipped"], counts["failed"], unsupported_count, lrc_copied)
