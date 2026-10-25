"""Bounded extract/merge contracts using synthetic PDFs, never private data."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pypdf import PdfReader, PdfWriter
from pdfstudio.page_tools import PageToolError, extract_pages, merge_pdfs
from pdfstudio.jobs import sha256_file


@unittest.skipUnless(os.name in ("posix", "nt"), "bounded POSIX or Windows worker required")
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
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_existing_output_never_replaced(self):
        self.out.write_bytes(b"private-output")
        with self.assertRaises(FileExistsError):
            merge_pdfs([self.a, self.b], self.out)
        self.assertEqual(self.out.read_bytes(), b"private-output")

    def test_only_parent_publishes_after_staging(self):
        real_link = os.link
        observed = []

        def check_publication(staged, destination):
            self.assertEqual(Path(destination), self.out)
            self.assertEqual(Path(staged).name, "result.pdf")
            self.assertTrue(Path(staged).parent.name.startswith(".pdfstudio-stage-"))
            self.assertTrue(Path(staged).is_file())
            self.assertFalse(self.out.exists())
            observed.append(True)
            return real_link(staged, destination)

        with mock.patch("pdfstudio.page_isolation.os.link", side_effect=check_publication):
            extract_pages(self.a, self.out, "1")
        self.assertEqual(observed, [True])
        self.assertTrue(self.out.is_file())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_failed_parent_publication_leaves_no_output_or_staging(self):
        with mock.patch("pdfstudio.page_isolation.os.link",
                        side_effect=PermissionError("publication disallowed")):
            with self.assertRaises(PermissionError):
                extract_pages(self.a, self.out, "1")
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_source_changed_after_worker_success_never_publishes(self):
        before = self.a.read_bytes()
        calls = 0

        def altered_digest(path):
            nonlocal calls
            calls += 1
            return sha256_file(path) if calls == 1 else "0" * 64

        with mock.patch("pdfstudio.page_isolation.sha256_file", side_effect=altered_digest):
            with self.assertRaises(PageToolError):
                extract_pages(self.a, self.out, "1")
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(self.a.read_bytes(), before)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_invalid_limits_rejected(self):
        for settings in ({"max_pages": 0}, {"max_memory_mb": True},
                         {"timeout_seconds": float("inf")}, {"timeout_seconds": -1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                extract_pages(self.a, self.out, [0], **settings)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
