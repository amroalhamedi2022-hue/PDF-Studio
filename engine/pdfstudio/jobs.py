"""Crash-safe *metadata* journal; not an OCR engine or complete PDF processor.

Checkpoints represent immutable page artifacts produced by other pipeline
adapters. SHA verification forbids accidentally resuming changed inputs or
silently trusting damaged artifacts. SQLite is used transactionally.
"""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from typing import Iterator
import sqlite3
import uuid
from pathlib import Path

from .worker_lease import exclusive_worker_lock

STAGES = frozenset({"prepared", "native_text", "ocr", "layout", "assembled"})
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


class WorkerSession:
    """Fenced owner of one job. Keep the context open while processing pages."""

    def __init__(self, journal: "JobJournal", job_id: str, token: str,
                 source_path: str, source_fingerprint: tuple[int, ...]):
        self._journal = journal
        self.job_id = job_id
        self._token = token
        self._source_path = source_path
        self._source_fingerprint = source_fingerprint

    def checkpoint(self, page_index: int, stage: str, artifact: str | Path) -> None:
        self._journal.checkpoint(
            self.job_id, page_index, stage, artifact, worker_token=self._token,
            source_fingerprint=self._source_fingerprint,
        )

    def set_state(self, next_state: str) -> None:
        self._journal.set_state(self.job_id, next_state, worker_token=self._token)

    def recovery_snapshot(self, stage: str) -> dict:
        return self._journal.recovery_snapshot(self.job_id, stage)


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
                    output_key TEXT,
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
                CREATE TABLE IF NOT EXISTS worker_owners (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
                    generation INTEGER NOT NULL CHECK(generation >= 1),
                    token TEXT
                );
                CREATE TABLE IF NOT EXISTS checkpoint_artifact_identity (
                    job_id TEXT NOT NULL,
                    page_index INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    artifact_identity TEXT,
                    PRIMARY KEY (job_id, page_index, stage),
                    FOREIGN KEY (job_id, page_index, stage)
                        REFERENCES checkpoints(job_id, page_index, stage) ON DELETE CASCADE
                );
            """)
            # Serialize the schema check/backfill so two app processes opening an
            # older journal cannot both attempt the same ALTER TABLE migration.
            db.execute("BEGIN IMMEDIATE")
            # Older B00/B01 journals do not have output_key. Backfill it without
            # rewriting the user-visible output path; the index makes subsequent
            # alias checks cheap while keeping legacy duplicate rows readable.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "output_key" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN output_key TEXT")
            legacy_rows = db.execute(
                "SELECT job_id, output FROM jobs WHERE output_key IS NULL OR output_key=''"
            ).fetchall()
            for row in legacy_rows:
                db.execute(
                    "UPDATE jobs SET output_key=? WHERE job_id=?",
                    (self._destination_key(row["output"]), row["job_id"]),
                )
            db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_output_key ON jobs(output_key)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoint_artifact_identity "
                "ON checkpoint_artifact_identity(artifact_identity)"
            )
            # Older journals have checkpoint rows but no file-identity map.
            # Backfill existing artifacts without deleting or rewriting checkpoints.
            # Missing files remain represented with a NULL identity and are still
            # rejected by the normal SHA verification when a resume is attempted.
            legacy_artifacts = db.execute("""
                SELECT c.job_id, c.page_index, c.stage, c.artifact
                FROM checkpoints AS c
                LEFT JOIN checkpoint_artifact_identity AS i
                  ON i.job_id=c.job_id AND i.page_index=c.page_index AND i.stage=c.stage
                WHERE i.job_id IS NULL
            """).fetchall()
            for row in legacy_artifacts:
                try:
                    identity = self._artifact_identity(row["artifact"])
                except OSError:
                    identity = None
                db.execute(
                    "INSERT OR IGNORE INTO checkpoint_artifact_identity "
                    "(job_id, page_index, stage, artifact_identity) VALUES (?,?,?,?)",
                    (row["job_id"], row["page_index"], row["stage"], identity),
                )

    @staticmethod
    def _destination_key(path: str | Path) -> str:
        """Canonical key for ordinary destination paths on the current platform.

        Win32 aliases ordinary path components that end in dots or spaces.
        Extended/device namespace paths keep their separate path semantics.
        """
        raw = str(Path(path))
        if os.name == "nt" and raw.startswith(("\\\\?\\", "\\\\.\\")):
            return os.path.normcase(os.path.normpath(raw))

        value = os.path.normpath(raw)
        if os.name == "nt":
            drive, tail = os.path.splitdrive(value)
            components = [component.rstrip(" .") for component in tail.split(os.sep)]
            tail = os.path.normpath(os.sep.join(components))
            value = drive + tail
        return os.path.normcase(os.path.normpath(value))

    @staticmethod
    def _identity_from_stat(info) -> str | None:
        """Return a stable file identity where the host exposes an inode."""
        if not getattr(info, "st_ino", 0):
            return None
        return f"{info.st_dev}:{info.st_ino}"

    @staticmethod
    def _artifact_identity(path: str | Path) -> str | None:
        """Stable device/inode identity for detecting hard-link aliases."""
        return JobJournal._identity_from_stat(Path(path).stat())

    @staticmethod
    def _artifact_stat_fingerprint(info) -> tuple[int, ...]:
        """Metadata used to detect replacement or mutation during one open read."""
        return (
            info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns,
        )

    @staticmethod
    def _is_reparse_point(info) -> bool:
        """Return whether Windows marks an entry as a reparse point."""
        mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(mask and getattr(info, "st_file_attributes", 0) & mask)

    @staticmethod
    def _checkpoint_artifact_matches(
        path: str | Path,
        expected_sha256: str,
        expected_identity: str | None,
    ) -> bool:
        """Hash one opened regular file and reject path aliases/replacements.

        POSIX uses O_NOFOLLOW. Windows checks the path entry before and after
        opening and compares it with the opened handle; this is not a complete
        defense against every same-user Windows path race.
        """
        file_path = Path(path)
        try:
            path_before = file_path.lstat()
            if stat.S_ISLNK(path_before.st_mode) or JobJournal._is_reparse_point(path_before):
                return False

            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            if os.name == "posix":
                nofollow = getattr(os, "O_NOFOLLOW", None)
                if nofollow is None:
                    return False
                flags |= nofollow

            fd = os.open(file_path, flags)
            with os.fdopen(fd, "rb") as stream:
                opened_before = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened_before.st_mode):
                    return False
                identity_before = JobJournal._identity_from_stat(opened_before)
                if identity_before != expected_identity:
                    return False
                if JobJournal._artifact_stat_fingerprint(path_before) != JobJournal._artifact_stat_fingerprint(opened_before):
                    return False

                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)

                opened_after = os.fstat(stream.fileno())
                path_after = file_path.lstat()
                if stat.S_ISLNK(path_after.st_mode) or JobJournal._is_reparse_point(path_after):
                    return False
                if not stat.S_ISREG(opened_after.st_mode):
                    return False
                if JobJournal._artifact_stat_fingerprint(opened_before) != JobJournal._artifact_stat_fingerprint(opened_after):
                    return False
                if JobJournal._artifact_stat_fingerprint(opened_after) != JobJournal._artifact_stat_fingerprint(path_after):
                    return False
                if JobJournal._identity_from_stat(path_after) != expected_identity:
                    return False
                return digest.hexdigest() == expected_sha256
        except (OSError, ValueError):
            return False

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=30000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_job(self, source: str | Path, output: str | Path, page_count: int) -> str:
        """Pin source digest. Never overwrite the input or an existing output."""
        src = Path(source).resolve(strict=True)
        dst = Path(output).resolve()
        destination_key = self._destination_key(dst)
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
        # Refuse to pin a digest if the source identity/metadata changes while
        # streaming it. Otherwise a transient write can leave a pending job
        # whose recorded digest never represented a stable input snapshot.
        try:
            fingerprint_before = self._source_stat_fingerprint(src)
            digest = sha256_file(src)
            fingerprint_after = self._source_stat_fingerprint(src)
        except OSError as exc:
            raise SourceChangedError(
                "original PDF changed or became unreadable while creating job"
            ) from exc
        if fingerprint_before != fingerprint_after:
            raise SourceChangedError("original PDF changed while creating job")
        job_id = str(uuid.uuid4())
        with self._open() as db:
            # Serialize destination reservation with SQLite's writer lock.
            # The earlier filesystem check is only a fast path; two creators
            # can both pass it while the destination is still absent.
            db.execute("BEGIN IMMEDIATE")
            if dst.exists():
                raise FileExistsError(f"output already exists: {dst}")
            reserved = db.execute(
                "SELECT 1 FROM jobs WHERE output_key=? LIMIT 1", (destination_key,)
            ).fetchone()
            if reserved is not None:
                raise FileExistsError(
                    "output path is already reserved by a journaled job"
                )
            db.execute(
                "INSERT INTO jobs(job_id, source, input_sha256, output, output_key, page_count, state) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_id, str(src), digest, str(dst), destination_key, page_count, "pending"),
            )
        return job_id

    def get_job(self, job_id: str) -> dict:
        with self._open() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    @staticmethod
    def _source_stat_fingerprint(source: str | Path) -> tuple[int, ...]:
        stat = Path(source).stat()
        return (
            stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns,
        )

    def _verified_source_fingerprint(self, job_id: str) -> tuple[int, ...]:
        """Hash once and reject metadata/content changes during that hash."""
        job = self.get_job(job_id)
        try:
            before = self._source_stat_fingerprint(job["source"])
            current = sha256_file(job["source"])
            after = self._source_stat_fingerprint(job["source"])
        except OSError as exc:
            raise SourceChangedError("original PDF is missing or unreadable") from exc
        if before != after:
            raise SourceChangedError("original PDF changed during verification")
        if current != job["input_sha256"]:
            raise SourceChangedError("original PDF content changed; do not resume")
        return after

    @staticmethod
    def _assert_source_fingerprint(source: str | Path, expected: tuple[int, ...]) -> None:
        """Cheap per-page guard; the worker session performs full SHA audits at entry/exit."""
        try:
            current = JobJournal._source_stat_fingerprint(source)
        except OSError as exc:
            raise SourceChangedError("original PDF is missing or unreadable") from exc
        if current != expected:
            raise SourceChangedError("original PDF identity or metadata changed during processing")

    def verify_source(self, job_id: str) -> None:
        self._verified_source_fingerprint(job_id)

    @staticmethod
    def _check_worker_token(db: sqlite3.Connection, job_id: str, token: str | None) -> None:
        owner = db.execute("SELECT token FROM worker_owners WHERE job_id=?", (job_id,)).fetchone()
        if (owner is not None and (owner["token"] is None or owner["token"] != token)) \
                or (owner is None and token is not None):
            raise JobSafetyError("worker ownership token is absent or stale")

    def set_state(self, job_id: str, next_state: str, *, worker_token: str | None = None) -> None:
        if next_state not in STATES:
            raise ValueError("unknown state")
        self.verify_source(job_id)
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if current is None:
                raise KeyError(job_id)
            self._check_worker_token(db, job_id, worker_token)
            if next_state not in TRANSITIONS[current["state"]]:
                raise JobSafetyError(f"invalid transition: {current['state']} -> {next_state}")
            self.verify_source(job_id)
            db.execute("UPDATE jobs SET state=? WHERE job_id=?", (next_state, job_id))

    @contextmanager
    def worker_session(self, job_id: str) -> Iterator[WorkerSession]:
        """Exclusive crash-recoverable worker ownership on one local host.

        The OS lock must be held for the ENTIRE worker lifetime. A crashed
        process releases the OS lock; a new session rotates the SQLite token,
        fencing stale writers. Legacy running/paused/failed jobs with no
        ownership history fail closed: their workers may still be live.
        """
        with exclusive_worker_lock(self.database, job_id):
            fingerprint = self._verified_source_fingerprint(job_id)
            token = uuid.uuid4().hex
            with self._open() as db:
                db.execute("BEGIN IMMEDIATE")
                job = db.execute("SELECT state, output, source FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if job is None:
                    raise KeyError(job_id)
                owner = db.execute("SELECT generation FROM worker_owners WHERE job_id=?", (job_id,)).fetchone()
                if job["state"] not in ("pending", "paused", "failed", "running"):
                    raise JobSafetyError("terminal job cannot be resumed")
                if owner is None and job["state"] != "pending":
                    raise JobSafetyError("legacy job has no verified worker ownership history")
                if Path(job["output"]).exists():
                    raise FileExistsError("final output already exists; refusing resume")
                self._assert_source_fingerprint(job["source"], fingerprint)
                db.execute("""INSERT INTO worker_owners(job_id, generation, token) VALUES(?, 1, ?)
                    ON CONFLICT(job_id) DO UPDATE SET generation=generation+1, token=excluded.token""",
                    (job_id, token))
                db.execute("UPDATE jobs SET state='running' WHERE job_id=?", (job_id,))
            try:
                yield WorkerSession(self, job_id, token, job["source"], fingerprint)
            finally:
                source_error = None
                try:
                    verified = self._verified_source_fingerprint(job_id)
                    self._assert_source_fingerprint(job["source"], fingerprint)
                    if verified != fingerprint:
                        raise SourceChangedError("original PDF changed during processing")
                except SourceChangedError as exc:
                    source_error = exc
                with self._open() as db:
                    db.execute("BEGIN IMMEDIATE")
                    owner = db.execute("SELECT token FROM worker_owners WHERE job_id=?", (job_id,)).fetchone()
                    if owner is not None and owner["token"] == token:
                        final_state = "failed" if source_error else "paused"
                        db.execute("UPDATE jobs SET state=? WHERE job_id=? AND state='running'",
                                   (final_state, job_id))
                        db.execute("UPDATE worker_owners SET token=NULL WHERE job_id=?", (job_id,))
                if source_error is not None:
                    raise source_error

    def checkpoint(self, job_id: str, page_index: int, stage: str, artifact: str | Path,
                   *, worker_token: str | None = None,
                   source_fingerprint: tuple[int, ...] | None = None) -> None:
        if stage not in STAGES:
            raise ValueError("unsupported stage")
        if source_fingerprint is not None and worker_token is None:
            raise JobSafetyError("source fingerprint requires a fenced worker")
        job = self.get_job(job_id)
        if not isinstance(page_index, int) or isinstance(page_index, bool) or not 0 <= page_index < job["page_count"]:
            raise ValueError("page index out of bounds")
        if source_fingerprint is None:
            self.verify_source(job_id)
        else:
            self._assert_source_fingerprint(job["source"], source_fingerprint)
        file = Path(artifact).resolve(strict=True)
        if file == Path(job["source"]) or file == Path(job["output"]):
            raise JobSafetyError("artifact may not be the original or final PDF")
        if not file.is_file():
            raise JobSafetyError("artifact must be a file")
        if file == self.database.resolve():
            raise JobSafetyError("journal database cannot be a checkpoint artifact")
        digest = sha256_file(file)
        artifact_identity = self._artifact_identity(file)
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if current is None:
                raise KeyError(job_id)
            self._check_worker_token(db, job_id, worker_token)
            if current["state"] != "running":
                raise JobSafetyError("cannot write checkpoints outside a running job")
            if source_fingerprint is None:
                self.verify_source(job_id)
            else:
                self._assert_source_fingerprint(job["source"], source_fingerprint)
            if sha256_file(file) != digest:
                raise JobSafetyError("checkpoint artifact changed during verification")

            # A checkpoint is an immutable page/stage artifact, not a shared
            # scratch filename. Reusing one path for another key (or job) lets
            # a later page overwrite bytes already trusted by an earlier one.
            if self._artifact_identity(file) != artifact_identity:
                raise JobSafetyError("checkpoint artifact identity changed during verification")
            reused = db.execute(
                """SELECT c.job_id, c.page_index, c.stage
                FROM checkpoints AS c
                LEFT JOIN checkpoint_artifact_identity AS i
                  ON i.job_id=c.job_id AND i.page_index=c.page_index AND i.stage=c.stage
                WHERE (c.artifact=? OR (? IS NOT NULL AND i.artifact_identity=?))
                  AND NOT (c.job_id=? AND c.page_index=? AND c.stage=?)
                LIMIT 1""",
                (str(file), artifact_identity, artifact_identity, job_id, page_index, stage),
            ).fetchone()
            if reused is not None:
                raise JobSafetyError(
                    "checkpoint artifact path or file identity is already assigned to another page or job"
                )
            existing = db.execute(
                """SELECT c.artifact, c.artifact_sha256, i.artifact_identity
                FROM checkpoints AS c
                LEFT JOIN checkpoint_artifact_identity AS i
                  ON i.job_id=c.job_id AND i.page_index=c.page_index AND i.stage=c.stage
                WHERE c.job_id=? AND c.page_index=? AND c.stage=?""",
                (job_id, page_index, stage),
            ).fetchone()
            same_artifact = existing is not None and (
                existing["artifact"] == str(file)
                or (artifact_identity is not None and existing["artifact_identity"] == artifact_identity)
            )
            if (
                existing is not None
                and existing["artifact"] == str(file)
                and existing["artifact_identity"] != artifact_identity
            ):
                raise JobSafetyError(
                    "checkpoint artifact identity changed; use a new path for changed bytes"
                )
            if same_artifact and existing["artifact_sha256"] != digest:
                raise JobSafetyError("checkpoint artifacts are immutable; use a new path for changed bytes")
            db.execute("""INSERT INTO checkpoints
                (job_id, page_index, stage, artifact, artifact_sha256)
                VALUES (?,?,?,?,?)
                ON CONFLICT(job_id,page_index,stage) DO UPDATE SET
                artifact=excluded.artifact, artifact_sha256=excluded.artifact_sha256""",
                (job_id, page_index, stage, str(file), digest))
            db.execute(
                """INSERT INTO checkpoint_artifact_identity
                (job_id, page_index, stage, artifact_identity) VALUES (?,?,?,?)
                ON CONFLICT(job_id,page_index,stage) DO UPDATE SET
                artifact_identity=excluded.artifact_identity""",
                (job_id, page_index, stage, artifact_identity),
            )


    def recovery_snapshot(self, job_id: str, stage: str) -> dict:
        """Read-only post-interruption audit; NEVER starts or claims a worker lease.

        An interrupted 'running' row may belong to a *live* process. Callers
        must independently stop/join that worker before any state transition.
        Checkpoint files are rehashed, not trusted from SQLite alone.
        """
        if stage not in STAGES:
            raise ValueError("unsupported stage")
        self.verify_source(job_id)
        with self._open() as db:
            job = db.execute("SELECT state, page_count FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            rows = db.execute(
                "SELECT c.page_index, c.artifact, c.artifact_sha256, i.artifact_identity "
                "FROM checkpoints AS c LEFT JOIN checkpoint_artifact_identity AS i "
                "ON i.job_id=c.job_id AND i.page_index=c.page_index AND i.stage=c.stage "
                "WHERE c.job_id=? AND c.stage=? ORDER BY c.page_index", (job_id, stage)
            ).fetchall()
            state, page_count = job["state"], job["page_count"]
        valid, invalid = [], []
        for row in rows:
            page_index = row["page_index"]
            # The public checkpoint API enforces this bound, but a damaged or
            # manually edited SQLite journal can bypass that API. Never report
            # an out-of-document artifact as a valid page during recovery.
            if not isinstance(page_index, int) or not 0 <= page_index < page_count:
                invalid.append(page_index)
                continue
            matched = self._checkpoint_artifact_matches(
                row["artifact"], row["artifact_sha256"], row["artifact_identity"]
            )
            (valid if matched else invalid).append(page_index)
        self.verify_source(job_id)  # reject source replacement during the audit
        verified = set(valid)
        return {
            "job_id": job_id, "stage": stage, "state": state,
            "valid_pages": tuple(valid), "invalid_pages": tuple(invalid),
            "missing_pages": tuple(i for i in range(page_count) if i not in verified),
            "requires_worker_quiescence": state == "running",
            "resume_authorized": False,
        }


    def verified_checkpoint(self, job_id: str, page_index: int, stage: str) -> bool:
        """Verify one checkpoint using the (job, page, stage) SQLite primary key.

        Intended for callers holding worker_session. Unlike resumable_pages,
        this avoids rehashing every other artifact for each resumed page.
        The stage runner performs full source and artifact audits at entry
        and exit; this method checks only the requested artifact.
        """
        if stage not in STAGES:
            raise ValueError("unsupported stage")
        if not isinstance(page_index, int) or isinstance(page_index, bool) or page_index < 0:
            raise ValueError("invalid page index")
        job = self.get_job(job_id)
        if page_index >= job["page_count"]:
            raise ValueError("page index out of bounds")
        with self._open() as db:
            row = db.execute(
                "SELECT c.artifact, c.artifact_sha256, i.artifact_identity "
                "FROM checkpoints AS c LEFT JOIN checkpoint_artifact_identity AS i "
                "ON i.job_id=c.job_id AND i.page_index=c.page_index AND i.stage=c.stage "
                "WHERE c.job_id=? AND c.page_index=? AND c.stage=?",
                (job_id, page_index, stage)
            ).fetchone()
        if row is None:
            return False
        return self._checkpoint_artifact_matches(
            row["artifact"], row["artifact_sha256"], row["artifact_identity"]
        )

    def resumable_pages(self, job_id: str, stage: str) -> list[int]:
        """Return only pages with verified artifacts at this pipeline stage."""
        if stage not in STAGES:
            raise ValueError("unsupported stage")
        self.verify_source(job_id)
        page_count = self.get_job(job_id)["page_count"]
        with self._open() as db:
            entries = db.execute(
                "SELECT c.page_index, c.artifact, c.artifact_sha256, i.artifact_identity "
                "FROM checkpoints AS c LEFT JOIN checkpoint_artifact_identity AS i "
                "ON i.job_id=c.job_id AND i.page_index=c.page_index AND i.stage=c.stage "
                "WHERE c.job_id=? AND c.stage=? ORDER BY c.page_index", (job_id, stage)
            ).fetchall()
        good = []
        for row in entries:
            page_index = row["page_index"]
            # Ignore malformed journal rows; recovery_snapshot reports them as
            # invalid so they cannot inflate progress or suppress real work.
            if not isinstance(page_index, int) or not 0 <= page_index < page_count:
                continue
            if self._checkpoint_artifact_matches(
                row["artifact"], row["artifact_sha256"], row["artifact_identity"]
            ):
                good.append(page_index)
        return good
