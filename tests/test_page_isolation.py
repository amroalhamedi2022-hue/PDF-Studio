"""Bounded extract/merge contracts using synthetic PDFs, never private data."""
import os
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pdfstudio.page_tools import PageToolError, extract_pages, merge_pdfs


@unittest.skipUnless(os.name == "posix", "POSIX RLIMIT worker required")
class IsolatedPageToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.a, self.b, self.out = (self.root / name for name in ("a.pdf", "b.pdf", "out.pdf"))
        for path, widths in ((self.a, (200, 300)), (self.b, (400,))):
            writer = PdfWriter()
            for width in widths:
                writer.add_blank_page(width=width, height=500)
            with path.open("wb") as stream:
                writer.write(stream)

    def test_extract_roundtrip_with_source_hash_unchanged(self):
        before = self.a.read_bytes()
        extract_pages(self.a, self.out, "2,1")
        with self.out.open("rb") as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages], [300, 200])
        self.assertEqual(self.a.read_bytes(), before)

    def test_merge_roundtrip_with_both_sources_unchanged(self):
        originals = (self.a.read_bytes(), self.b.read_bytes())
        merge_pdfs([self.a, self.b], self.out)
        with self.out.open("rb") as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages], [200, 300, 400])
        self.assertEqual((self.a.read_bytes(), self.b.read_bytes()), originals)

    def test_merge_total_page_cap_rejects_without_output(self):
        with self.assertRaises(PageToolError):
            merge_pdfs([self.a, self.b], self.out, max_pages=2)
        self.assertFalse(self.out.exists())

    def test_extract_page_cap_rejects_without_output(self):
        with self.assertRaises(PageToolError):
            extract_pages(self.a, self.out, [0], max_pages=1)
        self.assertFalse(self.out.exists())

    def test_timeout_fails_closed_without_source_modification(self):
        before = self.a.read_bytes()
        with self.assertRaises(PageToolError):
            extract_pages(self.a, self.out, [0], timeout_seconds=0.000001)
        self.assertEqual(self.a.read_bytes(), before)
        self.assertFalse(self.out.exists())

    def test_existing_output_never_replaced(self):
        self.out.write_bytes(b"private-output")
        with self.assertRaises(FileExistsError):
            merge_pdfs([self.a, self.b], self.out)
        self.assertEqual(self.out.read_bytes(), b"private-output")

    def test_invalid_limits_rejected(self):
        for settings in ({"max_pages": 0}, {"max_memory_mb": True},
                         {"timeout_seconds": float("inf")}, {"timeout_seconds": -1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                extract_pages(self.a, self.out, [0], **settings)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
