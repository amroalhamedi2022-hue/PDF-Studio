"""Checkpointed born-digital text extraction using a bounded PDF parser.

This is NOT OCR. A single isolated parser processes only pages missing a
SHA-verified artifact, under one local fenced worker lease. The caller must
provide an application-private artifact directory on the same local host.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from .bounded_parser import IsolatedPdfReader
from .jobs import JobJournal, JobSafetyError


def _save_page_text(directory: Path, page: int, text: str) -> Path:
    """Durably stage a new immutable artifact; never overwrite an old one."""
    final = directory / ("page-%08d-%s.txt" % (page, uuid.uuid4().hex))
    fd, temp_name = tempfile.mkstemp(prefix=".text-", suffix=".tmp", dir=directory)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, final)
        return final
    finally:
        temp.unlink(missing_ok=True)


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
        snapshot = owner.recovery_snapshot("native_text")
        job = journal.get_job(job_id)
        if job["page_count"] > max_pages:
            raise ValueError("job exceeds configured page limit")
        verified = set(snapshot["valid_pages"])
        missing: list[int] = []
        for page in range(job["page_count"]):
            if page in verified and journal.verified_checkpoint(job_id, page, "native_text"):
                skipped.append(page)
            else:
                missing.append(page)

        # Bound the bytes referenced by this stage before allocating more
        # checkpoint artifacts. Count registered files even if their digest
        # is bad: a corrupt large file still occupies disk and is not ours to
        # delete automatically. Orphan files from an abrupt crash are outside
        # this journal-derived accounting and require a separate safe cleanup
        # policy.
        with journal._open() as db:
            rows = db.execute(
                "SELECT artifact FROM checkpoints WHERE job_id=? AND stage='native_text'",
                (job_id,),
            ).fetchall()
        checkpoint_bytes = 0
        for row in rows:
            try:
                checkpoint_bytes += Path(row["artifact"]).stat().st_size
            except OSError:
                # Missing artifacts are reported by the snapshot and rebuilt.
                continue
        if checkpoint_bytes > max_checkpoint_bytes:
            raise ValueError("registered checkpoint artifacts exceed configured byte cap")

        if cancel_requested is not None and cancel_requested():
            owner.set_state("paused")
            return {"processed": (), "skipped": tuple(skipped), "cancelled": True}
        if missing:
            with IsolatedPdfReader(
                job["source"], max_pages=max_pages,
                max_chars_per_page=max_chars_per_page,
                max_total_chars=max_total_chars,
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
                    encoded_size = len(text.encode("utf-8"))
                    if checkpoint_bytes + encoded_size > max_checkpoint_bytes:
                        raise ValueError("native-text checkpoints exceed configured byte cap")
                    artifact = _save_page_text(folder, page, text)
                    owner.checkpoint(page, "native_text", artifact)
                    checkpoint_bytes += encoded_size
                    processed.append(page)
        final = owner.recovery_snapshot("native_text")
        if final["invalid_pages"] or final["missing_pages"]:
            raise JobSafetyError("native text stage is incomplete or damaged")
    return {"processed": tuple(processed), "skipped": tuple(skipped), "cancelled": False}
