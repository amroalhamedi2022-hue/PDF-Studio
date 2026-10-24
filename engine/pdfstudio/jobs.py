"""Crash-safe *metadata* journal; not an OCR engine or complete PDF processor.

Checkpoints represent immutable page artifacts produced by other pipeline
adapters. SHA verification forbids accidentally resuming changed inputs or
silently trusting damaged artifacts. SQLite is used transactionally.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path

STAGES = frozenset({"prepared", "ocr", "layout", "assembled"})
STATES = frozenset({"pending", "running", "paused", "failed", "complete", "cancelled"})
TRANSITIONS = {
    "pending": frozenset({"running", "cancelled"}),
    "running": frozenset({"paused", "failed", "complete", "cancelled"}),
    "paused": frozenset({"running", "cancelled"}),
    "failed": frozenset({"running", "cancelled"}),
    "complete": frozenset(),
    "cancelled": frozenset(),
}


class JobSafetyError(ValueError):
    """A job violates a no-data-loss or journal invariant."""


class SourceChangedError(JobSafetyError):
    """The source PDF no longer matches the pinned input digest."""


def sha256_file(path: str | Path, chunk_bytes: int = 1024 * 1024) -> str:
    """Stream a file without reading it all into RAM."""
    if chunk_bytes < 1:
        raise ValueError("chunk_bytes must be positive")
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(chunk_bytes), b""):
            result.update(block)
    return result.hexdigest()


class JobJournal:
    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    output TEXT NOT NULL,
                    page_count INTEGER NOT NULL CHECK (page_count > 0),
                    state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    page_index INTEGER NOT NULL CHECK (page_index >= 0),
                    stage TEXT NOT NULL,
                    artifact TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    PRIMARY KEY (job_id, page_index, stage)
                );
            """)

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def create_job(self, source: str | Path, output: str | Path, page_count: int) -> str:
        """Pin source digest. Never overwrite the input or an existing output."""
        src = Path(source).resolve(strict=True)
        dst = Path(output).resolve()
        if not src.is_file():
            raise JobSafetyError("source must be an ordinary file")
        if dst == src:
            raise JobSafetyError("source and output must be different files")
        if dst.exists():
            raise FileExistsError(f"output already exists: {dst}")
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
            raise ValueError("page_count must be a positive integer")
        with src.open("rb") as pdf:
            if pdf.read(5) != b"%PDF-":
                raise JobSafetyError("source does not start with a PDF header")
        job_id = str(uuid.uuid4())
        digest = sha256_file(src)
        with self._open() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)", (job_id, str(src), digest, str(dst), page_count, "pending"))
        return job_id

    def get_job(self, job_id: str) -> dict:
        with self._open() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    def verify_source(self, job_id: str) -> None:
        job = self.get_job(job_id)
        try:
            current = sha256_file(job["source"])
        except OSError as exc:
            raise SourceChangedError("original PDF is missing or unreadable") from exc
        if current != job["input_sha256"]:
            raise SourceChangedError("original PDF content changed; do not resume")

    def set_state(self, job_id: str, next_state: str) -> None:
        if next_state not in STATES:
            raise ValueError("unknown state")
        self.verify_source(job_id)
        with self._open() as db:
            current = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if current is None:
                raise KeyError(job_id)
            if next_state not in TRANSITIONS[current["state"]]:
                raise JobSafetyError(f"invalid transition: {current['state']} -> {next_state}")
            db.execute("UPDATE jobs SET state=? WHERE job_id=?", (next_state, job_id))

    def checkpoint(self, job_id: str, page_index: int, stage: str, artifact: str | Path) -> None:
        if stage not in STAGES:
            raise ValueError("unsupported stage")
        job = self.get_job(job_id)
        if not isinstance(page_index, int) or isinstance(page_index, bool) or not 0 <= page_index < job["page_count"]:
            raise ValueError("page index out of bounds")
        if job["state"] != "running":
            raise JobSafetyError("cannot write checkpoints outside a running job")
        self.verify_source(job_id)
        file = Path(artifact).resolve(strict=True)
        if file == Path(job["source"]) or file == Path(job["output"]):
            raise JobSafetyError("artifact may not be the original or final PDF")
        if not file.is_file():
            raise JobSafetyError("artifact must be a file")
        digest = sha256_file(file)
        with self._open() as db:
            db.execute("""INSERT INTO checkpoints VALUES (?,?,?,?,?)
                ON CONFLICT(job_id,page_index,stage) DO UPDATE SET
                artifact=excluded.artifact, artifact_sha256=excluded.artifact_sha256""",
                (job_id, page_index, stage, str(file), digest))

    def resumable_pages(self, job_id: str, stage: str) -> list[int]:
        """Return only pages with verified artifacts at this pipeline stage."""
        if stage not in STAGES:
            raise ValueError("unsupported stage")
        self.verify_source(job_id)
        with self._open() as db:
            entries = db.execute("SELECT * FROM checkpoints WHERE job_id=? AND stage=? ORDER BY page_index", (job_id, stage)).fetchall()
        good = []
        for row in entries:
            try:
                if sha256_file(row["artifact"]) == row["artifact_sha256"]:
                    good.append(row["page_index"])
            except OSError:
                continue
        return good
