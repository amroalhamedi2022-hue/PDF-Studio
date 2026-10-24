"""Use synthetically generated valid PDFs; no user's scanned documents."""

import tempfile
import unittest
from pathlib import Path

from pdfstudio.inspection import DocumentInspectionError, inspect_pdf
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


if __name__ == '__main__':
    unittest.main()
