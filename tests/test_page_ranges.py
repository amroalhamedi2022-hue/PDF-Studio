"""B01 page selection contract and real synthetic PDF extraction checks."""

import tempfile
import unittest
from pathlib import Path

from pdfstudio.page_ranges import PageRangeError, parse_page_ranges
from pdfstudio.page_tools import PageToolError, extract_pages
from pypdf import PdfReader, PdfWriter


class PageRangeTests(unittest.TestCase):
    def test_inclusive_ranges_and_user_visible_one_based_indices(self):
        self.assertEqual(parse_page_ranges("1-3,5", 6), (0, 1, 2, 4))

    def test_reverse_open_ended_and_all(self):
        self.assertEqual(parse_page_ranges("5-3,2-", 6), (4, 3, 2, 1, 2, 3, 4, 5))
        self.assertEqual(parse_page_ranges("ALL", 3), (0, 1, 2))

    def test_duplicate_pages_are_preserved(self):
        self.assertEqual(parse_page_ranges("2,2,1", 3), (1, 1, 0))

    def test_invalid_and_out_of_range_are_rejected(self):
        for spec in ("", "0", "4", "1-4", "2-0", "1,,2", "1--2", "-2",
                     "all,2", "1:2", "one", "2-3-4"):
            with self.subTest(spec=spec), self.assertRaises(PageRangeError):
                parse_page_ranges(spec, 3)

    def test_bad_page_count_is_rejected(self):
        for count in (0, -1, True, 1.5):
            with self.subTest(count=count), self.assertRaises(PageRangeError):
                parse_page_ranges("1", count)

    def test_selection_expansion_is_bounded_including_duplicates_and_all(self):
        for spec in ("1-3", "1,1,1", "all"):
            with self.subTest(spec=spec), self.assertRaises(PageRangeError):
                parse_page_ranges(spec, 3, max_selections=2)
        for cap in (0, -1, True, 1.5):
            with self.subTest(cap=cap), self.assertRaises(PageRangeError):
                parse_page_ranges("1", 3, max_selections=cap)
        with self.assertRaises(PageRangeError):
            parse_page_ranges(",".join(["1"] * 100), 3, max_selections=2)

    def test_whitespace_and_single_page(self):
        self.assertEqual(parse_page_ranges(" 2 , 3- ", 4), (1, 2, 3))


class PdfExtractionRangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "source.pdf"
        self.output = root / "selected.pdf"
        writer = PdfWriter()
        for width in (300, 400, 500):
            writer.add_blank_page(width=width, height=700)
        with self.source.open("wb") as fh:
            writer.write(fh)

    def test_user_range_roundtrip_preserves_source(self):
        original = self.source.read_bytes()
        extract_pages(self.source, self.output, "2-3,1,2")
        with self.output.open("rb") as fh:
            widths = [float(p.mediabox.width) for p in PdfReader(fh).pages]
        self.assertEqual(widths, [400, 500, 300, 400])
        self.assertEqual(self.source.read_bytes(), original)

    def test_invalid_range_never_publishes_output(self):
        original = self.source.read_bytes()
        with self.assertRaises(PageToolError):
            extract_pages(self.source, self.output, "1-4")
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
