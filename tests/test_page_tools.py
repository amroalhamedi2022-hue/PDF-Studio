"""Real synthetic PDF tests for page-level primitives, no user files."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdfstudio.jobs import sha256_file
from pdfstudio.page_tools import (PageToolError, _publish, crop_pages, delete_pages, extract_pages,
                                  merge_pdfs, reorder_pages, rotate_pages, split_pdf, split_pdf_by_bookmarks)
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

    def test_delete_selected_pages_preserves_order_and_source(self):
        before = self.a.read_bytes()
        delete_pages(self.a, self.out, [1])
        with self.out.open('rb') as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages],
                             [300.0, 500.0])
        self.assertEqual(before, self.a.read_bytes())

    def test_delete_rejects_all_pages_and_invalid_selection(self):
        for selection in ([0, 1, 2], [], [3], "0"):
            with self.subTest(selection=selection), self.assertRaises(PageToolError):
                delete_pages(self.a, self.out, selection)
        self.assertFalse(self.out.exists())

    def test_reorder_pages_requires_complete_permutation_and_preserves_source(self):
        before = self.a.read_bytes()
        reorder_pages(self.a, self.out, [2, 0, 1])
        with self.out.open('rb') as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages],
                             [500.0, 300.0, 400.0])
        self.assertEqual(before, self.a.read_bytes())

    def test_reorder_rejects_duplicate_missing_and_out_of_range_indexes(self):
        for order in ([0, 0, 2], [0, 1], [0, 1, 3], "1,1,3"):
            with self.subTest(order=order), self.assertRaises(PageToolError):
                reorder_pages(self.a, self.out, order)
        self.assertFalse(self.out.exists())

    def test_crop_selected_pages_sets_cropbox_and_preserves_source(self):
        before = self.a.read_bytes()
        crop_pages(self.a, self.out, (10, 20, 250, 650), [0, 2])
        with self.out.open('rb') as stream:
            reader = PdfReader(stream)
            self.assertEqual(len(reader.pages), 3)
            self.assertEqual(
                [(float(p.cropbox.left), float(p.cropbox.bottom),
                  float(p.cropbox.right), float(p.cropbox.top)) for p in reader.pages],
                [(10.0, 20.0, 250.0, 650.0), (0.0, 0.0, 400.0, 700.0),
                 (10.0, 20.0, 250.0, 650.0)],
            )
            self.assertEqual([float(p.mediabox.width) for p in reader.pages],
                             [300.0, 400.0, 500.0])
        self.assertEqual(before, self.a.read_bytes())

    def test_crop_rejects_invalid_geometry_and_out_of_bounds_without_output(self):
        invalid_boxes = (
            (10, 10, 10, 20),
            (10, 30, 20, 20),
            (float('nan'), 0, 10, 10),
            (10 ** 1000, 0, 10 ** 1001, 10),
            (True, 0, 10, 10),
        )
        for box in invalid_boxes:
            with self.subTest(box=box), self.assertRaises(PageToolError):
                crop_pages(self.a, self.out, box, [0])
        with self.assertRaises(PageToolError):
            crop_pages(self.a, self.out, (0, 0, 301, 650), [0])
        with self.assertRaises(PageToolError):
            crop_pages(self.a, self.out, (10, 20, 250, 650), [3])
        self.assertFalse(self.out.exists())

    def test_crop_refuses_existing_output_without_clobber(self):
        self.out.write_bytes(b'keep this')
        with self.assertRaises(FileExistsError):
            crop_pages(self.a, self.out, (10, 20, 250, 650), [0])
        self.assertEqual(self.out.read_bytes(), b'keep this')

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

    def test_extract_bounds_iterable_materialization_before_worker_spawn(self):
        consumed = []

        def oversized_selection():
            for index in range(100):
                consumed.append(index)
                yield index

        with self.assertRaises(PageToolError):
            extract_pages(self.a, self.out, oversized_selection(), max_pages=3)
        self.assertEqual(len(consumed), 4)  # limit + one sentinel only
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


    def test_split_pdf_by_ranges_creates_ordered_parts_without_mutating_source(self):
        before = self.a.read_bytes()
        out_dir = self.root / "parts"
        out_dir.mkdir()
        outputs = split_pdf(self.a, out_dir, ["1-2", [2]], base_name="report")
        self.assertEqual([p.name for p in outputs],
                         ["report-part-001.pdf", "report-part-002.pdf"])
        with outputs[0].open("rb") as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages],
                             [300.0, 400.0])
        with outputs[1].open("rb") as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages],
                             [500.0])
        self.assertEqual(before, self.a.read_bytes())
        self.assertEqual(list(out_dir.glob(".pdfstudio-split-*")), [])
        self.assertEqual(list(out_dir.glob(".pdfstudio-stage-*")), [])

    def test_split_pdf_preflights_all_outputs_without_clobber(self):
        out_dir = self.root / "parts"
        out_dir.mkdir()
        sentinel = b"preserve concurrent output"
        (out_dir / "report-part-002.pdf").write_bytes(sentinel)
        with self.assertRaises(FileExistsError):
            split_pdf(self.a, out_dir, ["1", "2"], base_name="report")
        self.assertFalse((out_dir / "report-part-001.pdf").exists())
        self.assertEqual((out_dir / "report-part-002.pdf").read_bytes(), sentinel)

    def test_split_pdf_rolls_back_staged_parts_when_later_range_is_invalid(self):
        out_dir = self.root / "parts"
        out_dir.mkdir()
        before = self.a.read_bytes()
        with self.assertRaises(PageToolError):
            split_pdf(self.a, out_dir, ["1-2", "9"], base_name="report")
        self.assertFalse((out_dir / "report-part-001.pdf").exists())
        self.assertFalse((out_dir / "report-part-002.pdf").exists())
        self.assertEqual(before, self.a.read_bytes())
        self.assertEqual(list(out_dir.glob(".pdfstudio-split-*")), [])
        self.assertEqual(list(out_dir.glob(".pdfstudio-stage-*")), [])

    def test_split_pdf_rejects_unsafe_names_and_excessive_part_count(self):
        out_dir = self.root / "parts"
        out_dir.mkdir()
        for name in ("../escape", ".hidden", "bad/name", ""):
            with self.subTest(name=name), self.assertRaises(PageToolError):
                split_pdf(self.a, out_dir, ["1"], base_name=name)
        with self.assertRaises(PageToolError):
            split_pdf(self.a, out_dir, [["0"]] * 101, base_name="many")
        self.assertEqual(list(out_dir.iterdir()), [])

    def test_split_pdf_by_top_level_bookmarks_keeps_preface_and_ignores_nested_items(self):
        writer = PdfWriter()
        for width in (300, 400, 500, 600, 700):
            writer.add_blank_page(width=width, height=700)
        chapter_one = writer.add_outline_item("Chapter One", 1)
        writer.add_outline_item("Nested section", 2, parent=chapter_one)
        writer.add_outline_item("Chapter Two", 3)
        with self.a.open("wb") as stream:
            writer.write(stream)
        before = self.a.read_bytes()
        out_dir = self.root / "bookmark-parts"
        out_dir.mkdir()

        outputs = split_pdf_by_bookmarks(self.a, out_dir, base_name="chapters")

        self.assertEqual([p.name for p in outputs], [
            "chapters-part-001.pdf", "chapters-part-002.pdf", "chapters-part-003.pdf"
        ])
        actual = []
        for output in outputs:
            with output.open("rb") as stream:
                actual.append([float(page.mediabox.width) for page in PdfReader(stream).pages])
        self.assertEqual(actual, [[300.0], [400.0, 500.0], [600.0, 700.0]])
        self.assertEqual(before, self.a.read_bytes())
        self.assertEqual(list(out_dir.glob(".pdfstudio-split-*")), [])
        self.assertEqual(list(out_dir.glob(".pdfstudio-stage-*")), [])

    def test_split_pdf_by_bookmarks_rejects_missing_outline_without_outputs(self):
        out_dir = self.root / "no-bookmarks"
        out_dir.mkdir()
        with self.assertRaises(PageToolError):
            split_pdf_by_bookmarks(self.a, out_dir, base_name="chapters")
        self.assertEqual(list(out_dir.iterdir()), [])

    def test_split_pdf_by_bookmarks_rejects_source_changed_after_outline_plan(self):
        out_dir = self.root / "stale-bookmark-plan"
        out_dir.mkdir()
        with patch(
            "pdfstudio.page_tools.inspect_top_level_bookmark_starts",
            return_value=(3, (0,), "0" * 64),
        ):
            with self.assertRaisesRegex(PageToolError, "source changed after bookmark split planning"):
                split_pdf_by_bookmarks(self.a, out_dir, base_name="chapters")
        self.assertEqual(list(out_dir.iterdir()), [])

    def test_split_pdf_bounds_infinite_group_iterable(self):
        out_dir = self.root / "parts"
        out_dir.mkdir()
        consumed = []

        def infinite_groups():
            index = 0
            while True:
                consumed.append(index)
                yield "1"
                index += 1

        with self.assertRaises(PageToolError):
            split_pdf(self.a, out_dir, infinite_groups(), base_name="bounded")
        self.assertEqual(len(consumed), 101)  # fixed group cap + sentinel
        self.assertEqual(list(out_dir.iterdir()), [])

    def test_split_pdf_bounds_infinite_page_group(self):
        out_dir = self.root / "parts"
        out_dir.mkdir()
        consumed = []

        def infinite_indexes():
            index = 0
            while True:
                consumed.append(index)
                yield 0
                index += 1

        with self.assertRaises(PageToolError):
            split_pdf(self.a, out_dir, [infinite_indexes()], base_name="bounded",
                      max_pages=3)
        self.assertEqual(len(consumed), 4)  # configured page cap + sentinel
        self.assertEqual(list(out_dir.iterdir()), [])

    def test_split_pdf_total_output_cap_fails_without_final_outputs(self):
        out_dir = self.root / "parts"
        out_dir.mkdir()
        with self.assertRaises((PageToolError, ValueError)):
            split_pdf(self.a, out_dir, ["1", "2"], base_name="report",
                      max_total_output_bytes=5)
        self.assertFalse((out_dir / "report-part-001.pdf").exists())
        self.assertFalse((out_dir / "report-part-002.pdf").exists())


    def test_publish_preserves_replacement_path_when_writer_fails(self):
        staging = self.root / "raced-staging.pdf"
        moved = self.root / "moved-original-staging.pdf"
        staging.write_bytes(b"reserved")

        class ReplacingWriter:
            def write(self, stream):
                original_path = Path(stream.name)
                # Close the open descriptor first so the same deterministic
                # race fixture also works on Windows filesystems.
                stream._stream.close()
                original_path.rename(moved)
                original_path.write_bytes(b"concurrent replacement")
                raise RuntimeError("injected write failure")

        with self.assertRaisesRegex(RuntimeError, "injected write failure"):
            _publish(
                ReplacingWriter(), staging, (self.a,), (sha256_file(self.a),),
                1024 * 1024, expected_pages=1,
            )

        # Cleanup must not unlink a different inode that appeared at the same
        # path after the writer started. The moved original is also left alone.
        self.assertEqual(staging.read_bytes(), b"concurrent replacement")
        self.assertEqual(moved.read_bytes(), b"reserved")
        self.assertEqual(len(PdfReader(self.a).pages), 3)

    def test_publish_rejects_page_count_mismatch_and_removes_staging(self):
        staging = self.root / "reserved-count.pdf"
        staging.write_bytes(b"reserved")
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)

        with self.assertRaisesRegex(PageToolError, "page count mismatch"):
            _publish(
                writer, staging, (self.a,), (sha256_file(self.a),),
                1024 * 1024, expected_pages=2,
            )
        self.assertFalse(staging.exists())
        self.assertEqual(len(PdfReader(self.a).pages), 3)

    def test_publish_rejects_malformed_pdf_and_removes_staging(self):
        staging = self.root / "reserved-malformed.pdf"
        staging.write_bytes(b"reserved")

        class MalformedWriter:
            def write(self, stream):
                stream.write(b"%PDF-1.7\\nnot-a-valid-page-tree\\n")

        with self.assertRaisesRegex(PageToolError, "structural validation"):
            _publish(
                MalformedWriter(), staging, (self.a,), (sha256_file(self.a),),
                1024 * 1024, expected_pages=1,
            )
        self.assertFalse(staging.exists())
        self.assertEqual(len(PdfReader(self.a).pages), 3)


if __name__ == '__main__':
    unittest.main()
