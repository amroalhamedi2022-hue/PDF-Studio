"""Resource-bounded POSIX worker for PDF page extraction and merge.

Not a security sandbox: process retains caller filesystem privileges. Windows
fails closed until Job Object confinement is implemented. Never logs PDF text.
"""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import tempfile
import time
from pathlib import Path

from .jobs import sha256_file


def _worker(pipe, action, sources, output, selection, digests, memory_bytes, cpu_seconds, max_pages):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
        import logging
        logging.getLogger("pypdf").setLevel(logging.CRITICAL)
        from .page_tools import _extract_pages_direct, _merge_pdfs_direct, PageToolError
        # Reject a changed source before parsing and immediately before publishing.
        if any(sha256_file(p) != h for p, h in zip(sources, digests)):
            pipe.send(("error", "source-changed"))
            return
        if action == "extract":
            _extract_pages_direct(sources[0], output, selection, digests, max_pages)
        elif action == "merge":
            _merge_pdfs_direct(sources, output, digests, max_pages)
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
                       max_pages: int = 10000) -> Path:
    from .page_tools import PageToolError, _validated_output, _validated_source
    if os.name != "posix":
        raise PageToolError("isolated page tools unavailable on this host")
    try:
        import resource
        if any(not hasattr(resource, name) for name in ("RLIMIT_AS", "RLIMIT_CPU", "RLIMIT_FSIZE")):
            raise ImportError("resource limits unavailable")
    except ImportError as exc:
        raise PageToolError("OS resource limits unavailable") from exc
    if action not in ("extract", "merge"):
        raise ValueError("unsupported operation")
    for key, value in (("max_memory_mb", max_memory_mb), ("max_pages", max_pages)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if action == "extract" and len(sources) != 1:
        raise ValueError("extract requires one source")
    if action == "merge" and len(sources) < 2:
        raise PageToolError("merge requires at least two PDF inputs")
    resolved = tuple(_validated_source(p) for p in sources)
    destination = _validated_output(output, resolved)
    digests = tuple(sha256_file(p) for p in resolved)
    # The child writes only into a private same-filesystem staging directory.
    # The parent alone publishes the finished result after worker acknowledgement
    # and source revalidation. A timed-out/crashed child cannot leave a new final
    # output behind; publication remains atomic and no-clobber via os.link.
    with tempfile.TemporaryDirectory(prefix=".pdfstudio-stage-", dir=destination.parent) as stage_dir:
        staged = Path(stage_dir) / "result.pdf"
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_worker, args=(child, action, tuple(map(str, resolved)),
                           str(staged), selection, digests,
                           max_memory_mb * 1024 * 1024, max(2, math.ceil(timeout_seconds) + 1),
                           max_pages), daemon=True)
        try:
            proc.start()
            child.close()
            if not parent.poll(timeout_seconds):
                raise PageToolError("isolated page operation timed out")
            try:
                response = parent.recv()
            except (EOFError, OSError) as exc:
                raise PageToolError("isolated page worker terminated") from exc
            if response == ("error", "output-exists"):
                raise FileExistsError("output already exists")
            if response != ("ok",):
                raise PageToolError("isolated page operation rejected input or exceeded limits")
            if not staged.is_file() or staged.stat().st_size == 0:
                raise PageToolError("isolated page operation did not stage a PDF")
            if any(sha256_file(p) != digest for p, digest in zip(resolved, digests)):
                raise PageToolError("source changed during page operation")
            # This is the *only* publication of the caller-visible destination.
            # A pre-existing file or symlink causes FileExistsError, never replace.
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
