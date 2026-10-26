"""Local OS worker lock for crash-safe SQLite job ownership.

Python standard library only; advisory lock, not a distributed lock or sandbox.
The lock file MUST remain on the same private local host for all workers.
Never unlink lock files: replacing the inode would permit competing owners.
"""
from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _is_reparse_point(info) -> bool:
    """Return whether Windows marks a path as a reparse point."""
    mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(mask and getattr(info, "st_file_attributes", 0) & mask)


def _open_lock_file(path: Path):
    """Open a persistent regular lock file without following symlink aliases.

    POSIX requires O_NOFOLLOW; if the host does not provide it, fail closed.
    Windows has no equivalent flag in Python's portable os.open API, so check
    the path before opening and compare the opened handle with a fresh lstat.
    The Windows checks reject ordinary symlink/reparse aliases, but do not
    claim to eliminate every same-user path-replacement race.
    """
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise RuntimeError("symlink-safe lock opening is unavailable")
        flags |= nofollow
    else:
        try:
            before = path.lstat()
        except FileNotFoundError:
            before = None
        except OSError as exc:
            raise RuntimeError("lock path could not be inspected safely") from exc
        if before is not None and (
            stat.S_ISLNK(before.st_mode) or _is_reparse_point(before)
        ):
            raise RuntimeError("lock path must not be a symlink or reparse point")

    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError("lock file could not be opened safely") from exc

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise RuntimeError("lock path must be a regular file")
        if os.name == "nt":
            current = path.lstat()
            if (
                not stat.S_ISREG(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or _is_reparse_point(current)
                or (
                    opened.st_ino and current.st_ino
                    and (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                )
            ):
                raise RuntimeError("lock path changed while opening")
        return os.fdopen(fd, "r+b")
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def exclusive_worker_lock(database: Path, job_id: str) -> Iterator[None]:
    if os.name not in ("posix", "nt"):
        raise RuntimeError("worker lock unsupported on this platform")
    name = database.name + "." + hashlib.sha256(job_id.encode("utf-8")).hexdigest() + ".worker.lock"
    lock_path = database.resolve().with_name(name)
    with _open_lock_file(lock_path) as stream:
        # Windows byte-range locks can extend past EOF. Never read/write a
        # region while another process holds the Windows mandatory lock.
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another worker owns this job") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("another worker owns this job") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_artifact_directory_lock(directory: Path) -> Iterator[None]:
    """Serialize cooperating PDF-stage writers and cleanup for one local folder.

    The lock file is persistent by design: unlinking it could let two
    processes lock different inodes and both believe they own the directory.
    This is a local coordination primitive, not a distributed lock or sandbox.
    """
    if os.name not in ("posix", "nt"):
        raise RuntimeError("artifact directory lock unsupported on this platform")
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("artifact directory must be a directory")
    lock_path = root / ".pdfstudio-artifacts.lock"
    with _open_lock_file(lock_path) as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another worker owns this artifact directory") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("another worker owns this artifact directory") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
