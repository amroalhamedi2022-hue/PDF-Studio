"""Use synthetically generated valid PDFs; no user's scanned documents."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pdfstudio.inspection import DocumentInspectionError, inspect_pdf
from pdfstudio.jobs import sha256_file_before_deadline
from pdfstudio.windows_job import WindowsJobError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sample.pdf"

    def write_pdf(self, *, with_text=False, password=None):
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=400, height=600)
        writer.add_metadata({"/Title": "Synthetic QA", "/Author": "PDF Studio"})
        if with_text:
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(b'BT /F1 12 Tf 40 700 Td (Hello PDF Studio) Tj ET')
            page[NameObject('/Contents')] = writer._add_object(stream)
        if password:
            writer.encrypt(password)
        with self.path.open('wb') as fh:
            writer.write(fh)

    def test_blank_document_preflight(self):
        self.write_pdf()
        before = self.path.read_bytes()
        info = inspect_pdf(self.path)
        self.assertEqual(info.page_count, 2)
        self.assertEqual([p.width_pt for p in info.sampled_pages], [612, 400])
        self.assertFalse(any(p.has_extracted_text for p in info.sampled_pages))
        self.assertEqual(info.title, 'Synthetic QA')
        self.assertEqual(before, self.path.read_bytes())

    def test_extractable_text_detected_without_ocr(self):
        self.write_pdf(with_text=True)
        info = inspect_pdf(self.path)
        self.assertTrue(info.sampled_pages[0].has_extracted_text)
        self.assertFalse(info.sampled_pages[1].has_extracted_text)

    def test_encryption_requires_correct_password(self):
        self.write_pdf(password='secret')
        info = inspect_pdf(self.path)
        self.assertTrue(info.password_required)
        self.assertIsNone(info.page_count)
        self.assertEqual(inspect_pdf(self.path, password='wrong').page_count, None)
        unlocked = inspect_pdf(self.path, password='secret')
        self.assertEqual(unlocked.page_count, 2)
        self.assertTrue(unlocked.encrypted)
        self.assertFalse(unlocked.password_required)

    def test_bad_header_and_malformed_pdf_rejected(self):
        self.path.write_bytes(b'garbage')
        with self.assertRaises(DocumentInspectionError):
            inspect_pdf(self.path)
        self.path.write_bytes(b'%PDF-1.7\nnot an actual PDF')
        with self.assertRaises(DocumentInspectionError):
            inspect_pdf(self.path)

    def test_sample_limit_validation(self):
        self.write_pdf()
        with self.assertRaises(ValueError):
            inspect_pdf(self.path, sample_limit=33)
        self.assertEqual(inspect_pdf(self.path, sample_limit=0).sampled_pages, ())
        self.assertEqual(len(inspect_pdf(self.path, sample_limit=1).sampled_pages), 1)

    @unittest.skipUnless(os.name in ("posix", "nt"), "OS worker limits required")
    def test_isolated_timeout_fails_closed_and_preserves_input(self):
        self.write_pdf(with_text=True)
        original = self.path.read_bytes()
        with self.assertRaises(DocumentInspectionError):
            inspect_pdf(self.path, timeout_seconds=0.000001)
        self.assertEqual(self.path.read_bytes(), original)

    def test_deadline_aware_hash_rejects_expired_budget(self):
        self.write_pdf()
        with self.assertRaisesRegex(TimeoutError, "deadline exceeded during source hashing"):
            sha256_file_before_deadline(self.path, time.monotonic() - 1.0)

    @unittest.skipUnless(os.name in ("posix", "nt"), "OS worker limits required")
    def test_parent_source_rehash_timeout_is_reported_and_input_preserved(self):
        self.write_pdf(with_text=True)
        original = self.path.read_bytes()
        # The spawned parser remains real; only the parent's post-worker
        # verification hash is forced over budget to exercise the failure path.
        with patch("pdfstudio.inspection.sha256_file_before_deadline",
                   side_effect=TimeoutError("simulated source verification deadline")):
            with self.assertRaisesRegex(
                    DocumentInspectionError, "timed out during source verification"):
                inspect_pdf(self.path, timeout_seconds=20)
        self.assertEqual(self.path.read_bytes(), original)

    @unittest.skipUnless(os.name in ("posix", "nt"), "OS worker limits required")
    def test_timeout_includes_worker_process_startup(self):
        # A slow process.start() must consume the same wall-clock budget as
        # parsing; the old implementation started its deadline only afterward.
        self.write_pdf(with_text=True)
        original = self.path.read_bytes()
        import multiprocessing.context as mp_context
        real_start = mp_context.SpawnProcess.start

        def delayed_start(process):
            time.sleep(0.05)
            return real_start(process)

        with patch("multiprocessing.context.SpawnProcess.start", new=delayed_start):
            with self.assertRaisesRegex(DocumentInspectionError, "timed out"):
                inspect_pdf(self.path, timeout_seconds=0.01)
        self.assertEqual(self.path.read_bytes(), original)

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object required')
    def test_windows_job_assignment_failure_fails_closed(self):
        self.write_pdf()
        original = self.path.read_bytes()
        with patch('pdfstudio.inspection.WindowsJob.assign', side_effect=WindowsJobError('denied')):
            with self.assertRaises(DocumentInspectionError):
                inspect_pdf(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_isolation_limits_fail_before_parsing(self):
        self.write_pdf()
        for kwargs in ({"max_memory_mb": 0}, {"max_pages": 0},
                       {"timeout_seconds": float("nan")}, {"timeout_seconds": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                inspect_pdf(self.path, **kwargs)

    @unittest.skipUnless(os.name in ("posix", "nt"), "OS worker limits required")
    def test_page_count_limit_and_original_integrity(self):
        self.write_pdf()
        before = self.path.read_bytes()
        with self.assertRaises(DocumentInspectionError):
            inspect_pdf(self.path, max_pages=1)
        self.assertEqual(self.path.read_bytes(), before)

    @unittest.skipUnless(os.name in ("posix", "nt"), "OS worker limits required")
    def test_isolated_metadata_and_password_never_logged(self):
        self.write_pdf(password="private-test-password")
        info = inspect_pdf(self.path, password="private-test-password")
        self.assertEqual(info.page_count, 2)
        self.assertEqual(info.title, "Synthetic QA")


if __name__ == '__main__':
    unittest.main()
