"""B00 source-only tests (no PDF renderer or Android/Windows package)."""

import tempfile
import unittest
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

    def test_bad_header_rejected(self):
        self.original.write_text("not pdf")
        with self.assertRaises(JobSafetyError):
            self.journal.create_job(self.original, self.output, 1)

    def test_checkpoint_and_resumption_verifies_output(self):
        job = self.journal.create_job(self.original, self.output, 3)
        self.journal.set_state(job, "running")
        artifact = self.root / "page0.txt"
        artifact.write_text("recognized text")
        self.journal.checkpoint(job, 0, "ocr", artifact)
        self.assertEqual(self.journal.resumable_pages(job, "ocr"), [0])
        artifact.write_text("damaged")
        self.assertEqual(self.journal.resumable_pages(job, "ocr"), [])
        artifact.write_text("corrected")
        self.journal.checkpoint(job, 0, "ocr", artifact)
        self.journal.set_state(job, "paused")
        self.assertEqual(JobJournal(self.root / "jobs.sqlite").resumable_pages(job, "ocr"), [0])

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
        self.journal.set_state(job, "running")
        artifact = self.root / "artifact"
        artifact.write_bytes(b"data")
        with self.assertRaises(ValueError):
            self.journal.checkpoint(job, 2, "ocr", artifact)
        with self.assertRaises(ValueError):
            self.journal.checkpoint(job, 0, "invalid", artifact)
        with self.assertRaises(JobSafetyError):
            self.journal.checkpoint(job, 0, "ocr", self.original)

    def test_cannot_resume_with_missing_artifact(self):
        job = self.journal.create_job(self.original, self.output, 1)
        self.journal.set_state(job, "running")
        artifact = self.root / "artifact"
        artifact.write_text("hello")
        self.journal.checkpoint(job, 0, "prepared", artifact)
        artifact.unlink()
        self.assertEqual(self.journal.resumable_pages(job, "prepared"), [])

    def test_arabic_normalization_for_index_only(self):
        self.assertEqual(normalize_arabic_search("  إِلَى  المُدَرِّسِ  "), "الي المدرس")
        self.assertEqual(normalize_arabic_search("آثار ـ وثيقة"), "اثار وثيقة")
        with self.assertRaises(TypeError):
            normalize_arabic_search(None)


if __name__ == "__main__":
    unittest.main()
