"""Cross-process ownership and crash recovery for B01 journal (no real OCR)."""
import hashlib
import multiprocessing as mp
import os
import tempfile
import unittest
from pathlib import Path

from pdfstudio import JobJournal, JobSafetyError, SourceChangedError
from pdfstudio.worker_lease import exclusive_artifact_directory_lock, exclusive_worker_lock


def _abrupt_worker_exit(database: str, job_id: str) -> None:
    with JobJournal(database).worker_session(job_id):
        os._exit(0)


class WorkerLeaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "input.pdf"
        self.source.write_bytes(b"%PDF-1.7\\nfixture\\n")
        self.database = self.root / "jobs.sqlite"
        self.journal = JobJournal(self.database)
        self.output = self.root / "out.pdf"
        self.job = self.journal.create_job(self.source, self.output, 3)

    def test_checkpoint_and_clean_resume(self):
        page = self.root / "page.txt"
        page.write_text("hello")
        with self.journal.worker_session(self.job) as owner:
            owner.checkpoint(0, "ocr", page)
            self.assertEqual(owner.recovery_snapshot("ocr")["valid_pages"], (0,))
            with self.assertRaises(JobSafetyError):
                self.journal.checkpoint(self.job, 1, "ocr", page)
            with self.assertRaises(JobSafetyError):
                self.journal.set_state(self.job, "paused")
            with self.assertRaises(RuntimeError):
                with JobJournal(self.database).worker_session(self.job):
                    pass
        self.assertEqual(self.journal.get_job(self.job)["state"], "paused")
        with self.journal.worker_session(self.job) as owner:
            self.assertEqual(owner.recovery_snapshot("ocr")["missing_pages"], (1, 2))
            owner.set_state("complete")
        self.assertEqual(self.journal.get_job(self.job)["state"], "complete")
        with self.assertRaises(JobSafetyError):
            with self.journal.worker_session(self.job):
                pass

    def test_checkpoint_artifacts_are_unique_immutable_and_not_the_journal(self):
        page = self.root / "page.txt"
        page.write_text("first page", encoding="utf-8")
        with self.journal.worker_session(self.job) as owner:
            with self.assertRaisesRegex(JobSafetyError, "journal database"):
                owner.checkpoint(0, "ocr", self.database)
            owner.checkpoint(0, "ocr", page)
            # A different page may not alias the same mutable file.
            with self.assertRaisesRegex(JobSafetyError, "already assigned"):
                owner.checkpoint(1, "ocr", page)
            # Replacing bytes at a registered path violates immutability;
            # a processor must publish a new path and then checkpoint it.
            page.write_text("replacement bytes", encoding="utf-8")
            with self.assertRaisesRegex(JobSafetyError, "immutable"):
                owner.checkpoint(0, "ocr", page)
        snapshot = self.journal.recovery_snapshot(self.job, "ocr")
        self.assertEqual(snapshot["invalid_pages"], (0,))

    def test_stale_owner_fenced_after_resume(self):
        page = self.root / "page.txt"
        page.write_text("safe")
        with self.journal.worker_session(self.job) as owner:
            stale_token = owner._token
        with self.journal.worker_session(self.job) as current:
            self.assertNotEqual(stale_token, current._token)
            with self.assertRaises(JobSafetyError):
                self.journal.checkpoint(self.job, 0, "ocr", page, worker_token=stale_token)
            with self.assertRaises(JobSafetyError):
                self.journal.set_state(self.job, "paused", worker_token=stale_token)
            current.checkpoint(0, "ocr", page)

    def test_crash_releases_lock_and_new_session_can_resume(self):
        process = mp.get_context("spawn").Process(
            target=_abrupt_worker_exit, args=(str(self.database), self.job))
        process.start()
        process.join(timeout=20)
        if process.is_alive():
            process.kill()
            process.join()
            self.fail("worker failed to exit")
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(self.journal.get_job(self.job)["state"], "running")
        with self.journal.worker_session(self.job) as owner:
            self.assertEqual(owner.recovery_snapshot("prepared")["missing_pages"], (0, 1, 2))
        self.assertEqual(self.journal.get_job(self.job)["state"], "paused")

    def test_legacy_running_job_cannot_claim_without_owner_history(self):
        self.journal.set_state(self.job, "running")
        with self.assertRaises(JobSafetyError):
            with self.journal.worker_session(self.job):
                pass

    def test_source_mutation_and_existing_output_refuse_resume(self):
        with self.journal.worker_session(self.job):
            pass
        self.output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            with self.journal.worker_session(self.job):
                pass
        self.output.unlink()
        self.source.write_bytes(self.source.read_bytes() + b"changed")
        with self.assertRaises(SourceChangedError):
            with self.journal.worker_session(self.job):
                pass


    def test_checkpoint_verification_rejects_symlink_to_same_inode(self):
        page = self.root / "page.txt"
        alias = self.root / "page-hardlink.txt"
        page.write_text("immutable checkpoint", encoding="utf-8")
        with self.journal.worker_session(self.job) as owner:
            owner.checkpoint(0, "ocr", page)

        try:
            os.link(page, alias)
        except OSError as exc:
            self.skipTest("hard-link creation is unavailable on this host: %s" % exc)
        page.unlink()
        self._symlink_or_skip(page, alias)

        # The symlink resolves to the exact same inode and bytes. It must still
        # be rejected because the recorded artifact path is no longer a regular
        # file entry, rather than being silently trusted via stat()/open().
        snapshot = self.journal.recovery_snapshot(self.job, "ocr")
        self.assertEqual(snapshot["valid_pages"], ())
        self.assertEqual(snapshot["invalid_pages"], (0,))

    def test_checkpoint_registration_rejects_hard_link_alias(self):
        page = self.root / "page.txt"
        alias = self.root / "page-alias.txt"
        page.write_text("immutable checkpoint", encoding="utf-8")
        self._hardlink_or_skip(page, alias)

        with self.journal.worker_session(self.job) as owner:
            with self.assertRaisesRegex(JobSafetyError, "hard-link aliases"):
                owner.checkpoint(0, "ocr", page)

        self.assertEqual(page.read_text(encoding="utf-8"), "immutable checkpoint")
        self.assertEqual(alias.read_text(encoding="utf-8"), "immutable checkpoint")
        self.assertEqual(page.stat().st_nlink, 2)
        self.assertEqual(self.journal.recovery_snapshot(self.job, "ocr")["valid_pages"], ())

    def test_recovery_rejects_hard_link_added_after_checkpoint(self):
        page = self.root / "page.txt"
        alias = self.root / "page-alias.txt"
        page.write_text("immutable checkpoint", encoding="utf-8")
        with self.journal.worker_session(self.job) as owner:
            owner.checkpoint(0, "ocr", page)
        self._hardlink_or_skip(page, alias)

        snapshot = self.journal.recovery_snapshot(self.job, "ocr")
        self.assertEqual(snapshot["valid_pages"], ())
        self.assertEqual(snapshot["invalid_pages"], (0,))
        self.assertEqual(page.stat().st_nlink, 2)
        self.assertEqual(alias.read_bytes(), page.read_bytes())

    def _hardlink_or_skip(self, source: Path, alias: Path) -> None:
        try:
            os.link(source, alias)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("hard-link creation is unavailable on this host: %s" % exc)

    def _symlink_or_skip(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink creation is unavailable on this host: %s" % exc)

    def test_worker_lock_rejects_symlink_alias_without_touching_target(self):
        target = self.root / "unrelated-target.bin"
        target.write_bytes(b"must remain unchanged")
        suffix = hashlib.sha256(self.job.encode("utf-8")).hexdigest()
        lock_path = self.database.resolve().with_name(
            self.database.name + "." + suffix + ".worker.lock"
        )
        self._symlink_or_skip(lock_path, target)

        with self.assertRaisesRegex(RuntimeError, "symlink|reparse|safely"):
            with exclusive_worker_lock(self.database, self.job):
                self.fail("a symlink must never become a worker lock")

        self.assertEqual(target.read_bytes(), b"must remain unchanged")
        self.assertTrue(lock_path.is_symlink())

    def test_artifact_directory_lock_rejects_symlink_alias_without_touching_target(self):
        target = self.root / "unrelated-artifacts-target.bin"
        target.write_bytes(b"must remain unchanged")
        lock_path = self.root / ".pdfstudio-artifacts.lock"
        self._symlink_or_skip(lock_path, target)

        with self.assertRaisesRegex(RuntimeError, "symlink|reparse|safely"):
            with exclusive_artifact_directory_lock(self.root):
                self.fail("a symlink must never become an artifact-directory lock")

        self.assertEqual(target.read_bytes(), b"must remain unchanged")
        self.assertTrue(lock_path.is_symlink())


if __name__ == "__main__":
    unittest.main()
