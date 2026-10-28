"""Stage-runner recovery tests using synthetic artifacts, not real OCR."""
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pdfstudio.jobs as jobs_module
from pdfstudio import JobJournal, JobSafetyError, SourceChangedError
from pdfstudio.stage_runner import run_page_stage


class StageRunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "input.pdf"
        self.source.write_bytes(b"%PDF-1.7\\nsynthetic\\n")
        self.journal = JobJournal(self.root / "journal.sqlite")
        self.job = self.journal.create_job(self.source, self.root / "final.pdf", 3)
        self.calls = []

    def produce(self, page):
        self.calls.append(page)
        path = self.root / ("page_%d_%d.txt" % (page, len(self.calls)))
        path.write_text("page=%d" % page, encoding="utf-8")
        return path

    def test_create_job_reserves_shared_output_atomically(self):
        shared_output = self.root / "shared-final.pdf"
        start = threading.Barrier(2)

        def create_same_destination():
            start.wait(timeout=10)
            try:
                return ("created", self.journal.create_job(
                    self.source, shared_output, 3))
            except FileExistsError:
                return ("reserved", None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: create_same_destination(), range(2)))

        self.assertCountEqual(
            [status for status, _ in outcomes], ["created", "reserved"])
        with self.journal._open() as db:
            rows = db.execute(
                "SELECT job_id FROM jobs WHERE output=?",
                (str(shared_output.resolve()),),
            ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_first_run_then_verified_resume(self):
        result = run_page_stage(self.journal, self.job, "ocr", self.produce)
        self.assertEqual(result, {"processed": (0, 1, 2), "skipped": (), "cancelled": False})
        result = run_page_stage(self.journal, self.job, "ocr", self.produce)
        self.assertEqual(result["processed"], ())
        self.assertEqual(result["skipped"], (0, 1, 2))
        self.assertEqual(self.calls, [0, 1, 2])

    def test_damaged_artifact_is_rebuilt(self):
        run_page_stage(self.journal, self.job, "ocr", self.produce)
        (self.root / "page_1_2.txt").write_text("tampered")
        result = run_page_stage(self.journal, self.job, "ocr", self.produce)
        self.assertEqual(result["processed"], (1,))
        self.assertEqual(result["skipped"], (0, 2))

    def test_cancel_then_resume_without_duplicate_pages(self):
        result = run_page_stage(
            self.journal, self.job, "ocr", self.produce,
            cancel_requested=lambda: len(self.calls) >= 1)
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.journal.get_job(self.job)["state"], "paused")
        result = run_page_stage(self.journal, self.job, "ocr", self.produce)
        self.assertEqual(result["processed"], (1, 2))
        self.assertEqual(result["skipped"], (0,))

    def test_processor_error_leaves_resumable_job(self):
        def fail_second(page):
            if page == 1:
                raise RuntimeError("worker failure")
            return self.produce(page)
        with self.assertRaises(RuntimeError):
            run_page_stage(self.journal, self.job, "ocr", fail_second)
        self.assertEqual(self.journal.get_job(self.job)["state"], "paused")
        result = run_page_stage(self.journal, self.job, "ocr", self.produce)
        self.assertEqual(result["processed"], (1, 2))
        self.assertEqual(result["skipped"], (0,))

    def test_reject_invalid_callback_without_claim(self):
        with self.assertRaises(TypeError):
            run_page_stage(self.journal, self.job, "ocr", None)
        self.assertEqual(self.journal.get_job(self.job)["state"], "pending")


    def test_resume_uses_linear_page_lookups_not_full_rescans(self):
        large_job = self.journal.create_job(self.source, self.root / "large.pdf", 128)
        run_page_stage(self.journal, large_job, "ocr", self.produce)
        original = self.journal.verified_checkpoint
        original_hash = jobs_module.sha256_file
        checks = []
        source_hashes = []

        def check_one(job_id, page, stage):
            checks.append(page)
            return original(job_id, page, stage)

        def count_source_hash(path, *args, **kwargs):
            if Path(path).resolve() == self.source.resolve():
                source_hashes.append(1)
            return original_hash(path, *args, **kwargs)

        with patch("pdfstudio.jobs.sha256_file", side_effect=count_source_hash):
            with patch.object(self.journal, "resumable_pages",
                              side_effect=AssertionError("quadratic full scan")):
                with patch.object(self.journal, "verified_checkpoint", side_effect=check_one):
                    result = run_page_stage(self.journal, large_job, "ocr", self.produce)
        self.assertEqual(result["processed"], ())
        self.assertEqual(len(result["skipped"]), 128)
        self.assertEqual(checks, list(range(128)))
        # Full source SHA is constant per stage (entry/exit audits), not once or
        # twice per page. Artifact SHA verification remains per checkpoint.
        self.assertEqual(len(source_hashes), 6)

    def test_checkpoint_changed_after_snapshot_is_reprocessed(self):
        run_page_stage(self.journal, self.job, "ocr", self.produce)
        original = self.root / "page_0_1.txt"
        calls = 0

        def mutate_before_first_skip():
            nonlocal calls
            calls += 1
            if calls == 1:
                original.write_text("corrupted after audit", encoding="utf-8")
            return False

        result = run_page_stage(
            self.journal, self.job, "ocr", self.produce,
            cancel_requested=mutate_before_first_skip)
        self.assertEqual(result["processed"], (0,))
        self.assertEqual(result["skipped"], (1, 2))
        self.assertEqual(self.journal.recovery_snapshot(self.job, "ocr")["invalid_pages"], ())

    def test_source_change_during_page_stage_fails_closed(self):
        def mutate_source(page):
            artifact = self.root / ("mutated-page-%d.txt" % page)
            artifact.write_text("page=%d" % page, encoding="utf-8")
            if page == 0:
                self.source.write_bytes(b"%PDF-1.7\\nchanged during processing\\n")
            return artifact

        with self.assertRaises(SourceChangedError):
            run_page_stage(self.journal, self.job, "ocr", mutate_source)
        self.assertEqual(self.journal.get_job(self.job)["state"], "failed")
        with self.assertRaises(SourceChangedError):
            self.journal.recovery_snapshot(self.job, "ocr")
        with self.assertRaises(SourceChangedError):
            run_page_stage(self.journal, self.job, "ocr", self.produce)

    def test_single_page_checkpoint_validation(self):
        with self.assertRaises(ValueError):
            self.journal.verified_checkpoint(self.job, -1, "ocr")
        with self.assertRaises(ValueError):
            self.journal.verified_checkpoint(self.job, True, "ocr")
        with self.assertRaises(ValueError):
            self.journal.verified_checkpoint(self.job, 0, "unrecognized")
        self.assertFalse(self.journal.verified_checkpoint(self.job, 0, "ocr"))
        run_page_stage(self.journal, self.job, "ocr", self.produce)
        self.assertTrue(self.journal.verified_checkpoint(self.job, 0, "ocr"))
        (self.root / "page_0_1.txt").unlink()
        self.assertFalse(self.journal.verified_checkpoint(self.job, 0, "ocr"))


if __name__ == "__main__":
    unittest.main()
