"""Checkpointed born-digital text extraction using a bounded PDF parser.

This is NOT OCR. A single isolated parser processes only pages missing a
SHA-verified artifact, under one local fenced worker lease. The caller must
provide an application-private artifact directory on the same local host.
"""
from __future__ import annotations

import math
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Callable

from .bounded_parser import IsolatedPdfReader
from .jobs import JobJournal, JobSafetyError
from .worker_lease import exclusive_artifact_directory_lock


DEFAULT_ORPHAN_MIN_AGE_SECONDS = 24 * 60 * 60


def _fsync_directory(directory: Path) -> None:
    """Persist directory-entry changes where POSIX supports directory fsync."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cleanup_orphaned_native_text_artifacts_locked(
    journal: JobJournal,
    folder: Path,
    *,
    min_age_seconds: float,
    now: float | None = None,
) -> dict:
    if (isinstance(min_age_seconds, bool)
            or not isinstance(min_age_seconds, (int, float))
            or not math.isfinite(min_age_seconds)
            or min_age_seconds < 0):
        raise ValueError("min_age_seconds must be a finite non-negative number")
    current_time = time.time() if now is None else now
    if (isinstance(current_time, bool) or not isinstance(current_time, (int, float))
            or not math.isfinite(current_time)):
        raise ValueError("now must be a finite timestamp")
    cutoff = float(current_time) - float(min_age_seconds)

    referenced_paths: set[Path] = set()
    referenced_inodes: set[tuple[int, int]] = set()
    with journal._open() as db:
        rows = db.execute("SELECT artifact FROM checkpoints").fetchall()
    for row in rows:
        try:
            path = Path(row["artifact"]).resolve(strict=True)
            details = path.stat()
        except OSError:
            continue
        if stat.S_ISREG(details.st_mode):
            referenced_paths.add(path)
            referenced_inodes.add((details.st_dev, details.st_ino))

    removed: list[str] = []
    bytes_removed = 0
    remaining_orphan_bytes = 0
    errors: list[str] = []
    # Only names emitted by _save_page_text are candidates. Never recurse,
    # follow symlinks, or delete an artifact referenced by any job/stage.
    for pattern in ("page-*.txt", ".text-*.tmp"):
        for candidate in folder.glob(pattern):
            try:
                before = candidate.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(before.st_mode):
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            inode = (before.st_dev, before.st_ino)
            if resolved in referenced_paths or inode in referenced_inodes:
                continue
            if before.st_mtime > cutoff:
                remaining_orphan_bytes += before.st_size
                continue
            try:
                after = candidate.lstat()
                if (not stat.S_ISREG(after.st_mode)
                        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)):
                    if stat.S_ISREG(after.st_mode):
                        remaining_orphan_bytes += after.st_size
                    continue
                candidate.unlink()
                removed.append(str(candidate))
                bytes_removed += before.st_size
            except OSError:
                errors.append(str(candidate))
                remaining_orphan_bytes += before.st_size
    if removed:
        _fsync_directory(folder)
    return {
        "removed": tuple(removed),
        "bytes_removed": bytes_removed,
        "remaining_orphan_bytes": remaining_orphan_bytes,
        "errors": tuple(errors),
    }


def cleanup_orphaned_native_text_artifacts(
    journal: JobJournal,
    artifact_directory: str | Path,
    *,
    min_age_seconds: float = DEFAULT_ORPHAN_MIN_AGE_SECONDS,
    now: float | None = None,
) -> dict:
    """Safely prune stale, unreferenced artifacts from a private local stage folder.

    A directory lock excludes cooperating stage writers. Only old page-*.txt
    and .text-*.tmp regular files are eligible. Registered paths and hard-link
    aliases of registered files are preserved. Recent orphans remain and are
    included in the byte budget; the default grace period is 24 hours.
    """
    folder = Path(artifact_directory).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("artifact_directory must be a directory")
    with exclusive_artifact_directory_lock(folder):
        return _cleanup_orphaned_native_text_artifacts_locked(
            journal, folder, min_age_seconds=min_age_seconds, now=now
        )


def _save_page_text(directory: Path, page: int, text: str) -> Path:
    """Durably create a unique checkpoint artifact without clobbering any path.

    O_EXCL reserves the final random name atomically. The artifact is not
    registered in SQLite until this function returns, so a crash during the
    write leaves only an unreferenced orphan for the age-gated cleanup path.
    Failure cleanup is identity-fenced: a concurrently substituted path is
    never unlinked as if it were the file created here.
    """
    final = directory / ("page-%08d-%s.txt" % (page, uuid.uuid4().hex))
    payload = text.encode("utf-8")
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(final, flags, 0o600)
    expected_identity: tuple[int, int] | None = None
    path_identity: tuple[int, int] | None = None
    path_fingerprint: tuple[int, ...] | None = None
    published = False

    def fingerprint(info) -> tuple[int, ...]:
        return (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, getattr(info, "st_nlink", 1),
        )

    def regular_unique_entry(info) -> bool:
        return (
            stat.S_ISREG(info.st_mode)
            and not JobJournal._is_reparse_point(info)
            and getattr(info, "st_nlink", 1) == 1
        )

    try:
        opened = os.fstat(fd)
        if not regular_unique_entry(opened):
            raise JobSafetyError("native-text artifact handle is not a unique regular file")
        expected_identity = (opened.st_dev, opened.st_ino)

        # Windows fstat/stat file-identity fields are not consistently
        # comparable across handles on every supported filesystem. Pin the
        # path's own identity there and compare it across publication; POSIX
        # additionally requires the pathname to match the opened descriptor.
        initial_path = final.lstat()
        if not regular_unique_entry(initial_path) or initial_path.st_size != 0:
            raise JobSafetyError("native-text artifact path changed during reservation")
        path_identity = (initial_path.st_dev, initial_path.st_ino)
        if os.name != "nt" and path_identity != expected_identity:
            raise JobSafetyError("native-text artifact path does not match its opened handle")

        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
            if (not regular_unique_entry(written)
                    or (written.st_dev, written.st_ino) != expected_identity
                    or written.st_size != len(payload)):
                raise JobSafetyError("native-text artifact changed while writing")

        current = final.lstat()
        if (not regular_unique_entry(current)
                or (current.st_dev, current.st_ino) != path_identity
                or current.st_size != len(payload)
                or (os.name != "nt" and path_identity != expected_identity)):
            raise JobSafetyError("native-text artifact path changed before publication")
        path_fingerprint = fingerprint(current)

        _fsync_directory(directory)
        # Recheck after directory durability work. Never return a path that now
        # names a different file, symlink, reparse point, or hard-link alias.
        current = final.lstat()
        if (not regular_unique_entry(current)
                or (current.st_dev, current.st_ino) != path_identity
                or fingerprint(current) != path_fingerprint):
            raise JobSafetyError("native-text artifact path changed during publication")
        published = True
        return final
    finally:
        if fd >= 0:
            os.close(fd)
        if not published and path_identity is not None:
            try:
                current = final.lstat()
                if (regular_unique_entry(current)
                        and (current.st_dev, current.st_ino) == path_identity
                        and (path_fingerprint is None
                             or fingerprint(current) == path_fingerprint)):
                    final.unlink()
                    _fsync_directory(directory)
            except OSError:
                # Preserve any entry that cannot be proven to be ours.
                pass

def _count_utf8_characters(path: Path, limit: int) -> int:
    """Count UTF-8 characters in bounded chunks, stopping once limit is exceeded.

    Existing checkpoint files are included in the job-wide text budget on resume;
    never load a whole page artifact into memory just to account for its size.
    """
    count = 0
    with path.open("r", encoding="utf-8", newline="") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return count
            count += len(chunk)
            if count > limit:
                return count

def run_native_text_stage(
    journal: JobJournal,
    job_id: str,
    artifact_directory: str | Path,
    *,
    max_pages: int = 10000,
    max_chars_per_page: int = 500000,
    max_total_chars: int = 20000000,
    max_checkpoint_bytes: int = 96 * 1024 * 1024,
    timeout_seconds: float = 90.0,
    max_memory_mb: int = 768,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict:
    """Resume 'native_text' page artifacts with actual bounded PDF text extraction.

    Each artifact is UTF-8 page text, not a searchable PDF or OCR result.
    A failed or cancelled worker leaves committed pages available for resume.
    No job is marked complete and no final output is published.
    """
    if cancel_requested is not None and not callable(cancel_requested):
        raise TypeError("cancel_requested must be callable")
    for name, value in (
        ("max_pages", max_pages),
        ("max_chars_per_page", max_chars_per_page),
        ("max_total_chars", max_total_chars),
        ("max_memory_mb", max_memory_mb),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if (not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("timeout_seconds must be finite and positive")
    if (not isinstance(max_checkpoint_bytes, int)
            or isinstance(max_checkpoint_bytes, bool)
            or max_checkpoint_bytes < 1):
        raise ValueError("max_checkpoint_bytes must be a positive integer")
    folder = Path(artifact_directory)
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.is_dir():
        raise ValueError("artifact_directory must be a directory")
    folder = folder.resolve(strict=True)
    processed: list[int] = []
    skipped: list[int] = []
    with journal.worker_session(job_id) as owner:
        with exclusive_artifact_directory_lock(folder):
            orphan_report = _cleanup_orphaned_native_text_artifacts_locked(
                journal, folder, min_age_seconds=DEFAULT_ORPHAN_MIN_AGE_SECONDS
            )
            snapshot = owner.recovery_snapshot("native_text")
            job = journal.get_job(job_id)
            if job["page_count"] > max_pages:
                raise ValueError("job exceeds configured page limit")
            verified = set(snapshot["valid_pages"])
            with journal._open() as db:
                rows = db.execute(
                    "SELECT page_index, artifact FROM checkpoints WHERE job_id=? AND stage='native_text'",
                    (job_id,),
                ).fetchall()
            checkpoint_artifacts = {
                row["page_index"]: Path(row["artifact"]) for row in rows
            }

            missing: list[int] = []
            checkpoint_chars = 0
            for page in range(job["page_count"]):
                if page not in verified or not journal.verified_checkpoint(
                    job_id, page, "native_text"
                ):
                    missing.append(page)
                    continue

                artifact = checkpoint_artifacts.get(page)
                if artifact is None:
                    raise JobSafetyError("verified native-text checkpoint row is missing")
                try:
                    page_chars = _count_utf8_characters(artifact, max_chars_per_page)
                except (OSError, UnicodeDecodeError):
                    # A byte-valid legacy artifact may still not be valid UTF-8;
                    # rebuild it rather than trusting it as a native-text page.
                    missing.append(page)
                    continue

                # Recheck after reading: a checkpoint may be replaced while it is
                # being counted. Never count or skip bytes that no longer verify.
                if not journal.verified_checkpoint(job_id, page, "native_text"):
                    missing.append(page)
                    continue
                if page_chars > max_chars_per_page:
                    raise ValueError(
                        "verified native-text checkpoint exceeds configured per-page character cap"
                    )
                if checkpoint_chars + page_chars > max_total_chars:
                    raise ValueError(
                        "verified native-text checkpoints exceed configured total character cap"
                    )
                checkpoint_chars += page_chars
                skipped.append(page)

            # Bound checkpoint and leftover bytes before allocating more artifacts.
            # Registered files count even with a bad digest; stale unreferenced
            # stage files were pruned under the directory lock, while recent
            # leftovers remain counted against the same cap.
            checkpoint_bytes = 0
            for row in rows:
                try:
                    checkpoint_bytes += Path(row["artifact"]).stat().st_size
                except OSError:
                    # Missing artifacts are reported by the snapshot and rebuilt.
                    continue
            checkpoint_bytes += orphan_report["remaining_orphan_bytes"]
            if checkpoint_bytes > max_checkpoint_bytes:
                raise ValueError("registered checkpoint artifacts exceed configured byte cap")

            remaining_total_chars = max_total_chars - checkpoint_chars
            if missing and remaining_total_chars < 1:
                raise ValueError(
                    "native-text checkpoints exhaust the configured total character cap"
                )

            if cancel_requested is not None and cancel_requested():
                owner.set_state("paused")
                return {"processed": (), "skipped": tuple(skipped), "cancelled": True}
            if missing:
                processed_chars = 0
                with IsolatedPdfReader(
                    job["source"], max_pages=max_pages,
                    max_chars_per_page=max_chars_per_page,
                    max_total_chars=remaining_total_chars,
                    timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
                    page_indexes=missing,
                ) as reader:
                    if reader.page_count != job["page_count"]:
                        raise JobSafetyError("PDF page count differs from pinned job")
                    for page, text in reader.pages():
                        if cancel_requested is not None and cancel_requested():
                            owner.set_state("paused")
                            return {"processed": tuple(processed),
                                    "skipped": tuple(skipped), "cancelled": True}
                        page_chars = len(text)
                        if page_chars > max_chars_per_page:
                            raise JobSafetyError(
                                "native-text parser returned text beyond the per-page character cap"
                            )
                        if checkpoint_chars + processed_chars + page_chars > max_total_chars:
                            raise JobSafetyError(
                                "native-text stage exceeds the configured total character cap"
                            )
                        encoded_size = len(text.encode("utf-8"))
                        if checkpoint_bytes + encoded_size > max_checkpoint_bytes:
                            raise ValueError("native-text checkpoints exceed configured byte cap")
                        artifact = _save_page_text(folder, page, text)
                        owner.checkpoint(page, "native_text", artifact)
                        checkpoint_bytes += encoded_size
                        processed_chars += page_chars
                        processed.append(page)
            final = owner.recovery_snapshot("native_text")
            if final["invalid_pages"] or final["missing_pages"]:
                raise JobSafetyError("native text stage is incomplete or damaged")
    return {"processed": tuple(processed), "skipped": tuple(skipped), "cancelled": False}
