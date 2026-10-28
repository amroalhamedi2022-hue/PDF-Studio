"""Regression tests for hard-link aliases on persistent worker lock files."""
import os
import tempfile
import unittest
from pathlib import Path

from pdfstudio.worker_lease import (
    exclusive_artifact_directory_lock,
    exclusive_worker_lock,
)


@unittest.skipUnless(os.name in ("posix", "nt"), "local POSIX/Windows lock semantics required")
class HardLinkLockTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _hardlink_or_skip(self, target: Path, alias: Path) -> None:
        try:
            os.link(target, alias)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("hard-link creation is unavailable on this host: %s" % exc)

    def test_worker_lock_rejects_hardlink_alias_without_touching_target(self):
        target = self.root / "unrelated-target.bin"
        target.write_bytes(b"must remain unchanged")
        database = self.root / "jobs.sqlite"
        job_id = "hard-link-regression"
        import hashlib
        lock_name = database.name + "." + hashlib.sha256(job_id.encode("utf-8")).hexdigest() + ".worker.lock"
        lock_path = database.with_name(lock_name)
        self._hardlink_or_skip(target, lock_path)

        with self.assertRaisesRegex(RuntimeError, "hard-link aliases"):
            with exclusive_worker_lock(database, job_id):
                self.fail("a hard-linked file must never become a worker lock")

        self.assertEqual(target.read_bytes(), b"must remain unchanged")
        self.assertEqual(target.stat().st_ino, lock_path.stat().st_ino)
        self.assertEqual(target.stat().st_nlink, 2)

    def test_artifact_lock_rejects_hardlink_alias_without_touching_target(self):
        target = self.root / "unrelated-artifacts-target.bin"
        target.write_bytes(b"must remain unchanged")
        lock_path = self.root / ".pdfstudio-artifacts.lock"
        self._hardlink_or_skip(target, lock_path)

        with self.assertRaisesRegex(RuntimeError, "hard-link aliases"):
            with exclusive_artifact_directory_lock(self.root):
                self.fail("a hard-linked file must never become an artifact lock")

        self.assertEqual(target.read_bytes(), b"must remain unchanged")
        self.assertEqual(target.stat().st_ino, lock_path.stat().st_ino)
        self.assertEqual(target.stat().st_nlink, 2)


if __name__ == "__main__":
    unittest.main()
