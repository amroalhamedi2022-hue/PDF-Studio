"""Local OS worker lock for crash-safe SQLite job ownership.

Python standard library only; advisory lock, not a distributed lock or sandbox.
The lock file MUST remain on the same private local host for all workers.
Never unlink lock files: replacing the inode would permit competing owners.
"""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def exclusive_worker_lock(database: Path, job_id: str) -> Iterator[None]:
    if os.name not in ("posix", "nt"):
        raise RuntimeError("worker lock unsupported on this platform")
    name = database.name + "." + hashlib.sha256(job_id.encode("utf-8")).hexdigest() + ".worker.lock"
    lock_path = database.resolve().with_name(name)
    with lock_path.open("a+b") as stream:
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
