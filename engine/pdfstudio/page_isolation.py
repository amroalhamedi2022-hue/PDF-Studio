"""Resource-bounded PDF page worker on POSIX and Windows.

POSIX RLIMIT or Windows Job Object limits apply before PDF parsing. This is
NOT a security sandbox: workers retain caller filesystem privileges.
"""
from __future__ import annotations

import hashlib
import math
import multiprocessing as mp
import os
import tempfile
import time
from pathlib import Path

from .jobs import sha256_file
from .windows_job import WindowsJob, WindowsJobError


def _sha256_file_before_deadline(path: str | Path, deadline: float,
                                 chunk_bytes: int = 1024 * 1024) -> str:
    """Hash a source in bounded chunks while honoring the operation wall deadline."""
    if chunk_bytes < 1:
        raise ValueError("chunk_bytes must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("PDF operation deadline exceeded during source hashing")
            block = stream.read(chunk_bytes)
            if not block:
                break
            digest.update(block)
    if time.monotonic() >= deadline:
        raise TimeoutError("PDF operation deadline exceeded during source hashing")
    return digest.hexdigest()


def _require_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("PDF operation deadline exceeded")


def _worker(pipe, gate, action, sources, output, selection, digests, memory_bytes,
            cpu_seconds, max_pages, max_output_bytes):
    try:
        if os.name == "nt":
            # Parent assigns the Job Object before releasing this gate.
            if gate is None or not gate.wait(15):
                return
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            _, hard_file_size = resource.getrlimit(resource.RLIMIT_FSIZE)
            file_size_soft = (max_output_bytes if hard_file_size == resource.RLIM_INFINITY
                              else min(max_output_bytes, hard_file_size))
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_size_soft, hard_file_size))
        import logging
        logging.getLogger("pypdf").setLevel(logging.CRITICAL)
        from .page_tools import _extract_pages_direct, _merge_pdfs_direct, PageToolError
        # Reject a changed source before parsing and immediately before publishing.
        if any(sha256_file(p) != h for p, h in zip(sources, digests)):
            pipe.send(("error", "source-changed"))
            return
        if action == "extract":
            _extract_pages_direct(sources[0], output, selection, digests,
                                  max_pages, max_output_bytes)
        elif action == "merge":
            _merge_pdfs_direct(sources, output, digests, max_pages, max_output_bytes)
        else:
            pipe.send(("error", "operation"))
            return
        pipe.send(("ok",))
    except FileExistsError:
        pipe.send(("error", "output-exists"))
    except (ValueError, OSError, MemoryError):
        try:
            pipe.send(("error", "invalid-or-resource-limit"))
        except (OSError, MemoryError):
            pass
    except BaseException:
        try:
            pipe.send(("error", "worker-failed"))
        except (OSError, MemoryError):
            pass
    finally:
        pipe.close()


