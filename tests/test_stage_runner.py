"""Stage-runner recovery tests using synthetic artifacts, not real OCR."""
import tempfile
import unittest
from pathlib import Path

from pdfstudio import JobJournal, JobSafetyError
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


if __name__ == "__main__":
    unittest.main()
