"""Crash-resumable, single-host per-page stage execution.

The caller supplies a trusted page processor; this module does not perform OCR.
WorkerSession keeps the local OS lock for the whole batch. Only SHA-verified
checkpoints are skipped; failed or missing artifacts must be rebuilt.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .jobs import JobJournal, JobSafetyError, STAGES


def run_page_stage(
    journal: JobJournal,
    job_id: str,
    stage: str,
    process_page: Callable[[int], str | Path],
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict:
    """Run missing/damaged pages under an exclusive fenced worker session.

    The processor must create a *new immutable* artifact for each page, and
    must not modify an existing checkpoint artifact. The caller owns cleanup.
    No automatic job completion or final PDF publication is performed here.
    """
    if stage not in STAGES:
        raise ValueError("unsupported stage")
    if not callable(process_page):
        raise TypeError("process_page must be callable")
    if cancel_requested is not None and not callable(cancel_requested):
        raise TypeError("cancel_requested must be callable")

    processed = []
    skipped = []
    with journal.worker_session(job_id) as owner:
        snapshot = owner.recovery_snapshot(stage)
        good = set(snapshot["valid_pages"])
        page_count = journal.get_job(job_id)["page_count"]
        for page in range(page_count):
            if cancel_requested is not None and cancel_requested():
                owner.set_state("paused")
                return {"processed": tuple(processed), "skipped": tuple(skipped), "cancelled": True}
            # Re-verify immediately before skipping; a file can change after
            # the snapshot. A changed artifact is rebuilt rather than trusted.
            if page in good and journal.verified_checkpoint(job_id, page, stage):
                skipped.append(page)
                continue
            artifact = Path(process_page(page)).resolve(strict=True)
            if not artifact.is_file():
                raise JobSafetyError("page processor did not produce a file")
            owner.checkpoint(page, stage, artifact)
            processed.append(page)
        # A page processor may have mutated another previously verified page.
        # Do not report success until every checkpoint is verified again.
        final = owner.recovery_snapshot(stage)
        if final["invalid_pages"] or final["missing_pages"]:
            raise JobSafetyError("stage artifacts changed or are incomplete")
    return {"processed": tuple(processed), "skipped": tuple(skipped), "cancelled": False}
