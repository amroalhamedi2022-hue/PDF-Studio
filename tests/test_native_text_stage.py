"""Real pypdf subprocess + SQLite resume tests for born-digital text only."""
import os
import multiprocessing as mp
import tempfile
import time
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pdfstudio import JobJournal, JobSafetyError, sha256_file
from pdfstudio.bounded_parser import IsolatedPdfReader, ParserIsolationError
from pdfstudio.native_text_stage import (
    cleanup_orphaned_native_text_artifacts,
    run_native_text_stage,
)
from pdfstudio.worker_lease import exclusive_artifact_directory_lock



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

    def test_total_character_cap_applies_across_resume(self):
        # Page 0 consumes 20 characters; each later page consumes 14. The
        # first invocation commits page 0, then fails at page 1 under a 25-char
        # total cap. Resume must count those 20 already-committed characters:
        # only five remain, so page 1 must not be committed.
        source = self.root / "character-cap.pdf"
        writer = PdfWriter()
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font)
        for text in ("Page zero has twenty", "Page 1 English", "Page 2 English"):
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
            })
            stream = DecodedStreamObject()
            stream.set_data(
                f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode("ascii")
            )
            page[NameObject("/Contents")] = writer._add_object(stream)
        with source.open("wb") as output:
            writer.write(output)

        job = self.journal.create_job(source, self.root / "character-cap-output.pdf", 3)
        artifacts = self.root / "character-cap-artifacts"
        with self.assertRaises(ParserIsolationError):
            run_native_text_stage(
                self.journal, job, artifacts, max_total_chars=25,
            )
        first_snapshot = self.journal.recovery_snapshot(job, "native_text")
        self.assertEqual(first_snapshot["valid_pages"], (0,))
        self.assertEqual(first_snapshot["missing_pages"], (1, 2))

        with self.assertRaises(ParserIsolationError):
            run_native_text_stage(
                self.journal, job, artifacts, max_total_chars=25,
            )

        resumed_snapshot = self.journal.recovery_snapshot(job, "native_text")
        self.assertEqual(resumed_snapshot["valid_pages"], (0,))
        self.assertEqual(resumed_snapshot["missing_pages"], (1, 2))
        self.assertEqual(self.journal.get_job(job)["state"], "paused")

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

    def test_checkpoint_byte_cap_rejects_before_writing_over_budget_page(self):
        with self.assertRaisesRegex(ValueError, "byte cap"):
            run_native_text_stage(
                self.journal, self.job, self.artifacts, max_checkpoint_bytes=5
            )
        snapshot = self.journal.recovery_snapshot(self.job, "native_text")
        self.assertEqual(snapshot["valid_pages"], ())
        self.assertEqual(snapshot["missing_pages"], (0, 1, 2))
        self.assertEqual(list(self.artifacts.glob("page-*.txt")) if self.artifacts.exists() else [], [])
        self.assertEqual(sha256_file(self.source), self.digest)

    def test_checkpoint_byte_cap_applies_to_verified_resume_artifacts(self):
        polls = 0
        def cancel_after_one_page():
            nonlocal polls
            polls += 1
            return polls >= 3
        first = run_native_text_stage(
            self.journal, self.job, self.artifacts,
            max_checkpoint_bytes=1024, cancel_requested=cancel_after_one_page,
        )
        self.assertEqual(first["processed"], (0,))
        with self.assertRaisesRegex(ValueError, "registered checkpoint artifacts"):
            run_native_text_stage(
                self.journal, self.job, self.artifacts, max_checkpoint_bytes=1
            )
        self.assertEqual(self.journal.recovery_snapshot(self.job, "native_text")["valid_pages"], (0,))
        self.assertEqual(self.journal.get_job(self.job)["state"], "paused")

    def test_checkpoint_byte_cap_rejects_invalid_values(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_native_text_stage(
                    self.journal, self.job, self.artifacts, max_checkpoint_bytes=value
                )
        self.assertEqual(self.journal.get_job(self.job)["state"], "pending")

    def test_orphan_cleanup_removes_only_old_unreferenced_stage_files(self):
        run_native_text_stage(self.journal, self.job, self.artifacts)
        protected = self._artifact(1)
        stale_page = self.artifacts / "page-00000009-abandoned.txt"
        stale_temp = self.artifacts / ".text-abandoned.tmp"
        recent_page = self.artifacts / "page-00000010-recent.txt"
        stale_page.write_text("orphan page", encoding="utf-8")
        stale_temp.write_bytes(b"partial")
        recent_page.write_text("recent orphan", encoding="utf-8")
        old = time.time() - 3 * 24 * 60 * 60
        os.utime(stale_page, (old, old))
        os.utime(stale_temp, (old, old))
        report = cleanup_orphaned_native_text_artifacts(
            self.journal, self.artifacts, min_age_seconds=3600
        )
        removed_names = {Path(item).name for item in report["removed"]}
        self.assertIn(stale_page.name, removed_names)
        self.assertIn(stale_temp.name, removed_names)
        self.assertEqual(report["bytes_removed"], len("orphan page".encode()) + len(b"partial"))
        self.assertEqual(report["remaining_orphan_bytes"], recent_page.stat().st_size)
        self.assertFalse(stale_page.exists())
        self.assertFalse(stale_temp.exists())
        self.assertTrue(recent_page.exists())
        self.assertTrue(protected.exists())
        self.assertEqual(self.journal.recovery_snapshot(self.job, "native_text")["valid_pages"], (0, 1, 2))

    def test_orphan_cleanup_preserves_hardlink_alias_of_registered_artifact(self):
        run_native_text_stage(self.journal, self.job, self.artifacts)
        registered = self._artifact(1)
        alias = self.artifacts / "page-00000001-hardlink.txt"
        try:
            os.link(registered, alias)
        except OSError as exc:
            self.skipTest("hard links unavailable on this filesystem: %s" % exc)
        old = time.time() - 3 * 24 * 60 * 60
        os.utime(alias, (old, old))
        report = cleanup_orphaned_native_text_artifacts(
            self.journal, self.artifacts, min_age_seconds=3600
        )
        self.assertNotIn(str(alias), report["removed"])
        self.assertTrue(alias.exists())
        self.assertEqual(alias.read_bytes(), registered.read_bytes())

    def test_orphan_cleanup_refuses_to_race_an_active_directory_writer(self):
        self.artifacts.mkdir()
        with exclusive_artifact_directory_lock(self.artifacts):
            with self.assertRaisesRegex(RuntimeError, "owns this artifact directory"):
                cleanup_orphaned_native_text_artifacts(
                    self.journal, self.artifacts, min_age_seconds=0
                )

    def test_orphan_cleanup_validates_age_and_timestamp(self):
        self.artifacts.mkdir()
        for kwargs in ({"min_age_seconds": -1}, {"min_age_seconds": True},
                       {"min_age_seconds": float("inf")}, {"now": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                cleanup_orphaned_native_text_artifacts(
                    self.journal, self.artifacts, **kwargs
                )

    def test_native_stage_cleans_stale_orphans_before_enforcing_byte_budget(self):
        self.artifacts.mkdir()
        orphan = self.artifacts / "page-00000009-crash-leftover.txt"
        orphan.write_bytes(b"x" * 100)
        old = time.time() - 3 * 24 * 60 * 60
        os.utime(orphan, (old, old))
        result = run_native_text_stage(
            self.journal, self.job, self.artifacts, max_checkpoint_bytes=64
        )
        self.assertEqual(result["processed"], (0, 1, 2))
        self.assertFalse(orphan.exists())
        self.assertEqual(self.journal.recovery_snapshot(self.job, "native_text")["valid_pages"], (0, 1, 2))

    def test_recent_orphan_bytes_count_toward_checkpoint_budget(self):
        self.artifacts.mkdir()
        orphan = self.artifacts / "page-00000009-recent-leftover.txt"
        orphan.write_bytes(b"x" * 60)
        with self.assertRaisesRegex(ValueError, "byte cap"):
            run_native_text_stage(
                self.journal, self.job, self.artifacts, max_checkpoint_bytes=64
            )
        self.assertTrue(orphan.exists())
        self.assertEqual(self.journal.recovery_snapshot(self.job, "native_text")["valid_pages"], ())


if __name__ == "__main__":
    unittest.main()
