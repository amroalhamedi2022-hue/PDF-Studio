"""B01 integration: fenced native-text checkpoints -> transactional SQLite FTS5.

Only synthetic PDFs and private temporary artifacts; no OCR or user documents.
"""
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pdfstudio.jobs import JobJournal, sha256_file
from pdfstudio.library_index import LibraryIndex, LibraryIndexCancelled, LibraryIndexError
from pdfstudio.native_text_stage import run_native_text_stage


@unittest.skipUnless(os.name in ("posix", "nt"), "bounded parser host required")
class CheckpointIndexTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pdf = self.root / "original.pdf"
        writer = PdfWriter()
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font)
        for index in range(2):
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
            })
            stream = DecodedStreamObject()
            stream.set_data(
                ("BT /F1 12 Tf 40 700 Td (Page %d native) Tj ET" % index).encode("ascii")
            )
            page[NameObject("/Contents")] = writer._add_object(stream)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        self.digest = sha256_file(self.pdf)
        self.journal = JobJournal(self.root / "jobs.sqlite")
        self.job_id = self.journal.create_job(self.pdf, self.root / "output.pdf", 2)
        self.index = LibraryIndex(self.root / "search.sqlite")
        self.artifacts = self.root / "private-artifacts"

    def stage(self):
        return run_native_text_stage(self.journal, self.job_id, self.artifacts)

    def checkpoint_file(self, page):
        with self.journal._open() as db:
            row = db.execute(
                "SELECT artifact FROM checkpoints WHERE job_id=? AND stage='native_text' AND page_index=?",
                (self.job_id, page),
            ).fetchone()
        return Path(row["artifact"])

    def replace_page(self, page, data):
        self.artifacts.mkdir(exist_ok=True)
        artifact = self.artifacts / ("replacement-%d-%d.txt" % (page, len(data)))
        artifact.write_bytes(data)
        with self.journal.worker_session(self.job_id) as owner:
            owner.checkpoint(page, "native_text", artifact)

    def test_real_pdf_stage_indexes_page_linked_text_without_modifying_source(self):
        self.stage()
        result = self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.assertEqual((result.page_count, result.indexed_pages, result.provenance), (2, 2, "native"))
        self.assertEqual([(hit.page_number, hit.provenance) for hit in self.index.search("page 1")], [(2, "native")])
        self.assertEqual(sha256_file(self.pdf), self.digest)
        self.assertFalse((self.root / "output.pdf").exists())
        self.index.integrity_check()

    def test_same_source_changed_checkpoint_replaces_stale_search_terms(self):
        self.stage()
        self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.replace_page(0, "إِلَى المُدَرِّسِ corrected".encode("utf-8"))
        result = self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.assertTrue(result.changed)
        self.assertEqual(self.index.search("page 0"), [])
        hits = self.index.search("الى المدرس")
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0].page_number, hits[0].provenance), (1, "native"))
        self.index.integrity_check()

    def test_corrupted_or_missing_artifact_rolls_back_old_fts(self):
        self.stage()
        self.index.index_native_text_checkpoints(self.journal, self.job_id)
        artifact = self.checkpoint_file(1)
        artifact.write_text("corrupted after checkpoint", encoding="utf-8")
        with self.assertRaises(LibraryIndexError):
            self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.assertEqual(len(self.index.search("page 1")), 1)
        artifact.unlink()
        with self.assertRaises(LibraryIndexError):
            self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.assertEqual(len(self.index.search("page 1")), 1)
        self.index.integrity_check()

    def test_missing_stage_page_rejected_without_partial_index(self):
        self.artifacts.mkdir()
        self.replace_page(0, b"only first page")
        with self.assertRaises(LibraryIndexError):
            self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.assertEqual(self.index.search("only"), [])

    def test_forged_page_count_cannot_be_indexed(self):
        wrong = self.journal.create_job(self.pdf, self.root / "wrong.pdf", 1)
        self.artifacts.mkdir()
        artifact = self.artifacts / "forged.txt"
        artifact.write_text("forged", encoding="utf-8")
        with self.journal.worker_session(wrong) as owner:
            owner.checkpoint(0, "native_text", artifact)
        with self.assertRaises(LibraryIndexError):
            self.index.index_native_text_checkpoints(self.journal, wrong)
        self.assertEqual(self.index.search("forged"), [])

    def test_invalid_utf8_and_byte_cap_fail_closed(self):
        self.stage()
        self.replace_page(0, b"\xff\xfe")
        with self.assertRaises(LibraryIndexError):
            self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.replace_page(0, b"x" * 80)
        small = LibraryIndex(self.root / "small.sqlite", max_chars_per_page=10)
        with self.assertRaises(LibraryIndexError):
            small.index_native_text_checkpoints(self.journal, self.job_id)
        self.assertEqual(small.search("xxx"), [])

    def test_mid_ingest_cancellation_preserves_previous_index(self):
        self.stage()
        self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.replace_page(0, b"replacement")
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            return calls >= 6

        with self.assertRaises(LibraryIndexCancelled):
            self.index.index_native_text_checkpoints(
                self.journal, self.job_id, cancel_requested=cancel
            )
        self.assertEqual(len(self.index.search("page 0")), 1)
        self.assertEqual(self.index.search("replacement"), [])
        self.index.integrity_check()


    def ocr_checkpoint(self, page, data):
        self.artifacts.mkdir(exist_ok=True)
        artifact = self.artifacts / ("ocr-%d-%s.txt" % (page, uuid.uuid4().hex))
        artifact.write_bytes(data)
        with self.journal.worker_session(self.job_id) as owner:
            owner.checkpoint(page, "ocr", artifact)
        return artifact

    def test_ocr_checkpoints_index_arabic_and_english_with_page_links(self):
        self.ocr_checkpoint(0, "إِلَى الطَّبِيب English".encode("utf-8"))
        self.ocr_checkpoint(1, "Second scanned page".encode("utf-8"))
        result = self.index.index_ocr_checkpoints(self.journal, self.job_id)
        self.assertEqual((result.page_count, result.indexed_pages, result.provenance),
                         (2, 2, "ocr"))
        self.assertEqual(self.index.search("الى الطبيب")[0].page_number, 1)
        self.assertEqual(self.index.search("scanned")[0].page_number, 2)
        self.assertEqual(sha256_file(self.pdf), self.digest)
        self.assertFalse((self.root / "output.pdf").exists())
        self.index.integrity_check()

    def test_ocr_incomplete_stage_does_not_replace_native_search(self):
        self.stage()
        self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.ocr_checkpoint(0, b"unpublished OCR")
        with self.assertRaises(LibraryIndexError):
            self.index.index_ocr_checkpoints(self.journal, self.job_id)
        self.assertEqual(self.index.search("unpublished"), [])
        self.assertEqual(self.index.search("page 1")[0].provenance, "native")
        self.index.integrity_check()

    def test_ocr_corruption_and_invalid_utf8_preserve_old_index(self):
        self.ocr_checkpoint(0, b"first OCR")
        artifact = self.ocr_checkpoint(1, b"second OCR")
        self.index.index_ocr_checkpoints(self.journal, self.job_id)
        artifact.write_bytes(b"tampered")
        with self.assertRaises(LibraryIndexError):
            self.index.index_ocr_checkpoints(self.journal, self.job_id)
        self.assertEqual(len(self.index.search("second")), 1)
        self.ocr_checkpoint(1, b"\xff")
        with self.assertRaises(LibraryIndexError):
            self.index.index_ocr_checkpoints(self.journal, self.job_id)
        self.assertEqual(len(self.index.search("second")), 1)
        self.index.integrity_check()

    def test_ocr_correction_same_source_replaces_stale_terms(self):
        self.ocr_checkpoint(0, b"incorrect")
        self.ocr_checkpoint(1, b"untouched")
        self.index.index_ocr_checkpoints(self.journal, self.job_id)
        self.ocr_checkpoint(0, b"corrected")
        self.assertTrue(self.index.index_ocr_checkpoints(self.journal, self.job_id).changed)
        self.assertEqual(self.index.search("incorrect"), [])
        self.assertEqual(self.index.search("corrected")[0].provenance, "ocr")
        self.index.integrity_check()

    def test_ocr_cancel_rolls_back_search_transaction(self):
        self.stage()
        self.index.index_native_text_checkpoints(self.journal, self.job_id)
        self.ocr_checkpoint(0, b"new scan")
        self.ocr_checkpoint(1, b"new scan")
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            return calls >= 6
        with self.assertRaises(LibraryIndexCancelled):
            self.index.index_ocr_checkpoints(
                self.journal, self.job_id, cancel_requested=cancel
            )
        self.assertEqual(self.index.search("scan"), [])
        self.assertEqual(self.index.search("page 0")[0].provenance, "native")
        self.index.integrity_check()



if __name__ == "__main__":
    unittest.main()
