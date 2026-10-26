"""Resource-bounded PDF page worker on POSIX and Windows.

POSIX RLIMIT or Windows Job Object limits apply before PDF parsing. This is
NOT a security sandbox: workers retain caller filesystem privileges.
"""
from __future__ import annotations

import hashlib
import math
import multiprocessing as mp
import os
import shutil
import stat
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


_DEFAULT_FREE_SPACE_MARGIN_BYTES = 16 * 1024 * 1024
_MIN_ESTIMATED_OUTPUT_BYTES = 1024 * 1024


def _estimate_output_bytes(sources, max_output_bytes: int) -> int:
    """Conservative heuristic: 2x input bytes plus 1 MiB per source, capped."""
    from .page_tools import PageToolError

    try:
        input_bytes = sum(Path(source).stat().st_size for source in sources)
    except OSError as exc:
        raise PageToolError("cannot estimate PDF output space from source sizes") from exc
    estimate = max(_MIN_ESTIMATED_OUTPUT_BYTES,
                   (2 * input_bytes) + (len(sources) * _MIN_ESTIMATED_OUTPUT_BYTES))
    return min(max_output_bytes, estimate)


def _check_free_space(directory: Path, estimated_output_bytes: int,
                      free_space_margin_bytes: int) -> None:
    """Fail before worker spawn unless estimate plus safety margin is available."""
    from .page_tools import PageToolError

    try:
        free_bytes = shutil.disk_usage(directory).free
    except OSError as exc:
        raise PageToolError("cannot determine free disk space for PDF output") from exc
    required_bytes = estimated_output_bytes + free_space_margin_bytes
    if free_bytes < required_bytes:
        raise PageToolError(
            "insufficient free space for PDF output estimate "
            f"(free={free_bytes}, estimated_output={estimated_output_bytes}, "
            f"required_margin={free_space_margin_bytes})"
        )


def _reserve_staging_file(path: Path, estimated_output_bytes: int) -> tuple[int, int]:
    """Reserve output space and return the reserved file's device/inode identity."""
    from .page_tools import PageToolError

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except OSError as exc:
        raise PageToolError("could not reserve estimated PDF output space") from exc
    try:
        if os.name == "posix":
            fallocate = getattr(os, "posix_fallocate", None)
            if fallocate is None:
                raise OSError("physical preallocation is unavailable on this POSIX host")
            fallocate(fd, 0, estimated_output_bytes)
        else:
            # Windows CRT file extension allocates the reserved staging length;
            # the later writer truncates it to the actual PDF size.
            os.ftruncate(fd, estimated_output_bytes)
        os.fsync(fd)
        if os.fstat(fd).st_size != estimated_output_bytes:
            raise OSError("reserved staging file has an unexpected size")
    except BaseException as exc:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        if isinstance(exc, (OSError, NotImplementedError)):
            raise PageToolError("could not reserve estimated PDF output space") from exc
        raise
    else:
        info = os.fstat(fd)
        os.close(fd)
        return (info.st_dev, info.st_ino)


def _verify_staging_file(path: Path, expected_identity: tuple[int, int],
                         max_output_bytes: int) -> None:
    """Reject replaced, linked, non-regular, oversized, or non-PDF staging output.

    The worker must exit before this check. Identity is captured from the
    parent's preallocated inode, so a worker that replaces result.pdf with a
    symlink or a different file cannot trick the parent into publishing it.
    """
    from .page_tools import PageToolError

    try:
        path_info = path.lstat()
    except OSError as exc:
        raise PageToolError("isolated page operation did not stage a regular PDF") from exc
    if (not stat.S_ISREG(path_info.st_mode)
            or (path_info.st_dev, path_info.st_ino) != expected_identity):
        raise PageToolError("staged PDF identity changed; refusing publication")
    if path_info.st_size < 5 or path_info.st_size > max_output_bytes:
        raise PageToolError("staged PDF size is invalid; refusing publication")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PageToolError("staged PDF could not be opened safely") from exc
    try:
        opened_info = os.fstat(fd)
        if (not stat.S_ISREG(opened_info.st_mode)
                or (opened_info.st_dev, opened_info.st_ino) != expected_identity
                or opened_info.st_size != path_info.st_size
                or opened_info.st_size < 5
                or opened_info.st_size > max_output_bytes):
            raise PageToolError("staged PDF changed during validation")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            if stream.read(5) != b"%PDF-":
                raise PageToolError("staged output has no PDF header")
        after_info = os.fstat(fd)
        if ((after_info.st_dev, after_info.st_ino) != expected_identity
                or after_info.st_size != opened_info.st_size
                or after_info.st_mtime_ns != opened_info.st_mtime_ns):
            raise PageToolError("staged PDF changed during validation")
    finally:
        os.close(fd)