def run_page_operation(action: str, sources, output, selection=None, *,
                       timeout_seconds: float = 90.0, max_memory_mb: int = 768,
                       max_pages: int = 10000,
                       max_output_bytes: int = 1024 * 1024 * 1024) -> Path:
    from .page_tools import PageToolError, _validated_output, _validated_source
    if os.name not in ("posix", "nt"):
        raise PageToolError("isolated page tools unavailable on this host")
    if os.name == "posix":
        try:
            import resource
            if any(not hasattr(resource, name) for name in ("RLIMIT_AS", "RLIMIT_CPU", "RLIMIT_FSIZE")):
                raise ImportError("resource limits unavailable")
        except ImportError as exc:
            raise PageToolError("OS resource limits unavailable") from exc
    if action not in ("extract", "merge"):
        raise ValueError("unsupported operation")
    for key, value in (("max_memory_mb", max_memory_mb), ("max_pages", max_pages),
                       ("max_output_bytes", max_output_bytes)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if action == "extract" and len(sources) != 1:
        raise ValueError("extract requires one source")
    if action == "merge" and len(sources) < 2:
        raise PageToolError("merge requires at least two PDF inputs")
    # The timeout is a wall-clock budget for the entire operation, including
    # parent-side input validation and full-file SHA-256 preflight. Without this,
    # a large or slow source could spend unbounded time hashing before spawn.
    deadline = time.monotonic() + timeout_seconds
    try:
        resolved_items = []
        for source in sources:
            _require_deadline(deadline)
            resolved_items.append(_validated_source(source))
            _require_deadline(deadline)
        resolved = tuple(resolved_items)
        destination = _validated_output(output, resolved)
        _require_deadline(deadline)
        digests = tuple(_sha256_file_before_deadline(p, deadline) for p in resolved)
    except TimeoutError as exc:
        raise PageToolError("isolated page operation timed out") from exc
    # The child writes only into a private same-filesystem staging directory.
    # The parent alone publishes the finished result after worker acknowledgement
    # and source revalidation. A timed-out/crashed child cannot leave a new final
    # output behind; publication remains atomic and no-clobber via os.link.
    with tempfile.TemporaryDirectory(prefix=".pdfstudio-stage-", dir=destination.parent) as stage_dir:
        staged = Path(stage_dir) / "result.pdf"
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        gate = ctx.Event() if os.name == "nt" else None
        try:
            job = WindowsJob(max_memory_mb * 1024 * 1024) if os.name == "nt" else None
        except WindowsJobError as exc:
            child.close()
            parent.close()
            raise PageToolError("Windows worker limits unavailable") from exc
        proc = ctx.Process(target=_worker, args=(child, gate, action, tuple(map(str, resolved)),
                           str(staged), selection, digests,
                           max_memory_mb * 1024 * 1024, max(2, math.ceil(timeout_seconds) + 1),
                           max_pages, max_output_bytes), daemon=True)
        # The operation deadline includes process startup and Windows Job
        # Object assignment, not just the wait for the worker's first response.
        try:
            proc.start()
            child.close()
            if job is not None:
                try:
                    job.assign(proc.pid)
                except WindowsJobError as exc:
                    raise PageToolError("Windows worker could not be confined") from exc
                gate.set()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not parent.poll(remaining):
                raise PageToolError("isolated page operation timed out")
            try:
                response = parent.recv()
            except (EOFError, OSError) as exc:
                raise PageToolError("isolated page worker terminated") from exc
            if response == ("error", "output-exists"):
                raise FileExistsError("output already exists")
            if response != ("ok",):
                raise PageToolError("isolated page operation rejected input or exceeded limits")
            # A worker may acknowledge and then hang/crash or mutate the staging
            # inode. Never publish until the worker has fully exited cleanly.
            proc.join(timeout=max(0.0, deadline - time.monotonic()))
            if proc.is_alive() or proc.exitcode != 0:
                raise PageToolError("isolated page worker did not exit cleanly")
            if not staged.is_file() or staged.stat().st_size == 0:
                raise PageToolError("isolated page operation did not stage a PDF")
            try:
                _require_deadline(deadline)
                unchanged = all(
                    _sha256_file_before_deadline(path, deadline) == digest
                    for path, digest in zip(resolved, digests)
                )
                _require_deadline(deadline)
            except TimeoutError as exc:
                raise PageToolError("isolated page operation timed out") from exc
            if not unchanged:
                raise PageToolError("source changed during page operation")
            # This is the *only* publication of the caller-visible destination.
            # A pre-existing file or symlink causes FileExistsError, never replace.
            try:
                _require_deadline(deadline)
            except TimeoutError as exc:
                raise PageToolError("isolated page operation timed out") from exc
            os.link(staged, destination)
            return destination
        finally:
            child.close()
            parent.close()
            if proc.pid is not None:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=1)
                    if proc.is_alive():
                        proc.kill()
                proc.join(timeout=1)
            proc.close()
            if job is not None:
                job.close()
