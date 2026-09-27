from __future__ import annotations

import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .database import Database
from .scanner import FileEntry, OUTPUT_EXTENSIONS
from .verifier import verify_audio


def configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("QQmusicX")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def hash_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def process_one(entry: FileEntry, source_root: Path, output_root: Path, db: Database,
                qmdec: str, ffprobe: str, keep_structure: bool = True,
                keep_filename: bool = True, log_path: Path | None = None) -> tuple[str, str]:
    logger = configure_logging(log_path or (output_root.parent / "logs" / "app.log"))
    source = entry.source
    rel = entry.relative
    try:
        stat = source.stat()
        if stat.st_size != entry.size or stat.st_mtime != entry.mtime:
            raise RuntimeError("Source changed after scan; rescan before processing")

        # Avoid decrypting again when an output appeared after the scan. An
        # existing valid output is reusable only when the database ties it to
        # this exact source version; otherwise preserve it and report a conflict.
        if keep_filename:
            original_rel = Path(rel)
            for ext in OUTPUT_EXTENSIONS:
                output_rel = (original_rel.with_name(original_rel.stem + ext)
                              if keep_structure else Path(original_rel.stem + ext))
                destination = output_root / output_rel
                if not destination.is_file():
                    continue
                valid, why = verify_audio(ffprobe, destination)
                record = db.get(rel)
                same_processed_source = bool(record
                    and record.get("processed_source_size") == stat.st_size
                    and record.get("processed_source_mtime") == stat.st_mtime
                    and record.get("output_relative_path") == output_rel.as_posix())
                if valid and same_processed_source:
                    db.mark_success(rel, output_rel.as_posix(), destination.stat().st_size)
                    return "success", ""
                message = f"Output already exists; refusing to overwrite: {destination}"
                if not valid:
                    message += f" (existing output is invalid: {why})"
                elif not same_processed_source:
                    message += " (it is not recorded as belonging to this source version)"
                logger.warning("source=%s output=%s returncode= error=%s", source, destination, message)
                db.mark_failed(rel, message)
                return "failed", message

        db.mark_processing(rel)
        with tempfile.TemporaryDirectory(prefix=".qmdecx-", dir=output_root) as temp_name:
            temp_dir = Path(temp_name)
            proc = subprocess.run(
                [qmdec, "decrypt", str(source), "-o", str(temp_dir)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=None,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                result = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
            except json.JSONDecodeError:
                result = {}
            items = result.get("results", [])
            item = items[0] if items else {}
            if proc.returncode != 0 or not item.get("ok"):
                message = item.get("error") or proc.stderr.strip() or proc.stdout.strip() or f"qmdec exit {proc.returncode}"
                logger.error("source=%s output= returncode=%s error=%s", source, proc.returncode, message)
                db.mark_failed(rel, message[:1500], hash_file(source))
                return "failed", message[:1500]

            generated = Path(item.get("output", ""))
            valid, why = verify_audio(ffprobe, generated)
            if not valid:
                message = f"Output verification failed: {why}"
                logger.error("source=%s output=%s returncode=%s error=%s", source, generated, proc.returncode, message)
                db.mark_failed(rel, message, hash_file(source))
                return "failed", message

            extension = generated.suffix
            original_rel = Path(rel)
            out_name = original_rel.stem + extension if keep_filename else generated.name
            output_rel = (original_rel.parent / out_name) if keep_structure else Path(out_name)
            destination = output_root / output_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                existing_ok, _ = verify_audio(ffprobe, destination)
                if existing_ok:
                    message = f"Output already exists; refusing to overwrite: {destination}"
                    logger.warning("source=%s output=%s returncode=%s error=%s", source, destination, proc.returncode, message)
                    db.mark_failed(rel, message)
                    return "failed", message
                message = f"Invalid existing output; refusing to overwrite: {destination}"
                logger.error("source=%s output=%s returncode=%s error=%s", source, destination, proc.returncode, message)
                db.mark_failed(rel, message)
                return "failed", message
            shutil.move(str(generated), str(destination))
            out_size = destination.stat().st_size
            db.mark_success(rel, output_rel.as_posix(), out_size)
            return "success", ""
    except Exception as exc:
        message = str(exc)[:1500]
        logger.exception("source=%s output= returncode= error=%s", source, message)
        db.mark_failed(rel, message, hash_file(source) if source.exists() else None)
        return "failed", message
