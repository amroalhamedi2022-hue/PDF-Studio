"""B00 source-only tests (no PDF renderer or Android/Windows package)."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from pdfstudio import JobJournal, JobSafetyError, SourceChangedError, normalize_arabic_search, sha256_file


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / "original.pdf"
        self.original.write_bytes(b"%PDF-1.7\nminimal fixture, not a parsable PDF\n")
        self.output = self.root / "output.pdf"
        self.journal = JobJournal(self.root / "jobs.sqlite")

    def test_streamed_digest_independent_of_chunk_size(self):
        self.assertEqual(sha256_file(self.original, 1), sha256_file(self.original, 4096))

    def test_no_source_overwrite_and_no_preexisting_output_overwrite(self):
        with self.assertRaises(JobSafetyError):
            self.journal.create_job(self.original, self.original, 1)
        self.output.touch()
        with self.assertRaises(FileExistsError):
            self.journal.create_job(self.original, self.output, 1)

    def test_create_job_rejects_broken_output_symlink_without_journaling(self):
        target = self.root / "not-created-target.pdf"
        link = self.root / "dangling-output.pdf"
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable on this host: {exc}")

        with self.assertRaisesRegex(JobSafetyError, "symlink or reparse point"):
            self.journal.create_job(self.original, link, 1)
        self.assertFalse(target.exists())
        with self.journal._open() as db:
            count = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_worker_session_rejects_output_symlink_added_after_reservation(self):
        job = self.journal.create_job(self.original, self.output, 1)
        target = self.root / "late-target.pdf"
        try:
            os.symlink(str(target), str(self.output))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable on this host: {exc}")

        with self.assertRaisesRegex(JobSafetyError, "symlink or reparse point"):
            with self.journal.worker_session(job):
                pass
        self.assertEqual(self.journal.get_job(job)["state"], "pending")
        self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive path semantics required")
    def test_output_reservation_rejects_case_aliases_on_windows(self):
        first_path = self.root / "Output-Case-Alias.PDF"
        second_path = self.root / "output-case-alias.pdf"
        self.journal.create_job(self.original, first_path, 1)
        with self.assertRaisesRegex(FileExistsError, "already reserved"):
            self.journal.create_job(self.original, second_path, 1)
        with self.journal._open() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE output_key=?",
                (self.journal._destination_key(first_path.resolve()),),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    @unittest.skipUnless(os.name == "nt", "Win32 trailing-dot/space alias semantics required")
    def test_output_reservation_rejects_trailing_dot_and_space_aliases_on_windows(self):
        first_path = self.root / "Trailing-Alias.pdf"
        self.journal.create_job(self.original, first_path, 1)
        aliases = (
            self.root / "Trailing-Alias.pdf.",
            self.root / "Trailing-Alias.pdf ",
        )
        for alias in aliases:
            with self.subTest(alias=str(alias)):
                with self.assertRaisesRegex(FileExistsError, "already reserved"):
                    self.journal.create_job(self.original, alias, 1)

    def test_legacy_journal_migrates_output_reservation_key(self):
        legacy_db = self.root / "legacy-jobs.sqlite"
        output = self.root / "legacy-output.pdf"
        legacy_artifact = self.root / "legacy-page.txt"
        legacy_artifact.write_text("legacy checkpoint", encoding="utf-8")
        db = sqlite3.connect(legacy_db)
        try:
            db.execute("""CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                output TEXT NOT NULL,
                page_count INTEGER NOT NULL CHECK (page_count > 0),
                state TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE checkpoints (
                job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                page_index INTEGER NOT NULL CHECK (page_index >= 0),
                stage TEXT NOT NULL,
                artifact TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                PRIMARY KEY (job_id, page_index, stage)
            )""")
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                ("legacy-job", str(self.original.resolve()), sha256_file(self.original),
                 str(output.resolve()), 1, "pending"),
            )
            db.execute(
                "INSERT INTO checkpoints VALUES (?,?,?,?,?)",
                ("legacy-job", 0, "ocr", str(legacy_artifact.resolve()), sha256_file(legacy_artifact)),
            )
            db.commit()
        finally:
            db.close()
        migrated = type(self.journal)(legacy_db)
        with migrated._open() as db:
            row = db.execute(
                "SELECT output_key FROM jobs WHERE job_id='legacy-job'"
            ).fetchone()
            identity_row = db.execute(
                """SELECT artifact_identity FROM checkpoint_artifact_identity
                WHERE job_id='legacy-job' AND page_index=0 AND stage='ocr'"""
            ).fetchone()
        self.assertEqual(
            row["output_key"],
            migrated._destination_key(output.resolve()),
        )
        self.assertEqual(
            identity_row["artifact_identity"],
            migrated._artifact_identity(legacy_artifact.resolve()),
        )
        self.assertEqual(migrated.resumable_pages("legacy-job", "ocr"), [0])
        with self.assertRaisesRegex(FileExistsError, "already reserved"):
            migrated.create_job(self.original, output, 1)

    def test_bad_header_rejected(self):
        self.original.write_text("not pdf")
        with self.assertRaises(JobSafetyError):
            self.journal.create_job(self.original, self.output, 1)

    def test_checkpoint_and_resumption_verifies_output(self):
        job = self.journal.create_job(self.original, self.output, 3)
        artifact = self.root / "page0.txt"
        with self.journal.worker_session(job) as owner:
            artifact.write_text("recognized text")
            owner.checkpoint(0, "ocr", artifact)
            self.assertEqual(self.journal.resumable_pages(job, "ocr"), [0])
            artifact.write_text("damaged")
            self.assertEqual(self.journal.resumable_pages(job, "ocr"), [])
            with self.assertRaisesRegex(JobSafetyError, "immutable"):
                owner.checkpoint(0, "ocr", artifact)
            corrected = self.root / "page0-corrected.txt"
            corrected.write_text("corrected")
            owner.checkpoint(0, "ocr", corrected)
        self.assertEqual(JobJournal(self.root / "jobs.sqlite").resumable_pages(job, "ocr"), [0])

    def test_checkpoint_hardlink_identity_cannot_be_reused(self):
        first_job = self.journal.create_job(self.original, self.output, 2)
        second_job = self.journal.create_job(
            self.original, self.root / "second-output.pdf", 1
        )
        artifact = self.root / "checkpoint-page.txt"
        alias = self.root / "checkpoint-page-hardlink.txt"
        artifact.write_text("immutable checkpoint bytes", encoding="utf-8")
        with self.journal.worker_session(first_job) as first_owner, self.journal.worker_session(second_job) as second_owner:
            # Register a clean single-link checkpoint first. Creating an alias
            # later must invalidate it, and the alias itself must never be
            # registered for either job.
            first_owner.checkpoint(0, "ocr", artifact)
            try:
                os.link(artifact, alias)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"hard links unavailable on this filesystem: {exc}")

            with self.assertRaisesRegex(JobSafetyError, "hard-link aliases"):
                first_owner.checkpoint(1, "ocr", alias)
            with self.assertRaisesRegex(JobSafetyError, "hard-link aliases"):
                second_owner.checkpoint(0, "ocr", alias)
            self.assertEqual(self.journal.resumable_pages(first_job, "ocr"), [])
            self.assertEqual(self.journal.resumable_pages(second_job, "ocr"), [])
            alias.unlink()
            self.assertEqual(self.journal.resumable_pages(first_job, "ocr"), [0])

    def test_replaced_checkpoint_path_with_identical_bytes_is_not_trusted(self):
        job = self.journal.create_job(self.original, self.output, 2)
        artifact = self.root / "checkpoint-page.txt"
        replacement = self.root / "replacement-page.txt"
        payload = b"same bytes, different file identity"
        with self.journal.worker_session(job) as owner:
            artifact.write_bytes(payload)
            owner.checkpoint(0, "ocr", artifact)
            recorded_identity = self.journal._artifact_identity(artifact)
            if recorded_identity is None:
                self.skipTest("filesystem does not expose a useful inode identity")

            replacement.write_bytes(payload)
            replacement_identity = self.journal._artifact_identity(replacement)
            if replacement_identity is None or replacement_identity == recorded_identity:
                self.skipTest("filesystem cannot distinguish the replacement file identity")
            expected_digest = sha256_file(artifact)
            os.replace(replacement, artifact)
            self.assertEqual(sha256_file(artifact), expected_digest)
            self.assertNotEqual(self.journal._artifact_identity(artifact), recorded_identity)

            # A byte-identical replacement must not be mistaken for the original
            # immutable checkpoint merely because its SHA-256 still matches.
            self.assertFalse(self.journal.verified_checkpoint(job, 0, "ocr"))
            self.assertEqual(self.journal.resumable_pages(job, "ocr"), [])
            audit = self.journal.recovery_snapshot(job, "ocr")
            self.assertEqual(audit["valid_pages"], ())
            self.assertEqual(audit["invalid_pages"], (0,))
            self.assertEqual(audit["missing_pages"], (0, 1))
            with self.assertRaisesRegex(JobSafetyError, "identity changed"):
                owner.checkpoint(0, "ocr", artifact)

    def test_create_job_rejects_source_mutation_during_digest_without_journaling(self):
        original = self.original.read_bytes()
        real_hash = sha256_file

        def mutate_after_digest(path, chunk_bytes=1024 * 1024):
            digest = real_hash(path, chunk_bytes)
            Path(path).write_bytes(original + b"mutated-during-hash")
            return digest

        with mock.patch("pdfstudio.jobs.sha256_file", side_effect=mutate_after_digest):
            with self.assertRaisesRegex(SourceChangedError, "changed while creating"):
                self.journal.create_job(self.original, self.output, 1)

        with self.journal._open() as db:
            job_count = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        self.assertEqual(job_count, 0)

    def test_changed_original_invalidates_resume(self):
        job = self.journal.create_job(self.original, self.output, 2)
        self.original.write_bytes(self.original.read_bytes() + b"changed")
        with self.assertRaises(SourceChangedError):
            self.journal.set_state(job, "running")
        with self.assertRaises(SourceChangedError):
            self.journal.resumable_pages(job, "ocr")

    def test_invalid_stage_index_and_transition(self):
        job = self.journal.create_job(self.original, self.output, 2)
        with self.assertRaises(JobSafetyError):
            self.journal.set_state(job, "complete")
        artifact = self.root / "artifact"
        artifact.write_bytes(b"data")
        with self.journal.worker_session(job) as owner:
            with self.assertRaises(ValueError):
                owner.checkpoint(2, "ocr", artifact)
            with self.assertRaises(ValueError):
                owner.checkpoint(0, "invalid", artifact)
            with self.assertRaises(JobSafetyError):
                owner.checkpoint(0, "ocr", self.original)

    def test_cannot_resume_with_missing_artifact(self):
        job = self.journal.create_job(self.original, self.output, 1)
        artifact = self.root / "artifact"
        artifact.write_text("hello")
        with self.journal.worker_session(job) as owner:
            owner.checkpoint(0, "prepared", artifact)
            artifact.unlink()
        self.assertEqual(self.journal.resumable_pages(job, "prepared"), [])

    def test_interrupted_recovery_audit_survives_restart_and_detects_damage(self):
        job = self.journal.create_job(self.original, self.output, 4)
        good = self.root / "good.txt"
        damaged = self.root / "damaged.txt"
        with self.journal.worker_session(job) as owner:
            good.write_text("page zero")
            damaged.write_text("page one")
            owner.checkpoint(0, "ocr", good)
            owner.checkpoint(1, "ocr", damaged)
            damaged.write_text("corrupted after checkpoint")

        # Model an interrupted legacy/running row without acquiring or
        # pretending to hold a worker lease; the recovery audit is read-only.
        with self.journal._open() as db:
            db.execute("UPDATE jobs SET state='running' WHERE job_id=?", (job,))
        reopened = JobJournal(self.root / "jobs.sqlite")
        audit = reopened.recovery_snapshot(job, "ocr")
        self.assertEqual(audit["state"], "running")
        self.assertEqual(audit["valid_pages"], (0,))
        self.assertEqual(audit["invalid_pages"], (1,))
        self.assertEqual(audit["missing_pages"], (1, 2, 3))
        self.assertTrue(audit["requires_worker_quiescence"])
        self.assertFalse(audit["resume_authorized"])
        self.assertEqual(reopened.get_job(job)["state"], "running")
        with self.assertRaises(JobSafetyError):
            reopened.set_state(job, "running")  # no silent concurrent worker resume
        good.unlink()
        self.assertEqual(reopened.recovery_snapshot(job, "ocr")["invalid_pages"], (0, 1))
        self.original.write_bytes(self.original.read_bytes() + b"changed")
        with self.assertRaises(SourceChangedError):
            reopened.recovery_snapshot(job, "ocr")

    def test_recovery_rejects_out_of_range_checkpoint_rows(self):
        job = self.journal.create_job(self.original, self.output, 3)
        good = self.root / "page0.txt"
        out_of_range = self.root / "orphan-page-99.txt"
        good.write_text("page zero", encoding="utf-8")
        out_of_range.write_text("not a page in this document", encoding="utf-8")
        with self.journal.worker_session(job) as owner:
            owner.checkpoint(0, "ocr", good)

            # Simulate a malformed/legacy SQLite row that bypassed checkpoint().
            # The schema only enforces page_index >= 0; recovery must also enforce
            # the immutable page_count stored with the job.
            with self.journal._open() as db:
                db.execute(
                    "INSERT INTO checkpoints VALUES (?,?,?,?,?)",
                    (job, 99, "ocr", str(out_of_range), sha256_file(out_of_range)),
                )

        audit = self.journal.recovery_snapshot(job, "ocr")
        self.assertEqual(audit["valid_pages"], (0,))
        self.assertEqual(audit["invalid_pages"], (99,))
        self.assertEqual(audit["missing_pages"], (1, 2))
        self.assertEqual(self.journal.resumable_pages(job, "ocr"), [0])
        with self.assertRaisesRegex(ValueError, "page index out of bounds"):
            self.journal.verified_checkpoint(job, 99, "ocr")

    def test_recovery_audit_is_read_only_for_paused_jobs(self):
        job = self.journal.create_job(self.original, self.output, 2)
        with self.journal.worker_session(job):
            pass  # clean worker exit persists paused state
        audit = JobJournal(self.root / "jobs.sqlite").recovery_snapshot(job, "prepared")
        self.assertEqual(audit["valid_pages"], ())
        self.assertEqual(audit["missing_pages"], (0, 1))
        self.assertFalse(audit["requires_worker_quiescence"])
        self.assertFalse(audit["resume_authorized"])
        self.assertEqual(self.journal.get_job(job)["state"], "paused")
        with self.assertRaises(ValueError):
            self.journal.recovery_snapshot(job, "bad-stage")

    def test_arabic_normalization_for_index_only(self):
        self.assertEqual(normalize_arabic_search("  إِلَى  المُدَرِّسِ  "), "الي المدرس")
        self.assertEqual(normalize_arabic_search("آثار ـ وثيقة"), "اثار وثيقة")
        with self.assertRaises(TypeError):
            normalize_arabic_search(None)


if __name__ == "__main__":
    unittest.main()