def _publish_verified_staging_file(staged: Path, destination: Path,
                                   expected_identity: tuple[int, int],
                                   max_output_bytes: int) -> Path:
    """Publish only if the destination is the validated staging inode.

    os.link provides atomic no-clobber behavior for the destination. The
    post-link identity check closes a second gap: a staging path replaced after
    pre-publication validation must not be reported as a successful export.
    Cleanup is limited to a destination that still aliases the unexpected
    staging inode created by this publication attempt; unrelated racing outputs
    are never removed.
    """
    from .page_tools import PageToolError

    _verify_staging_file(staged, expected_identity, max_output_bytes)
    # Hard-link creation is atomic and does not overwrite an existing output.
    # If another process creates the destination first, preserve its bytes.
    os.link(staged, destination)

    try:
        published_info = destination.lstat()
        staged_info = staged.lstat()
    except OSError as exc:
        raise PageToolError("published PDF disappeared during validation") from exc

    published_identity = (published_info.st_dev, published_info.st_ino)
    staging_identity = (staged_info.st_dev, staged_info.st_ino)
    if not stat.S_ISREG(published_info.st_mode) or published_identity != expected_identity:
        # We may remove only the link this call just created: destination and
        # current staging path must still identify the same unexpected inode.
        # If another actor replaced destination independently, leave it alone.
        if published_identity == staging_identity and published_identity != expected_identity:
            try:
                destination.unlink()
            except OSError:
                pass
        raise PageToolError("published PDF identity changed; refusing publication")

    # Validate the caller-visible path itself, not only the temporary name.
    # This also rechecks the PDF header, size cap, and metadata stability.
    try:
        _verify_staging_file(destination, expected_identity, max_output_bytes)
    except PageToolError:
        try:
            current = destination.lstat()
            if (stat.S_ISREG(current.st_mode)
                    and (current.st_dev, current.st_ino) == expected_identity):
                destination.unlink()
        except OSError:
            pass
        raise
    return destination


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
        from .page_tools import _extract_pages_direct, _merge_pdfs_direct, _rotate_pages_direct, PageToolError
        # Reject a changed source before parsing and immediately before publishing.
        if any(sha256_file(p) != h for p, h in zip(sources, digests)):
            pipe.send(("error", "source-changed"))
            return
        if action == "extract":
            _extract_pages_direct(sources[0], output, selection, digests,
                                  max_pages, max_output_bytes)
        elif action == "merge":
            _merge_pdfs_direct(sources, output, digests, max_pages, max_output_bytes)
        elif action == "rotate":
            pages, rotation = selection
            _rotate_pages_direct(sources[0], output, pages, rotation, digests,
                                 max_pages, max_output_bytes)
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
                       max_output_bytes: int = 1024 * 1024 * 1024,
                       free_space_margin_bytes: int = _DEFAULT_FREE_SPACE_MARGIN_BYTES) -> Path:
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
    if action not in ("extract", "merge", "rotate"):
        raise ValueError("unsupported operation")
    for key, value in (("max_memory_mb", max_memory_mb), ("max_pages", max_pages),
                       ("max_output_bytes", max_output_bytes)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    if (not isinstance(free_space_margin_bytes, int)
            or isinstance(free_space_margin_bytes, bool) or free_space_margin_bytes < 0):
        raise ValueError("free_space_margin_bytes must be a non-negative integer")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if action in ("extract", "rotate") and len(sources) != 1:
        raise ValueError(f"{action} requires one source")
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
        estimated_output_bytes = _estimate_output_bytes(resolved, max_output_bytes)
        _check_free_space(destination.parent, estimated_output_bytes, free_space_margin_bytes)
        _require_deadline(deadline)
        digests = tuple(_sha256_file_before_deadline(p, deadline) for p in resolved)
        _require_deadline(deadline)
        # Recheck after hashing to catch disk usage changes during source preflight.
        estimated_output_bytes = _estimate_output_bytes(resolved, max_output_bytes)
        _check_free_space(destination.parent, estimated_output_bytes, free_space_margin_bytes)
        _require_deadline(deadline)
    except TimeoutError as exc:
        raise PageToolError("isolated page operation timed out") from exc
    # The child writes only into a private same-filesystem staging directory.
    # The parent alone publishes the finished result after worker acknowledgement
    # and source revalidation. A timed-out/crashed child cannot leave a new final
    # output behind; publication remains atomic and no-clobber via os.link.
    with tempfile.TemporaryDirectory(prefix=".pdfstudio-stage-", dir=destination.parent) as stage_dir:
        staged = Path(stage_dir) / "result.pdf"
        try:
            staging_identity = _reserve_staging_file(staged, estimated_output_bytes)
            _require_deadline(deadline)
        except TimeoutError as exc:
            raise PageToolError("isolated page operation timed out") from exc
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
            _verify_staging_file(staged, staging_identity, max_output_bytes)
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
            return _publish_verified_staging_file(
                staged, destination, staging_identity, max_output_bytes
            )
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
