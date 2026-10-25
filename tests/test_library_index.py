"""Synthetic B01 local full-text library tests; no private PDFs or packaging."""

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pdfstudio.jobs import SourceChangedError, sha256_file
from pdfstudio.library_index import LibraryIndex, LibraryIndexError, LibraryIndexCancelled


class LibraryIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / "original.pdf"
        self.db = self.root / "private.sqlite"
        self.write_pdf("Hello World PDF Studio")
        self.index = LibraryIndex(self.db)

    def write_pdf(self, content, encrypted=False, page_count=1):
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 40 700 Td ({content}) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
        for _ in range(page_count - 1):
            writer.add_blank_page(width=612, height=792)
        if encrypted:
            writer.encrypt('secret')
        with self.pdf.open('wb') as fh:
            writer.write(fh)

    def test_real_pdf_index_search_and_no_source_mutation(self):
        before = sha256_file(self.pdf)
        result = self.index.index_pdf(self.pdf)
        self.assertTrue(result.changed)
        self.assertEqual(result.indexed_pages, 1)
        hits = self.index.search('hello PDF')
        self.assertEqual([(x.page_number, x.provenance) for x in hits], [(1, 'native')])
        self.assertIn('Hello World', hits[0].preview)
        self.assertEqual(sha256_file(self.pdf), before)
        self.assertTrue(self.index.verify_source(self.pdf))
        self.index.integrity_check()

    def test_unchanged_source_is_skipped_and_reopen_works(self):
        self.index.index_pdf(self.pdf)
        fresh = LibraryIndex(self.db)
        self.assertFalse(fresh.index_pdf(self.pdf).changed)
        self.assertEqual(len(fresh.search('world')), 1)

    def test_changed_source_reindexes_without_stale_terms(self):
        self.index.index_pdf(self.pdf)
        self.write_pdf('Updated Document')
        with self.assertRaises(SourceChangedError):
            self.index.verify_source(self.pdf)
        self.assertTrue(self.index.index_pdf(self.pdf).changed)
        self.assertEqual(self.index.search('hello'), [])
        self.assertEqual(len(self.index.search('updated')), 1)
        self.index.integrity_check()

    def test_ocr_arabic_search_and_provenance(self):
        digest = sha256_file(self.pdf)
        self.index.index_ocr_text(self.pdf, 1, [(0, 'إِلَى المُدَرِّسِ English OCR')], expected_sha256=digest)
        self.assertEqual(self.index.search('الى المدرس')[0].provenance, 'ocr')
        self.assertEqual(self.index.search('ENGLISH')[0].page_number, 1)
        self.assertIn('إِلَى', self.index.search('المدرس')[0].preview)
        self.index.integrity_check()

    def test_ocr_sha_mismatch_rejected_before_any_write(self):
        with self.assertRaises(SourceChangedError):
            self.index.index_ocr_text(self.pdf, 1, [(0, 'private')], expected_sha256='0' * 64)
        self.assertEqual(self.index.search('private'), [])

    def test_invalid_ocr_batch_rolls_back_existing_index(self):
        self.index.index_pdf(self.pdf)
        with self.assertRaises(LibraryIndexError):
            self.index.index_ocr_text(self.pdf, 1, [(0, 'first'), (0, 'duplicate')], expected_sha256=sha256_file(self.pdf))
        self.assertEqual(len(self.index.search('hello')), 1)
        self.assertEqual(self.index.search('first'), [])
        self.index.integrity_check()

    def test_limits_reject_partial_index_and_query_injection(self):
        small = LibraryIndex(self.root / 'small.sqlite', max_chars_per_page=4)
        with self.assertRaises(LibraryIndexError):
            small.index_pdf(self.pdf)
        self.assertEqual(small.search('hello'), [])
        self.index.index_pdf(self.pdf)
        self.assertEqual(self.index.search('" OR * --'), [])
        self.assertEqual(len(self.index.search('PDF OR')), 0)
        with self.assertRaises(ValueError):
            self.index.search('x' * 513)

    def test_encrypted_source_rejected_without_index(self):
        self.write_pdf('Hidden', encrypted=True)
        with self.assertRaises(LibraryIndexError):
            self.index.index_pdf(self.pdf)
        self.assertEqual(self.index.search('hidden'), [])

    def test_new_ocr_correction_replaces_previous_text_same_source(self):
        digest = sha256_file(self.pdf)
        self.index.index_ocr_text(self.pdf, 1, [(0, 'wrong word')], expected_sha256=digest)
        self.index.index_ocr_text(self.pdf, 1, [(0, 'correct word')], expected_sha256=digest)
        self.assertEqual(self.index.search('wrong'), [])
        self.assertEqual(len(self.index.search('correct')), 1)
        self.index.integrity_check()

    def test_cross_document_search_keeps_page_and_file_provenance(self):
        other = self.root / 'other.pdf'
        other.write_bytes(self.pdf.read_bytes())
        self.index.index_pdf(self.pdf)
        self.index.index_pdf(other)
        hits = self.index.search('world')
        self.assertEqual(len(hits), 2)
        self.assertEqual({h.source for h in hits}, {str(self.pdf.resolve()), str(other.resolve())})
        self.assertTrue(all(h.page_number == 1 for h in hits))

    def test_partial_ocr_pages_and_rejected_update_preserve_previous_index(self):
        self.write_pdf('Hello World PDF Studio', page_count=3)
        self.index.index_ocr_text(self.pdf, 3, [(1, 'middle only')], expected_sha256=sha256_file(self.pdf))
        self.assertEqual(self.index.search('middle')[0].page_number, 2)
        with self.assertRaises(LibraryIndexError):
            self.index.index_ocr_text(self.pdf, 3, [(3, 'outside')], expected_sha256=sha256_file(self.pdf))
        self.assertEqual(len(self.index.search('middle')), 1)
        self.index.integrity_check()

    def test_ocr_page_count_mismatch_rejected(self):
        with self.assertRaises(LibraryIndexError):
            self.index.index_ocr_text(self.pdf, 4, [(0, 'not real')], expected_sha256=sha256_file(self.pdf))
        self.assertEqual(self.index.search('real'), [])

    def test_native_cancel_before_start_preserves_existing_index(self):
        self.index.index_pdf(self.pdf)
        before = self.pdf.read_bytes()
        with self.assertRaises(LibraryIndexCancelled):
            self.index.index_pdf(self.pdf, cancel_requested=lambda: True)
        self.assertEqual(self.pdf.read_bytes(), before)
        self.assertEqual(len(self.index.search('hello')), 1)
        self.index.integrity_check()

    def test_ocr_cancel_mid_batch_rolls_back_old_fts_and_document_row(self):
        self.write_pdf('Hello World PDF Studio', page_count=3)
        self.index.index_pdf(self.pdf)
        original = self.pdf.read_bytes()
        calls = 0

        def cancel_after_first_page():
            nonlocal calls
            calls += 1
            # API entry, transaction entry, page zero, then page one.
            return calls >= 4

        with self.assertRaises(LibraryIndexCancelled):
            self.index.index_ocr_text(
                self.pdf, 3, [(0, 'newterm first'), (1, 'newterm second')],
                expected_sha256=sha256_file(self.pdf),
                cancel_requested=cancel_after_first_page)
        self.assertEqual(self.pdf.read_bytes(), original)
        self.assertEqual(len(self.index.search('hello')), 1)
        self.assertEqual(self.index.search('newterm'), [])
        self.assertEqual(self.index.search('world')[0].provenance, 'native')
        self.index.integrity_check()

    def test_cancel_just_before_commit_does_not_publish_partial_ocr(self):
        self.index.index_pdf(self.pdf)
        calls = 0

        def cancel_at_commit():
            nonlocal calls
            calls += 1
            # API entry, transaction entry, page insertion, pre-commit.
            return calls >= 4

        with self.assertRaises(LibraryIndexCancelled):
            self.index.index_ocr_text(
                self.pdf, 1, [(0, 'newterm')],
                expected_sha256=sha256_file(self.pdf),
                cancel_requested=cancel_at_commit)
        self.assertEqual(len(self.index.search('hello')), 1)
        self.assertEqual(self.index.search('newterm'), [])
        self.index.integrity_check()

    def test_uncancelled_ocr_and_invalid_cancel_callback(self):
        with self.assertRaises(TypeError):
            self.index.index_pdf(self.pdf, cancel_requested=True)
        with self.assertRaises(TypeError):
            self.index.index_ocr_text(self.pdf, 1, [(0, 'unused')],
                                      expected_sha256=sha256_file(self.pdf),
                                      cancel_requested='stop')
        result = self.index.index_ocr_text(
            self.pdf, 1, [(0, 'OCR confirmed')],
            expected_sha256=sha256_file(self.pdf),
            cancel_requested=lambda: False)
        self.assertEqual(result.indexed_pages, 1)
        self.assertEqual(self.index.search('confirmed')[0].provenance, 'ocr')
        self.index.integrity_check()

    def test_remove_clears_fts_and_keeps_original(self):
        original = self.pdf.read_bytes()
        self.index.index_pdf(self.pdf)
        self.assertTrue(self.index.remove(self.pdf))
        self.assertFalse(self.index.remove(self.pdf))
        self.assertEqual(self.index.search('hello'), [])
        self.index.integrity_check()
        self.assertEqual(self.pdf.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
