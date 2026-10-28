"""Bounded extract/merge contracts using synthetic PDFs, never private data."""
import errno
import os
import tempfile
import time
import multiprocessing.process
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from pypdf import PdfReader, PdfWriter
from pdfstudio.page_tools import (PageToolError, _publish, crop_pages, delete_pages,
                                  extract_pages, merge_pdfs, reorder_pages)
from pdfstudio.jobs import sha256_file
from pdfstudio.page_isolation import _reserve_staging_file, _verify_staging_file


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

    def test_delete_and_reorder_use_isolated_worker_without_source_mutation(self):
        before = self.a.read_bytes()
        deleted = self.root / "deleted.pdf"
        reordered = self.root / "reordered.pdf"
        delete_pages(self.a, deleted, [1])
        reorder_pages(self.a, reordered, [1, 0])
        with deleted.open("rb") as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages],
                             [200.0])
        with reordered.open("rb") as stream:
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(stream).pages],
                             [300.0, 200.0])
        self.assertEqual(self.a.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_crop_runs_in_isolated_worker_and_keeps_source_unchanged(self):
        before = self.a.read_bytes()
        crop_pages(self.a, self.out, (10, 10, 190, 490), [0])
        with self.out.open("rb") as stream:
            reader = PdfReader(stream)
            self.assertEqual(len(reader.pages), 2)
            self.assertEqual(
                (float(reader.pages[0].cropbox.left), float(reader.pages[0].cropbox.bottom),
                 float(reader.pages[0].cropbox.right), float(reader.pages[0].cropbox.top)),
                (10.0, 10.0, 190.0, 490.0),
            )
            self.assertEqual(
                (float(reader.pages[1].cropbox.left), float(reader.pages[1].cropbox.bottom),
                 float(reader.pages[1].cropbox.right), float(reader.pages[1].cropbox.top)),
                (0.0, 0.0, 300.0, 500.0),
            )
        self.assertEqual(self.a.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

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

    def test_deadline_includes_parent_source_hashing(self):
        before = self.a.read_bytes()
        real_hash = __import__("pdfstudio.page_isolation", fromlist=["_sha256_file_before_deadline"])._sha256_file_before_deadline

        def delayed_hash(path, deadline, chunk_bytes=1024 * 1024):
            time.sleep(0.05)
            return real_hash(path, deadline, chunk_bytes)

        with mock.patch("pdfstudio.page_isolation._sha256_file_before_deadline",
                        side_effect=delayed_hash), \
             mock.patch("pdfstudio.page_isolation.mp.get_context") as get_context:
            with self.assertRaisesRegex(PageToolError, "timed out"):
                extract_pages(self.a, self.out, "1", timeout_seconds=0.02)
        get_context.assert_not_called()
        self.assertEqual(self.a.read_bytes(), before)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_free_space_preflight_fails_before_hash_or_worker(self):
        before = self.a.read_bytes()
        with mock.patch("pdfstudio.page_isolation.shutil.disk_usage",
                        return_value=SimpleNamespace(total=1024, used=1023, free=1)), \
             mock.patch("pdfstudio.page_isolation._sha256_file_before_deadline") as hash_file, \
             mock.patch("pdfstudio.page_isolation.mp.get_context") as get_context:
            with self.assertRaisesRegex(PageToolError, "insufficient free space"):
                extract_pages(self.a, self.out, "1", free_space_margin_bytes=0)
        hash_file.assert_not_called()
        get_context.assert_not_called()
        self.assertEqual(self.a.read_bytes(), before)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_staging_reservation_allocates_requested_length(self):
        reserved = self.root / "reserved.bin"
        requested = 2 * 1024 * 1024
        identity = _reserve_staging_file(reserved, requested)
        info = reserved.stat()
        self.assertEqual(identity, (info.st_dev, info.st_ino))
        self.assertEqual(info.st_size, requested)
        # POSIX posix_fallocate should allocate real blocks, not just a sparse EOF.
        if os.name == "posix" and hasattr(info, "st_blocks"):
            self.assertGreaterEqual(info.st_blocks * 512, requested)

    def test_staging_validation_rejects_replaced_inode(self):
        staged = self.root / "result.pdf"
        identity = _reserve_staging_file(staged, 1024 * 1024)
        moved = self.root / "reserved-original.bin"
        staged.rename(moved)
        staged.write_bytes(b"%PDF-1.4\\nreplacement")
        with self.assertRaisesRegex(PageToolError, "identity changed"):
            _verify_staging_file(staged, identity, 2 * 1024 * 1024)

    def test_staging_validation_rejects_hard_link_alias(self):
        staged = self.root / "result.pdf"
        identity = _reserve_staging_file(staged, 1024 * 1024)
        with staged.open("r+b") as stream:
            stream.write(b"%PDF-1.7\\n")
        alias = self.root / "external-stage-alias.bin"
        try:
            os.link(staged, alias)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hard-link creation unavailable on this host: {exc}")

        self.assertEqual(staged.stat().st_nlink, 2)
        with self.assertRaisesRegex(PageToolError, "hard-link aliases"):
            _verify_staging_file(staged, identity, 2 * 1024 * 1024)
        self.assertTrue(alias.is_file())

    def test_staging_validation_rejects_non_pdf_header(self):
        staged = self.root / "result.pdf"
        identity = _reserve_staging_file(staged, 1024 * 1024)
        with staged.open("r+b") as stream:
            stream.write(b"NOTPDF")
            stream.truncate()
        with self.assertRaisesRegex(PageToolError, "size is invalid|no PDF header"):
            _verify_staging_file(staged, identity, 2 * 1024 * 1024)

    def test_parent_refuses_publication_when_staging_validation_fails(self):
        with mock.patch("pdfstudio.page_isolation._verify_staging_file",
                        side_effect=PageToolError("staged PDF identity changed")):
            with self.assertRaisesRegex(PageToolError, "identity changed"):
                extract_pages(self.a, self.out, "1")
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_staging_reservation_failure_cleans_up_without_publishing(self):
        patch_target = ("pdfstudio.page_isolation.os.posix_fallocate"
                        if os.name == "posix" else "pdfstudio.page_isolation.os.ftruncate")
        with mock.patch(patch_target, side_effect=OSError("simulated disk full")):
            with self.assertRaisesRegex(PageToolError, "could not reserve estimated"):
                extract_pages(self.a, self.out, "1")
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_staging_reservation_failure_preserves_concurrent_path_replacement(self):
        staged = self.root / "reserved-race.bin"
        moved = self.root / "reserved-original.bin"
        sentinel = b"owned by concurrent creator"
        patch_target = ("pdfstudio.page_isolation.os.posix_fallocate"
                        if os.name == "posix" else "pdfstudio.page_isolation.os.ftruncate")

        def replace_path_then_fail(fd, *_args):
            # Close the injected descriptor so the same deterministic race
            # fixture works on Windows, where renaming an open file may fail.
            # The failure handler must tolerate this and preserve the new path.
            os.close(fd)
            staged.rename(moved)
            staged.write_bytes(sentinel)
            raise OSError(errno.ENOSPC, "simulated disk full after path replacement")

        with mock.patch(patch_target, side_effect=replace_path_then_fail):
            with self.assertRaisesRegex(PageToolError, "could not reserve estimated"):
                _reserve_staging_file(staged, 1024 * 1024)

        self.assertEqual(staged.read_bytes(), sentinel)
        self.assertTrue(moved.is_file())
        self.assertNotEqual((staged.stat().st_dev, staged.stat().st_ino),
                            (moved.stat().st_dev, moved.stat().st_ino))

    def test_existing_output_never_replaced(self):
        self.out.write_bytes(b"private-output")
        with self.assertRaises(FileExistsError):
            merge_pdfs([self.a, self.b], self.out)
        self.assertEqual(self.out.read_bytes(), b"private-output")

    def test_timeout_includes_worker_startup(self):
        original_start = multiprocessing.process.BaseProcess.start

        def delayed_start(process):
            time.sleep(0.1)
            return original_start(process)

        with mock.patch("multiprocessing.process.BaseProcess.start", new=delayed_start):
            with self.assertRaisesRegex(PageToolError, "timed out"):
                extract_pages(self.a, self.out, "1", timeout_seconds=0.02)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_only_parent_publishes_after_staging(self):
        real_link = os.link
        observed = []

        def check_publication(staged, destination):
            self.assertEqual(Path(destination).resolve(), self.out.resolve())
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

    def test_publication_rejects_hard_link_alias_created_during_link(self):
        real_link = os.link
        alias = self.root / "external-stage-alias.bin"

        def add_alias_then_publish(staged, destination):
            real_link(staged, alias)
            return real_link(staged, destination)

        with mock.patch("pdfstudio.page_isolation.os.link", side_effect=add_alias_then_publish):
            with self.assertRaisesRegex(PageToolError, "hard-link count|hard-link aliases"):
                extract_pages(self.a, self.out, "1")

        # Publication failed: the caller-visible path may remain, but it is
        # explicitly unverified. The extra alias is intentionally preserved.
        self.assertTrue(self.out.is_file())
        self.assertEqual((self.out.stat().st_dev, self.out.stat().st_ino),
                         (alias.stat().st_dev, alias.stat().st_ino))
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])
        self.assertTrue(alias.is_file())

    def test_publication_cleanup_does_not_delete_destination_replaced_after_identity_check(self):
        real_link = os.link
        path_type = type(self.out)
        real_lstat = path_type.lstat
        alias = self.root / "external-stage-alias.bin"
        displaced = self.root / "displaced-publication-inode.bin"
        sentinel = b"owned by concurrent destination creator"
        publication_complete = False
        replacement_done = False

        def alias_then_publish(staged, destination):
            nonlocal publication_complete
            real_link(staged, alias)
            result = real_link(staged, destination)
            publication_complete = True
            return result

        def lstat_with_path_replacement(path):
            nonlocal replacement_done
            info = real_lstat(path)
            if publication_complete and not replacement_done and Path(path) == self.out:
                # Return the inode observed by the caller, but replace the
                # pathname before its later cleanup action. This deterministically
                # models the check-then-unlink race.
                replacement_done = True
                Path(path).rename(displaced)
                Path(path).write_bytes(sentinel)
            return info

        with mock.patch("pdfstudio.page_isolation.os.link", side_effect=alias_then_publish):
            with mock.patch.object(path_type, "lstat", new=lstat_with_path_replacement):
                with self.assertRaisesRegex(PageToolError, "published PDF identity or hard-link count changed"):
                    extract_pages(self.a, self.out, "1")

        self.assertTrue(replacement_done)
        self.assertEqual(self.out.read_bytes(), sentinel)
        self.assertTrue(displaced.is_file())
        self.assertTrue(alias.is_file())

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
        real_hash = __import__("pdfstudio.page_isolation", fromlist=["_sha256_file_before_deadline"])._sha256_file_before_deadline

        def altered_digest(path, deadline, chunk_bytes=1024 * 1024):
            nonlocal calls
            calls += 1
            return real_hash(path, deadline, chunk_bytes) if calls == 1 else "0" * 64

        with mock.patch("pdfstudio.page_isolation._sha256_file_before_deadline",
                        side_effect=altered_digest):
            with self.assertRaises(PageToolError):
                extract_pages(self.a, self.out, "1")
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(self.a.read_bytes(), before)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_destination_created_at_publication_race_is_never_replaced(self):
        sentinel = b"created by concurrent owner"
        real_link = os.link

        def create_destination_then_link(staged, destination):
            Path(destination).write_bytes(sentinel)
            return real_link(staged, destination)

        with mock.patch("pdfstudio.page_isolation.os.link", side_effect=create_destination_then_link):
            with self.assertRaises(FileExistsError):
                extract_pages(self.a, self.out, "1")

        self.assertEqual(self.out.read_bytes(), sentinel)
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_staging_inode_replaced_at_publication_is_rejected_without_deleting_unverified_output(self):
        real_link = os.link

        def replace_staging_then_link(staged, destination):
            staged = Path(staged)
            displaced = staged.with_name("original-reservation.bin")
            staged.rename(displaced)
            staged.write_bytes(b"%PDF-1.7\\nreplacement inode")
            return real_link(staged, destination)

        with mock.patch("pdfstudio.page_isolation.os.link", side_effect=replace_staging_then_link):
            with self.assertRaisesRegex(PageToolError, "published PDF identity (changed|or hard-link count changed)"):
                extract_pages(self.a, self.out, "1")

        # The destination now names an inode that cannot be proven to belong
        # to this operation. Fail closed, but preserve it rather than deleting
        # a potentially concurrent owner's file.
        self.assertEqual(self.out.read_bytes(), b"%PDF-1.7\\nreplacement inode")
        self.assertEqual(list(self.root.glob(".pdfstudio-stage-*")), [])

    def test_mid_write_disk_full_cleans_partial_stage_and_preserves_source(self):
        staged = self.root / "reserved-result.pdf"
        staged.write_bytes(b"\\0" * 4096)
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        source_before = self.a.read_bytes()
        original_write = __import__(
            "pdfstudio.page_tools", fromlist=["_CappedWriter"]
        )._CappedWriter.write

        def partial_then_enospc(capped, data):
            original_write(capped, data[:max(1, len(data) // 2)])
            raise OSError(errno.ENOSPC, "simulated concurrent disk exhaustion")

        with mock.patch("pdfstudio.page_tools._CappedWriter.write", new=partial_then_enospc):
            with self.assertRaisesRegex(OSError, "concurrent disk exhaustion"):
                _publish(writer, staged, (self.a,), (sha256_file(self.a),), 1024 * 1024,
                         expected_pages=1)

        self.assertFalse(staged.exists())
        self.assertEqual(self.a.read_bytes(), source_before)
        self.assertFalse(self.out.exists())

    def test_invalid_limits_rejected(self):
        for settings in ({"max_pages": 0}, {"max_memory_mb": True},
                         {"free_space_margin_bytes": -1},
                         {"free_space_margin_bytes": True},
                         {"free_space_margin_bytes": 1.5},
                         {"timeout_seconds": float("inf")}, {"timeout_seconds": -1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                extract_pages(self.a, self.out, [0], **settings)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
