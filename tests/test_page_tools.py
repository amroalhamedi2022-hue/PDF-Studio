"""Real synthetic PDF tests for page-level primitives, no user files."""

import tempfile
import unittest
from pathlib import Path

from pdfstudio.page_tools import PageToolError, extract_pages, merge_pdfs, rotate_pages
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject


class PageToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.a = self.root / 'a.pdf'
        self.b = self.root / 'b.pdf'
        self.out = self.root / 'out.pdf'
        self.write_file(self.a, [300, 400, 500])
        self.write_file(self.b, [600, 700])

    def write_file(self, path, widths, encrypted=False, fake_sig=False):
        w = PdfWriter()
        for width in widths:
            w.add_blank_page(width=width, height=700)
        if encrypted:
            w.encrypt('secret')
        if fake_sig:
            w._root_object[NameObject('/Perms')] = DictionaryObject({NameObject('/DocMDP'): DictionaryObject()})
        with path.open('wb') as fh:
            w.write(fh)

    def test_extract_custom_order_without_mutating_source(self):
        before = self.a.read_bytes()
        extract_pages(self.a, self.out, [2, 0])
        with self.out.open('rb') as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages], [500, 300])
        self.assertEqual(before, self.a.read_bytes())

    def test_merge_page_count_and_order(self):
        merge_pdfs([self.a, self.b], self.out)
        with self.out.open('rb') as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages], [300, 400, 500, 600, 700])

    def test_rotate_selected_pages_preserves_order_and_source(self):
        before = self.a.read_bytes()
        rotate_pages(self.a, self.out, [1], degrees=90)
        with self.out.open('rb') as stream:
            reader = PdfReader(stream)
            pages = list(reader.pages)
            self.assertEqual([float(page.mediabox.width) for page in pages], [300, 400, 500])
            self.assertEqual([int(page.get('/Rotate', 0)) % 360 for page in pages], [0, 90, 0])
        self.assertEqual(before, self.a.read_bytes())

    def test_rotate_rejects_invalid_angles_and_page_selections(self):
        for angle in (0, 45, 360, True):
            with self.subTest(angle=angle), self.assertRaises(PageToolError):
                rotate_pages(self.a, self.out, [0], degrees=angle)
        for selection in ([], [3], "0", "4"):
            with self.subTest(selection=selection), self.assertRaises(PageToolError):
                rotate_pages(self.a, self.out, selection, degrees=90)
        self.assertFalse(self.out.exists())

    def test_rotate_refuses_existing_output_without_clobber(self):
        self.out.write_bytes(b'keep this')
        with self.assertRaises(FileExistsError):
            rotate_pages(self.a, self.out, [0], degrees=90)
        self.assertEqual(self.out.read_bytes(), b'keep this')

    def test_existing_output_refused(self):
        self.out.write_bytes(b'keep this')
        with self.assertRaises(FileExistsError):
            extract_pages(self.a, self.out, [0])
        self.assertEqual(self.out.read_bytes(), b'keep this')

    def test_invalid_page_and_empty_list_refused(self):
        with self.assertRaises(PageToolError):
            extract_pages(self.a, self.out, [10])
        with self.assertRaises(PageToolError):
            extract_pages(self.a, self.out, [])
        self.assertFalse(self.out.exists())

    def test_output_byte_cap_fails_closed_without_final_output(self):
        before = self.a.read_bytes()
        with self.assertRaises(PageToolError):
            extract_pages(self.a, self.out, [0], max_output_bytes=128)
        self.assertEqual(self.a.read_bytes(), before)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_merge_output_byte_cap_fails_closed_without_final_output(self):
        originals = (self.a.read_bytes(), self.b.read_bytes())
        with self.assertRaises(PageToolError):
            merge_pdfs([self.a, self.b], self.out, max_output_bytes=128)
        self.assertEqual((self.a.read_bytes(), self.b.read_bytes()), originals)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_output_byte_cap_requires_positive_integer(self):
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                extract_pages(self.a, self.out, [0], max_output_bytes=limit)
        self.assertFalse(self.out.exists())

    def test_original_as_target_refused(self):
        before = self.a.read_bytes()
        with self.assertRaises(PageToolError):
            merge_pdfs([self.a, self.b], self.a)
        self.assertEqual(before, self.a.read_bytes())

    def test_encrypted_input_refused(self):
        self.write_file(self.a, [300], encrypted=True)
        with self.assertRaises(PageToolError):
            merge_pdfs([self.a, self.b], self.out)
        self.assertFalse(self.out.exists())

    def test_signature_permission_marker_refused(self):
        self.write_file(self.a, [300], fake_sig=True)
        with self.assertRaises(PageToolError):
            extract_pages(self.a, self.out, [0])
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
