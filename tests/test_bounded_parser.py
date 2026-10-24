"""Real child-process PDF parser contracts; generated PDFs only."""

import os
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from pdfstudio.bounded_parser import IsolatedPdfReader, ParserIsolationError


@unittest.skipUnless(os.name == 'posix', 'POSIX resource caps required')
class BoundedParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'original.pdf'
        self._make_pdf()

    def _make_pdf(self, *, encrypted=False):
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 40 700 Td (Hello Sandbox) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        if encrypted:
            writer.encrypt('secret')
        with self.path.open('wb') as fh:
            writer.write(fh)

    def _reader(self, **kwargs):
        return IsolatedPdfReader(self.path, max_pages=100,
                                 max_chars_per_page=1000,
                                 max_total_chars=10000, **kwargs)

    def test_child_process_extracts_real_pdf_and_does_not_mutate_input(self):
        original = self.path.read_bytes()
        with self._reader() as reader:
            self.assertEqual(reader.page_count, 1)
            self.assertEqual(list(reader.pages()), [(0, 'Hello Sandbox')])
        self.assertEqual(self.path.read_bytes(), original)

    def test_encrypted_input_rejected(self):
        self._make_pdf(encrypted=True)
        with self.assertRaises(ParserIsolationError):
            with self._reader():
                pass

    def test_count_only_does_not_extract_text(self):
        with self._reader(extract_text=False) as reader:
            self.assertEqual(reader.page_count, 1)
            self.assertEqual(list(reader.pages()), [])

    def test_rejects_page_and_text_caps(self):
        with self.assertRaises(ParserIsolationError):
            with IsolatedPdfReader(self.path, max_pages=1, max_chars_per_page=3,
                                   max_total_chars=10) as reader:
                list(reader.pages())
        with self.assertRaises(ParserIsolationError):
            with IsolatedPdfReader(self.path, max_pages=1, max_chars_per_page=100,
                                   max_total_chars=4) as reader:
                list(reader.pages())

    def test_bad_file_and_bad_config(self):
        self.path.write_bytes(b'not a pdf')
        with self.assertRaises(ParserIsolationError):
            with self._reader():
                pass
        with self.assertRaises(ValueError):
            self._reader(timeout_seconds=0)
        with self.assertRaises(ValueError):
            self._reader(max_memory_mb=0)

    def test_wall_clock_deadline_fail_closed(self):
        with self.assertRaises(ParserIsolationError):
            with self._reader(timeout_seconds=0.000001):
                pass


if __name__ == '__main__':
    unittest.main()
