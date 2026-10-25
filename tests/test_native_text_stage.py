"""Real pypdf subprocess + SQLite resume tests for born-digital text only."""
import os
import multiprocessing as mp
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pdfstudio import JobJournal, JobSafetyError, sha256_file
from pdfstudio.bounded_parser import IsolatedPdfReader, ParserIsolationError
from pdfstudio.native_text_stage import run_native_text_stage



def _crash_after_one_checkpoint(database: str, job_id: str, folder: str) -> None:
    calls = 0
    def crash():
        nonlocal calls
        calls += 1
        if calls >= 3:
            os._exit(23)
        return False
    run_native_text_stage(JobJournal(database), job_id, folder, cancel_requested=crash)


@unittest.skipUnless(os.name in ("posix", "nt"), "OS resource caps required")
class NativeTextStageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "input.pdf"
        writer = PdfWriter()
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font)
        for index in range(3):
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
            })
            stream = DecodedStreamObject()
            stream.set_data(
                ("BT /F1 12 Tf 40 700 Td (Page %d English) Tj ET" % index).encode("ascii")
            )
            page[NameObject("/Contents")] = writer._add_object(stream)
        with self.source.open("wb") as output:
            writer.write(output)
        self.digest = sha256_file(self.source)
        self.journal = JobJournal(self.root / "journal.sqlite")
        self.output = self.root / "output.pdf"
        self.job = self.journal.create_job(self.source, self.output, 3)
        self.artifacts = self.root / "artifacts"

    def _artifact(self, page):
        with self.journal._open() as db:
            row = db.execute(
                "SELECT artifact FROM checkpoints WHERE job_id=? AND page_index=? AND stage='native_text'",
                (self.job, page),
            ).fetchone()
        return Path(row["artifact"])

    def test_selected_parser_extracts_only_requested_pages(self):
        with IsolatedPdfReader(
            self.source, max_pages=10, max_chars_per_page=100,
            max_total_chars=100, page_indexes=[2, 0],
        ) as reader:
            self.assertEqual(reader.page_count, 3)
            self.assertEqual(list(reader.pages()), [(0, "Page 0 English"), (2, "Page 2 English")])
        with IsolatedPdfReader(
            self.source, max_pages=10, max_chars_per_page=100,
            max_total_chars=100, page_indexes=[],
        ) as reader:
            self.assertEqual(list(reader.pages()), [])
        self.assertEqual(sha256_file(self.source), self.digest)

    def test_selected_parser_rejects_invalid_selection(self):
        for indexes in ([0, 0], [-1], [True], ["0"]):
            with self.subTest(indexes=indexes):
                with self.assertRaises(ValueError):
                    IsolatedPdfReader(
                        self.source, max_pages=10, max_chars_per_page=100,
                        max_total_chars=100, page_indexes=indexes,
                    )
        with self.assertRaises(ParserIsolationError):
            with IsolatedPdfReader(
                self.source, max_pages=10, max_chars_per_page=100,
                max_total_chars=100, page_indexes=[3],
            ):
                pass

    def test_native_stage_writes_verified_text_and_resumes(self):
        first = run_native_text_stage(self.journal, self.job, self.artifacts)
        self.assertEqual(first, {"processed": (0, 1, 2), "skipped": (), "cancelled": False})
        self.assertEqual(self._artifact(1).read_text(encoding="utf-8"), "Page 1 English")
        second = run_native_text_stage(self.journal, self.job, self.artifacts)
        self.assertEqual(second["processed"], ())
        self.assertEqual(second["skipped"], (0, 1, 2))
        self.assertEqual(self.journal.recovery_snapshot(self.job, "native_text")["valid_pages"], (0, 1, 2))
        self.assertEqual(sha256_file(self.source), self.digest)
        self.assertFalse(self.output.exists())

    def test_tampered_checkpoint_is_reextracted(self):
        run_native_text_stage(self.journal, self.job, self.artifacts)
        old = self._artifact(1)
        old.write_text("corrupted", encoding="utf-8")
        result = run_native_text_stage(self.journal, self.job, self.artifacts)
        self.assertEqual(result["processed"], (1,))
        self.assertEqual(result["skipped"], (0, 2))
        self.assertNotEqual(self._artifact(1), old)
        self.assertEqual(self._artifact(1).read_text(encoding="utf-8"), "Page 1 English")

    def test_cancel_midstream_then_resume_remaining_pages(self):
        checks = 0
        def cancel():
            nonlocal checks
            checks += 1
            return checks >= 3
        first = run_native_text_stage(
            self.journal, self.job, self.artifacts, cancel_requested=cancel
        )
        self.assertTrue(first["cancelled"])
        self.assertEqual(first["processed"], (0,))
        self.assertEqual(self.journal.get_job(self.job)["state"], "paused")
        second = run_native_text_stage(self.journal, self.job, self.artifacts)
        self.assertEqual(second["processed"], (1, 2))
        self.assertEqual(second["skipped"], (0,))

    def test_process_crash_keeps_verified_page_and_recovers(self):
        process = mp.get_context("spawn").Process(
            target=_crash_after_one_checkpoint,
            args=(str(self.journal.database), self.job, str(self.artifacts)),
        )
        process.start()
        process.join(timeout=45)
        if process.is_alive():
            process.kill()
            process.join()
            self.fail("worker did not exit within timeout")
        self.assertEqual(process.exitcode, 23)
        self.assertEqual(self.journal.get_job(self.job)["state"], "running")
        self.assertEqual(self.journal.recovery_snapshot(self.job, "native_text")["valid_pages"], (0,))
        resumed = run_native_text_stage(self.journal, self.job, self.artifacts)
        self.assertEqual(resumed["processed"], (1, 2))
        self.assertEqual(resumed["skipped"], (0,))
        self.assertEqual(self.journal.get_job(self.job)["state"], "paused")

    def test_page_count_mismatch_fails_without_checkpoint(self):
        other = self.journal.create_job(self.source, self.root / "wrong.pdf", 2)
        with self.assertRaises(JobSafetyError):
            run_native_text_stage(self.journal, other, self.artifacts)
        self.assertEqual(self.journal.get_job(other)["state"], "paused")
        self.assertEqual(self.journal.recovery_snapshot(other, "native_text")["valid_pages"], ())


if __name__ == "__main__":
    unittest.main()
